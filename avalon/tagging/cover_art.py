"""Format-agnostic cover art extract/embed.

Three different underlying mechanisms: ID3 APIC frames (MP3/AIFF/WAV),
FLAC's dedicated picture-block API (`.pictures`/`.add_picture`, distinct
from its Vorbis-comment tag dict), and MP4 `covr` atoms (`MP4Cover`).
"""

from __future__ import annotations

from mutagen.flac import Picture
from mutagen.id3 import APIC
from mutagen.mp4 import MP4Cover

from avalon.constants import ID3_FAMILY, FileFormat

Artwork = tuple[str, bytes]


def _sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "application/octet-stream"


def extract(audio, file_format: FileFormat) -> Artwork | None:
    if file_format in ID3_FAMILY:
        return _extract_id3(audio)
    if file_format is FileFormat.FLAC:
        return _extract_flac(audio)
    return _extract_mp4(audio)


def embed(audio, file_format: FileFormat, artwork: Artwork) -> None:
    if file_format in ID3_FAMILY:
        _embed_id3(audio, artwork)
    elif file_format is FileFormat.FLAC:
        _embed_flac(audio, artwork)
    else:
        _embed_mp4(audio, artwork)


def _extract_id3(audio) -> Artwork | None:
    pictures = audio.tags.getall("APIC")
    if not pictures:
        return None
    return pictures[0].mime, bytes(pictures[0].data)


def _embed_id3(audio, artwork: Artwork) -> None:
    mime, data = artwork
    for key in [k for k in audio.tags.keys() if k.startswith("APIC")]:
        del audio.tags[key]
    audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))


def _extract_flac(audio) -> Artwork | None:
    if not audio.pictures:
        return None
    picture = audio.pictures[0]
    return picture.mime, bytes(picture.data)


def _embed_flac(audio, artwork: Artwork) -> None:
    mime, data = artwork
    audio.clear_pictures()
    picture = Picture()
    picture.type = 3
    picture.mime = mime
    picture.data = data
    audio.add_picture(picture)


def _extract_mp4(audio) -> Artwork | None:
    covers = audio.tags.get("covr") if audio.tags else None
    if not covers:
        return None
    cover = covers[0]
    if isinstance(cover, MP4Cover):
        mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        return mime, bytes(cover)
    data = bytes(cover)
    return _sniff_mime(data), data


def _embed_mp4(audio, artwork: Artwork) -> None:
    mime, data = artwork
    image_format = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
    audio.tags["covr"] = [MP4Cover(data, imageformat=image_format)]
