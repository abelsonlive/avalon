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
    return CAMELOT_WHEEL.get((key, scale))


class FileFormat(Enum):
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


@dataclass(frozen=True, slots=True)
class TagFrameMap:
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
    ),
}
