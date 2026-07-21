"""Pipeline-integration tests for --identify. Essentia is never invoked
here (too heavy to fake cheaply for a real analysis, and unnecessary --
these tests fake both `Pipeline._analyzer` and `Pipeline._identity_resolver`
directly, the same lazy-construction attributes `_get_analyzer()`/
`_get_identity_resolver()` check before building the real thing, achieving
dependency injection without any monkeypatching machinery).
"""

import shutil
from pathlib import Path


from avalon.models import Label, TrackAnalysis, TrackIdentity
from avalon.pipeline import Pipeline, PipelineOptions
from avalon.tagging import identity_blob, tag_writer

FIXTURES = Path(__file__).parent / "fixtures"


def _copy_fixture(name: str, tmp_path: Path) -> str:
    dest = tmp_path / name
    shutil.copy2(FIXTURES / name, dest)
    return str(dest)


def _sample_analysis(**overrides) -> TrackAnalysis:
    defaults = dict(
        bpm=128.3,
        bpm_confidence=0.92,
        key="A",
        scale="minor",
        camelot="8A",
        key_strength=0.87,
        loudness=-8.2,
        dynamic_complexity=0.41,
        mood_aggressive=0.71,
        mood_happy=0.22,
        mood_sad=0.08,
        mood_relaxed=0.15,
        mood_party=0.63,
        danceability=0.62,
        mood_acoustic=0.04,
        mood_electronic=0.88,
        voice_probability=0.42,
        gender="male",
        gender_confidence=0.55,
        tonal_probability=0.9,
        timbre="bright",
        timbre_confidence=0.53,
        genres=[Label("Essentia Guessed Genre", 0.82)],
        mood_themes=[Label("driving", 0.71)],
    )
    defaults.update(overrides)
    return TrackAnalysis(**defaults)


def _sample_identity(**overrides) -> TrackIdentity:
    defaults = dict(
        musicbrainz_recording_id="rec-1",
        musicbrainz_release_id="rel-1",
        musicbrainz_artist_id="artist-1",
        discogs_release_id="10817694",
        acoustid_id="acoustid-1",
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


class FakeAnalyzer:
    def __init__(self, analysis: TrackAnalysis):
        self._analysis = analysis
        self.calls = 0

    def analyze(self, path: str) -> TrackAnalysis:
        self.calls += 1
        return self._analysis


class FakeResolver:
    def __init__(
        self, identity: TrackIdentity | None = None, exc: Exception | None = None
    ):
        self._identity = identity
        self._exc = exc
        self.calls = 0

    def resolve(self, path: str, existing_fields: dict) -> TrackIdentity:
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._identity


class TestIdentifyOffByDefault:
    def test_no_credentials_needed_when_identify_not_requested(self, tmp_path):
        path = _copy_fixture("test.mp3", tmp_path)
        pipeline = Pipeline(PipelineOptions(do_analyze=False, do_convert=False))
        result = pipeline.process_file(path)
        assert result.error is None
        assert result.identified is False


class TestIdentifyWritesTags:
    def test_end_to_end(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        pipeline = Pipeline(
            PipelineOptions(do_analyze=False, do_convert=False, do_identify=True)
        )
        pipeline._identity_resolver = FakeResolver(identity=_sample_identity())

        result = pipeline.process_file(path)
        assert result.error is None
        assert result.identified is True

        fmt = tag_writer.detect_format(path)
        audio = tag_writer.load(path, fmt)
        identity_fields = tag_writer.read_identity_fields(audio, fmt)
        assert identity_fields["musicbrainz_recording_id"] == "rec-1"
        assert identity_fields["discogs_release_id"] == "10817694"

        raw = tag_writer.read_identity_extended(audio, fmt)
        assert identity_blob.has_current_schema(raw) is True


class TestGenrePrecedence:
    def test_identity_genre_beats_essentia_guess_when_genre_missing(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        pipeline = Pipeline(
            PipelineOptions(do_analyze=True, do_convert=False, do_identify=True)
        )
        pipeline._analyzer = FakeAnalyzer(_sample_analysis())
        pipeline._identity_resolver = FakeResolver(
            identity=_sample_identity(genre="Rock / Grunge")
        )

        fmt = tag_writer.detect_format(path)
        before_genre = tag_writer.read_canonical(tag_writer.load(path, fmt), fmt).get(
            "genre"
        )

        result = pipeline.process_file(path)
        assert result.error is None

        after = tag_writer.read_canonical(tag_writer.load(path, fmt), fmt)
        if before_genre:
            assert after["genre"] == before_genre
        else:
            assert after["genre"] == "Rock / Grunge"
            assert after["genre"] != "Essentia Guessed Genre"

    def test_essentia_guess_used_when_no_identity_genre(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        pipeline = Pipeline(
            PipelineOptions(do_analyze=True, do_convert=False, do_identify=True)
        )
        pipeline._analyzer = FakeAnalyzer(_sample_analysis())
        pipeline._identity_resolver = FakeResolver(
            identity=_sample_identity(genre=None)
        )

        fmt = tag_writer.detect_format(path)
        before_genre = tag_writer.read_canonical(tag_writer.load(path, fmt), fmt).get(
            "genre"
        )

        pipeline.process_file(path)

        after = tag_writer.read_canonical(tag_writer.load(path, fmt), fmt)
        if not before_genre:
            assert after["genre"] == "Essentia Guessed Genre"


class TestForceReidentify:
    def test_skips_when_already_current(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        fmt = tag_writer.detect_format(path)
        audio = tag_writer.load(path, fmt)
        tag_writer.write_identity_extended(
            audio, fmt, identity_blob.encode_identity(_sample_identity())
        )
        tag_writer.save(audio)

        pipeline = Pipeline(
            PipelineOptions(do_analyze=False, do_convert=False, do_identify=True)
        )
        resolver = FakeResolver(identity=_sample_identity())
        pipeline._identity_resolver = resolver

        result = pipeline.process_file(path)
        assert resolver.calls == 0
        assert result.identified is False

    def test_force_reidentify_runs_anyway(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        fmt = tag_writer.detect_format(path)
        audio = tag_writer.load(path, fmt)
        tag_writer.write_identity_extended(
            audio, fmt, identity_blob.encode_identity(_sample_identity())
        )
        tag_writer.save(audio)

        pipeline = Pipeline(
            PipelineOptions(
                do_analyze=False,
                do_convert=False,
                do_identify=True,
                force_reidentify=True,
            )
        )
        resolver = FakeResolver(identity=_sample_identity())
        pipeline._identity_resolver = resolver

        result = pipeline.process_file(path)
        assert resolver.calls == 1
        assert result.identified is True


class TestIdentifyFailureIsolation:
    def test_transient_identify_error_does_not_abort_the_file(self, tmp_path):
        path = _copy_fixture("test.m4a", tmp_path)
        pipeline = Pipeline(
            PipelineOptions(do_analyze=True, do_convert=False, do_identify=True)
        )
        pipeline._analyzer = FakeAnalyzer(_sample_analysis())
        pipeline._identity_resolver = FakeResolver(exc=RuntimeError("network timeout"))

        result = pipeline.process_file(path)

        assert result.error is None
        assert result.analyzed is True
        assert result.identified is False

        fmt = tag_writer.detect_format(path)
        extended = tag_writer.read_extended(tag_writer.load(path, fmt), fmt)
        assert extended is not None
