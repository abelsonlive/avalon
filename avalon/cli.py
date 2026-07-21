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
from avalon.identity import credentials as identity_credentials
from avalon.pathing import DEFAULT_TEMPLATE
from avalon.pipeline import Pipeline, PipelineOptions
from avalon.tagging import analysis_blob, identity_blob, tag_writer
from avalon import state as state_module
from avalon import watcher

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avalon", description="Audio analysis, tagging, and organization"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose (info) logging"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_pipeline_flags(_add_analyze_parser(subparsers))
    _add_pipeline_flags(_add_watch_parser(subparsers))
    _add_inspect_parser(subparsers)
    return parser


def _add_analyze_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "analyze", help="Analyze/tag/convert a file or folder"
    )
    parser.add_argument(
        "sources", nargs="+", help="Audio file(s) or folder(s) to process"
    )
    parser.add_argument(
        "--recursive", action="store_true", help="Recurse into subfolders"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without writing anything",
    )
    parser.set_defaults(func=run_analyze)
    return parser


def _add_watch_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "watch", help="Watch folder(s) and process new/changed files"
    )
    parser.add_argument("sources", nargs="+", help="Folder(s) to watch")
    parser.add_argument(
        "--debounce-seconds",
        type=int,
        default=5,
        help="Quiet period before processing a file (default: 5)",
    )
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="Skip processing pre-existing files on startup",
    )
    parser.set_defaults(func=run_watch)
    return parser


def _headline_fields_type(raw: str) -> tuple[str, ...]:
    try:
        return analysis_blob.parse_headline_fields(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _min_confidence_type(raw: str) -> float:
    try:
        return identity_credentials.parse_min_confidence(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _add_pipeline_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dest", type=str, default=None, help="Destination root; omit to tag in place"
    )
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
    parser.add_argument(
        "--no-analyze", action="store_true", help="Skip essentia analysis"
    )
    parser.add_argument(
        "--no-convert", action="store_true", help="Skip format/rate/depth conversion"
    )
    parser.add_argument(
        "--force-reanalyze",
        action="store_true",
        help="Re-run analysis even if already current",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing destination files"
    )
    parser.add_argument(
        "--overwrite-description",
        action="store_true",
        help="Replace the headline tag instead of merging into it",
    )
    parser.add_argument(
        "--headline-tag",
        type=str,
        default=None,
        help="Tag/field name to write the headline to (default: COMM for MP3/AIFF/WAV, "
        "DESCRIPTION for FLAC, desc for MP4). A name other than the default becomes a "
        "TXXX frame (ID3-family) or freeform atom (MP4) rather than the native comment field",
    )
    parser.add_argument(
        "--headline-format",
        type=_headline_fields_type,
        default=analysis_blob.DEFAULT_HEADLINE_FIELDS,
        help="Comma-separated fields (and order) for the headline tag. Available: "
        f"{', '.join(analysis_blob.HEADLINE_FIELD_VALUES)} "
        f"(default: {','.join(analysis_blob.DEFAULT_HEADLINE_FIELDS)})",
    )
    parser.add_argument(
        "--identify",
        action="store_true",
        help="Reconcile against MusicBrainz/Discogs (fingerprint via AcoustID). Off by "
        "default -- requires the ACOUSTID_API_KEY and/or DISCOGS_TOKEN environment "
        "variable(s); errors if --identify is passed but neither is set",
    )
    parser.add_argument(
        "--force-reidentify",
        action="store_true",
        help="Re-run --identify even if already current",
    )
    parser.add_argument(
        "--min-identify-confidence",
        type=_min_confidence_type,
        default=0.7,
        help="Minimum AcoustID match score (0-1) to trust a fingerprint match (default: 0.7)",
    )
    parser.add_argument(
        "--delete-original",
        action="store_true",
        help="Delete the source file after successful processing",
    )


def _add_inspect_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "inspect", help="Show a file's parsed canonical + analysis tags"
    )
    parser.add_argument("path", help="Audio file to inspect")
    parser.set_defaults(func=run_inspect)


def setup_logging(debug: bool, verbose: bool) -> None:
    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)
    logging.basicConfig(
        level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    # urllib3 logs full request lines (incl. query params) at DEBUG -- the
    # Discogs client authenticates via a `token=` query param, so --debug
    # must never drop this logger's floor below INFO or it leaks credentials.
    logging.getLogger("urllib3").setLevel(logging.INFO)


def _is_audio_file(path: Path) -> bool:
    """Excludes macOS AppleDouble sidecar files (`._track.mp3`), which SMB/
    NFS/FAT shares cause macOS to create for extended attributes a native
    filesystem would store inline -- these carry no real audio but would
    otherwise pass the plain extension check."""
    return path.suffix.lower() in AUDIO_EXTENSIONS and not path.name.startswith("._")


def gather_files(sources: list[str], recursive: bool) -> list[Path]:
    files: list[Path] = []
    for source in sources:
        path = Path(source)
        if path.is_file():
            if _is_audio_file(path):
                files.append(path)
            continue
        if not path.is_dir():
            logger.warning("Source not found: %s", path)
            continue
        walker = path.rglob("*") if recursive else path.glob("*")
        files.extend(p for p in walker if p.is_file() and _is_audio_file(p))
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
        headline_tag=args.headline_tag,
        headline_fields=args.headline_format,
        do_identify=args.identify,
        force_reidentify=args.force_reidentify,
        min_identify_confidence=args.min_identify_confidence,
        delete_original=args.delete_original,
        dry_run=getattr(args, "dry_run", False),
    )


def _check_identify_credentials(args: argparse.Namespace) -> int | None:
    """Returns an exit code if --identify was requested without the
    required credentials configured, else None to tell the caller to
    proceed. Shared by run_analyze/run_watch so this is checked once,
    before either mode's per-file loop starts."""
    if not args.identify:
        return None
    try:
        identity_credentials.ensure_configured()
    except identity_credentials.MissingCredentialsError as exc:
        logger.error(str(exc))
        return 1
    return None


def run_analyze(args: argparse.Namespace) -> int:
    if (exit_code := _check_identify_credentials(args)) is not None:
        return exit_code

    files = gather_files(args.sources, args.recursive)
    if not files:
        logger.warning("No audio files found")
        return 0
    logger.info("Found %d audio file(s)", len(files))

    options = _pipeline_options_from_args(args)
    pipeline = Pipeline(options)
    dest_root = options.dest_root or _default_state_dir(args.sources[0])
    state = state_module.load(dest_root)

    skip_fast_path = (
        args.dry_run or args.identify or args.force_reanalyze or args.force_reidentify
    )

    failures: list[tuple[Path, str]] = []
    processed = 0
    for path in files:
        if not skip_fast_path and state_module.is_unchanged(state, path):
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


def run_watch(args: argparse.Namespace) -> int:
    if (exit_code := _check_identify_credentials(args)) is not None:
        return exit_code

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

    skip_fast_path = args.identify or args.force_reanalyze or args.force_reidentify

    def handle(path: Path) -> None:
        if not skip_fast_path and state_module.is_unchanged(state, path):
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
                print(
                    f"  {field} labels: "
                    + ", ".join(
                        f"{label.name} ({label.confidence:.2f})" for label in labels
                    )
                )

    identity_fields = tag_writer.read_identity_fields(audio, file_format)
    if identity_fields:
        print("\nidentity fields (Picard-interop):")
        for key, value in identity_fields.items():
            print(f"  {key}: {value}")

    identity_extended = tag_writer.read_identity_extended(audio, file_format)
    print(f"\nidentity tag (raw): {identity_extended}")
    if identity_extended:
        print("identity tag (parsed):")
        for key, value in identity_blob.decode_identity(identity_extended).items():
            print(f"  {key}: {value}")
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.debug, args.verbose)
    exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
