from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from avalon.analysis.essentia_analyzer import EssentiaAnalyzer
from avalon.conversion import converter
from avalon.models import ProcessResult, TrackAnalysis
from avalon.pathing import DEFAULT_TEMPLATE, PathRenderer
from avalon.tagging import analysis_blob, cover_art, tag_writer

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
    delete_original: bool = False
    dry_run: bool = False


@dataclass
class PlannedFile:
    source_path: Path
    output_path: Path
    will_analyze: bool
    will_convert: bool
    target_format: str


class Pipeline:
    def __init__(self, options: PipelineOptions):
        self.options = options
        self._analyzer: EssentiaAnalyzer | None = None
        self._path_renderer: PathRenderer | None = None
        if options.dest_root:
            self._path_renderer = PathRenderer(options.dest_root, options.path_template)

    def _get_analyzer(self) -> EssentiaAnalyzer:
        if self._analyzer is None:
            self._analyzer = EssentiaAnalyzer()
        return self._analyzer

    def process_file(self, source_path: Path | str) -> ProcessResult:
        source_path = Path(source_path)
        try:
            planned = self.plan(source_path)
        except Exception as exc:
            logger.error("Failed processing %s: %s", source_path, exc)
            return ProcessResult(
                source_path=str(source_path),
                output_path=str(source_path),
                analyzed=False,
                converted=False,
                error=str(exc),
            )
        return self.process_planned(planned)

    def plan(self, source_path: Path | str) -> PlannedFile:
        source_path = Path(source_path)
        opts = self.options
        file_format = tag_writer.detect_format(str(source_path))
        source_audio = tag_writer.load(str(source_path), file_format)
        existing_fields = tag_writer.read_canonical(source_audio, file_format)
        existing_extended = tag_writer.read_extended(source_audio, file_format)

        skip_analysis = not opts.force_reanalyze and analysis_blob.has_current_schema(
            existing_extended
        )
        will_analyze = opts.do_analyze and not skip_analysis

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
        return PlannedFile(
            source_path=source_path,
            output_path=output_path,
            will_analyze=will_analyze,
            will_convert=will_convert,
            target_format=target_format,
        )

    def process_planned(self, planned: PlannedFile) -> ProcessResult:
        try:
            return self._execute(planned)
        except Exception as exc:
            logger.error("Failed processing %s: %s", planned.source_path, exc)
            return ProcessResult(
                source_path=str(planned.source_path),
                output_path=str(planned.output_path),
                analyzed=False,
                converted=False,
                error=str(exc),
            )

    def _execute(self, planned: PlannedFile) -> ProcessResult:
        opts = self.options
        source_path = planned.source_path
        output_path = planned.output_path

        if opts.dry_run:
            return ProcessResult(
                source_path=str(source_path),
                output_path=str(output_path),
                analyzed=planned.will_analyze,
                converted=planned.will_convert,
                skipped_reason="dry-run",
            )

        file_format = tag_writer.detect_format(str(source_path))
        source_audio = tag_writer.load(str(source_path), file_format)
        artwork = cover_art.extract(source_audio, file_format)

        analysis: TrackAnalysis | None = None
        if planned.will_analyze:
            analysis = self._get_analyzer().analyze(str(source_path))

        self._materialize(
            source_path, output_path, planned.will_convert, planned.target_format
        )

        output_format = tag_writer.detect_format(str(output_path))
        output_audio = (
            source_audio
            if output_path == source_path and not planned.will_convert
            else tag_writer.load(str(output_path), output_format)
        )

        if analysis is not None:
            tag_writer.write_generated_fields(
                output_audio,
                output_format,
                bpm=str(round(analysis.bpm)),
                key=analysis_blob.standard_key(analysis),
                genre=analysis.top_genre,
                fill_only_if_missing=True,
            )
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

        if artwork is not None:
            cover_art.embed(output_audio, output_format, artwork)

        tag_writer.save(output_audio)

        if opts.delete_original and output_path != source_path:
            source_path.unlink(missing_ok=True)

        return ProcessResult(
            source_path=str(source_path),
            output_path=str(output_path),
            analyzed=analysis is not None,
            converted=planned.will_convert,
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


_worker_pipeline: Pipeline | None = None


def init_worker(options: PipelineOptions) -> None:
    global _worker_pipeline
    _worker_pipeline = Pipeline(options)


def process_planned_in_worker(planned: PlannedFile) -> ProcessResult:
    return _worker_pipeline.process_planned(planned)
