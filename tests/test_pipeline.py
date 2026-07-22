import pickle
import shutil
from pathlib import Path

from avalon.models import Label, TrackAnalysis
from avalon.pipeline import Pipeline, PipelineOptions
from avalon.tagging import analysis_blob, tag_writer

FIXTURES = Path(__file__).parent / "fixtures"


def _copy_fixture(name: str, tmp_path: Path) -> str:
    dest = tmp_path / name
    shutil.copy2(FIXTURES / name, dest)
    return str(dest)


def _sample_analysis(**overrides) -> TrackAnalysis:
    defaults = dict(
        bpm=128.3,
        bpm_confidence=0.92,
        key="A",
        scale="minor",
        camelot="8A",
        key_strength=0.87,
        loudness=-8.2,
        dynamic_complexity=0.41,
        mood_aggressive=0.71,
        mood_happy=0.22,
        mood_sad=0.08,
        mood_relaxed=0.15,
        mood_party=0.63,
        danceability=0.62,
        mood_acoustic=0.04,
        mood_electronic=0.88,
        voice_probability=0.42,
        gender="male",
        gender_confidence=0.55,
        tonal_probability=0.9,
        timbre="bright",
        timbre_confidence=0.53,
        genres=[Label("Essentia Guessed Genre", 0.82)],
        mood_themes=[Label("driving", 0.71)],
    )
    defaults.update(overrides)
    return TrackAnalysis(**defaults)


class FakeAnalyzer:
    def __init__(self, analysis: TrackAnalysis):
        self._analysis = analysis
        self.calls = 0

    def analyze(self, path: str) -> TrackAnalysis:
        self.calls += 1
        return self._analysis


class TestPlanAndExecuteSplit:
    def test_process_file_equals_plan_then_process_planned(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        pipeline = Pipeline(PipelineOptions(do_analyze=False, do_convert=False))
        planned = pipeline.plan(path)
        result = pipeline.process_planned(planned)
        assert result.error is None
        assert result.output_path == path

    def test_plan_failure_is_reported_without_raising(self, tmp_path):
        pipeline = Pipeline(PipelineOptions(do_analyze=False, do_convert=False))
        result = pipeline.process_file(str(tmp_path / "does-not-exist.mp3"))
        assert result.error is not None


class TestPlannedFileIsPicklable:
    def test_survives_a_pickle_round_trip(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        pipeline = Pipeline(PipelineOptions(do_analyze=False, do_convert=False))
        planned = pipeline.plan(path)
        assert pickle.loads(pickle.dumps(planned)) == planned


class TestForceReanalyze:
    def test_skips_when_already_current(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        fmt = tag_writer.detect_format(path)
        audio = tag_writer.load(path, fmt)
        tag_writer.write_extended(
            audio, fmt, analysis_blob.encode_extended(_sample_analysis())
        )
        tag_writer.save(audio)

        pipeline = Pipeline(PipelineOptions(do_analyze=True, do_convert=False))
        analyzer = FakeAnalyzer(_sample_analysis())
        pipeline._analyzer = analyzer

        result = pipeline.process_file(path)
        assert analyzer.calls == 0
        assert result.analyzed is False

    def test_force_reanalyze_runs_anyway(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        fmt = tag_writer.detect_format(path)
        audio = tag_writer.load(path, fmt)
        tag_writer.write_extended(
            audio, fmt, analysis_blob.encode_extended(_sample_analysis())
        )
        tag_writer.save(audio)

        pipeline = Pipeline(
            PipelineOptions(do_analyze=True, do_convert=False, force_reanalyze=True)
        )
        analyzer = FakeAnalyzer(_sample_analysis())
        pipeline._analyzer = analyzer

        result = pipeline.process_file(path)
        assert analyzer.calls == 1
        assert result.analyzed is True


class TestDryRun:
    def test_dry_run_does_not_write_anything(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        before = Path(path).read_bytes()
        pipeline = Pipeline(
            PipelineOptions(do_analyze=True, do_convert=False, dry_run=True)
        )
        pipeline._analyzer = FakeAnalyzer(_sample_analysis())

        result = pipeline.process_file(path)
        assert result.skipped_reason == "dry-run"
        assert Path(path).read_bytes() == before
