import shutil
from pathlib import Path

from avalon.cli import _process_parallel, build_parser, gather_files
from avalon.pipeline import Pipeline, PipelineOptions

FIXTURES = Path(__file__).parent / "fixtures"


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


class TestGatherFilesExcludesAppleDouble:
    def test_sidecar_excluded_when_walking_a_folder(self, tmp_path):
        _touch(tmp_path / "track.mp3")
        _touch(tmp_path / "._track.mp3")
        found = {p.name for p in gather_files([str(tmp_path)], recursive=False)}
        assert found == {"track.mp3"}

    def test_sidecar_excluded_even_as_an_explicit_source(self, tmp_path):
        sidecar = tmp_path / "._track.mp3"
        _touch(sidecar)
        assert list(gather_files([str(sidecar)], recursive=False)) == []


class TestGatherFilesIsLazy:
    def test_returns_an_iterator_not_a_list(self, tmp_path):
        _touch(tmp_path / "a.mp3")
        result = gather_files([str(tmp_path)], recursive=False)
        assert not isinstance(result, list)
        assert list(result) == [tmp_path / "a.mp3"]


class TestGatherFilesRecursion:
    def test_non_recursive_ignores_subfolders(self, tmp_path):
        _touch(tmp_path / "top.mp3")
        _touch(tmp_path / "sub" / "nested.mp3")
        found = {p.name for p in gather_files([str(tmp_path)], recursive=False)}
        assert found == {"top.mp3"}

    def test_recursive_finds_nested_files(self, tmp_path):
        _touch(tmp_path / "top.mp3")
        _touch(tmp_path / "sub" / "nested.mp3")
        found = {p.name for p in gather_files([str(tmp_path)], recursive=True)}
        assert found == {"top.mp3", "nested.mp3"}


class TestGatherFilesSampleSize:
    def test_caps_at_sample_size(self, tmp_path):
        for i in range(20):
            _touch(tmp_path / f"track{i}.mp3")
        found = list(gather_files([str(tmp_path)], recursive=False, sample_size=5))
        assert len(found) == 5

    def test_none_returns_everything(self, tmp_path):
        for i in range(5):
            _touch(tmp_path / f"track{i}.mp3")
        found = list(gather_files([str(tmp_path)], recursive=False, sample_size=None))
        assert len(found) == 5

    def test_still_respects_recursive_flag(self, tmp_path):
        _touch(tmp_path / "top.mp3")
        for i in range(10):
            _touch(tmp_path / "sub" / f"nested{i}.mp3")
        found = list(gather_files([str(tmp_path)], recursive=False, sample_size=5))
        assert found == [tmp_path / "top.mp3"]

    def test_spans_multiple_sources(self, tmp_path):
        dir_a, dir_b = tmp_path / "a", tmp_path / "b"
        for i in range(10):
            _touch(dir_a / f"a{i}.mp3")
            _touch(dir_b / f"b{i}.mp3")
        found = list(
            gather_files([str(dir_a), str(dir_b)], recursive=False, sample_size=5)
        )
        assert len(found) == 5


class TestWorkersFlag:
    def test_defaults_to_one(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "somefile.mp3"])
        assert args.workers == 1

    def test_parses_explicit_value(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "somefile.mp3", "--workers", "4"])
        assert args.workers == 4


class TestProcessParallel:
    def test_processes_every_file_and_reports_each_result(self, tmp_path):
        paths = []
        for i in range(4):
            dest = tmp_path / f"track{i}.m4a"
            shutil.copy2(FIXTURES / "test.m4a", dest)
            paths.append(dest)

        pipeline = Pipeline(PipelineOptions(do_analyze=False, do_convert=False))
        results = {}

        def handle(path, result):
            results[path] = result

        _process_parallel(pipeline, iter(paths), workers=2, handle=handle)

        assert set(results) == set(paths)
        assert all(result.error is None for result in results.values())

    def test_a_planning_failure_is_reported_not_raised(self, tmp_path):
        good = tmp_path / "good.m4a"
        shutil.copy2(FIXTURES / "test.m4a", good)
        bad = tmp_path / "missing.m4a"

        pipeline = Pipeline(PipelineOptions(do_analyze=False, do_convert=False))
        results = {}

        def handle(path, result):
            results[path] = result

        _process_parallel(pipeline, iter([good, bad]), workers=2, handle=handle)

        assert results[good].error is None
        assert results[bad].error is not None
