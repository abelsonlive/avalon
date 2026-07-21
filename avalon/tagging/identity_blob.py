"""Encode/decode the `AVALON_IDENTITY` tag (TXXX:AVALON_IDENTITY / a third
Vorbis field / a third MP4 atom) -- the --identify result, as the same
style of compact `key=value;...` string as the existing extended analysis
tag. Kept as its own tag rather than folded into `AVALON_ANALYSIS`:
`--force-reanalyze` and `--force-reidentify` are independently triggerable,
and `analysis_blob.encode_extended`'s whole design is "always fully
replace, nothing to merge" -- a shared blob would force read-modify-write
logic neither tag currently needs.

Always fully owned by avalon, so like the extended analysis tag, it's just
overwritten wholesale each run -- nothing here merges with existing content.
"""

from __future__ import annotations

from avalon.constants import IDENTITY_SCHEMA_VERSION
from avalon.models import TrackIdentity


def _fmt(value: float) -> str:
    return f"{value:.4f}"


def encode_identity(identity: TrackIdentity) -> str:
    """Builds the identity string. Always fully replaces -- this tag is
    exclusively avalon's, so there's nothing to merge/preserve."""
    fields = {
        "iv": str(IDENTITY_SCHEMA_VERSION),
        "mb_recording": identity.musicbrainz_recording_id or "",
        "mb_release": identity.musicbrainz_release_id or "",
        "mb_artist": identity.musicbrainz_artist_id or "",
        "discogs": identity.discogs_release_id or "",
        "acoustid": identity.acoustid_id or "",
        "conf": _fmt(identity.match_confidence),
        "isrc": identity.isrc or "",
        "reldate": identity.release_date or "",
        "relcountry": identity.release_country or "",
        "label": identity.label or "",
        "catno": identity.catalog_number or "",
        "genre": identity.genre or "",
    }
    return ";".join(f"{k}={v}" for k, v in fields.items())


def decode_identity(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    result: dict[str, str] = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        result[key.strip()] = val.strip()
    return result


def has_current_schema(existing_identity: str | None) -> bool:
    """Whether `existing_identity` already carries avalon's current
    identity-schema version -- used to skip re-identifying unchanged
    files. Mirrors `analysis_blob.has_current_schema` exactly, kept
    separate since analysis/identify are independently force-able."""
    fields = decode_identity(existing_identity)
    return fields.get("iv") == str(IDENTITY_SCHEMA_VERSION)
