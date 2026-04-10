"""Reader and selector for the prepared footage library (v2 format).

Library file is `library.json` at the library root, produced by
`backend/scripts/prepare_footage.py`. Structure:

    {
      "version": 2,
      "categories": {
        "paint": {
          "sources": [...],
          "total_chunks": 315,
          "by_duration": {
            "15": [{"path": "paint/xxxx_chunk_0001_d15.0s.mp4", "source_id": "xxxx"}, ...],
            "20": [...],
            "30": [...],
            "45": [...],
            "60": [...]
          }
        }
      }
    }

Selection logic (session-aware, Redis-backed dedup):
    1. Pick smallest bucket >= requested duration within the category.
    2. Filter out chunks already marked used in `footage:used:session:<session_id>`.
    3. If fresh candidates exist → pick one, SADD to used set, return path.
    4. Else walk up to the next bigger bucket and retry.
    5. If every bucket exhausted → warn and recycle (pick any chunk from the best bucket).
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "library.json"
LIBRARY_VERSION = 2
SESSION_KEY_PREFIX = "footage:used:session:"
SESSION_TTL_SECONDS = 86400  # 24h, matches existing job state TTL


class FootageLibrary:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._data: dict = {}
        self._loaded = False

    def load(self) -> FootageLibrary:
        import json  # local import keeps cold-start footprint tiny

        manifest_path = self.root / MANIFEST_FILENAME

        # Try local manifest first, fall back to MinIO
        if not manifest_path.exists():
            from services.storage import storage

            if storage.enabled:
                s3_key = f"footage_library/{MANIFEST_FILENAME}"
                self.root.mkdir(parents=True, exist_ok=True)
                if storage.download(s3_key, manifest_path):
                    logger.info(f"Loaded footage manifest from MinIO: {s3_key}")

        if not manifest_path.exists():
            self._data = {"version": LIBRARY_VERSION, "categories": {}}
            self._loaded = True
            return self

        with manifest_path.open("r", encoding="utf-8") as f:
            self._data = json.load(f)
        self._loaded = True

        version = self._data.get("version")
        if version != LIBRARY_VERSION:
            raise RuntimeError(
                f"Footage library at {manifest_path} is v{version}, expected v{LIBRARY_VERSION}. "
                f"Re-run `python -m scripts.prepare_footage --source-dir storage/adhd_cut`."
            )
        return self

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        if not self._loaded:
            self.load()
        cats = self._data.get("categories", {})
        return sum(c.get("total_chunks", 0) for c in cats.values()) == 0

    def list_categories(self) -> list[str]:
        if not self._loaded:
            self.load()
        return sorted(self._data.get("categories", {}).keys())

    def stats(self) -> dict[str, dict[str, int]]:
        """Return per-category chunk counts grouped by duration bucket."""
        if not self._loaded:
            self.load()
        out: dict[str, dict[str, int]] = {}
        for cat_name, cat in self._data.get("categories", {}).items():
            out[cat_name] = {bucket: len(chunks) for bucket, chunks in cat.get("by_duration", {}).items()}
            out[cat_name]["_total"] = cat.get("total_chunks", 0)
        return out

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _bucket_keys_sorted(self, category: str) -> list[int]:
        cat = self._data.get("categories", {}).get(category)
        if not cat:
            return []
        return sorted(int(k) for k in cat.get("by_duration", {}).keys())

    def _chunks_in_bucket(self, category: str, bucket: int) -> list[dict]:
        cat = self._data.get("categories", {}).get(category, {})
        return list(cat.get("by_duration", {}).get(str(bucket), []))

    def _resolve_category(self, requested: str | None) -> str:
        """Pick the category to use. If requested is missing/unknown, fall back to any."""
        all_cats = self.list_categories()
        if not all_cats:
            raise RuntimeError("Footage library is empty — no categories available.")
        if requested and requested in all_cats:
            return requested
        if requested:
            logger.warning(
                f"Category {requested!r} not found in footage library; falling back to all categories {all_cats}"
            )
        return all_cats[0]  # deterministic fallback

    def pick(
        self,
        duration: float,
        category: str | None,
        session_id: str,
        redis_client,
        seed: int | None = None,
    ) -> Path:
        """Pick a fresh (unused in this session) chunk with duration >= requested.

        Writes the picked path to `footage:used:session:<session_id>` so subsequent
        picks in the same session won't collide.
        """
        if not self._loaded:
            self.load()
        if self.is_empty():
            raise RuntimeError(
                "Footage library is empty. Run `python -m scripts.prepare_footage --source-dir storage/adhd_cut` first."
            )

        cat_name = self._resolve_category(category)
        bucket_keys = self._bucket_keys_sorted(cat_name)
        if not bucket_keys:
            raise RuntimeError(f"Category {cat_name!r} has no chunks")

        # Find the starting bucket: smallest bucket >= requested duration.
        # Fall through to larger buckets if smaller ones are exhausted.
        start_bucket = None
        for b in bucket_keys:
            if b >= duration:
                start_bucket = b
                break
        if start_bucket is None:
            # Requested duration exceeds largest bucket — use the largest anyway
            start_bucket = bucket_keys[-1]
            logger.warning(
                f"Clip duration {duration:.1f}s exceeds largest bucket {start_bucket}s "
                f"in category {cat_name!r}; chunk will be trimmed to clip length"
            )

        session_key = SESSION_KEY_PREFIX + session_id
        used: set[bytes] = set(redis_client.smembers(session_key))

        rng = random.Random(seed) if seed is not None else random.Random()

        # Walk upward from start_bucket, pick the first bucket that has a fresh candidate
        search_order = [b for b in bucket_keys if b >= start_bucket]
        for bucket in search_order:
            candidates = self._chunks_in_bucket(cat_name, bucket)
            fresh = [c for c in candidates if c["path"].encode("utf-8") not in used]
            if fresh:
                picked = rng.choice(fresh)
                picked_path = picked["path"]
                pipe = redis_client.pipeline()
                pipe.sadd(session_key, picked_path)
                pipe.expire(session_key, SESSION_TTL_SECONDS)
                pipe.execute()
                logger.info(
                    f"Footage pick: category={cat_name} bucket={bucket}s "
                    f"path={picked_path} (session={session_id[:8]}…, fresh={len(fresh)}/{len(candidates)})"
                )
                return self._ensure_local(picked_path)

        # Every bucket in the preferred range is exhausted — recycle.
        logger.warning(
            f"Footage library exhausted for category={cat_name!r} duration>={start_bucket}s "
            f"in session={session_id[:8]}…; recycling (may repeat footage)"
        )
        candidates = self._chunks_in_bucket(cat_name, start_bucket) or self._chunks_in_bucket(cat_name, bucket_keys[-1])
        picked = rng.choice(candidates)
        return self._ensure_local(picked["path"])

    def _ensure_local(self, relative_path: str) -> Path:
        """Return a local Path for the chunk, downloading from MinIO if needed."""
        local = self.root / relative_path
        if local.exists():
            return local

        from services.storage import storage

        if storage.enabled:
            s3_key = f"footage_library/{relative_path}"
            local.parent.mkdir(parents=True, exist_ok=True)
            if storage.download(s3_key, local):
                logger.info(f"Footage chunk fetched from MinIO: {s3_key}")
                return local

        raise FileNotFoundError(
            f"Footage chunk not found locally or in MinIO: {relative_path}"
        )
