"""Generalized per-format tag reader/writer.

Three code paths, not five: MP3/AIFF/WAV share one ID3v2 implementation
(mutagen's WAVE class wraps an embedded ID3 chunk the same way AIFF does --
confirmed directly, not assumed), FLAC (Vorbis comments) and MP4 (iTunes
atoms) each need their own.

avalon only *writes* the canonical fields its own analysis produces --
genre, bpm, and key (as Camelot). It never invents title/artist/album; those
are read here for path-templating purposes elsewhere in the pipeline, not
generated.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import COMM, TBPM, TCON, TKEY, TXXX
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.wave import WAVE

from avalon.constants import EXTENSION_TO_FORMAT, ID3_FAMILY, TAG_FRAME_MAPS, FileFormat

logger = logging.getLogger(__name__)

_MUTAGEN_CLASS = {
    FileFormat.MP3: MP3,
    FileFormat.AIFF: AIFF,
    FileFormat.WAV: WAVE,
    FileFormat.FLAC: FLAC,
    FileFormat.MP4: MP4,
}

CANONICAL_READ_FIELDS = (
    "title", "artist", "album", "album_artist", "track_number", "date", "genre", "bpm", "key",
)


class UnsupportedFormatError(Exception):
    pass


def detect_format(path: str) -> FileFormat:
    ext = Path(path).suffix.lower()
    fmt = EXTENSION_TO_FORMAT.get(ext)
    if fmt is None:
        raise UnsupportedFormatError(f"Unsupported audio extension: {ext!r}")
    return fmt


def load(path: str, file_format: FileFormat | None = None):
    """Opens `path` with the right mutagen class, ensuring tags exist."""
    file_format = file_format or detect_format(path)
    audio = _MUTAGEN_CLASS[file_format](path)
    if audio.tags is None:
        audio.add_tags()
    return audio


def save(audio) -> None:
    audio.save()


# ---- reading canonical fields (never written back verbatim by avalon) ----


def read_canonical(audio, file_format: FileFormat) -> dict[str, str]:
    if file_format in ID3_FAMILY:
        return _read_id3_canonical(audio)
    if file_format is FileFormat.FLAC:
        return _read_flac_canonical(audio)
    return _read_mp4_canonical(audio)


def _read_id3_canonical(audio) -> dict[str, str]:
    frames = TAG_FRAME_MAPS[FileFormat.MP3]  # frame names identical across the ID3 family
    result = {}
    for field in CANONICAL_READ_FIELDS:
        frame_id = getattr(frames, field)
        frame = audio.tags.get(frame_id)
        if frame is not None and frame.text:
            result[field] = str(frame.text[0])
    return result


def _read_flac_canonical(audio) -> dict[str, str]:
    frames = TAG_FRAME_MAPS[FileFormat.FLAC]
    result = {}
    for field in CANONICAL_READ_FIELDS:
        values = audio.tags.get(getattr(frames, field)) if audio.tags else None
        if values:
            result[field] = str(values[0])
    return result


def _read_mp4_canonical(audio) -> dict[str, str]:
    frames = TAG_FRAME_MAPS[FileFormat.MP4]
    result = {}
    for field in ("title", "artist", "album", "album_artist", "date", "genre"):
        values = audio.tags.get(getattr(frames, field)) if audio.tags else None
        if values:
            result[field] = str(values[0])
    if audio.tags and frames.track_number in audio.tags:
        track_no = audio.tags[frames.track_number][0][0]
        result["track_number"] = str(track_no)
    if audio.tags and frames.key in audio.tags:
        result["key"] = audio.tags[frames.key][0].decode("utf-8")
    if audio.tags and frames.bpm in audio.tags:
        result["bpm"] = str(audio.tags[frames.bpm][0])
    return result


# ---- writing avalon-generated canonical fields (genre, bpm, key) ----


def write_generated_fields(
    audio,
    file_format: FileFormat,
    *,
    bpm: str | None,
    key: str | None,
    genre: str | None,
    fill_only_if_missing: bool,
) -> None:
    existing = read_canonical(audio, file_format)
    to_write = {}
    for field, value in (("bpm", bpm), ("key", key), ("genre", genre)):
        if value is None:
            continue
        existing_value = existing.get(field)
        # "0" is a common sentinel for "no BPM tagged" -- treat it as absent
        # rather than as a real value blocking fill-only-if-missing.
        if field == "bpm" and existing_value == "0":
            existing_value = None
        if fill_only_if_missing and existing_value:
            continue
        to_write[field] = value
    if not to_write:
        return

    if file_format in ID3_FAMILY:
        _write_id3_generated(audio, to_write)
    elif file_format is FileFormat.FLAC:
        _write_flac_generated(audio, to_write)
    else:
        _write_mp4_generated(audio, to_write)


def _write_id3_generated(audio, values: dict[str, str]) -> None:
    if "bpm" in values:
        audio.tags.add(TBPM(encoding=3, text=values["bpm"]))
    if "key" in values:
        audio.tags.add(TKEY(encoding=3, text=values["key"]))
    if "genre" in values:
        audio.tags.add(TCON(encoding=3, text=values["genre"]))


def _write_flac_generated(audio, values: dict[str, str]) -> None:
    frames = TAG_FRAME_MAPS[FileFormat.FLAC]
    if "bpm" in values:
        audio.tags[frames.bpm] = [values["bpm"]]
    if "key" in values:
        audio.tags[frames.key] = [values["key"]]
    if "genre" in values:
        audio.tags[frames.genre] = [values["genre"]]


def _write_mp4_generated(audio, values: dict[str, str]) -> None:
    frames = TAG_FRAME_MAPS[FileFormat.MP4]
    if "bpm" in values:
        audio.tags[frames.bpm] = [int(round(float(values["bpm"])))]
    if "key" in values:
        audio.tags[frames.key] = values["key"].encode("utf-8")
    if "genre" in values:
        audio.tags[frames.genre] = [values["genre"]]


# ---- headline + extended analysis tags ----


def read_headline(audio, file_format: FileFormat) -> str | None:
    frames = TAG_FRAME_MAPS[file_format]
    if file_format in ID3_FAMILY:
        # COMM frames are keyed by (desc, lang), e.g. "COMM::eng" -- not the
        # plain "COMM" this maps to, so a direct .get() would always miss.
        comments = audio.tags.getall(frames.headline)
        return str(comments[0].text[0]) if comments and comments[0].text else None
    if file_format is FileFormat.FLAC:
        values = audio.tags.get(frames.headline) if audio.tags else None
        return str(values[0]) if values else None
    values = audio.tags.get(frames.headline) if audio.tags else None
    return str(values[0]) if values else None


def write_headline(audio, file_format: FileFormat, value: str) -> None:
    frames = TAG_FRAME_MAPS[file_format]
    if file_format in ID3_FAMILY:
        for existing_key in [k for k in audio.tags.keys() if k.startswith("COMM")]:
            del audio.tags[existing_key]
        audio.tags.add(COMM(encoding=3, lang="eng", desc="", text=value))
    elif file_format is FileFormat.FLAC:
        audio.tags[frames.headline] = [value]
    else:
        audio.tags[frames.headline] = [value]


def read_extended(audio, file_format: FileFormat) -> str | None:
    frames = TAG_FRAME_MAPS[file_format]
    if file_format in ID3_FAMILY:
        frame = audio.tags.get(frames.extended)
        return str(frame.text[0]) if frame and frame.text else None
    if file_format is FileFormat.FLAC:
        values = audio.tags.get(frames.extended) if audio.tags else None
        return str(values[0]) if values else None
    values = audio.tags.get(frames.extended) if audio.tags else None
    return values[0].decode("utf-8") if values else None


def write_extended(audio, file_format: FileFormat, value: str) -> None:
    frames = TAG_FRAME_MAPS[file_format]
    if file_format in ID3_FAMILY:
        _, _, description = frames.extended.partition(":")
        audio.tags.add(TXXX(encoding=3, desc=description, text=value))
    elif file_format is FileFormat.FLAC:
        audio.tags[frames.extended] = [value]
    else:
        audio.tags[frames.extended] = value.encode("utf-8")
