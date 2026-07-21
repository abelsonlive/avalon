"""Encode/decode the two analysis tags avalon writes.

Headline tag (COMM/DESCRIPTION/desc): a short, human-scannable
`key:value;key:value` string. Extends the convention already used by
swinsian-sync's `rekordbox_sync.py` (`bpm:120;key:Am`) -- existing content
that doesn't look machine-generated (no reliable `key:value;...` shape) is
treated as a genuine freeform comment and preserved rather than clobbered.

Extended tag (TXXX:AVALON_ANALYSIS / a second Vorbis field / a second MP4
atom): the full descriptor roster, exclusively avalon's own, as the same
style of compact `key=value;...` string (note `=` not `:`, to keep it
visually distinct from the headline convention) -- always fully owned, so
it's just overwritten wholesale each run.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from avalon.constants import ANALYSIS_SCHEMA_VERSION
from avalon.models import Label, TrackAnalysis

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def standard_key(analysis: TrackAnalysis) -> str:
    """Standard notation (e.g. "C", "F#m") -- what ID3's TKEY/Vorbis'
    INITIALKEY conventionally hold, and what this library's existing tags
    already use (as opposed to Camelot-wheel notation)."""
    suffix = "m" if analysis.scale == "minor" else ""
    return f"{analysis.key}{suffix}"


HEADLINE_FIELD_VALUES: dict[str, Callable[[TrackAnalysis], str]] = {
    "bpm": lambda a: str(round(a.bpm)),
    "key": standard_key,
    "camelot": lambda a: a.camelot or "",
    "energy": lambda a: f"{a.mood_aggressive:.2f}",
    "genre": lambda a: a.top_genre or "",
    "dance": lambda a: f"{a.danceability:.2f}",
    "acoustic": lambda a: f"{a.mood_acoustic:.2f}",
    "electronic": lambda a: f"{a.mood_electronic:.2f}",
    "vocal": lambda a: f"{a.voice_probability:.2f}",
    "happy": lambda a: f"{a.mood_happy:.2f}",
    "sad": lambda a: f"{a.mood_sad:.2f}",
    "relaxed": lambda a: f"{a.mood_relaxed:.2f}",
    "party": lambda a: f"{a.mood_party:.2f}",
    "moodtheme": lambda a: a.mood_themes[0].name if a.mood_themes else "",
}

DEFAULT_HEADLINE_FIELDS: tuple[str, ...] = ("bpm", "key", "camelot", "energy", "genre")


def parse_headline_fields(raw: str) -> tuple[str, ...]:
    """Parses a `--headline-format` value: a comma-separated, ordered list
    of field names. Raises ValueError (not caught here -- callers decide
    how to surface it) listing the valid set if anything doesn't match."""
    fields = tuple(f.strip() for f in raw.split(",") if f.strip())
    unknown = [f for f in fields if f not in HEADLINE_FIELD_VALUES]
    if unknown or not fields:
        valid = ", ".join(HEADLINE_FIELD_VALUES)
        reason = (
            f"unknown field(s) {unknown}" if unknown else "must name at least one field"
        )
        raise ValueError(f"{reason} -- valid fields: {valid}")
    return fields


def parse_headline(value: str | None) -> dict[str, str] | None:
    """Parses a `key:value;key:value` string.

    Returns None if `value` doesn't match that shape (i.e. looks like a
    genuine freeform comment rather than machine-generated data).
    """
    if not value:
        return {}
    result: dict[str, str] = {}
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            return None
        key, _, val = part.partition(":")
        key = key.strip()
        if not _KEY_RE.match(key):
            return None
        result[key] = val.strip()
    return result


def encode_headline(
    analysis: TrackAnalysis,
    existing: str | None = None,
    fields: tuple[str, ...] = DEFAULT_HEADLINE_FIELDS,
) -> str:
    """Builds the headline string, merging into `existing` when possible."""
    new_values = {name: HEADLINE_FIELD_VALUES[name](analysis) for name in fields}
    new_values = {k: v for k, v in new_values.items() if v}

    parsed = parse_headline(existing)
    if parsed is None:
        generated = ";".join(f"{k}:{v}" for k, v in new_values.items())
        return f"{existing} | {generated}"

    parsed.update(new_values)
    return ";".join(f"{k}:{v}" for k, v in parsed.items())


def _fmt(value: float) -> str:
    return f"{value:.4f}"


def _encode_labels(labels: list[Label]) -> str:
    return ",".join(f"{label.name}@{label.confidence:.2f}" for label in labels)


def _decode_labels(value: str) -> list[Label]:
    labels: list[Label] = []
    for item in value.split(","):
        if not item:
            continue
        name, _, confidence = item.rpartition("@")
        try:
            labels.append(Label(name=name, confidence=float(confidence)))
        except ValueError:
            continue
    return labels


def encode_extended(analysis: TrackAnalysis) -> str:
    """Builds the extended string. Always fully replaces -- this tag is
    exclusively avalon's, so there's nothing to merge/preserve."""
    fields = {
        "av": str(ANALYSIS_SCHEMA_VERSION),
        "bpm": _fmt(analysis.bpm),
        "bpmconf": _fmt(analysis.bpm_confidence),
        "key": analysis.key,
        "scale": analysis.scale,
        "camelot": analysis.camelot or "",
        "keystr": _fmt(analysis.key_strength),
        "loud": _fmt(analysis.loudness),
        "dyncx": _fmt(analysis.dynamic_complexity),
        "mood_agg": _fmt(analysis.mood_aggressive),
        "mood_happy": _fmt(analysis.mood_happy),
        "mood_sad": _fmt(analysis.mood_sad),
        "mood_relaxed": _fmt(analysis.mood_relaxed),
        "mood_party": _fmt(analysis.mood_party),
        "dance": _fmt(analysis.danceability),
        "acoustic": _fmt(analysis.mood_acoustic),
        "electronic": _fmt(analysis.mood_electronic),
        "vocal": _fmt(analysis.voice_probability),
        "gender": (analysis.gender or "")[:1],
        "genderconf": _fmt(analysis.gender_confidence),
        "tonal": _fmt(analysis.tonal_probability),
        "timbre": analysis.timbre,
        "timbreconf": _fmt(analysis.timbre_confidence),
        "genre": _encode_labels(analysis.genres),
        "moodtheme": _encode_labels(analysis.mood_themes),
    }
    return ";".join(f"{k}={v}" for k, v in fields.items())


def decode_extended(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    result: dict[str, str] = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        result[key.strip()] = val.strip()
    return result


def decode_extended_labels(value: str | None, field: str) -> list[Label]:
    """Convenience: decode just the `genre` or `moodtheme` field of an
    extended string into Label objects."""
    fields = decode_extended(value)
    return _decode_labels(fields.get(field, ""))


def has_current_schema(existing_extended: str | None) -> bool:
    """Whether `existing_extended` already carries avalon's current schema
    version -- used to skip re-analysis on unchanged files."""
    fields = decode_extended(existing_extended)
    return fields.get("av") == str(ANALYSIS_SCHEMA_VERSION)
