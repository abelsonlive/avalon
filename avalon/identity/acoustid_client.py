"""Thin wrapper around `pyacoustid`: fingerprint a file, look it up against
the AcoustID API, and parse the response into a clean dataclass.

`import acoustid` is deferred into `__init__` (mirrors `EssentiaAnalyzer`'s
deferred `import essentia`) so a user without chromaprint/`fpcalc`
installed never hits an import error just from having avalon installed --
only when `--identify` is actually used.

Rate limiting: pyacoustid's `lookup()` already rate-limits itself
internally (confirmed from source: a `@_rate_limit` decorator wraps every
API request at 3 req/s) -- nothing extra needed here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_LOOKUP_META = ["recordings", "releases", "releasegroups", "compress"]


@dataclass(slots=True)
class AcoustidMatch:
    """One parsed result from an AcoustID lookup -- the best-scoring
    recording match, plus whatever MB IDs/titles came back with it."""

    acoustid_id: str
    score: float
    recording_id: str | None = None
    recording_title: str | None = None
    artist_id: str | None = None
    artist_name: str | None = None
    release_id: str | None = None


class AcoustidClient:
    """Constructed once per CLI run (mirrors `EssentiaAnalyzer`'s shape);
    `identify()` is called once per file."""

    def __init__(self, api_key: str) -> None:
        import acoustid

        self._acoustid = acoustid
        self._api_key = api_key

    def identify(self, path: str) -> AcoustidMatch | None:
        """Fingerprints `path` and looks it up. Returns the best-scoring
        match, or None if AcoustID has no match at all. Raises
        `acoustid.AcoustidError` (or a subclass) on fingerprinting/network
        failure -- callers (`IdentityResolver`) decide how to handle that."""
        duration, fingerprint = self._acoustid.fingerprint_file(path)
        response = self._acoustid.lookup(
            self._api_key, fingerprint, duration, meta=_LOOKUP_META
        )
        if response.get("status") != "ok":
            raise self._acoustid.WebServiceError(
                f"AcoustID lookup returned status={response.get('status')!r}"
            )
        results = response.get("results") or []
        if not results:
            return None
        best = max(results, key=lambda r: r.get("score") or 0.0)
        return self._parse_match(best)

    @staticmethod
    def _parse_match(result: dict) -> AcoustidMatch:
        recording = (result.get("recordings") or [{}])[0]
        artist = (recording.get("artists") or [{}])[0]
        release = (recording.get("releases") or [{}])[0]
        return AcoustidMatch(
            acoustid_id=result["id"],
            score=float(result.get("score") or 0.0),
            recording_id=recording.get("id"),
            recording_title=recording.get("title"),
            artist_id=artist.get("id"),
            artist_name=artist.get("name"),
            release_id=release.get("id"),
        )
