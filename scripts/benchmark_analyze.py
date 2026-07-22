#!/usr/bin/env python3
"""Benchmark `avalon analyze` throughput against a fixed, reproducible
sample of files, reported as a realtime multiple (seconds of audio
processed per wall-clock second) so results are comparable across
--workers counts and machines regardless of which files got sampled.

Usage:
    uv run scripts/benchmark_analyze.py /path/to/library --workers 8
    uv run scripts/benchmark_analyze.py /path/to/library --sample-size 100 --workers 4
"""

from __future__ import annotations

import argparse
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avalon.cli import gather_files  # noqa: E402


def _duration_seconds(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="Directory to sample audio files from")
    parser.add_argument("--sample-size", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260721, help="Fixed seed for reproducible sampling")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()

    random.seed(args.seed)
    files = list(gather_files([args.source], recursive=True, sample_size=args.sample_size))
    if not files:
        print(f"No audio files found under {args.source}", file=sys.stderr)
        return 1

    print(f"sampling {len(files)} files (seed={args.seed})...")
    total_duration = sum(_duration_seconds(f) for f in files)
    print(f"total audio: {total_duration / 60:.1f} min")

    dest = Path(tempfile.mkdtemp(prefix="avalon-bench-"))
    try:
        cmd = ["uv", "run", "avalon", "analyze", *[str(f) for f in files], "--dest", str(dest)]
        if args.workers is not None:
            cmd += ["--workers", str(args.workers)]

        start = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        wall = time.monotonic() - start

        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            return proc.returncode

        print(f"\nworkers={args.workers or 1}  wall={wall:.1f}s  "
              f"s/file={wall / len(files):.2f}  "
              f"realtime_multiple={total_duration / wall:.1f}x")
    finally:
        shutil.rmtree(dest, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
