from avalon.models import Label, TrackAnalysis
from avalon.tagging import analysis_blob


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
        genres=[Label("Electronic / Techno", 0.82), Label("Electronic / Tech House", 0.61)],
        mood_themes=[Label("driving", 0.71), Label("dark", 0.5)],
    )
    defaults.update(overrides)
    return TrackAnalysis(**defaults)


class TestHeadline:
    def test_encode_from_scratch(self):
        result = analysis_blob.encode_headline(_sample_analysis(), existing=None)
        assert result == "bpm:128;key:Am;camelot:8A;energy:0.71;genre:Electronic / Techno"

    def test_key_is_standard_notation_regardless_of_camelot(self):
        analysis = _sample_analysis(camelot=None, key="F#", scale="minor")
        result = analysis_blob.encode_headline(analysis, existing=None)
        parsed = analysis_blob.parse_headline(result)
        assert parsed["key"] == "F#m"
        assert "camelot" not in parsed  # nothing to report when essentia's key/scale has no mapping

    def test_merges_into_existing_generated_style_tag(self):
        existing = "bpm:120;key:Am;mynote:keep-me"
        result = analysis_blob.encode_headline(_sample_analysis(), existing=existing)
        parsed = analysis_blob.parse_headline(result)
        assert parsed["bpm"] == "128"
        assert parsed["key"] == "Am"
        assert parsed["camelot"] == "8A"
        assert parsed["mynote"] == "keep-me"  # untouched, not clobbered

    def test_preserves_freeform_comment_instead_of_clobbering(self):
        existing = "great track for warmup sets, played at Fabric 2019"
        result = analysis_blob.encode_headline(_sample_analysis(), existing=existing)
        assert result.startswith(existing + " | ")
        assert "bpm:128" in result

    def test_force_replace_via_existing_none(self):
        # Simulates --overwrite-description: caller passes existing=None.
        existing_but_ignored = "bpm:999;key:1A"
        result = analysis_blob.encode_headline(_sample_analysis(), existing=None)
        assert "999" not in result
        assert existing_but_ignored not in result

    def test_parse_headline_rejects_non_generated_shape(self):
        assert analysis_blob.parse_headline("just some notes, no colons here") is None

    def test_parse_headline_empty(self):
        assert analysis_blob.parse_headline(None) == {}
        assert analysis_blob.parse_headline("") == {}


class TestExtended:
    def test_round_trip(self):
        analysis = _sample_analysis()
        encoded = analysis_blob.encode_extended(analysis)
        decoded = analysis_blob.decode_extended(encoded)

        assert decoded["av"] == "1"
        assert float(decoded["bpm"]) == analysis.bpm
        assert decoded["camelot"] == "8A"
        assert decoded["gender"] == "m"
        assert float(decoded["mood_agg"]) == analysis.mood_aggressive

    def test_genre_labels_round_trip(self):
        analysis = _sample_analysis()
        encoded = analysis_blob.encode_extended(analysis)
        labels = analysis_blob.decode_extended_labels(encoded, "genre")

        assert [label.name for label in labels] == [
            "Electronic / Techno",
            "Electronic / Tech House",
        ]
        assert labels[0].confidence == 0.82

    def test_has_current_schema(self):
        encoded = analysis_blob.encode_extended(_sample_analysis())
        assert analysis_blob.has_current_schema(encoded) is True
        assert analysis_blob.has_current_schema("av=0;bpm=1") is False
        assert analysis_blob.has_current_schema(None) is False

    def test_empty_genre_list_round_trips_cleanly(self):
        analysis = _sample_analysis(genres=[], mood_themes=[])
        encoded = analysis_blob.encode_extended(analysis)
        assert analysis_blob.decode_extended_labels(encoded, "genre") == []
