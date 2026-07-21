"""Shared constants: supported formats, Camelot key notation, and per-format
tag frame names.

Centralizes format-specific knowledge so tagging/analysis code stays
declarative rather than branching on file extension everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

AUDIO_EXTENSIONS: Final[set[str]] = {
    ".mp3",
    ".flac",
    ".aiff",
    ".aif",
    ".m4a",
    ".mp4",
    ".wav",
}

ANALYSIS_SCHEMA_VERSION: Final[int] = 1

MODEL_CACHE_DIRNAME: Final[str] = "avalon"

CAMELOT_WHEEL: Final[dict[tuple[str, str], str]] = {
    ("C", "major"): "8B",
    ("C#", "major"): "3B",
    ("D", "major"): "10B",
    ("D#", "major"): "5B",
    ("E", "major"): "12B",
    ("F", "major"): "7B",
    ("F#", "major"): "2B",
    ("G", "major"): "9B",
    ("G#", "major"): "4B",
    ("A", "major"): "11B",
    ("A#", "major"): "6B",
    ("B", "major"): "1B",
    ("A", "minor"): "8A",
    ("E", "minor"): "9A",
    ("B", "minor"): "10A",
    ("F#", "minor"): "11A",
    ("C#", "minor"): "12A",
    ("G#", "minor"): "1A",
    ("D#", "minor"): "2A",
    ("A#", "minor"): "3A",
    ("F", "minor"): "4A",
    ("C", "minor"): "5A",
    ("G", "minor"): "6A",
    ("D", "minor"): "7A",
}


def to_camelot(key: str, scale: str) -> str | None:
    """Map an essentia (key, scale) pair to Camelot notation, if known."""
    return CAMELOT_WHEEL.get((key, scale))


class FileFormat(Enum):
    """Audio container/tag-format families avalon knows how to tag."""

    MP3 = "mp3"
    FLAC = "flac"
    AIFF = "aiff"
    MP4 = "mp4"
    WAV = "wav"


EXTENSION_TO_FORMAT: Final[dict[str, FileFormat]] = {
    ".mp3": FileFormat.MP3,
    ".flac": FileFormat.FLAC,
    ".aiff": FileFormat.AIFF,
    ".aif": FileFormat.AIFF,
    ".m4a": FileFormat.MP4,
    ".mp4": FileFormat.MP4,
    ".wav": FileFormat.WAV,
}

ID3_FAMILY: Final[set[FileFormat]] = {FileFormat.MP3, FileFormat.AIFF, FileFormat.WAV}

EXTENDED_TAG_NAME: Final[str] = "AVALON_ANALYSIS"
MP4_EXTENDED_ATOM: Final[str] = "----:com.avalon:analysis"
MP4_KEY_ATOM: Final[str] = "----:com.apple.iTunes:INITIALKEY"

IDENTITY_TAG_NAME: Final[str] = "AVALON_IDENTITY"
MP4_IDENTITY_ATOM: Final[str] = "----:com.avalon:identity"
IDENTITY_SCHEMA_VERSION: Final[int] = 1

MB_RECORDING_UFID_OWNER: Final[str] = "http://musicbrainz.org"

FLAC_RELEASE_DATE_FIELD: Final[str] = "RELEASEDATE"


@dataclass(frozen=True, slots=True)
class TagFrameMap:
    """Per-format tag frame/field names for every canonical field avalon writes."""

    title: str
    artist: str
    album: str
    album_artist: str
    track_number: str
    date: str
    genre: str
    bpm: str
    key: str
    headline: str
    extended: str
    identity: str


_ID3_TAG_FRAME_MAP = TagFrameMap(
    title="TIT2",
    artist="TPE1",
    album="TALB",
    album_artist="TPE2",
    track_number="TRCK",
    date="TDRC",
    genre="TCON",
    bpm="TBPM",
    key="TKEY",
    headline="COMM",
    extended="TXXX:" + EXTENDED_TAG_NAME,
    identity="TXXX:" + IDENTITY_TAG_NAME,
)

TAG_FRAME_MAPS: Final[dict[FileFormat, TagFrameMap]] = {
    FileFormat.MP3: _ID3_TAG_FRAME_MAP,
    FileFormat.AIFF: _ID3_TAG_FRAME_MAP,
    FileFormat.WAV: _ID3_TAG_FRAME_MAP,
    FileFormat.FLAC: TagFrameMap(
        title="TITLE",
        artist="ARTIST",
        album="ALBUM",
        album_artist="ALBUMARTIST",
        track_number="TRACKNUMBER",
        date="DATE",
        genre="GENRE",
        bpm="BPM",
        key="INITIALKEY",
        headline="DESCRIPTION",
        extended=EXTENDED_TAG_NAME,
        identity=IDENTITY_TAG_NAME,
    ),
    FileFormat.MP4: TagFrameMap(
        title="\xa9nam",
        artist="\xa9ART",
        album="\xa9alb",
        album_artist="aART",
        track_number="trkn",
        date="\xa9day",
        genre="\xa9gen",
        bpm="tmpo",
        key=MP4_KEY_ATOM,
        headline="desc",
        extended=MP4_EXTENDED_ATOM,
        identity=MP4_IDENTITY_ATOM,
    ),
}


@dataclass(frozen=True, slots=True)
class IdentityFieldMap:
    """Per-format names for the Picard-interop identity fields --
    everything --identify writes beyond avalon's own AVALON_IDENTITY blob.

    `musicbrainz_recording_id`/`isrc`/`label` are `None` for the ID3-family
    row: those three use native UFID/TSRC/TPUB frames instead of TXXX there
    (Picard's own convention, confirmed against a real Picard-tagged
    fixture), handled as explicit special cases in tag_writer.py rather
    than through this generic map.
    """

    musicbrainz_recording_id: str | None
    musicbrainz_release_id: str
    musicbrainz_artist_id: str
    discogs_release_id: str
    acoustid_id: str
    isrc: str | None
    label: str | None
    catalog_number: str
    release_country: str


_ID3_IDENTITY_FIELD_MAP = IdentityFieldMap(
    musicbrainz_recording_id=None,
    musicbrainz_release_id="TXXX:MusicBrainz Album Id",
    musicbrainz_artist_id="TXXX:MusicBrainz Artist Id",
    discogs_release_id="TXXX:DISCOGS_RELEASE_ID",
    acoustid_id="TXXX:Acoustid Id",
    isrc=None,
    label=None,
    catalog_number="TXXX:CATALOGNUMBER",
    release_country="TXXX:MusicBrainz Album Release Country",
)

IDENTITY_FIELD_MAPS: Final[dict[FileFormat, IdentityFieldMap]] = {
    FileFormat.MP3: _ID3_IDENTITY_FIELD_MAP,
    FileFormat.AIFF: _ID3_IDENTITY_FIELD_MAP,
    FileFormat.WAV: _ID3_IDENTITY_FIELD_MAP,
    FileFormat.FLAC: IdentityFieldMap(
        musicbrainz_recording_id="MUSICBRAINZ_TRACKID",
        musicbrainz_release_id="MUSICBRAINZ_ALBUMID",
        musicbrainz_artist_id="MUSICBRAINZ_ARTISTID",
        discogs_release_id="DISCOGS_RELEASE_ID",
        acoustid_id="ACOUSTID_ID",
        isrc="ISRC",
        label="LABEL",
        catalog_number="CATALOGNUMBER",
        release_country="RELEASECOUNTRY",
    ),
    FileFormat.MP4: IdentityFieldMap(
        musicbrainz_recording_id="----:com.apple.iTunes:MusicBrainz Track Id",
        musicbrainz_release_id="----:com.apple.iTunes:MusicBrainz Album Id",
        musicbrainz_artist_id="----:com.apple.iTunes:MusicBrainz Artist Id",
        discogs_release_id="----:com.avalon:discogs_release_id",
        acoustid_id="----:com.apple.iTunes:Acoustid Id",
        isrc="----:com.apple.iTunes:ISRC",
        label="----:com.apple.iTunes:LABEL",
        catalog_number="----:com.apple.iTunes:CATALOGNUMBER",
        release_country="----:com.apple.iTunes:MusicBrainz Album Release Country",
    ),
}
