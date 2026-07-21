from avalon.models import TrackIdentity
from avalon.tagging import identity_blob


def _sample_identity(**overrides) -> TrackIdentity:
    defaults = dict(
        musicbrainz_recording_id="5fb524f1-8cc8-4c04-a921-e34c0a911ea7",
        musicbrainz_release_id="f922ec87-4758-421d-a839-3193455345ff",
        musicbrainz_artist_id="5b11f4ce-a62d-471e-81fc-a69a8278c7da",
        discogs_release_id="10817694",
        acoustid_id="aaaaaaaa-0000-0000-0000-000000000000",
        match_confidence=0.93,
        isrc="USGF19942501",
        release_date="1991-09-24",
        release_country="US",
        label="DGC Records",
        catalog_number="DGC-24425",
        genre="Rock / Grunge",
    )
    defaults.update(overrides)
    return TrackIdentity(**defaults)


class TestEncodeDecode:
    def test_round_trip(self):
        identity = _sample_identity()
        encoded = identity_blob.encode_identity(identity)
        decoded = identity_blob.decode_identity(encoded)

        assert decoded["iv"] == "1"
        assert decoded["mb_recording"] == identity.musicbrainz_recording_id
        assert decoded["mb_release"] == identity.musicbrainz_release_id
        assert decoded["mb_artist"] == identity.musicbrainz_artist_id
        assert decoded["discogs"] == identity.discogs_release_id
        assert decoded["acoustid"] == identity.acoustid_id
        assert float(decoded["conf"]) == identity.match_confidence
        assert decoded["isrc"] == identity.isrc
        assert decoded["reldate"] == identity.release_date
        assert decoded["relcountry"] == identity.release_country
        assert decoded["label"] == identity.label
        assert decoded["catno"] == identity.catalog_number
        assert decoded["genre"] == identity.genre

    def test_none_fields_round_trip_as_empty(self):
        identity = TrackIdentity()
        encoded = identity_blob.encode_identity(identity)
        decoded = identity_blob.decode_identity(encoded)

        assert decoded["iv"] == "1"
        assert decoded["mb_recording"] == ""
        assert decoded["conf"] == "0.0000"

    def test_decode_empty(self):
        assert identity_blob.decode_identity(None) == {}
        assert identity_blob.decode_identity("") == {}


class TestHasCurrentSchema:
    def test_true_for_freshly_encoded(self):
        encoded = identity_blob.encode_identity(_sample_identity())
        assert identity_blob.has_current_schema(encoded) is True

    def test_false_for_stale_or_missing(self):
        assert identity_blob.has_current_schema("iv=0;mb_recording=x") is False
        assert identity_blob.has_current_schema(None) is False
        assert identity_blob.has_current_schema("") is False
