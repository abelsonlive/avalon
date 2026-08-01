import shutil
from pathlib import Path

import pytest

import avalon.cli as cli
from avalon.cli import _process_parallel, build_parser, gather_files, run_analyze
from avalon.pipeline import Pipeline, PipelineOptions
from avalon import state as state_module
from avalon import watcher

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


class TestNewPipelineFlagDefaults:
    def test_defaults(self):
        args = build_parser().parse_args(["analyze", "somefile.mp3"])
        assert args.overwrite_genre is False
        assert args.n_genres == 1
        assert args.ignore_errors is False

    def test_parses_explicit_values(self):
        args = build_parser().parse_args(
            [
                "analyze",
                "somefile.mp3",
                "--overwrite-genre",
                "--n-genres",
                "3",
                "--ignore-errors",
            ]
        )
        assert args.overwrite_genre is True
        assert args.n_genres == 3
        assert args.ignore_errors is True

    def test_watch_also_accepts_them(self):
        args = build_parser().parse_args(
            ["watch", "somedir", "--overwrite-genre", "--n-genres", "2"]
        )
        assert args.overwrite_genre is True
        assert args.n_genres == 2


class TestIgnoreErrorsExitCode:
    def _args_for(self, tmp_path, *, ignore_errors: bool):
        # A file with an audio extension but garbage content: gather_files
        # picks it up (by extension), but the pipeline fails to read its
        # tags -> a recorded per-file failure, without needing essentia.
        (tmp_path / "corrupt.mp3").write_bytes(b"\x00not a real audio file\x00")
        argv = ["analyze", str(tmp_path), "--no-analyze"]
        if ignore_errors:
            argv.append("--ignore-errors")
        return build_parser().parse_args(argv)

    def test_failure_without_ignore_errors_exits_2(self, tmp_path):
        assert run_analyze(self._args_for(tmp_path, ignore_errors=False)) == 2

    def test_failure_with_ignore_errors_exits_0(self, tmp_path):
        assert run_analyze(self._args_for(tmp_path, ignore_errors=True)) == 0


class TestIncrementalStateSave:
    def test_state_persists_before_a_mid_run_crash(self, tmp_path, monkeypatch):
        for i in range(4):
            shutil.copy2(FIXTURES / "test.m4a", tmp_path / f"t{i}.m4a")

        # Save after every file so a crash on the 3rd leaves the first 2 on disk.
        monkeypatch.setattr(cli, "_STATE_SAVE_INTERVAL", 1)

        real_process = Pipeline.process_file
        calls = {"n": 0}

        def crashing_process(self, path):
            calls["n"] += 1
            if calls["n"] == 3:
                # Stand in for an essentia/TF SIGSEGV: kills the run before the
                # end-of-run state save would ever run.
                raise SystemExit("simulated native crash")
            return real_process(self, path)

        monkeypatch.setattr(Pipeline, "process_file", crashing_process)

        args = build_parser().parse_args(
            ["analyze", str(tmp_path), "--no-analyze", "--no-convert"]
        )
        with pytest.raises(SystemExit):
            run_analyze(args)

        # Incremental saves persisted the 2 files completed before the crash,
        # even though the end-of-run save never happened.
        assert len(state_module.load(tmp_path)) == 2


class TestAnalyzeWithDeleteOriginal:
    """`analyze --delete-original` used to die with FileNotFoundError trying
    to fingerprint the source it had just deleted -- on the *first* successful
    file, so a sweep of an album processed exactly one track. --ignore-errors
    did not help: the exception came from the result handler, not the
    pipeline, so it escaped the per-file failure path entirely."""

    def _args(self, tmp_path, count):
        for i in range(count):
            shutil.copy2(FIXTURES / "test.m4a", tmp_path / f"t{i}.m4a")
        # --overwrite because the fixtures share tags and so share one
        # templated destination path; the point here is the source side.
        return build_parser().parse_args(
            [
                "analyze",
                str(tmp_path),
                "--dest",
                str(tmp_path / "library"),
                "--no-analyze",
                "--no-convert",
                "--delete-original",
                "--overwrite",
            ]
        )

    def test_processes_every_file_not_just_the_first(self, tmp_path):
        assert run_analyze(self._args(tmp_path, 3)) == 0
        # Every source consumed => the run got past the first file.
        assert list(tmp_path.glob("*.m4a")) == []

    def test_deleted_sources_are_not_recorded_in_state(self, tmp_path):
        run_analyze(self._args(tmp_path, 2))
        # Nothing to fingerprint once the source is gone, so state stays empty
        # rather than holding entries for paths that can never be revisited.
        assert state_module.load(tmp_path / "library") == {}


class TestWatchStateFile:
    """Watch mode keys state by *source* path, so its entries and those of an
    `analyze` run over the destination library are disjoint sets. Sharing one
    file just meant each save() -- a whole-file rewrite from an in-memory
    copy -- clobbered the other's entries, and left it owned by whichever
    process ran as root."""

    def _run(self, tmp_path, monkeypatch, extra_argv):
        source = tmp_path / "downloads"
        dest = tmp_path / "library"
        source.mkdir()
        dest.mkdir()
        loaded: list[Path] = []
        monkeypatch.setattr(
            cli.state_module, "load", lambda d: loaded.append(Path(d)) or {}
        )
        monkeypatch.setattr(cli.watcher, "watch", lambda *a, **k: None)
        argv = ["watch", str(source), "--dest", str(dest), *extra_argv]
        cli.run_watch(build_parser().parse_args(argv))
        return source, dest, loaded

    def test_defaults_to_the_watched_folder_not_dest(self, tmp_path, monkeypatch):
        source, dest, loaded = self._run(tmp_path, monkeypatch, [])
        assert loaded == [source.resolve()]
        assert dest.resolve() not in loaded

    def test_state_dir_flag_overrides(self, tmp_path, monkeypatch):
        elsewhere = tmp_path / "state"
        elsewhere.mkdir()
        _, _, loaded = self._run(
            tmp_path, monkeypatch, ["--state-dir", str(elsewhere)]
        )
        assert loaded == [elsewhere]

    def test_analyze_still_defaults_to_dest(self, tmp_path, monkeypatch):
        source = tmp_path / "downloads"
        dest = tmp_path / "library"
        source.mkdir()
        dest.mkdir()
        loaded: list[Path] = []
        monkeypatch.setattr(
            cli.state_module, "load", lambda d: loaded.append(Path(d)) or {}
        )
        args = build_parser().parse_args(
            ["analyze", str(source), "--dest", str(dest), "--dry-run"]
        )
        run_analyze(args)
        assert loaded == [dest]


class TestRescanFlag:
    def test_defaults_to_the_watcher_default(self):
        args = build_parser().parse_args(["watch", "somedir"])
        assert args.rescan_seconds == watcher.DEFAULT_RESCAN_SECONDS

    def test_can_be_disabled(self):
        args = build_parser().parse_args(["watch", "somedir", "--rescan-seconds", "0"])
        assert args.rescan_seconds == 0

    def test_is_passed_through_to_the_watcher(self, tmp_path, monkeypatch):
        captured = {}
        monkeypatch.setattr(cli.state_module, "load", lambda d: {})
        monkeypatch.setattr(
            cli.watcher, "watch", lambda *a, **kw: captured.update(kw)
        )
        args = build_parser().parse_args(
            ["watch", str(tmp_path), "--rescan-seconds", "42", "--no-backfill"]
        )
        cli.run_watch(args)
        assert captured["rescan_seconds"] == 42
        assert captured["initial_scan"] is False


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
