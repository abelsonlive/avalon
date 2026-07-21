"""Hand-rolled MusicBrainz webservice client -- plain `requests` + JSON,
mirroring `avalon/analysis/model_cache.py`'s style, rather than depending
on `musicbrainzngs` (PyPI's "official" bindings, unmaintained since 2020).

One call per unique release (`inc=recordings+isrcs+labels+release-groups`)
covers everything Phase 2 needs -- ISRC (per-recording), label, catalog
number, and release date/country -- confirmed live against the real API
that this single `inc` combination nests ISRCs under each release's
tracklist. Results are cached in memory for the lifetime of one CLI run
only (not persisted to disk like model_cache.py -- MB data can be
community-edited, so a persistent cache risks serving stale data for a
benefit the in-memory cache already captures: one release commonly backs
many tracks in a folder/album run).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

from avalon.identity.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

_BASE_URL = "https://musicbrainz.org/ws/2"
_MIN_REQUEST_INTERVAL_SECONDS = 1.1


@dataclass(slots=True)
class ReleaseData:
    release_date: str | None = None
    release_country: str | None = None
    label: str | None = None
    catalog_number: str | None = None
    isrcs_by_recording: dict[str, str] = field(default_factory=dict)


class MusicBrainzClient:
    """Constructed once per CLI run (mirrors `EssentiaAnalyzer`'s shape);
    `get_release()` is called once per unique release, cached thereafter."""

    def __init__(
        self, contact: str, app_name: str = "avalon", app_version: str = "0.1.0"
    ) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = f"{app_name}/{app_version} ( {contact} )"
        self._rate_limiter = RateLimiter(_MIN_REQUEST_INTERVAL_SECONDS)
        self._cache: dict[str, ReleaseData | None] = {}

    def get_release(self, release_id: str) -> ReleaseData | None:
        """Returns release-level data for `release_id`, or None if the
        lookup fails (missing release, network error, etc.)."""
        if release_id in self._cache:
            return self._cache[release_id]

        self._rate_limiter.wait()
        try:
            response = self._session.get(
                f"{_BASE_URL}/release/{release_id}",
                params={"inc": "recordings+isrcs+labels+release-groups", "fmt": "json"},
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.warning(
                "MusicBrainz release lookup failed for %s: %s", release_id, exc
            )
            self._cache[release_id] = None
            return None

        if response.status_code != 200:
            logger.warning(
                "MusicBrainz release lookup failed for %s: HTTP %d",
                release_id,
                response.status_code,
            )
            self._cache[release_id] = None
            return None

        data = self._parse_release(response.json())
        self._cache[release_id] = data
        return data

    @staticmethod
    def _parse_release(payload: dict) -> ReleaseData:
        label_info = (payload.get("label-info") or [{}])[0]
        isrcs_by_recording: dict[str, str] = {}
        for medium in payload.get("media") or []:
            for track in medium.get("tracks") or []:
                recording = track.get("recording") or {}
                isrcs = recording.get("isrcs") or []
                if recording.get("id") and isrcs:
                    isrcs_by_recording[recording["id"]] = isrcs[0]
        return ReleaseData(
            release_date=payload.get("date") or None,
            release_country=payload.get("country") or None,
            label=(label_info.get("label") or {}).get("name"),
            catalog_number=label_info.get("catalog-number"),
            isrcs_by_recording=isrcs_by_recording,
        )
