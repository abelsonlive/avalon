import shutil
from pathlib import Path

import pytest

from avalon.tagging import tag_writer

FIXTURES = Path(__file__).parent / "fixtures"


def _copy_fixture(name: str, tmp_path: Path) -> str:
    dest = tmp_path / name
    shutil.copy2(FIXTURES / name, dest)
    return str(dest)


@pytest.fixture(params=["test.mp3", "test.flac", "test.aiff", "test.m4a"])
def fixture_path(request, tmp_path) -> str:
    return _copy_fixture(request.param, tmp_path)


class TestReadWriteRoundTrip:
    """Across every real-media format avalon supports (WAV is covered
    separately below since it needs a synthesized fixture, not a copy of
    one of the pre-existing library files)."""

    def test_write_then_reload_from_disk(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        audio = tag_writer.load(fixture_path, fmt)

        tag_writer.write_generated_fields(
            audio, fmt, bpm="128", key="8A", genre="Techno", fill_only_if_missing=False
        )
        tag_writer.write_headline(audio, fmt, "bpm:128;key:8A;energy:0.71;genre:Techno")
        tag_writer.write_extended(
            audio, fmt, "av=1;bpm=128.0000;key=A;scale=minor;camelot=8A"
        )
        tag_writer.save(audio)

        reloaded = tag_writer.load(fixture_path, fmt)
        canonical = tag_writer.read_canonical(reloaded, fmt)
        assert canonical["bpm"] == "128"
        assert canonical["key"] == "8A"
        assert canonical["genre"] == "Techno"
        assert (
            tag_writer.read_headline(reloaded, fmt)
            == "bpm:128;key:8A;energy:0.71;genre:Techno"
        )
        assert (
            tag_writer.read_extended(reloaded, fmt)
            == "av=1;bpm=128.0000;key=A;scale=minor;camelot=8A"
        )

    def test_fill_only_if_missing_preserves_existing_values(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        audio = tag_writer.load(fixture_path, fmt)
        before = tag_writer.read_canonical(audio, fmt)

        tag_writer.write_generated_fields(
            audio,
            fmt,
            bpm="999",
            key="1A",
            genre="ShouldNotAppear",
            fill_only_if_missing=True,
        )
        tag_writer.save(audio)

        after = tag_writer.read_canonical(tag_writer.load(fixture_path, fmt), fmt)
        for field in ("genre",):
            if before.get(field):
                assert after[field] == before[field]

    def test_rewrite_headline_does_not_leave_duplicate_frames(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        audio = tag_writer.load(fixture_path, fmt)
        tag_writer.write_headline(audio, fmt, "bpm:100;key:1A;energy:0.1;genre:House")
        tag_writer.save(audio)

        reloaded = tag_writer.load(fixture_path, fmt)
        tag_writer.write_headline(
            reloaded, fmt, "bpm:200;key:2A;energy:0.9;genre:Techno"
        )
        tag_writer.save(reloaded)

        final = tag_writer.load(fixture_path, fmt)
        assert (
            tag_writer.read_headline(final, fmt)
            == "bpm:200;key:2A;energy:0.9;genre:Techno"
        )


class TestHeadlineTagOverride:
    """--headline-tag: writing the headline somewhere other than the
    format's native comment slot (COMM/DESCRIPTION/desc)."""

    _NATIVE_NAME = {"mp3": "COMM", "aiff": "COMM", "flac": "DESCRIPTION", "mp4": "desc"}

    def test_custom_tag_name_round_trips_and_leaves_native_slot_untouched(
        self, fixture_path
    ):
        fmt = tag_writer.detect_format(fixture_path)
        audio = tag_writer.load(fixture_path, fmt)
        tag_writer.write_headline(audio, fmt, "bpm:128;key:8A", "AVALON_HEADLINE")
        tag_writer.save(audio)

        reloaded = tag_writer.load(fixture_path, fmt)
        assert (
            tag_writer.read_headline(reloaded, fmt, "AVALON_HEADLINE")
            == "bpm:128;key:8A"
        )
        assert not tag_writer.read_headline(reloaded, fmt)

    def test_explicit_native_name_behaves_like_the_default(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        native_name = self._NATIVE_NAME[fmt.value]
        audio = tag_writer.load(fixture_path, fmt)
        tag_writer.write_headline(audio, fmt, "bpm:128;key:8A", native_name)
        tag_writer.save(audio)

        reloaded = tag_writer.load(fixture_path, fmt)
        assert tag_writer.read_headline(reloaded, fmt) == "bpm:128;key:8A"


class TestGeneratedDateField:
    def test_date_fills_when_missing(self, tmp_path):
        import ffmpeg

        path = str(tmp_path / "fresh.wav")
        ffmpeg.output(
            ffmpeg.input(str(FIXTURES / "test.mp3")), path, t=1, loglevel="error"
        ).run(overwrite_output=True)
        fmt = tag_writer.detect_format(path)
        audio = tag_writer.load(path, fmt)
        assert tag_writer.read_canonical(audio, fmt).get("date") is None

        tag_writer.write_generated_fields(
            audio,
            fmt,
            bpm=None,
            key=None,
            genre=None,
            date="1991-09-24",
            fill_only_if_missing=True,
        )
        tag_writer.save(audio)

        after = tag_writer.read_canonical(tag_writer.load(path, fmt), fmt)
        assert after["date"] == "1991-09-24"

    def test_date_does_not_overwrite_existing(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        audio = tag_writer.load(fixture_path, fmt)
        before = tag_writer.read_canonical(audio, fmt).get("date")
        if not before:
            pytest.skip("fixture has no pre-existing date to protect")

        tag_writer.write_generated_fields(
            audio,
            fmt,
            bpm=None,
            key=None,
            genre=None,
            date="1900-01-01",
            fill_only_if_missing=True,
        )
        tag_writer.save(audio)

        after = tag_writer.read_canonical(tag_writer.load(fixture_path, fmt), fmt)
        assert after["date"] == before


class TestReleaseDate:
    """TDRL (ID3) / a literal `releasedate` Vorbis field -- distinct from
    the generic TDRC/DATE `date` field -- so Navidrome's album-PID fallback
    (which specifically wants `releasedate`) is satisfied too, not just the
    primary MBID-based path."""

    def test_id3_family_writes_tdrl(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        if fmt.value not in ("mp3", "aiff"):
            pytest.skip("TDRL is ID3-specific")
        audio = tag_writer.load(fixture_path, fmt)
        tag_writer.write_release_date(
            audio, fmt, "1991-09-24", fill_only_if_missing=False
        )
        tag_writer.save(audio)

        reloaded = tag_writer.load(fixture_path, fmt)
        frame = reloaded.tags.get("TDRL")
        assert frame is not None and str(frame.text[0]) == "1991-09-24"

    def test_flac_writes_dedicated_releasedate_field(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        if fmt.value != "flac":
            pytest.skip("Vorbis-specific")
        audio = tag_writer.load(fixture_path, fmt)
        tag_writer.write_release_date(
            audio, fmt, "1991-09-24", fill_only_if_missing=True
        )
        tag_writer.save(audio)

        reloaded = tag_writer.load(fixture_path, fmt)
        assert reloaded.tags["RELEASEDATE"] == ["1991-09-24"]
        assert (
            reloaded.tags.get("DATE") != ["1991-09-24"] or "DATE" not in reloaded.tags
        )

    def test_mp4_is_a_no_op(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        if fmt.value != "mp4":
            pytest.skip("MP4-specific")
        audio = tag_writer.load(fixture_path, fmt)
        tag_writer.write_release_date(
            audio, fmt, "1991-09-24", fill_only_if_missing=True
        )
        assert "RELEASEDATE" not in audio.tags

    def test_fill_only_if_missing_respected(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        if fmt.value == "mp4":
            pytest.skip("no-op for MP4")
        audio = tag_writer.load(fixture_path, fmt)
        tag_writer.write_release_date(
            audio, fmt, "1991-09-24", fill_only_if_missing=False
        )
        tag_writer.save(audio)

        reloaded = tag_writer.load(fixture_path, fmt)
        tag_writer.write_release_date(
            reloaded, fmt, "2000-01-01", fill_only_if_missing=True
        )
        tag_writer.save(reloaded)

        final = tag_writer.load(fixture_path, fmt)
        if fmt.value == "flac":
            assert final.tags["RELEASEDATE"] == ["1991-09-24"]
        else:
            assert str(final.tags["TDRL"].text[0]) == "1991-09-24"


class TestIdentityFields:
    """The 9 Picard-interop MB/Discogs/AcoustID fields -- 3 of which
    (musicbrainz_recording_id/isrc/label) use native ID3 frames (UFID/TSRC/
    TPUB) rather than TXXX, confirmed against Picard's own convention."""

    _VALUES = {
        "musicbrainz_recording_id": "5fb524f1-8cc8-4c04-a921-e34c0a911ea7",
        "musicbrainz_release_id": "f922ec87-4758-421d-a839-3193455345ff",
        "musicbrainz_artist_id": "5b11f4ce-a62d-471e-81fc-a69a8278c7da",
        "discogs_release_id": "10817694",
        "acoustid_id": "aaaaaaaa-0000-0000-0000-000000000000",
        "isrc": "USGF19942501",
        "label": "DGC Records",
        "catalog_number": "DGC-24425",
        "release_country": "US",
    }

    def test_round_trip(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        audio = tag_writer.load(fixture_path, fmt)
        tag_writer.write_identity_fields(audio, fmt, self._VALUES)
        tag_writer.save(audio)

        reloaded = tag_writer.load(fixture_path, fmt)
        assert tag_writer.read_identity_fields(reloaded, fmt) == self._VALUES

    def test_extended_blob_round_trip(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        audio = tag_writer.load(fixture_path, fmt)
        blob = "iv=1;mb_recording=rec-1;conf=0.9300"
        tag_writer.write_identity_extended(audio, fmt, blob)
        tag_writer.save(audio)

        reloaded = tag_writer.load(fixture_path, fmt)
        assert tag_writer.read_identity_extended(reloaded, fmt) == blob

    def test_does_not_collide_with_analysis_extended_tag(self, fixture_path):
        fmt = tag_writer.detect_format(fixture_path)
        audio = tag_writer.load(fixture_path, fmt)
        tag_writer.write_extended(audio, fmt, "av=1;bpm=128.0")
        tag_writer.write_identity_extended(audio, fmt, "iv=1;mb_recording=rec-1")
        tag_writer.save(audio)

        reloaded = tag_writer.load(fixture_path, fmt)
        assert tag_writer.read_extended(reloaded, fmt) == "av=1;bpm=128.0"
        assert (
            tag_writer.read_identity_extended(reloaded, fmt)
            == "iv=1;mb_recording=rec-1"
        )


class TestBpmZeroSentinel:
    def test_bpm_zero_is_treated_as_missing(self, tmp_path):
        path = _copy_fixture("test.flac", tmp_path)
        fmt = tag_writer.detect_format(path)
        audio = tag_writer.load(path, fmt)
        assert tag_writer.read_canonical(audio, fmt).get("bpm") == "0"

        tag_writer.write_generated_fields(
            audio, fmt, bpm="128", key=None, genre=None, fill_only_if_missing=True
        )
        tag_writer.save(audio)

        after = tag_writer.read_canonical(tag_writer.load(path, fmt), fmt)
        assert after["bpm"] == "128"


class TestWaveIsTaggable:
    """mutagen's WAVE class supports an embedded ID3 chunk -- confirmed
    directly rather than assumed, since swinsian-sync's rekordbox_sync.py
    skips .wav entirely (an older mutagen limitation, not a current one)."""

    @pytest.fixture
    def wav_path(self, tmp_path) -> str:
        import ffmpeg

        source = str(FIXTURES / "test.mp3")
        dest = str(tmp_path / "test.wav")
        ffmpeg.output(ffmpeg.input(source), dest, t=2, loglevel="error").run(
            overwrite_output=True
        )
        return dest

    def test_wave_round_trip(self, wav_path):
        fmt = tag_writer.detect_format(wav_path)
        audio = tag_writer.load(wav_path, fmt)
        tag_writer.write_generated_fields(
            audio, fmt, bpm="140", key="9A", genre="Trance", fill_only_if_missing=False
        )
        tag_writer.write_headline(audio, fmt, "bpm:140;key:9A;energy:0.8;genre:Trance")
        tag_writer.write_extended(audio, fmt, "av=1;bpm=140.0")
        tag_writer.save(audio)

        reloaded = tag_writer.load(wav_path, fmt)
        canonical = tag_writer.read_canonical(reloaded, fmt)
        assert canonical["bpm"] == "140"
        assert canonical["key"] == "9A"
        assert (
            tag_writer.read_headline(reloaded, fmt)
            == "bpm:140;key:9A;energy:0.8;genre:Trance"
        )
        assert tag_writer.read_extended(reloaded, fmt) == "av=1;bpm=140.0"


class TestUnsupportedFormat:
    def test_detect_format_rejects_unknown_extension(self):
        with pytest.raises(tag_writer.UnsupportedFormatError):
            tag_writer.detect_format("song.ogg")
