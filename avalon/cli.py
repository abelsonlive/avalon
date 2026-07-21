"""Command-line interface for avalon.

Subcommands:
  - analyze: single execution over a file or folder (optionally recursive)
  - watch:   daemon mode, watches folders and reacts to new/changed files
  - inspect: dumps a file's parsed tags for debugging

`analyze` and `watch` both funnel through the same `Pipeline.process_file`
(see pipeline.py) so there is exactly one place that knows the pipeline
order.

Note: essentia analysis runs sequentially (no `--workers` parallelism).
Concurrent calls into a shared, warm TensorFlow session are not something
this has been verified safe for, and a silent data race is worse than a
slower single-threaded run -- measured at ~1-2s/track once models are
warm, which is tractable even for large libraries.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from avalon.constants import AUDIO_EXTENSIONS
from avalon.pathing import DEFAULT_TEMPLATE
from avalon.pipeline import Pipeline, PipelineOptions
from avalon.tagging import analysis_blob, tag_writer
from avalon import state as state_module
from avalon import watcher

logger = logging.getLogger(__name__)


# -------- Argument parsing --------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="avalon", description="Audio analysis, tagging, and organization")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose (info) logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_pipeline_flags(_add_analyze_parser(subparsers))
    _add_pipeline_flags(_add_watch_parser(subparsers))
    _add_inspect_parser(subparsers)
    return parser


def _add_analyze_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("analyze", help="Analyze/tag/convert a file or folder")
    parser.add_argument("sources", nargs="+", help="Audio file(s) or folder(s) to process")
    parser.add_argument("--recursive", action="store_true", help="Recurse into subfolders")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing anything")
    parser.set_defaults(func=run_analyze)
    return parser


def _add_watch_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("watch", help="Watch folder(s) and process new/changed files")
    parser.add_argument("sources", nargs="+", help="Folder(s) to watch")
    parser.add_argument("--debounce-seconds", type=int, default=5, help="Quiet period before processing a file (default: 5)")
    parser.add_argument("--no-backfill", action="store_true", help="Skip processing pre-existing files on startup")
    parser.set_defaults(func=run_watch)
    return parser


def _add_pipeline_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dest", type=str, default=None, help="Destination root; omit to tag in place")
    parser.add_argument("--path-template", type=str, default=DEFAULT_TEMPLATE)
    parser.add_argument(
        "--convert-lossless-to",
        type=str,
        default=None,
        help="Re-encode lossless sources (FLAC/ALAC/WAV/AIFF/...) to this format (e.g. aiff); "
        "lossy sources (mp3, aac, ...) are left untouched regardless",
    )
    parser.add_argument("--max-sample-rate", type=int, default=None)
    parser.add_argument("--max-bit-depth", type=int, default=None)
    parser.add_argument("--no-analyze", action="store_true", help="Skip essentia analysis")
    parser.add_argument("--no-convert", action="store_true", help="Skip format/rate/depth conversion")
    parser.add_argument("--force-reanalyze", action="store_true", help="Re-run analysis even if already current")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing destination files")
    parser.add_argument("--overwrite-description", action="store_true", help="Replace the headline tag instead of merging into it")
    parser.add_argument("--delete-original", action="store_true", help="Delete the source file after successful processing")


def _add_inspect_parser(subparsers) -> None:
    parser = subparsers.add_parser("inspect", help="Show a file's parsed canonical + analysis tags")
    parser.add_argument("path", help="Audio file to inspect")
    parser.set_defaults(func=run_inspect)


def setup_logging(debug: bool, verbose: bool) -> None:
    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)
    logging.basicConfig(level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


# -------- File discovery --------


def gather_files(sources: list[str], recursive: bool) -> list[Path]:
    files: list[Path] = []
    for source in sources:
        path = Path(source)
        if path.is_file():
            if path.suffix.lower() in AUDIO_EXTENSIONS:
                files.append(path)
            continue
        if not path.is_dir():
            logger.warning("Source not found: %s", path)
            continue
        walker = path.rglob("*") if recursive else path.glob("*")
        files.extend(p for p in walker if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)
    return sorted(files)


def _default_state_dir(first_source: str) -> Path:
    """Where to keep .avalon_state.json when --dest wasn't given. `sources`
    for `analyze` may be individual files, not just folders, so this can't
    just assume the first source itself is a directory."""
    path = Path(first_source).resolve()
    return path if path.is_dir() else path.parent


def _pipeline_options_from_args(args: argparse.Namespace) -> PipelineOptions:
    return PipelineOptions(
        dest_root=Path(args.dest) if args.dest else None,
        path_template=args.path_template,
        convert_lossless_to=args.convert_lossless_to,
        max_sample_rate=args.max_sample_rate,
        max_bit_depth=args.max_bit_depth,
        do_analyze=not args.no_analyze,
        do_convert=not args.no_convert,
        force_reanalyze=args.force_reanalyze,
        overwrite=args.overwrite,
        overwrite_description=args.overwrite_description,
        delete_original=args.delete_original,
        dry_run=getattr(args, "dry_run", False),
    )


# -------- analyze --------


def run_analyze(args: argparse.Namespace) -> int:
    files = gather_files(args.sources, args.recursive)
    if not files:
        logger.warning("No audio files found")
        return 0
    logger.info("Found %d audio file(s)", len(files))

    options = _pipeline_options_from_args(args)
    pipeline = Pipeline(options)
    dest_root = options.dest_root or _default_state_dir(args.sources[0])
    state = state_module.load(dest_root)

    failures: list[tuple[Path, str]] = []
    processed = 0
    for path in files:
        if not args.dry_run and state_module.is_unchanged(state, path):
            logger.debug("Unchanged, skipping: %s", path)
            continue
        result = pipeline.process_file(path)
        if result.error:
            failures.append((path, result.error))
            logger.error("Failed: %s: %s", path, result.error)
        else:
            processed += 1
            logger.info(
                "%s%s -> %s",
                "[dry-run] " if args.dry_run else "",
                path,
                result.output_path,
            )
            if not args.dry_run:
                state_module.record(state, path)

    if not args.dry_run:
        state_module.save(dest_root, state)

    logger.info("Processed %d file(s), %d failure(s)", processed, len(failures))
    return 0 if not failures else 2


# -------- watch --------


def run_watch(args: argparse.Namespace) -> int:
    source_roots = [Path(s).resolve() for s in args.sources]
    missing = [s for s in source_roots if not s.is_dir()]
    if missing:
        for path in missing:
            logger.error("Source folder not found: %s", path)
        return 1

    options = _pipeline_options_from_args(args)
    pipeline = Pipeline(options)
    dest_root = options.dest_root or source_roots[0]
    state = state_module.load(dest_root)

    def handle(path: Path) -> None:
        if state_module.is_unchanged(state, path):
            return
        result = pipeline.process_file(path)
        if result.error:
            logger.error("Failed: %s: %s", path, result.error)
        else:
            logger.info("%s -> %s", path, result.output_path)
            state_module.record(state, path)
            state_module.save(dest_root, state)

    if not args.no_backfill:
        backlog = gather_files([str(root) for root in source_roots], recursive=True)
        logger.info("Backfilling %d existing file(s)", len(backlog))
        for path in backlog:
            handle(path)

    watcher.watch(source_roots, handle, debounce_seconds=args.debounce_seconds)
    return 0


# -------- inspect --------


def run_inspect(args: argparse.Namespace) -> int:
    path = args.path
    file_format = tag_writer.detect_format(path)
    audio = tag_writer.load(path, file_format)

    print(f"path: {path}")
    print(f"format: {file_format.value}")
    print("\ncanonical fields:")
    for key, value in tag_writer.read_canonical(audio, file_format).items():
        print(f"  {key}: {value}")

    headline = tag_writer.read_headline(audio, file_format)
    print(f"\nheadline tag (raw): {headline}")
    parsed_headline = analysis_blob.parse_headline(headline)
    if parsed_headline:
        print("headline tag (parsed):")
        for key, value in parsed_headline.items():
            print(f"  {key}: {value}")

    extended = tag_writer.read_extended(audio, file_format)
    print(f"\nextended tag (raw): {extended}")
    if extended:
        print("extended tag (parsed):")
        for key, value in analysis_blob.decode_extended(extended).items():
            print(f"  {key}: {value}")
        for field in ("genre", "moodtheme"):
            labels = analysis_blob.decode_extended_labels(extended, field)
            if labels:
                print(f"  {field} labels: " + ", ".join(f"{l.name} ({l.confidence:.2f})" for l in labels))
    return 0


# -------- entry point --------


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.debug, args.verbose)
    exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
