"""CLI to prepare a library of short footage chunks from raw long videos.

Usage:
    python -m backend.scripts.prepare_footage --source storage/adhd_cut/foo.mp4
    python -m backend.scripts.prepare_footage --source-dir storage/adhd_cut --max-chunks 50
    python -m backend.scripts.prepare_footage --source-dir storage/adhd_cut --dry-run

Output structure:
    <output-dir>/
      <source_stem>/
        chunk_0001_d10.0s.mp4
        chunk_0002_d05.0s.mp4
        category.json    (optional, manually created)
      library.json       (central manifest, updated incrementally)
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Allow invocation both as `python -m scripts.prepare_footage` (inside container, /app root)
# and as `python backend/scripts/prepare_footage.py` (local development).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.helpers import safe_filename  # noqa: E402

logger = logging.getLogger("prepare_footage")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}

# Weighted distribution of chunk durations (seconds → weight).
DEFAULT_DISTRIBUTION: dict[float, int] = {
    3.0: 10,
    5.0: 25,
    10.0: 30,
    15.0: 20,
    30.0: 10,
    60.0: 5,
}

MANIFEST_FILENAME = "library.json"
CATEGORY_SIDECAR = "category.json"


@dataclass
class ChunkPlan:
    start: float
    duration: float


@dataclass
class CutResult:
    rel_path: str
    duration: float
    width: int
    height: int


# ----------------------------------------------------------------------------
# Pure functions (unit-testable)
# ----------------------------------------------------------------------------

def plan_chunks(
    total_dur: float,
    skip_intro: float,
    skip_outro: float,
    distribution: dict[float, int],
    rng: random.Random,
    max_chunks: Optional[int] = None,
) -> list[ChunkPlan]:
    """Walk forward through the timeline, drawing random chunk durations from `distribution`.

    Returns a list of ChunkPlan(start, duration) covering the [skip_intro, total_dur - skip_outro)
    range without overlap and without overshooting.
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
        # Pick a random bucket; if it doesn't fit, try smaller ones; bail out if nothing fits.
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


# ----------------------------------------------------------------------------
# FFmpeg / ffprobe wrappers (sync — CLI script, not async hot path)
# ----------------------------------------------------------------------------

def probe_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def probe_dimensions(path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    parts = result.stdout.strip().split(",")
    return int(parts[0]), int(parts[1])


def cut_chunk(source: Path, start: float, duration: float, output: Path) -> None:
    """Cut a single chunk with re-encoding (so seeks are accurate). Audio dropped."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(source),
        "-t", str(duration),
        "-an",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-avoid_negative_ts", "make_zero",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed cutting {output.name}: {proc.stderr[-400:]}")


# ----------------------------------------------------------------------------
# Manifest helpers
# ----------------------------------------------------------------------------

def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def read_category(folder: Path, default: str) -> str:
    sidecar = folder / CATEGORY_SIDECAR
    if sidecar.exists():
        try:
            with sidecar.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return str(data.get("category", default))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read {sidecar}: {e}")
    return default


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------

def process_source(
    source_path: Path,
    output_root: Path,
    args: argparse.Namespace,
) -> list[CutResult]:
    logger.info(f"Probing {source_path.name}...")
    total_dur = probe_duration(source_path)
    src_w, src_h = probe_dimensions(source_path)
    logger.info(f"  duration={total_dur:.1f}s, size={src_w}x{src_h}")

    folder_name = safe_filename(source_path.stem, max_length=80)
    out_folder = output_root / folder_name

    if out_folder.exists() and not args.force and not args.dry_run:
        existing = list(out_folder.glob("chunk_*.mp4"))
        if existing:
            logger.warning(
                f"  {out_folder} already has {len(existing)} chunks; pass --force to overwrite"
            )
            return []

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
    logger.info(f"  planned {len(plans)} chunks (seed={seed})")

    if args.dry_run:
        for i, p in enumerate(plans[:10], start=1):
            logger.info(f"    [{i}] start={p.start:.1f}s dur={p.duration:.1f}s")
        if len(plans) > 10:
            logger.info(f"    ... ({len(plans) - 10} more)")
        return []

    out_folder.mkdir(parents=True, exist_ok=True)
    category = args.category or read_category(out_folder, default=folder_name)

    results: list[CutResult] = []
    for idx, plan in enumerate(plans, start=1):
        chunk_name = f"chunk_{idx:04d}_d{plan.duration:04.1f}s.mp4"
        chunk_path = out_folder / chunk_name
        try:
            cut_chunk(source_path, plan.start, plan.duration, chunk_path)
        except RuntimeError as e:
            logger.error(f"    chunk {idx} failed: {e}")
            continue

        rel = f"{folder_name}/{chunk_name}"
        results.append(CutResult(
            rel_path=rel,
            duration=plan.duration,
            width=src_w,
            height=src_h,
        ))
        if idx % 10 == 0 or idx == len(plans):
            logger.info(f"    cut {idx}/{len(plans)}")

    # Update central manifest
    manifest_path = output_root / MANIFEST_FILENAME
    manifest = load_manifest(manifest_path)
    # Remove old entries for this source folder (in case of --force re-run)
    manifest = [m for m in manifest if not str(m.get("path", "")).startswith(folder_name + "/")]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for r in results:
        manifest.append({
            "path": r.rel_path,
            "duration": round(r.duration, 2),
            "source": source_path.name,
            "category": category,
            "width": r.width,
            "height": r.height,
            "created_at": now,
        })
    save_manifest(manifest_path, manifest)
    logger.info(f"  manifest updated: {manifest_path} (+{len(results)} entries, total {len(manifest)})")

    return results


def gather_sources(args: argparse.Namespace) -> list[Path]:
    sources: list[Path] = []
    if args.source:
        for s in args.source:
            p = Path(s)
            if not p.exists():
                logger.warning(f"Source not found: {p}")
                continue
            sources.append(p)
    if args.source_dir:
        d = Path(args.source_dir)
        if not d.is_dir():
            logger.error(f"--source-dir is not a directory: {d}")
            sys.exit(1)
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
                sources.append(f)
    return sources


def parse_distribution(text: str) -> dict[float, int]:
    """Parse a JSON dict like '{"5": 25, "10": 30, "15": 20}' into {float: int}."""
    raw = json.loads(text)
    return {float(k): int(v) for k, v in raw.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a footage library from raw long videos.")
    parser.add_argument("--source", action="append", help="Single source video file (repeatable)")
    parser.add_argument("--source-dir", help="Directory with raw source videos")
    parser.add_argument(
        "--output-dir",
        default="storage/footage_library",
        help="Output directory for the prepared library (default: storage/footage_library)",
    )
    parser.add_argument("--skip-intro", type=float, default=30.0, help="Skip first N seconds of source")
    parser.add_argument("--skip-outro", type=float, default=30.0, help="Skip last N seconds of source")
    parser.add_argument("--max-chunks", type=int, default=None, help="Max chunks per source")
    parser.add_argument("--seed", type=int, default=None, help="Random seed (default: time-based)")
    parser.add_argument("--category", default=None, help="Override category name for all sources")
    parser.add_argument(
        "--distribution",
        type=parse_distribution,
        default=None,
        help='Override duration distribution as JSON, e.g. \'{"5": 25, "10": 30, "15": 20}\'',
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan without cutting")
    parser.add_argument("--force", action="store_true", help="Overwrite existing chunks")
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

    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing {len(sources)} source(s) → {output_root}")

    summary: list[tuple[str, int]] = []
    for src in sources:
        try:
            results = process_source(src, output_root, args)
            summary.append((src.name, len(results)))
        except Exception as e:
            logger.exception(f"Failed to process {src}: {e}")
            summary.append((src.name, 0))

    logger.info("=" * 60)
    logger.info("Summary:")
    for name, n in summary:
        logger.info(f"  {name}: {n} chunks")
    total = sum(n for _, n in summary)
    logger.info(f"  TOTAL: {total} chunks")


if __name__ == "__main__":
    main()
