"""Orchestrates the per-file pipeline: analyze -> compute destination ->
convert -> tag -> embed art -> place. Both CLI modes (`analyze`, `watch`)
funnel through `Pipeline.process_file` so there is exactly one place that
knows the pipeline order, rather than each mode reimplementing it.

Order matters: analysis always runs against the *original* file (highest
fidelity, before any bit-depth/sample-rate reduction), and cover art is
extracted from the original before conversion since ffmpeg's metadata
passthrough is unreliable for embedded images across container changes.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from avalon.analysis.essentia_analyzer import EssentiaAnalyzer
from avalon.conversion import converter
from avalon.identity import credentials as identity_credentials
from avalon.identity.identity_resolver import IdentityResolver
from avalon.models import ProcessResult, TrackAnalysis, TrackIdentity
from avalon.pathing import DEFAULT_TEMPLATE, PathRenderer
from avalon.tagging import analysis_blob, cover_art, identity_blob, tag_writer

logger = logging.getLogger(__name__)


@dataclass
class PipelineOptions:
    dest_root: Path | None = None
    path_template: str = DEFAULT_TEMPLATE
    convert_lossless_to: str | None = None
    max_sample_rate: int | None = None
    max_bit_depth: int | None = None
    do_analyze: bool = True
    do_convert: bool = True
    force_reanalyze: bool = False
    overwrite: bool = False
    overwrite_description: bool = False
    headline_tag: str | None = None
    headline_fields: tuple[str, ...] = analysis_blob.DEFAULT_HEADLINE_FIELDS
    do_identify: bool = False
    force_reidentify: bool = False
    min_identify_confidence: float = 0.7
    delete_original: bool = False
    dry_run: bool = False


class Pipeline:
    """Construct once per CLI run (`analyze` or `watch`); call
    `process_file` per source path. Essentia models load lazily on first
    use so `--no-analyze`/dry runs never pay that cost."""

    def __init__(self, options: PipelineOptions):
        self.options = options
        self._analyzer: EssentiaAnalyzer | None = None
        self._identity_resolver: IdentityResolver | None = None
        self._path_renderer: PathRenderer | None = None
        if options.dest_root:
            self._path_renderer = PathRenderer(options.dest_root, options.path_template)

    def _get_analyzer(self) -> EssentiaAnalyzer:
        if self._analyzer is None:
            self._analyzer = EssentiaAnalyzer()
        return self._analyzer

    def _get_identity_resolver(self) -> IdentityResolver:
        if self._identity_resolver is None:
            self._identity_resolver = identity_credentials.build_resolver(
                self.options.min_identify_confidence
            )
        return self._identity_resolver

    def process_file(self, source_path: Path | str) -> ProcessResult:
        source_path = Path(source_path)
        try:
            return self._process(source_path)
        except Exception as exc:
            logger.error("Failed processing %s: %s", source_path, exc)
            return ProcessResult(
                source_path=str(source_path),
                output_path=str(source_path),
                analyzed=False,
                converted=False,
                error=str(exc),
            )

    def _process(self, source_path: Path) -> ProcessResult:
        opts = self.options
        file_format = tag_writer.detect_format(str(source_path))

        source_audio = tag_writer.load(str(source_path), file_format)
        existing_fields = tag_writer.read_canonical(source_audio, file_format)
        existing_extended = tag_writer.read_extended(source_audio, file_format)
        existing_identity_extended = tag_writer.read_identity_extended(
            source_audio, file_format
        )
        artwork = cover_art.extract(source_audio, file_format)

        skip_analysis = not opts.force_reanalyze and analysis_blob.has_current_schema(
            existing_extended
        )
        will_analyze = opts.do_analyze and not skip_analysis

        skip_identify = not opts.force_reidentify and identity_blob.has_current_schema(
            existing_identity_extended
        )
        will_identify = opts.do_identify and not skip_identify

        will_convert = opts.do_convert and converter.needs_conversion(
            str(source_path),
            target_format=opts.convert_lossless_to,
            max_sample_rate=opts.max_sample_rate,
            max_bit_depth=opts.max_bit_depth,
        )
        target_format = (
            (opts.convert_lossless_to or file_format.value)
            if will_convert
            else source_path.suffix.lstrip(".")
        )

        output_path = self._compute_output_path(
            source_path, existing_fields, target_format, opts.overwrite
        )

        if opts.dry_run:
            return ProcessResult(
                source_path=str(source_path),
                output_path=str(output_path),
                analyzed=will_analyze,
                converted=will_convert,
                identified=will_identify,
                skipped_reason="dry-run",
            )

        analysis: TrackAnalysis | None = None
        if will_analyze:
            analysis = self._get_analyzer().analyze(str(source_path))

        identity: TrackIdentity | None = None
        if will_identify:
            try:
                identity = self._get_identity_resolver().resolve(
                    str(source_path), existing_fields
                )
            except Exception as exc:
                logger.warning(
                    "Identify failed for %s, continuing without it: %s",
                    source_path,
                    exc,
                )

        self._materialize(source_path, output_path, will_convert, target_format)

        output_format = tag_writer.detect_format(str(output_path))
        output_audio = (
            source_audio
            if output_path == source_path and not will_convert
            else tag_writer.load(str(output_path), output_format)
        )

        if analysis is not None or identity is not None:
            if identity and identity.genre:
                resolved_genre = identity.genre
            elif analysis:
                resolved_genre = analysis.top_genre
            else:
                resolved_genre = None
            tag_writer.write_generated_fields(
                output_audio,
                output_format,
                bpm=str(round(analysis.bpm)) if analysis else None,
                key=analysis_blob.standard_key(analysis) if analysis else None,
                genre=resolved_genre,
                date=identity.release_date if identity else None,
                fill_only_if_missing=True,
            )
            tag_writer.write_release_date(
                output_audio,
                output_format,
                identity.release_date if identity else None,
                fill_only_if_missing=True,
            )

        if analysis is not None:
            existing_headline = (
                None
                if opts.overwrite_description
                else tag_writer.read_headline(
                    output_audio, output_format, opts.headline_tag
                )
            )
            tag_writer.write_headline(
                output_audio,
                output_format,
                analysis_blob.encode_headline(
                    analysis, existing_headline, fields=opts.headline_fields
                ),
                opts.headline_tag,
            )
            tag_writer.write_extended(
                output_audio, output_format, analysis_blob.encode_extended(analysis)
            )

        if identity is not None:
            self._write_identity(output_audio, output_format, identity)

        if artwork is not None:
            cover_art.embed(output_audio, output_format, artwork)

        tag_writer.save(output_audio)

        if opts.delete_original and output_path != source_path:
            source_path.unlink(missing_ok=True)

        return ProcessResult(
            source_path=str(source_path),
            output_path=str(output_path),
            analyzed=analysis is not None,
            converted=will_convert,
            identified=identity is not None,
        )

    @staticmethod
    def _write_identity(output_audio, output_format, identity: TrackIdentity) -> None:
        """Picard-interop identity fields are fill-only-if-missing (a
        Picard-tagged file's existing MBIDs must never be clobbered); the
        avalon-owned AVALON_IDENTITY blob is always fully replaced, same
        as the analysis extended tag.

        The taggable field set comes from `tag_writer.IDENTITY_FIELD_NAMES`
        (itself derived from `IdentityFieldMap`), not hand-typed here, so
        `TrackIdentity`'s own fields are the only other place naming this
        set -- adding a field to one and not the other is the only
        remaining way for them to drift."""
        values = {
            field: value
            for field, value in asdict(identity).items()
            if field in tag_writer.IDENTITY_FIELD_NAMES and value
        }
        existing = tag_writer.read_identity_fields(output_audio, output_format)
        to_write = {k: v for k, v in values.items() if not existing.get(k)}
        if to_write:
            tag_writer.write_identity_fields(output_audio, output_format, to_write)
        tag_writer.write_identity_extended(
            output_audio, output_format, identity_blob.encode_identity(identity)
        )

    def _compute_output_path(
        self,
        source_path: Path,
        existing_fields: dict[str, str],
        target_format: str,
        overwrite: bool,
    ) -> Path:
        if self._path_renderer is not None:
            return self._path_renderer.render(
                existing_fields, target_format, allow_overwrite=overwrite
            )
        if target_format == source_path.suffix.lstrip("."):
            return source_path
        return source_path.with_suffix(f".{target_format}")

    def _materialize(
        self,
        source_path: Path,
        output_path: Path,
        will_convert: bool,
        target_format: str,
    ) -> None:
        """Gets bytes onto disk at `output_path`: converts, copies, or (if
        `output_path == source_path` and no conversion is needed) does
        nothing -- true in-place tagging of the original file."""
        if output_path == source_path and not will_convert:
            return
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if will_convert:
            convert_target = (
                output_path.with_name(f".{output_path.name}.avalon_tmp")
                if output_path == source_path
                else output_path
            )
            converter.convert(
                str(source_path),
                str(convert_target),
                target_format=target_format,
                max_sample_rate=self.options.max_sample_rate,
                max_bit_depth=self.options.max_bit_depth,
                overwrite=True,
            )
            if convert_target != output_path:
                convert_target.replace(output_path)
        elif output_path != source_path:
            shutil.copy2(source_path, output_path)
