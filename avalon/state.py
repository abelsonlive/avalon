"""JSON idempotency state: tracks which source files have already been
processed (by mtime + size + a partial content hash) so re-runs and daemon
backfills skip files that haven't actually changed, without needing to
open/tag every file on every run.

This is a fast pre-filter over the filesystem; the finer-grained "does this
file's own tags already reflect the current analysis schema" decision lives
in `tagging.analysis_blob.has_current_schema` and is checked inside the
pipeline itself, since it must survive even if this state file is deleted
or the file was moved.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_FILENAME = ".avalon_state.json"
_PARTIAL_HASH_BYTES = 65536


def load(dest_root: Path | str) -> dict[str, Any]:
    path = Path(dest_root) / STATE_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not parse state file %s, starting fresh", path)
        return {}


def save(dest_root: Path | str, state: dict[str, Any]) -> None:
    path = Path(dest_root) / STATE_FILENAME
    try:
        path.write_text(json.dumps(state, indent=2, sort_keys=True))
    except OSError:
        logger.warning("Failed writing state file %s", path)


def fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    with open(path, "rb") as fh:
        head = fh.read(_PARTIAL_HASH_BYTES)
    return {
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "partial_hash": hashlib.sha1(head).hexdigest(),
    }


def is_unchanged(state: dict[str, Any], path: Path) -> bool:
    recorded = state.get(str(path.resolve()))
    if recorded is None:
        return False
    return recorded == fingerprint(path)


def record(state: dict[str, Any], path: Path) -> None:
    state[str(path.resolve())] = fingerprint(path)
