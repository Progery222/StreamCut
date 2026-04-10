"""CLI to prepare a library of short footage chunks from raw long videos.

Usage:
    python -m scripts.prepare_footage --source-dir storage/adhd_cut
    python -m scripts.prepare_footage --source storage/adhd_cut/foo.mp4 --category gameplay
    python -m scripts.prepare_footage --source-dir storage/adhd_cut --dry-run

Output structure:
    storage/footage_library/
      <category>/
        <source_id>_chunk_<idx>_d<dur>s.mp4   # source_id = md5[:4] of source filename
      library.json                             # v2 format, grouped by category/duration

Category resolution (in order):
    1. --category CLI flag (applies to all sources in this run, explicit override)
    2. storage/adhd_cut/categories.json manifest (per-file mapping)
    3. Hard fail with clear message
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Allow invocation both as `python -m scripts.prepare_footage` (inside container, /app root)
# and as `python backend/scripts/prepare_footage.py` (local development).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger("prepare_footage")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}

# Uniform distribution over five useful clip-matching durations.
DEFAULT_DISTRIBUTION: dict[float, int] = {
    15.0: 1,
    20.0: 1,
    30.0: 1,
    45.0: 1,
    60.0: 1,
}

MANIFEST_FILENAME = "library.json"
CATEGORIES_MANIFEST = "categories.json"
LIBRARY_VERSION = 2


@dataclass
class ChunkPlan:
    start: float
    duration: float


@dataclass
class CutResult:
    rel_path: str
    duration: float
    source_id: str


# ----------------------------------------------------------------------------
# Pure functions
# ----------------------------------------------------------------------------


def plan_chunks(
    total_dur: float,
    skip_intro: float,
    skip_outro: float,
    distribution: dict[float, int],
    rng: random.Random,
    max_chunks: int | None = None,
) -> list[ChunkPlan]:
    """Sequential single-pass walk through the timeline.

    No overlap: each chunk starts where the previous one ended.
    No content duplication: cursor only moves forward.
    """
    if total_dur <= skip_intro + skip_outro:
        return []

    durations = list(distribution.keys())
    weights = list(distribution.values())
    if not durations or sum(weights) == 0:
        raise ValueError("distribution must be non-empty with positive weights")

    end_limit = total_dur - skip_outro
    cursor = skip_intro
    chunks: list[ChunkPlan] = []

    while cursor < end_limit:
        remaining = end_limit - cursor
        viable = [d for d in durations if d <= remaining]
        if not viable:
            break

        viable_weights = [distribution[d] for d in viable]
        chunk_dur = rng.choices(viable, weights=viable_weights, k=1)[0]

        chunks.append(ChunkPlan(start=cursor, duration=chunk_dur))
        cursor += chunk_dur

        if max_chunks is not None and len(chunks) >= max_chunks:
            break

    return chunks


def source_id_for(filename: str) -> str:
    """Stable short ID for a source file (first 4 hex chars of md5(filename))."""
    return hashlib.md5(filename.encode("utf-8")).hexdigest()[:4]


# ----------------------------------------------------------------------------
# FFmpeg / ffprobe wrappers
# ----------------------------------------------------------------------------


def probe_duration(path: Path) -> float:
    """Probe the VIDEO stream duration — not format/audio.

    Some sources (especially YouTube downloads) have video ending earlier than audio.
    Format duration reports the longest stream (usually audio), which leads to empty
    chunks when slicing past the actual video end. Always probe the video stream.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out = result.stdout.strip()
    if not out or out == "N/A":
        # Fallback to format duration if video stream doesn't expose duration
        cmd_fallback = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        result = subprocess.run(cmd_fallback, capture_output=True, text=True, check=True)
        out = result.stdout.strip()
    return float(out)


def probe_dimensions(path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    parts = result.stdout.strip().split(",")
    return int(parts[0]), int(parts[1])


SCALE_FILTER = "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease"


def cut_chunks_segmented(
    source: Path,
    plans: list[ChunkPlan],
    out_folder: Path,
    source_id: str,
    category: str,
) -> list[CutResult]:
    """Single-pass slicing via ffmpeg's segment muxer.

    Reads/decodes the source ONCE, encoder runs ONCE, ffmpeg auto-splits the
    output into files at the specified cumulative times. Massively faster than
    invoking ffmpeg 100+ times separately.

    Requires: plans are sequential (each chunk starts where previous ended).
    plan_chunks() guarantees this.

    Pipeline:
      1. `-ss first_start` fast-seek to where the first chunk begins
      2. `-t total_duration` stop after the sum of chunk durations
      3. `-vf scale=...` downscale sources above 1920x1080 on the fly
      4. `-force_key_frames` insert keyframes exactly at segment boundaries
         so libx264 can cut cleanly (without this, segment cuts would snap to
         the nearest existing keyframe)
      5. `-f segment -segment_times` split output into files at those times
      6. Rename temp outputs to {source_id}_chunk_{idx}_d{dur}s.mp4

    Afterwards validates every expected output file and drops any that are
    empty or under 5KB.
    """
    if not plans:
        return []

    first_start = plans[0].start
    total_dur = sum(p.duration for p in plans)

    # Cumulative time boundaries RELATIVE to the trimmed input (after -ss).
    # For N chunks we need N-1 boundaries; the final chunk runs to end-of-input.
    cumulative: list[str] = []
    acc = 0.0
    for p in plans[:-1]:
        acc += p.duration
        cumulative.append(f"{acc:.3f}")
    segment_times = ",".join(cumulative) if cumulative else ""

    # Clean up any leftover temp segments from a previous aborted run
    for stale in out_folder.glob(f"__{source_id}_seg_*.mp4"):
        stale.unlink()

    temp_pattern = out_folder / f"__{source_id}_seg_%04d.mp4"

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(first_start),
        "-i",
        str(source),
        "-t",
        str(total_dur),
        "-an",
        "-vf",
        SCALE_FILTER,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-avoid_negative_ts",
        "make_zero",
    ]
    if segment_times:
        cmd += [
            "-force_key_frames",
            segment_times,
            "-f",
            "segment",
            "-segment_times",
            segment_times,
            "-reset_timestamps",
            "1",
        ]
    else:
        # Single chunk — no segmentation, just a direct output.
        cmd += ["-f", "segment", "-segment_times", "0.001", "-reset_timestamps", "1"]
    cmd.append(str(temp_pattern))

    logger.info(f"  single-pass ffmpeg: {len(plans)} segments, total {total_dur:.0f}s")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg segment failed: {proc.stderr[-800:]}")

    # Rename temp files to final chunk names; validate each.
    results: list[CutResult] = []
    for idx, plan in enumerate(plans):
        temp_path = out_folder / f"__{source_id}_seg_{idx:04d}.mp4"
        if not temp_path.exists() or temp_path.stat().st_size < 5000:
            if temp_path.exists():
                temp_path.unlink()
            logger.error(
                f"    segment {idx} missing/empty (plan start={plan.start:.1f}s dur={plan.duration:.1f}s) — skipping"
            )
            continue

        chunk_name = f"{source_id}_chunk_{idx + 1:04d}_d{plan.duration:04.1f}s.mp4"
        final_path = out_folder / chunk_name
        temp_path.rename(final_path)
        results.append(
            CutResult(
                rel_path=f"{category}/{chunk_name}",
                duration=plan.duration,
                source_id=source_id,
            )
        )

    # Sanity: clean any trailing temp segments ffmpeg may have produced beyond N
    for leftover in out_folder.glob(f"__{source_id}_seg_*.mp4"):
        leftover.unlink()

    return results


# ----------------------------------------------------------------------------
# Manifest (v2 library.json)
# ----------------------------------------------------------------------------


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {
            "version": LIBRARY_VERSION,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "categories": {},
        }
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != LIBRARY_VERSION:
        logger.warning(f"Existing {path.name} is v{data.get('version')}, overwriting with v{LIBRARY_VERSION}")
        return {
            "version": LIBRARY_VERSION,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "categories": {},
        }
    return data


def save_manifest(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def merge_source_into_manifest(
    manifest: dict,
    category: str,
    source_id: str,
    source_filename: str,
    source_duration: float,
    width: int,
    height: int,
    results: list[CutResult],
) -> None:
    """Merge a newly-sliced source into the manifest (in place).

    Idempotent: re-running on the same source_id replaces its entries cleanly.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    cats = manifest.setdefault("categories", {})
    cat = cats.setdefault(
        category,
        {
            "sources": [],
            "total_chunks": 0,
            "width": width,
            "height": height,
            "by_duration": {},
        },
    )

    # Remove any existing entries for this source_id (idempotent re-run)
    for bucket_key in list(cat["by_duration"].keys()):
        cat["by_duration"][bucket_key] = [c for c in cat["by_duration"][bucket_key] if c["source_id"] != source_id]
        if not cat["by_duration"][bucket_key]:
            del cat["by_duration"][bucket_key]
    cat["sources"] = [s for s in cat["sources"] if s["id"] != source_id]

    # Register source
    cat["sources"].append(
        {
            "id": source_id,
            "filename": source_filename,
            "total_duration": round(source_duration, 2),
            "sliced_at": now,
        }
    )

    # Add new chunks bucketed by duration
    for r in results:
        bucket_key = str(int(round(r.duration)))
        cat["by_duration"].setdefault(bucket_key, []).append(
            {
                "path": r.rel_path,
                "source_id": r.source_id,
            }
        )

    # Recount
    cat["total_chunks"] = sum(len(v) for v in cat["by_duration"].values())
    # Track first seen dims (in case sources differ — use the first)
    cat.setdefault("width", width)
    cat.setdefault("height", height)


# ----------------------------------------------------------------------------
# Category resolution
# ----------------------------------------------------------------------------


def load_categories_manifest(source_dir: Path) -> dict[str, str]:
    manifest_path = source_dir / CATEGORIES_MANIFEST
    if not manifest_path.exists():
        return {}
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(f"{manifest_path} must be a JSON object, ignoring")
            return {}
        return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read {manifest_path}: {e}")
        return {}


def resolve_category(
    source_path: Path,
    cli_category: str | None,
    categories_map: dict[str, str],
) -> str:
    """CLI flag wins (explicit override); otherwise manifest lookup; otherwise fail."""
    if cli_category:
        return cli_category
    if source_path.name in categories_map:
        return categories_map[source_path.name]
    raise RuntimeError(
        f"No category for {source_path.name!r}. Add it to {CATEGORIES_MANIFEST} or pass --category NAME."
    )


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------


def source_already_sliced(manifest: dict, source_id: str) -> str | None:
    """Return category name if this source_id is already present in the manifest."""
    for cat_name, cat in manifest.get("categories", {}).items():
        for s in cat.get("sources", []):
            if s.get("id") == source_id:
                return cat_name
    return None


def process_source(
    source_path: Path,
    output_root: Path,
    category: str,
    args: argparse.Namespace,
    manifest: dict,
) -> list[CutResult]:
    source_id = source_id_for(source_path.name)

    # Skip if already sliced (unless --force)
    if not args.force:
        existing_cat = source_already_sliced(manifest, source_id)
        if existing_cat is not None:
            logger.info(
                f"Skipping {source_path.name} — already sliced "
                f"(source_id={source_id}, category={existing_cat}). Pass --force to re-slice."
            )
            return []

    logger.info(f"Probing {source_path.name}... (category={category})")
    total_dur = probe_duration(source_path)
    src_w, src_h = probe_dimensions(source_path)
    logger.info(f"  duration={total_dur:.1f}s, size={src_w}x{src_h}")

    out_folder = output_root / category

    seed = args.seed if args.seed is not None else int(time.time())
    rng = random.Random(seed)

    distribution = args.distribution or DEFAULT_DISTRIBUTION
    plans = plan_chunks(
        total_dur=total_dur,
        skip_intro=args.skip_intro,
        skip_outro=args.skip_outro,
        distribution=distribution,
        rng=rng,
        max_chunks=args.max_chunks,
    )
    logger.info(f"  planned {len(plans)} chunks (seed={seed}, source_id={source_id})")

    if args.dry_run:
        for i, p in enumerate(plans[:10], start=1):
            logger.info(f"    [{i}] start={p.start:.1f}s dur={p.duration:.1f}s")
        if len(plans) > 10:
            logger.info(f"    ... ({len(plans) - 10} more)")
        return []

    out_folder.mkdir(parents=True, exist_ok=True)

    # Re-run cleanup: drop any existing chunks for this source_id so segmentation
    # starts from a clean slate (already guaranteed by the skip-already-sliced
    # check upstream, but we also drop leftovers from prior aborted runs).
    stale = list(out_folder.glob(f"{source_id}_chunk_*.mp4"))
    for f in stale:
        f.unlink()
    if stale:
        logger.info(f"  removed {len(stale)} stale chunks from previous run")

    results = cut_chunks_segmented(
        source=source_path,
        plans=plans,
        out_folder=out_folder,
        source_id=source_id,
        category=category,
    )
    logger.info(f"  produced {len(results)}/{len(plans)} chunks")

    merge_source_into_manifest(
        manifest=manifest,
        category=category,
        source_id=source_id,
        source_filename=source_path.name,
        source_duration=total_dur,
        width=src_w,
        height=src_h,
        results=results,
    )

    return results


def gather_sources(args: argparse.Namespace) -> list[Path]:
    seen: set[Path] = set()
    sources: list[Path] = []

    def _add(p: Path) -> None:
        resolved = p.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        sources.append(p)

    if args.source:
        for s in args.source:
            p = Path(s)
            if not p.exists():
                logger.warning(f"Source not found: {p}")
                continue
            _add(p)
    if args.source_dir:
        d = Path(args.source_dir)
        if not d.is_dir():
            logger.error(f"--source-dir is not a directory: {d}")
            sys.exit(1)
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
                _add(f)
    return sources


def parse_distribution(text: str) -> dict[float, int]:
    """Parse a JSON dict like '{"15": 1, "30": 2}' into {float: int}."""
    raw = json.loads(text)
    return {float(k): int(v) for k, v in raw.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a footage library from raw long videos.")
    parser.add_argument("--source", action="append", help="Single source video file (repeatable)")
    parser.add_argument("--source-dir", help="Directory with raw source videos (e.g. storage/adhd_cut)")
    parser.add_argument(
        "--output-dir",
        default="storage/footage_library",
        help="Output directory for the prepared library (default: storage/footage_library)",
    )
    parser.add_argument("--skip-intro", type=float, default=30.0, help="Skip first N seconds")
    parser.add_argument("--skip-outro", type=float, default=30.0, help="Skip last N seconds")
    parser.add_argument("--max-chunks", type=int, default=None, help="Cap chunks per source (default: unlimited)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--category",
        default=None,
        help="Override category for ALL sources in this run (bypasses categories.json)",
    )
    parser.add_argument(
        "--distribution",
        type=parse_distribution,
        default=None,
        help='Override duration distribution as JSON, e.g. \'{"15": 1, "30": 2, "60": 1}\'',
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan without cutting")
    parser.add_argument("--force", action="store_true", help="(kept for compat — re-runs are idempotent by default)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.source and not args.source_dir:
        parser.error("Provide --source or --source-dir")

    sources = gather_sources(args)
    if not sources:
        logger.error("No source videos found")
        sys.exit(1)

    # Resolve categories up front so we can fail early.
    # Load the manifest from --source-dir if given, otherwise from the parent
    # directory of the first --source file (typical pattern: all sources live together).
    categories_map: dict[str, str] = {}
    if args.source_dir:
        categories_map = load_categories_manifest(Path(args.source_dir))
    elif sources:
        categories_map = load_categories_manifest(sources[0].parent)

    try:
        resolved = [(src, resolve_category(src, args.category, categories_map)) for src in sources]
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)

    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    manifest_path = output_root / MANIFEST_FILENAME
    manifest = load_manifest(manifest_path)

    logger.info(f"Processing {len(resolved)} source(s) → {output_root}")
    for src, cat in resolved:
        logger.info(f"  {src.name} → category={cat!r}")

    summary: list[tuple[str, str, int]] = []
    for src, cat in resolved:
        try:
            results = process_source(src, output_root, cat, args, manifest)
            summary.append((src.name, cat, len(results)))
            # Persist manifest after each source so aborted runs don't lose progress.
            if not args.dry_run and results:
                save_manifest(manifest_path, manifest)
                logger.info(f"  manifest checkpoint saved ({src.name})")
        except Exception as e:
            logger.exception(f"Failed to process {src}: {e}")
            summary.append((src.name, cat, 0))

    if not args.dry_run:
        save_manifest(manifest_path, manifest)
        logger.info(f"Manifest written: {manifest_path}")

    logger.info("=" * 60)
    logger.info("Summary:")
    for name, cat, n in summary:
        logger.info(f"  {name}  [{cat}]: {n} chunks")
    total = sum(n for _, _, n in summary)
    logger.info(f"  TOTAL: {total} chunks across {len({c for _, c, _ in summary})} categories")


if __name__ == "__main__":
    main()
