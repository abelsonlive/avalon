import threading
import time
from pathlib import Path

import pytest

from avalon import watcher
from avalon.watcher import _DebouncedHandler, _scan


def _touch(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class _Event:
    def __init__(self, path: Path, is_directory: bool = False):
        self.src_path = str(path)
        self.dest_path = str(path)
        self.is_directory = is_directory


class TestHandlerExcludesAppleDouble:
    """The bug that filled the daemon's log with 'not a valid FLAC file':
    macOS drops an AppleDouble sidecar next to every file copied to SMB, and
    the handler used to queue anything with an audio extension."""

    def _pending_after(self, path: Path) -> dict:
        pending: dict[Path, float] = {}
        _DebouncedHandler(pending, threading.Lock()).on_created(_Event(path))
        return pending

    def test_sidecar_is_not_queued(self, tmp_path):
        assert self._pending_after(tmp_path / "._track.flac") == {}

    def test_real_file_is_queued(self, tmp_path):
        path = tmp_path / "track.flac"
        assert list(self._pending_after(path)) == [path]

    def test_non_audio_is_not_queued(self, tmp_path):
        assert self._pending_after(tmp_path / "cover.png") == {}

    def test_directory_events_are_ignored(self, tmp_path):
        pending: dict[Path, float] = {}
        handler = _DebouncedHandler(pending, threading.Lock())
        handler.on_created(_Event(tmp_path / "album.flac", is_directory=True))
        assert pending == {}


class TestScan:
    def _scan(self, roots, pending=None, dispatched=None):
        pending = {} if pending is None else pending
        dispatched = {} if dispatched is None else dispatched
        queued = _scan(roots, pending, threading.Lock(), dispatched)
        return queued, pending, dispatched

    def test_finds_nested_files_and_skips_sidecars(self, tmp_path):
        _touch(tmp_path / "album" / "01.aiff")
        _touch(tmp_path / "album" / "._01.aiff")
        _touch(tmp_path / "album" / "cover.png")
        queued, pending, _ = self._scan([tmp_path])
        assert queued == 1
        assert list(pending) == [tmp_path / "album" / "01.aiff"]

    def test_already_dispatched_file_is_not_requeued(self, tmp_path):
        path = _touch(tmp_path / "track.mp3", b"data")
        _, _, dispatched = self._scan([tmp_path])
        # Stand in for the dispatch the main loop would have recorded.
        dispatched[path] = watcher._fingerprint(path)
        queued, pending, _ = self._scan([tmp_path], dispatched=dispatched)
        assert queued == 0
        assert pending == {}

    def test_a_changed_file_is_requeued(self, tmp_path):
        """A file that failed processing gets suppressed until it changes --
        then it's worth another attempt."""
        path = _touch(tmp_path / "track.mp3", b"data")
        dispatched = {path: watcher._fingerprint(path)}
        path.write_bytes(b"different data")
        queued, pending, _ = self._scan([tmp_path], dispatched=dispatched)
        assert queued == 1
        assert list(pending) == [path]

    def test_does_not_reset_an_existing_debounce_deadline(self, tmp_path):
        """Queuing from a scan must not push out a deadline set by an inotify
        event, or a file re-scanned often enough would never come due."""
        path = _touch(tmp_path / "track.mp3")
        queued_at = time.time() - 1000
        pending = {path: queued_at}
        queued, pending, _ = self._scan([tmp_path], pending=pending)
        assert queued == 0
        assert pending[path] == queued_at

    def test_prunes_fingerprints_for_vanished_files(self, tmp_path):
        """--delete-original removes each source on success; without pruning,
        the map would grow for the life of the daemon."""
        gone = tmp_path / "deleted.mp3"
        dispatched = {gone: (1.0, 1)}
        _, _, dispatched = self._scan([tmp_path], dispatched=dispatched)
        assert dispatched == {}


class _StubObserver:
    """Stands in for watchdog's observer, delivering no events ever.

    That is not a limitation, it's the point: "the watch descriptor for this
    folder was never added, so nothing here ever generates an event" is
    precisely the failure the rescan exists to recover from. It also keeps
    these tests off platform-specific observer behaviour -- macOS FSEvents
    replays events for files created shortly before the stream starts, which
    inotify does not."""

    def __init__(self):
        self.scheduled: list[str] = []

    def schedule(self, handler, path, recursive=False):
        self.scheduled.append(path)

    def start(self):
        pass

    def stop(self):
        pass

    def join(self, timeout=None):
        pass


@pytest.fixture
def deaf_observer(monkeypatch):
    monkeypatch.setattr(watcher, "Observer", _StubObserver)


def _run_watch(roots, on_file_ready, **kwargs) -> threading.Thread:
    thread = threading.Thread(
        target=watcher.watch,
        args=(roots, on_file_ready),
        kwargs={"debounce_seconds": 0, **kwargs},
        daemon=True,
    )
    thread.start()
    return thread


def _collector(stop_after: int = 1):
    """Records dispatched paths; raises KeyboardInterrupt once `stop_after`
    have arrived so `watch` returns instead of leaking a running thread."""
    seen: list[Path] = []
    done = threading.Event()

    def on_file_ready(path: Path) -> None:
        seen.append(path)
        if len(seen) >= stop_after:
            done.set()
            raise KeyboardInterrupt

    return seen, done, on_file_ready


class TestScanDrivenDispatch:
    """`watch` with no events arriving at all -- everything here comes from
    the startup scan or the periodic rescan."""

    def test_processes_files_present_at_startup(self, tmp_path, deaf_observer):
        path = _touch(tmp_path / "album" / "track.mp3", b"data")
        seen, done, callback = _collector()
        _run_watch([tmp_path], callback)
        assert done.wait(timeout=8.0), "startup scan never dispatched the file"
        assert seen == [path]

    def test_no_backfill_skips_pre_existing_files(self, tmp_path, deaf_observer):
        _touch(tmp_path / "track.mp3", b"data")
        seen, done, callback = _collector()
        _run_watch([tmp_path], callback, initial_scan=False, rescan_seconds=0)
        assert not done.wait(timeout=3.0)
        assert seen == []

    def test_rescan_recovers_a_file_that_produced_no_event(
        self, tmp_path, deaf_observer
    ):
        """The Visager failure: an album folder was created and filled faster
        than watchdog added its watch descriptor, so its 19 files generated no
        events and sat stranded for 45 hours. The rescan is what finds them."""
        seen, done, callback = _collector()
        _run_watch([tmp_path], callback, initial_scan=False, rescan_seconds=1)
        path = tmp_path / "album" / "track.mp3"
        path.parent.mkdir()
        path.write_bytes(b"data")
        assert done.wait(timeout=10.0), "rescan never recovered the file"
        assert seen == [path]

    def test_a_failing_file_is_dispatched_once_not_every_rescan(
        self, tmp_path, deaf_observer
    ):
        _touch(tmp_path / "corrupt.mp3", b"data")
        calls: list[Path] = []

        def on_file_ready(path: Path) -> None:
            calls.append(path)
            raise ValueError("simulated pipeline failure")

        _run_watch([tmp_path], on_file_ready, rescan_seconds=1)
        time.sleep(4.0)
        assert calls == [tmp_path / "corrupt.mp3"]


class TestObserverWiring:
    """One end-to-end pass over the real watchdog observer, to prove the
    event path is actually connected."""

    def test_reacts_to_a_file_created_after_startup(self, tmp_path):
        seen, done, callback = _collector()
        _run_watch([tmp_path], callback, initial_scan=False, rescan_seconds=0)
        time.sleep(1.0)  # let the observer settle before creating anything
        path = tmp_path / "track.mp3"
        path.write_bytes(b"data")
        assert done.wait(timeout=10.0), "no event dispatched the new file"
        assert seen == [path]
