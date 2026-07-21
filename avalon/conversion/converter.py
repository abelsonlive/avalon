"""ffmpeg-based format/sample-rate/bit-depth conversion.

Generalized from swinsian-sync's AudioConverter: any source format to any
target format/sample-rate/bit-depth, rather than a hardcoded flac/m4a->aiff
mapping. Cover art is handled separately (`tagging.cover_art`) -- ffmpeg's
metadata passthrough is unreliable for embedded images across container
conversions, particularly into AIFF.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import ffmpeg

logger = logging.getLogger(__name__)

_PCM_CODEC_BY_BIT_DEPTH = {
    8: "pcm_u8",
    16: "pcm_s16le",
    24: "pcm_s24le",
    32: "pcm_s32le",
}
_PCM_CONTAINER_FORMATS = {"aiff", "wav"}

_LOSSLESS_CODECS = {
    "flac",
    "alac",
    "ape",
    "wavpack",
    "tta",
    "pcm_s8",
    "pcm_u8",
    "pcm_s16le",
    "pcm_s16be",
    "pcm_s24le",
    "pcm_s24be",
    "pcm_s32le",
    "pcm_s32be",
    "pcm_f32le",
}


class ConversionError(Exception):
    pass


def is_lossless(codec_name: str | None) -> bool:
    return codec_name in _LOSSLESS_CODECS


def _select_pcm_codec(bit_depth: int) -> str:
    for depth in (8, 16, 24):
        if bit_depth <= depth:
            return _PCM_CODEC_BY_BIT_DEPTH[depth]
    return _PCM_CODEC_BY_BIT_DEPTH[32]


def probe(path: str) -> dict[str, Any]:
    """Sample rate/bit depth/bit rate/codec/duration of the audio stream."""
    info = ffmpeg.probe(path)
    stream = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if stream is None:
        raise ConversionError(f"No audio stream found in {path}")
    return {
        "sample_rate": int(stream.get("sample_rate", 0)),
        "bit_depth": int(stream.get("bits_per_sample", 0)) or None,
        "bit_rate": (int(stream.get("bit_rate", 0)) // 1000)
        if stream.get("bit_rate")
        else None,
        "codec_name": stream.get("codec_name"),
        "duration": float(info.get("format", {}).get("duration", 0)) or None,
    }


def needs_conversion(
    path: str,
    *,
    target_format: str | None,
    max_sample_rate: int | None,
    max_bit_depth: int | None,
) -> bool:
    """Whether `path` should be re-encoded.

    Already-lossy sources (mp3, aac, ...) are never converted, regardless
    of `target_format`/`max_sample_rate`/`max_bit_depth`: there's no
    quality to recover by moving lossy audio into a different container or
    bit depth, only a larger file baking in the same artifacts. This is a
    codec check, not an extension check -- M4A can hold either lossy AAC
    or lossless ALAC.
    """
    if target_format is None and max_sample_rate is None and max_bit_depth is None:
        return False
    info = probe(path)
    if not is_lossless(info["codec_name"]):
        return False
    current_format = Path(path).suffix.lower().lstrip(".")
    if target_format and target_format != current_format:
        return True
    if max_sample_rate and info["sample_rate"] > max_sample_rate:
        return True
    if max_bit_depth and info["bit_depth"] and info["bit_depth"] > max_bit_depth:
        return True
    return False


def convert(
    input_path: str,
    output_path: str,
    *,
    target_format: str,
    max_sample_rate: int | None = None,
    max_bit_depth: int | None = None,
    overwrite: bool = False,
) -> str:
    """Converts `input_path` to `output_path`. Returns `output_path`."""
    if Path(output_path).exists() and not overwrite:
        logger.info("Output already exists, skipping conversion: %s", output_path)
        return output_path

    info = probe(input_path)
    audio_params: dict[str, Any] = {}

    current_bit_depth = info["bit_depth"] or 16
    target_bit_depth = (
        min(current_bit_depth, max_bit_depth) if max_bit_depth else current_bit_depth
    )
    if target_bit_depth != current_bit_depth or target_format in _PCM_CONTAINER_FORMATS:
        audio_params["acodec"] = _select_pcm_codec(target_bit_depth)

    if max_sample_rate and info["sample_rate"] > max_sample_rate:
        audio_params["ar"] = max_sample_rate

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = dict(
        map_metadata=0, write_id3v2=1, map="0:a:0", **audio_params
    )
    if target_format == "aiff":
        kwargs["f"] = "aiff"

    output_stream = ffmpeg.output(ffmpeg.input(input_path), output_path, **kwargs)
    try:
        ffmpeg.run(
            output_stream,
            overwrite_output=True,
            quiet=True,
            capture_stdout=True,
            capture_stderr=True,
        )
    except ffmpeg.Error as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise ConversionError(
            f"ffmpeg conversion failed for {input_path}: {stderr[-2000:]}"
        ) from exc

    logger.info("Converted %s -> %s", input_path, output_path)
    return output_path
