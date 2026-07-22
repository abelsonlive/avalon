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


class PathRenderer:
    def __init__(self, dest_root: Path | str, template: str = DEFAULT_TEMPLATE):
        self.dest_root = Path(dest_root)
        self.template = template
        self._dir_casing: dict[str, str] = {}
        self._claimed: set[Path] = set()

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
        context = {
            "artist": sanitize_segment(fields.get("artist") or UNKNOWN_ARTIST),
            "album_artist": sanitize_segment(
                fields.get("album_artist") or fields.get("artist") or UNKNOWN_ARTIST
            ),
            "album": sanitize_segment(fields.get("album") or UNKNOWN_ALBUM),
            "title": sanitize_segment(fields.get("title") or UNKNOWN_TITLE),
            "track": _track_number(fields.get("track_number")),
            "genre": sanitize_segment(fields.get("genre") or ""),
        }
        rendered = self.template.format(**context)
        raw_parts = [sanitize_segment(part) for part in Path(rendered).parts]
        *dir_parts, filename = raw_parts
        resolved_dirs = self._resolve_dir_casing(dir_parts)
        candidate = self.dest_root.joinpath(
            *resolved_dirs, f"{filename}.{extension.lstrip('.')}"
        )
        result = candidate if allow_overwrite else self._dedupe(candidate)
        self._claimed.add(result)
        return result

    def _dedupe(self, candidate: Path) -> Path:
        if not self._is_taken(candidate):
            return candidate
        stem, suffix, parent = candidate.stem, candidate.suffix, candidate.parent
        n = 2
        while True:
            alt = parent / f"{stem} ({n}){suffix}"
            if not self._is_taken(alt):
                return alt
            n += 1

    def _is_taken(self, path: Path) -> bool:
        return path in self._claimed or path.exists()
