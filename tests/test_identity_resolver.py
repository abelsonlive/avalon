import pytest

from avalon.identity.acoustid_client import AcoustidMatch
from avalon.identity.discogs_client_wrapper import DiscogsMatch
from avalon.identity.identity_resolver import IdentityResolver
from avalon.identity.musicbrainz_client import ReleaseData


def _match(**overrides) -> AcoustidMatch:
    defaults = dict(
        acoustid_id="acoustid-1",
        score=0.9,
        recording_id="rec-1",
        recording_title="Test Track",
        artist_id="artist-1",
        artist_name="Test Artist",
        release_id="release-1",
    )
    defaults.update(overrides)
    return AcoustidMatch(**defaults)


class FakeAcoustid:
    def __init__(self, match=None, exc=None):
        self._match = match
        self._exc = exc
        self.calls = []

    def identify(self, path):
        self.calls.append(path)
        if self._exc:
            raise self._exc
        return self._match


class FakeMusicBrainz:
    def __init__(self, release_data=None):
        self._release_data = release_data
        self.calls = []

    def get_release(self, release_id):
        self.calls.append(release_id)
        return self._release_data


class FakeDiscogs:
    def __init__(self, match=None):
        self._match = match
        self.calls = []

    def search(self, artist, title):
        self.calls.append((artist, title))
        return self._match


class TestConfidenceGating:
    def test_below_threshold_is_discarded(self):
        acoustid = FakeAcoustid(match=_match(score=0.5))
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=None, discogs=None, min_confidence=0.7
        )
        identity = resolver.resolve("/track.mp3", {})
        assert identity.musicbrainz_recording_id is None
        assert identity.match_confidence == 0.0

    def test_at_or_above_threshold_is_kept(self):
        acoustid = FakeAcoustid(match=_match(score=0.7))
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=None, discogs=None, min_confidence=0.7
        )
        identity = resolver.resolve("/track.mp3", {})
        assert identity.musicbrainz_recording_id == "rec-1"
        assert identity.match_confidence == 0.7


class TestNoMatch:
    def test_no_acoustid_match_still_returns_a_complete_identity(self):
        acoustid = FakeAcoustid(match=None)
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=None, discogs=None, min_confidence=0.7
        )
        identity = resolver.resolve("/track.mp3", {})
        assert identity.musicbrainz_recording_id is None
        assert identity.schema_version == 1

    def test_no_acoustid_client_configured(self):
        resolver = IdentityResolver(
            acoustid=None, musicbrainz=None, discogs=None, min_confidence=0.7
        )
        identity = resolver.resolve("/track.mp3", {"artist": "X", "title": "Y"})
        assert identity.musicbrainz_recording_id is None


class TestAcoustidErrorsPropagate:
    def test_exception_from_identify_is_not_swallowed(self):
        acoustid = FakeAcoustid(exc=RuntimeError("fpcalc not found"))
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=None, discogs=None, min_confidence=0.7
        )
        with pytest.raises(RuntimeError, match="fpcalc not found"):
            resolver.resolve("/track.mp3", {})


class TestMusicBrainzSupplement:
    def test_only_queried_when_acoustid_matched_with_a_release_id(self):
        mb = FakeMusicBrainz(release_data=ReleaseData(release_date="1991-09-24"))
        acoustid = FakeAcoustid(match=_match())
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=mb, discogs=None, min_confidence=0.7
        )
        identity = resolver.resolve("/track.mp3", {})
        assert mb.calls == ["release-1"]
        assert identity.release_date == "1991-09-24"

    def test_not_queried_when_acoustid_found_nothing(self):
        mb = FakeMusicBrainz(release_data=ReleaseData(release_date="1991-09-24"))
        acoustid = FakeAcoustid(match=None)
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=mb, discogs=None, min_confidence=0.7
        )
        resolver.resolve("/track.mp3", {})
        assert mb.calls == []

    def test_isrc_looked_up_by_the_matched_recording_id_specifically(self):
        mb = FakeMusicBrainz(
            release_data=ReleaseData(
                isrcs_by_recording={
                    "rec-1": "USGF19942501",
                    "other-rec": "XXNOPE0000000",
                }
            )
        )
        acoustid = FakeAcoustid(match=_match(recording_id="rec-1"))
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=mb, discogs=None, min_confidence=0.7
        )
        identity = resolver.resolve("/track.mp3", {})
        assert identity.isrc == "USGF19942501"


class TestDiscogsSearchTerms:
    def test_prefers_acoustid_resolved_artist_and_title_when_confident(self):
        discogs = FakeDiscogs(match=DiscogsMatch(release_id="1", title="t"))
        acoustid = FakeAcoustid(
            match=_match(artist_name="Nirvana", recording_title="Teen Spirit")
        )
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=None, discogs=discogs, min_confidence=0.7
        )
        resolver.resolve(
            "/track.mp3", {"artist": "Existing Artist", "title": "Existing Title"}
        )
        assert discogs.calls == [("Nirvana", "Teen Spirit")]

    def test_falls_back_to_existing_tags_when_acoustid_has_no_match(self):
        discogs = FakeDiscogs(match=DiscogsMatch(release_id="1", title="t"))
        acoustid = FakeAcoustid(match=None)
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=None, discogs=discogs, min_confidence=0.7
        )
        resolver.resolve(
            "/track.mp3", {"artist": "Existing Artist", "title": "Existing Title"}
        )
        assert discogs.calls == [("Existing Artist", "Existing Title")]

    def test_not_queried_without_enough_search_terms(self):
        discogs = FakeDiscogs(match=DiscogsMatch(release_id="1", title="t"))
        acoustid = FakeAcoustid(match=None)
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=None, discogs=discogs, min_confidence=0.7
        )
        resolver.resolve("/track.mp3", {"artist": "Existing Artist"})
        assert discogs.calls == []


class TestMergePrecedence:
    def test_musicbrainz_label_and_catalog_preferred_over_discogs(self):
        mb = FakeMusicBrainz(
            release_data=ReleaseData(label="DGC Records", catalog_number="DGC-24425")
        )
        discogs = FakeDiscogs(
            match=DiscogsMatch(
                release_id="1", title="t", label="DGC", catalog_number="DIFFERENT"
            )
        )
        acoustid = FakeAcoustid(match=_match())
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=mb, discogs=discogs, min_confidence=0.7
        )
        identity = resolver.resolve("/track.mp3", {"artist": "a", "title": "b"})
        assert identity.label == "DGC Records"
        assert identity.catalog_number == "DGC-24425"

    def test_discogs_label_used_when_musicbrainz_has_none(self):
        mb = FakeMusicBrainz(release_data=ReleaseData())
        discogs = FakeDiscogs(
            match=DiscogsMatch(
                release_id="1", title="t", label="DGC", catalog_number="DGC-24425"
            )
        )
        acoustid = FakeAcoustid(match=_match())
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=mb, discogs=discogs, min_confidence=0.7
        )
        identity = resolver.resolve("/track.mp3", {"artist": "a", "title": "b"})
        assert identity.label == "DGC"
        assert identity.catalog_number == "DGC-24425"

    def test_genre_comes_from_discogs_only(self):
        discogs = FakeDiscogs(
            match=DiscogsMatch(release_id="1", title="t", genre="Rock / Grunge")
        )
        acoustid = FakeAcoustid(match=_match())
        resolver = IdentityResolver(
            acoustid=acoustid, musicbrainz=None, discogs=discogs, min_confidence=0.7
        )
        identity = resolver.resolve("/track.mp3", {"artist": "a", "title": "b"})
        assert identity.genre == "Rock / Grunge"
