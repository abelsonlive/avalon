"""Orchestrates --identify: fingerprints a track via AcoustID, optionally
cross-references MusicBrainz for release-level detail (ISRC, label,
catalog number, precise release date/country) and Discogs for
release/genre data. Constructed once per CLI run (mirrors
`EssentiaAnalyzer`'s shape) with already-constructed client objects -- not
credentials -- so it's fully unit-testable by injecting fakes, no network
required.

Error-handling asymmetry, deliberate: AcoustID failures (fingerprinting,
network, the service itself) propagate uncaught out of `resolve()` --
AcoustID is the *primary* identification mechanism, so a failure there
means this identify attempt didn't complete at all (pipeline.py's local
try/except around the whole call treats that as "retry next run", not "no
match"). MusicBrainz/Discogs failures are caught inside their own client
classes and surfaced as `None` -- both are supplementary enrichment on top
of whatever AcoustID already established, so their failure just means less
data this run, not a failed attempt.
"""

from __future__ import annotations

import logging

from avalon.identity.acoustid_client import AcoustidClient, AcoustidMatch
from avalon.identity.discogs_client_wrapper import DiscogsClientWrapper, DiscogsMatch
from avalon.identity.musicbrainz_client import MusicBrainzClient, ReleaseData
from avalon.models import TrackIdentity

logger = logging.getLogger(__name__)


class IdentityResolver:
    """Any of `acoustid`/`musicbrainz`/`discogs` may be None -- whichever
    credentials the user configured (see avalon/identity/credentials.py)."""

    def __init__(
        self,
        *,
        acoustid: AcoustidClient | None,
        musicbrainz: MusicBrainzClient | None,
        discogs: DiscogsClientWrapper | None,
        min_confidence: float,
    ) -> None:
        self._acoustid = acoustid
        self._musicbrainz = musicbrainz
        self._discogs = discogs
        self._min_confidence = min_confidence

    def resolve(self, path: str, existing_fields: dict[str, str]) -> TrackIdentity:
        """Always returns a `TrackIdentity`, even an all-`None` one when
        nothing matched -- that outcome is itself meaningful (see
        pipeline.py's idempotency handling), distinct from a raised
        exception meaning the attempt didn't complete at all."""
        acoustid_match = self._run_acoustid(path)

        mb_release: ReleaseData | None = None
        if acoustid_match and acoustid_match.release_id and self._musicbrainz:
            mb_release = self._musicbrainz.get_release(acoustid_match.release_id)

        search_artist, search_title = self._pick_search_terms(
            acoustid_match, existing_fields
        )
        discogs_match: DiscogsMatch | None = None
        if self._discogs and search_artist and search_title:
            discogs_match = self._discogs.search(search_artist, search_title)

        return self._merge(acoustid_match, mb_release, discogs_match)

    def _run_acoustid(self, path: str) -> AcoustidMatch | None:
        if self._acoustid is None:
            return None
        match = self._acoustid.identify(path)
        if match is None:
            return None
        if match.score < self._min_confidence:
            logger.info(
                "AcoustID match for %s scored %.2f, below --min-identify-confidence "
                "%.2f -- discarding",
                path,
                match.score,
                self._min_confidence,
            )
            return None
        return match

    @staticmethod
    def _pick_search_terms(
        acoustid_match: AcoustidMatch | None, existing_fields: dict[str, str]
    ) -> tuple[str | None, str | None]:
        """Prefers the AcoustID-resolved artist+title (higher trust than
        the file's own possibly-wrong tags) when a confident match exists;
        falls back to the file's existing tags otherwise (e.g. Discogs is
        the only configured service, or AcoustID found nothing)."""
        if (
            acoustid_match
            and acoustid_match.artist_name
            and acoustid_match.recording_title
        ):
            return acoustid_match.artist_name, acoustid_match.recording_title
        return existing_fields.get("artist"), existing_fields.get("title")

    @staticmethod
    def _merge(
        acoustid_match: AcoustidMatch | None,
        mb_release: ReleaseData | None,
        discogs_match: DiscogsMatch | None,
    ) -> TrackIdentity:
        recording_id = acoustid_match.recording_id if acoustid_match else None
        isrc = (
            mb_release.isrcs_by_recording.get(recording_id)
            if mb_release and recording_id
            else None
        )

        label = (mb_release.label if mb_release else None) or (
            discogs_match.label if discogs_match else None
        )
        catalog_number = (mb_release.catalog_number if mb_release else None) or (
            discogs_match.catalog_number if discogs_match else None
        )

        return TrackIdentity(
            musicbrainz_recording_id=recording_id,
            musicbrainz_release_id=acoustid_match.release_id
            if acoustid_match
            else None,
            musicbrainz_artist_id=acoustid_match.artist_id if acoustid_match else None,
            discogs_release_id=discogs_match.release_id if discogs_match else None,
            acoustid_id=acoustid_match.acoustid_id if acoustid_match else None,
            match_confidence=acoustid_match.score if acoustid_match else 0.0,
            isrc=isrc,
            release_date=mb_release.release_date if mb_release else None,
            release_country=mb_release.release_country if mb_release else None,
            label=label,
            catalog_number=catalog_number,
            genre=discogs_match.genre if discogs_match else None,
        )
