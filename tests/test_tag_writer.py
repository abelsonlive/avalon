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
