"""Live filesystem watching for daemon mode.

Deliberately narrow: this module only turns "a file appeared and stopped
changing" into calls to a caller-supplied callback. It knows nothing about
the pipeline or the state file -- those are orchestrated by `cli.py`, which
passes in the callback. Keeping this module to just "notice, debounce,
dispatch" makes it testable without dragging in essentia/mutagen/ffmpeg.

Files are noticed two ways, and both are required:

  - inotify events, for latency.
  - a periodic full rescan, for correctness.

The rescan exists because recursive inotify watches are not reliable. A
watch on a directory tree is really one watch descriptor per subdirectory,
added by the observer *after* it sees the parent's creation event; a folder
that is created and filled faster than that (a drag-and-drop of an album
into the watched root, an rsync, an unzip) can have its contents land before
its watch descriptor exists. Those files generate no event and, in a purely
event-driven design, are stranded silently and forever -- no error, no
retry, no log line. The rescan makes that failure self-healing.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from avalon.constants import is_audio_file

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0
_STABILITY_CHECK_SECONDS = 0.5
DEFAULT_RESCAN_SECONDS = 300


class _DebouncedHandler(FileSystemEventHandler):
    def __init__(self, pending: dict[Path, float], lock: threading.Lock):
        self._pending = pending
        self._lock = lock

    def _note(self, path_str: str) -> None:
        path = Path(path_str)
        if not is_audio_file(path):
            return
        with self._lock:
            self._pending[path] = time.time()

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._note(event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._note(event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._note(event.dest_path)


def _is_stable(path: Path) -> bool:
    """Cheap guard against reacting to a file mid-copy/mid-download: true
    if its size hasn't changed across a short sleep."""
    try:
        size_before = path.stat().st_size
        time.sleep(_STABILITY_CHECK_SECONDS)
        return path.exists() and path.stat().st_size == size_before
    except OSError:
        return False


def _fingerprint(path: Path) -> tuple[float, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)


def _scan(
    source_roots: Iterable[Path],
    pending: dict[Path, float],
    lock: threading.Lock,
    dispatched: dict[Path, tuple[float, int]],
) -> int:
    """Queue every audio file under `source_roots` that we haven't already
    dispatched at its current mtime/size. Returns how many were queued.

    Comparing against `dispatched` rather than just "is it new" is what keeps
    a file that fails every time (a corrupt download) from being retried on
    every single rescan: it stays suppressed until it actually changes on
    disk, at which point it's worth another attempt.
    """
    queued = 0
    for root in source_roots:
        for path in root.rglob("*"):
            if not is_audio_file(path):
                continue
            fingerprint = _fingerprint(path)
            if fingerprint is None or dispatched.get(path) == fingerprint:
                continue
            with lock:
                # Only queue if absent: if an inotify event already queued
                # this file, overwriting would push its debounce deadline out.
                if path not in pending:
                    pending[path] = time.time()
                    queued += 1
    # Drop fingerprints for files that are gone (--delete-original removes
    # each source on success), so this map tracks the watched tree rather
    # than growing for the life of the daemon.
    for path in [p for p in dispatched if not p.exists()]:
        del dispatched[path]
    return queued


def watch(
    source_roots: list[Path],
    on_file_ready: Callable[[Path], None],
    *,
    debounce_seconds: int = 5,
    rescan_seconds: int = DEFAULT_RESCAN_SECONDS,
    initial_scan: bool = True,
) -> None:
    """Blocks, watching `source_roots` recursively, calling `on_file_ready`
    for each audio file once it's been quiet for `debounce_seconds` and its
    size has stopped changing. Runs until interrupted.

    `initial_scan` picks up files that already exist at startup; the periodic
    rescan every `rescan_seconds` (0 disables) picks up anything inotify
    missed. Note the observer starts *before* the initial scan, so files
    arriving during a long scan are still caught.
    """
    pending: dict[Path, float] = {}
    dispatched: dict[Path, tuple[float, int]] = {}
    lock = threading.Lock()

    handler = _DebouncedHandler(pending, lock)
    observer = Observer()
    for root in source_roots:
        observer.schedule(handler, str(root), recursive=True)
    observer.start()
    logger.info(
        "Watching %d folder(s) (debounce=%ds, rescan=%s)",
        len(source_roots),
        debounce_seconds,
        f"{rescan_seconds}s" if rescan_seconds else "off",
    )

    if initial_scan:
        logger.info("Scanning for existing files")
        queued = _scan(source_roots, pending, lock, dispatched)
        logger.info("Queued %d existing file(s)", queued)
    last_rescan = time.monotonic()

    try:
        while True:
            now = time.time()
            with lock:
                ready_paths = [
                    path
                    for path, last_seen in pending.items()
                    if now - last_seen >= debounce_seconds
                ]
                for path in ready_paths:
                    del pending[path]

            for path in ready_paths:
                if not path.exists():
                    continue
                if not _is_stable(path):
                    with lock:
                        pending[path] = time.time()
                    continue
                # Captured before the callback runs, because a successful
                # --delete-original leaves nothing to stat afterwards.
                fingerprint = _fingerprint(path)
                try:
                    on_file_ready(path)
                except Exception:
                    logger.exception("Error handling %s", path)
                if fingerprint is not None:
                    # Recorded on failure too -- see `_scan`.
                    dispatched[path] = fingerprint

            if rescan_seconds and time.monotonic() - last_rescan >= rescan_seconds:
                last_rescan = time.monotonic()
                queued = _scan(source_roots, pending, lock, dispatched)
                if queued:
                    logger.info(
                        "Rescan found %d file(s) that produced no filesystem event",
                        queued,
                    )

            time.sleep(_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Stopping watcher")
    finally:
        observer.stop()
        observer.join()
