"""Live filesystem watching for daemon mode.

Deliberately narrow: this module only turns filesystem events into calls to
a caller-supplied callback once a file has stopped changing for
`debounce_seconds`. It knows nothing about the pipeline, state file, or
startup backfill -- those are orchestrated by `cli.py`, which runs the same
batch-processing helper for both the initial backfill pass and files this
watcher reports as ready. Keeping this module to just "watch and debounce"
makes it testable without dragging in essentia/mutagen/ffmpeg.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from avalon.constants import AUDIO_EXTENSIONS

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0
_STABILITY_CHECK_SECONDS = 0.5


class _DebouncedHandler(FileSystemEventHandler):
    def __init__(self, pending: dict[Path, float], lock: threading.Lock):
        self._pending = pending
        self._lock = lock

    def _note(self, path_str: str) -> None:
        path = Path(path_str)
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
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


def watch(
    source_roots: list[Path],
    on_file_ready: Callable[[Path], None],
    *,
    debounce_seconds: int = 5,
) -> None:
    """Blocks, watching `source_roots` recursively, calling `on_file_ready`
    for each audio file once it's been quiet for `debounce_seconds` and its
    size has stopped changing. Runs until interrupted."""
    pending: dict[Path, float] = {}
    lock = threading.Lock()

    handler = _DebouncedHandler(pending, lock)
    observer = Observer()
    for root in source_roots:
        observer.schedule(handler, str(root), recursive=True)
    observer.start()
    logger.info("Watching %d folder(s) (debounce=%ds)", len(source_roots), debounce_seconds)

    try:
        while True:
            now = time.time()
            with lock:
                ready_paths = [
                    path for path, last_seen in pending.items() if now - last_seen >= debounce_seconds
                ]
                for path in ready_paths:
                    del pending[path]

            for path in ready_paths:
                if not path.exists():
                    continue
                if not _is_stable(path):
                    with lock:
                        pending[path] = time.time()  # still changing -- recheck next cycle
                    continue
                try:
                    on_file_ready(path)
                except Exception:
                    logger.exception("Error handling %s", path)

            time.sleep(_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Stopping watcher")
    finally:
        observer.stop()
        observer.join()
