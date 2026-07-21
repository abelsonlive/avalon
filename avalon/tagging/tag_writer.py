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

import dataclasses
import logging
from pathlib import Path

from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import COMM, TBPM, TCON, TDRC, TDRL, TKEY, TPUB, TSRC, TXXX, UFID
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.wave import WAVE

from avalon.constants import (
    EXTENSION_TO_FORMAT,
    FLAC_RELEASE_DATE_FIELD,
    ID3_FAMILY,
    IDENTITY_FIELD_MAPS,
    MB_RECORDING_UFID_OWNER,
    TAG_FRAME_MAPS,
    FileFormat,
    IdentityFieldMap,
)

logger = logging.getLogger(__name__)

_MUTAGEN_CLASS = {
    FileFormat.MP3: MP3,
    FileFormat.AIFF: AIFF,
    FileFormat.WAV: WAVE,
    FileFormat.FLAC: FLAC,
    FileFormat.MP4: MP4,
}

CANONICAL_READ_FIELDS = (
    "title",
    "artist",
    "album",
    "album_artist",
    "track_number",
    "date",
    "genre",
    "bpm",
    "key",
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


def read_canonical(audio, file_format: FileFormat) -> dict[str, str]:
    if file_format in ID3_FAMILY:
        return _read_id3_canonical(audio)
    if file_format is FileFormat.FLAC:
        return _read_flac_canonical(audio)
    return _read_mp4_canonical(audio)


def _read_id3_canonical(audio) -> dict[str, str]:
    frames = TAG_FRAME_MAPS[FileFormat.MP3]
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


def write_generated_fields(
    audio,
    file_format: FileFormat,
    *,
    bpm: str | None,
    key: str | None,
    genre: str | None,
    date: str | None = None,
    fill_only_if_missing: bool,
) -> None:
    existing = read_canonical(audio, file_format)
    to_write = {}
    for field, value in (("bpm", bpm), ("key", key), ("genre", genre), ("date", date)):
        if value is None:
            continue
        existing_value = existing.get(field)
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
    if "date" in values:
        audio.tags.add(TDRC(encoding=3, text=values["date"]))


def _write_flac_generated(audio, values: dict[str, str]) -> None:
    frames = TAG_FRAME_MAPS[FileFormat.FLAC]
    if "bpm" in values:
        audio.tags[frames.bpm] = [values["bpm"]]
    if "key" in values:
        audio.tags[frames.key] = [values["key"]]
    if "genre" in values:
        audio.tags[frames.genre] = [values["genre"]]
    if "date" in values:
        audio.tags[frames.date] = [values["date"]]


def _write_mp4_generated(audio, values: dict[str, str]) -> None:
    frames = TAG_FRAME_MAPS[FileFormat.MP4]
    if "bpm" in values:
        audio.tags[frames.bpm] = [int(round(float(values["bpm"])))]
    if "key" in values:
        audio.tags[frames.key] = values["key"].encode("utf-8")
    if "genre" in values:
        audio.tags[frames.genre] = [values["genre"]]
    if "date" in values:
        audio.tags[frames.date] = [values["date"]]


def write_release_date(
    audio, file_format: FileFormat, value: str | None, *, fill_only_if_missing: bool
) -> None:
    """Navidrome's persistent-ID scheme (and TagLib's own tag mapping) treat
    "release date" -- ID3's TDRL frame / a literal `releasedate` Vorbis
    field -- as distinct from the generic recording date `write_generated_
    fields`'s `date` param already covers (TDRC/DATE). MP4 has no such
    distinction (`©day` already serves both via the existing `date` write),
    so this is a no-op there."""
    if value is None or file_format is FileFormat.MP4:
        return
    if file_format in ID3_FAMILY:
        existing = audio.tags.get("TDRL")
        if fill_only_if_missing and existing and existing.text:
            return
        audio.tags.add(TDRL(encoding=3, text=value))
    elif file_format is FileFormat.FLAC:
        existing = audio.tags.get(FLAC_RELEASE_DATE_FIELD) if audio.tags else None
        if fill_only_if_missing and existing:
            return
        audio.tags[FLAC_RELEASE_DATE_FIELD] = [value]


def _headline_frame_id(file_format: FileFormat, tag_name: str) -> str:
    """Resolves a user-chosen `--headline-tag` name to a concrete frame/atom
    id. The format's native comment slot (COMM for ID3-family, `desc` for
    MP4) is used verbatim when requested; any other name becomes a TXXX
    frame (ID3-family) or freeform atom (MP4) instead, mirroring how the
    extended tag already works. FLAC Vorbis comments have no special native
    slot, so any name is just used directly as the field key."""
    if file_format in ID3_FAMILY:
        return "COMM" if tag_name.upper() == "COMM" else f"TXXX:{tag_name}"
    if file_format is FileFormat.MP4:
        return "desc" if tag_name == "desc" else f"----:com.avalon:{tag_name}"
    return tag_name


def read_headline(
    audio, file_format: FileFormat, tag_name: str | None = None
) -> str | None:
    frames = TAG_FRAME_MAPS[file_format]
    frame_id = _headline_frame_id(file_format, tag_name or frames.headline)
    if file_format in ID3_FAMILY:
        if frame_id == "COMM":
            comments = audio.tags.getall("COMM")
            return str(comments[0].text[0]) if comments and comments[0].text else None
        frame = audio.tags.get(frame_id)
        return str(frame.text[0]) if frame and frame.text else None
    if file_format is FileFormat.MP4:
        values = audio.tags.get(frame_id) if audio.tags else None
        if not values:
            return None
        return str(values[0]) if frame_id == "desc" else values[0].decode("utf-8")
    values = audio.tags.get(frame_id) if audio.tags else None
    return str(values[0]) if values else None


def write_headline(
    audio, file_format: FileFormat, value: str, tag_name: str | None = None
) -> None:
    frames = TAG_FRAME_MAPS[file_format]
    frame_id = _headline_frame_id(file_format, tag_name or frames.headline)
    if file_format in ID3_FAMILY:
        if frame_id == "COMM":
            for existing_key in [k for k in audio.tags.keys() if k.startswith("COMM")]:
                del audio.tags[existing_key]
            audio.tags.add(COMM(encoding=3, lang="eng", desc="", text=value))
        else:
            _, _, description = frame_id.partition(":")
            audio.tags.add(TXXX(encoding=3, desc=description, text=value))
    elif file_format is FileFormat.MP4:
        if frame_id == "desc":
            audio.tags[frame_id] = [value]
        else:
            audio.tags[frame_id] = value.encode("utf-8")
    else:
        audio.tags[frame_id] = [value]


def _read_tag_value(audio, file_format: FileFormat, frame_id: str) -> str | None:
    """One string value at one frame/field/atom id. Shared by the extended
    and identity blob tags and the generic (non-ID3-native) identity
    fields below -- all boil down to the same "one value per format" shape,
    differing only in which id they live at."""
    if file_format in ID3_FAMILY:
        frame = audio.tags.get(frame_id)
        return str(frame.text[0]) if frame and frame.text else None
    if file_format is FileFormat.FLAC:
        values = audio.tags.get(frame_id) if audio.tags else None
        return str(values[0]) if values else None
    values = audio.tags.get(frame_id) if audio.tags else None
    return values[0].decode("utf-8") if values else None


def _write_tag_value(audio, file_format: FileFormat, frame_id: str, value: str) -> None:
    if file_format in ID3_FAMILY:
        _, _, description = frame_id.partition(":")
        audio.tags.add(TXXX(encoding=3, desc=description, text=value))
    elif file_format is FileFormat.FLAC:
        audio.tags[frame_id] = [value]
    else:
        audio.tags[frame_id] = value.encode("utf-8")


def read_extended(audio, file_format: FileFormat) -> str | None:
    return _read_tag_value(audio, file_format, TAG_FRAME_MAPS[file_format].extended)


def write_extended(audio, file_format: FileFormat, value: str) -> None:
    _write_tag_value(audio, file_format, TAG_FRAME_MAPS[file_format].extended, value)


def read_identity_extended(audio, file_format: FileFormat) -> str | None:
    return _read_tag_value(audio, file_format, TAG_FRAME_MAPS[file_format].identity)


def write_identity_extended(audio, file_format: FileFormat, value: str) -> None:
    _write_tag_value(audio, file_format, TAG_FRAME_MAPS[file_format].identity, value)


IDENTITY_FIELD_NAMES = tuple(f.name for f in dataclasses.fields(IdentityFieldMap))
_ID3_NATIVE_IDENTITY_FIELDS = {"musicbrainz_recording_id", "isrc", "label"}


def read_identity_fields(audio, file_format: FileFormat) -> dict[str, str]:
    field_map = IDENTITY_FIELD_MAPS[file_format]
    result: dict[str, str] = {}
    fields = IDENTITY_FIELD_NAMES
    if file_format in ID3_FAMILY:
        ufid = audio.tags.get(f"UFID:{MB_RECORDING_UFID_OWNER}")
        if ufid is not None and ufid.data:
            result["musicbrainz_recording_id"] = ufid.data.decode("ascii")
        for field, frame_id in (("isrc", "TSRC"), ("label", "TPUB")):
            frame = audio.tags.get(frame_id)
            if frame is not None and frame.text:
                result[field] = str(frame.text[0])
        fields = tuple(f for f in fields if f not in _ID3_NATIVE_IDENTITY_FIELDS)
    for field in fields:
        value = _read_tag_value(audio, file_format, getattr(field_map, field))
        if value is not None:
            result[field] = value
    return result


def write_identity_fields(
    audio, file_format: FileFormat, values: dict[str, str]
) -> None:
    """`values` keys are any subset of `IDENTITY_FIELD_NAMES`; the caller
    (pipeline.py) is responsible for fill-only-if-missing filtering, same
    as `write_generated_fields`'s own caller -- this just writes whatever
    it's handed."""
    field_map = IDENTITY_FIELD_MAPS[file_format]
    fields = IDENTITY_FIELD_NAMES
    if file_format in ID3_FAMILY:
        if "musicbrainz_recording_id" in values:
            audio.tags.add(
                UFID(
                    owner=MB_RECORDING_UFID_OWNER,
                    data=values["musicbrainz_recording_id"].encode("ascii"),
                )
            )
        if "isrc" in values:
            audio.tags.add(TSRC(encoding=3, text=values["isrc"]))
        if "label" in values:
            audio.tags.add(TPUB(encoding=3, text=values["label"]))
        fields = tuple(f for f in fields if f not in _ID3_NATIVE_IDENTITY_FIELDS)
    for field in fields:
        if field in values:
            _write_tag_value(
                audio, file_format, getattr(field_map, field), values[field]
            )
