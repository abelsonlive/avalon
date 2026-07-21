"""Credential detection and `IdentityResolver` construction for
`--identify`. Kept decoupled from cli.py the same way essentia is --
cli.py never imports `EssentiaAnalyzer` directly either, only `pipeline.py`
does.
"""

from __future__ import annotations

import logging
import os

from avalon.identity.acoustid_client import AcoustidClient
from avalon.identity.discogs_client_wrapper import DiscogsClientWrapper
from avalon.identity.identity_resolver import IdentityResolver
from avalon.identity.musicbrainz_client import MusicBrainzClient

logger = logging.getLogger(__name__)

ACOUSTID_API_KEY_ENV = "ACOUSTID_API_KEY"
DISCOGS_TOKEN_ENV = "DISCOGS_TOKEN"
CONTACT_ENV = "AVALON_CONTACT"

_DEFAULT_CONTACT = "https://github.com/abelsonlive/avalon"


class MissingCredentialsError(Exception):
    pass


def credentials_configured() -> dict[str, bool]:
    return {
        "acoustid": bool(os.environ.get(ACOUSTID_API_KEY_ENV)),
        "discogs": bool(os.environ.get(DISCOGS_TOKEN_ENV)),
    }


def ensure_configured() -> None:
    """Raises `MissingCredentialsError` if neither credential is set -- an
    explicit --identify deserves a clear error, not a silent no-op. Split
    out from `build_resolver` so cli.py can fail fast, before the per-file
    loop starts, without constructing any client objects."""
    if not any(credentials_configured().values()):
        raise MissingCredentialsError(
            "--identify requires the ACOUSTID_API_KEY (free: "
            "https://acoustid.org/api-key) or DISCOGS_TOKEN (generate one "
            "from your Discogs account's Developer settings) environment "
            "variable to be set -- neither is."
        )


def build_resolver(min_confidence: float) -> IdentityResolver:
    ensure_configured()

    acoustid_key = os.environ.get(ACOUSTID_API_KEY_ENV)
    discogs_token = os.environ.get(DISCOGS_TOKEN_ENV)
    contact = os.environ.get(CONTACT_ENV, _DEFAULT_CONTACT)

    acoustid_client = AcoustidClient(acoustid_key) if acoustid_key else None
    musicbrainz_client = MusicBrainzClient(contact) if acoustid_key else None
    discogs_client = DiscogsClientWrapper(discogs_token) if discogs_token else None

    logger.info(
        "Identify configured: acoustid=%s musicbrainz=%s discogs=%s",
        bool(acoustid_client),
        bool(musicbrainz_client),
        bool(discogs_client),
    )

    return IdentityResolver(
        acoustid=acoustid_client,
        musicbrainz=musicbrainz_client,
        discogs=discogs_client,
        min_confidence=min_confidence,
    )


def parse_min_confidence(raw: str) -> float:
    """--min-identify-confidence validator: plain ValueError, mirroring
    analysis_blob.parse_headline_fields -- cli.py wraps it in
    argparse.ArgumentTypeError so this module stays argparse-free."""
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"must be a number between 0 and 1, got {raw!r}") from None
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"must be between 0 and 1, got {value}")
    return value
