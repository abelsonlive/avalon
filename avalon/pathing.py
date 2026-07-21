"""Renders the `{artist}/{album}/{title}.{ext}`-style destination path and
sanitizes each segment for cross-platform (macOS + Linux) safety.
"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_TEMPLATE = "{album_artist}/{album}/{track:02d} - {title}"

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"
UNKNOWN_TITLE = "Unknown Title"

_ILLEGAL_CHARS_RE = re.compile(r'[/:*?"<>|]')
_TRAILING_DOTS_SPACES_RE = re.compile(r"[. ]+$")


def sanitize_segment(segment: str) -> str:
    """Makes one path segment (not a full path) safe on macOS and Linux."""
    cleaned = _ILLEGAL_CHARS_RE.sub("_", segment)
    cleaned = _TRAILING_DOTS_SPACES_RE.sub("", cleaned).strip()
    return cleaned or "_"


def _track_number(raw: str | None) -> int:
    if not raw:
        return 0
    try:
        return int(raw.split("/")[0].strip())
    except ValueError:
        return 0


def _dedupe(candidate: Path) -> Path:
    if not candidate.exists():
        return candidate
    stem, suffix, parent = candidate.stem, candidate.suffix, candidate.parent
    n = 2
    while True:
        alt = parent / f"{stem} ({n}){suffix}"
        if not alt.exists():
            return alt
        n += 1


class PathRenderer:
    """Renders destination paths for one run.

    Tracks directory-name casing as it goes: ext4 (Linux) is case-sensitive
    but APFS/HFS+ (macOS) generally isn't, so if two files' tags disagree
    only in case (e.g. "Artist Name" vs "artist name"), reusing the
    first-seen casing avoids silently creating two folders on Linux where
    macOS would have merged them into one.
    """

    def __init__(self, dest_root: Path | str, template: str = DEFAULT_TEMPLATE):
        self.dest_root = Path(dest_root)
        self.template = template
        self._dir_casing: dict[str, str] = {}

    def _resolve_dir_casing(self, parts: list[str]) -> list[str]:
        resolved = []
        prefix_key = ""
        for part in parts:
            prefix_key = f"{prefix_key}/{part.lower()}"
            canonical = self._dir_casing.setdefault(prefix_key, part)
            resolved.append(canonical)
        return resolved

    def render(
        self, fields: dict[str, str], extension: str, allow_overwrite: bool = False
    ) -> Path:
        """`allow_overwrite=True` returns the natural path even if it already
        exists (the caller intends to replace it -- e.g. reprocessing the
        same source on a later run). Otherwise an existing path is a genuine
        collision and gets a numbered suffix rather than being clobbered."""
        context = {
            "artist": fields.get("artist") or UNKNOWN_ARTIST,
            "album_artist": fields.get("album_artist")
            or fields.get("artist")
            or UNKNOWN_ARTIST,
            "album": fields.get("album") or UNKNOWN_ALBUM,
            "title": fields.get("title") or UNKNOWN_TITLE,
            "track": _track_number(fields.get("track_number")),
            "genre": fields.get("genre") or "",
        }
        rendered = self.template.format(**context)
        raw_parts = [sanitize_segment(part) for part in Path(rendered).parts]
        *dir_parts, filename = raw_parts
        resolved_dirs = self._resolve_dir_casing(dir_parts)
        candidate = self.dest_root.joinpath(*resolved_dirs, filename)
        candidate = candidate.with_suffix(f".{extension.lstrip('.')}")
        return candidate if allow_overwrite else _dedupe(candidate)
