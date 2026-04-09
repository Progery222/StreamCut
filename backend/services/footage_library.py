import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "library.json"


@dataclass
class FootageEntry:
    path: Path
    duration: float
    source: str
    category: str
    width: int
    height: int

    @classmethod
    def from_dict(cls, data: dict, root: Path) -> "FootageEntry":
        return cls(
            path=root / data["path"],
            duration=float(data["duration"]),
            source=str(data["source"]),
            category=str(data["category"]),
            width=int(data["width"]),
            height=int(data["height"]),
        )


class FootageLibrary:
    """Reader for the prepared footage library produced by prepare_footage CLI."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._entries: list[FootageEntry] = []
        self._loaded = False

    def load(self) -> "FootageLibrary":
        manifest_path = self.root / MANIFEST_FILENAME
        if not manifest_path.exists():
            self._entries = []
            self._loaded = True
            return self

        with manifest_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        self._entries = [FootageEntry.from_dict(item, self.root) for item in raw]
        self._loaded = True
        return self

    def is_empty(self) -> bool:
        if not self._loaded:
            self.load()
        return len(self._entries) == 0

    def list_categories(self) -> list[str]:
        if not self._loaded:
            self.load()
        return sorted({e.category for e in self._entries})

    def pick(self, duration: float, category: Optional[str], seed: int) -> Path:
        """Deterministically pick a chunk closest in duration to `duration`.

        Strategy: prefer the smallest chunk with chunk_duration >= duration.
        If none exist, pick the longest available (caller will loop it via -stream_loop).
        """
        if not self._loaded:
            self.load()
        if not self._entries:
            raise RuntimeError(
                "Footage library is empty. Run "
                "`python -m backend.scripts.prepare_footage --source-dir storage/adhd_cut` first."
            )

        candidates = self._entries
        if category:
            filtered = [e for e in self._entries if e.category == category]
            if filtered:
                candidates = filtered
            else:
                logger.warning(
                    f"Category '{category}' not found in footage library; using all categories"
                )

        rng = random.Random(seed)

        # Prefer chunks long enough to cover the clip without looping
        long_enough = [e for e in candidates if e.duration >= duration]
        if long_enough:
            # Take the 3 shortest "good fits" and pick one randomly for variety
            long_enough.sort(key=lambda e: e.duration)
            pool = long_enough[: min(3, len(long_enough))]
            return rng.choice(pool).path

        # No chunk long enough — pick from the 3 longest available, will be looped
        candidates_sorted = sorted(candidates, key=lambda e: e.duration, reverse=True)
        pool = candidates_sorted[: min(3, len(candidates_sorted))]
        return rng.choice(pool).path
