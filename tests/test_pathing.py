from avalon.pathing import PathRenderer, sanitize_segment


class TestSanitizeSegment:
    def test_strips_illegal_characters(self):
        assert sanitize_segment('AC/DC: Back "in" Black?') == "AC_DC_ Back _in_ Black_"

    def test_trims_trailing_dots_and_spaces(self):
        assert sanitize_segment("Live at... ") == "Live at"

    def test_empty_segment_falls_back(self):
        assert sanitize_segment("") == "_"
        assert sanitize_segment("...") == "_"


class TestPathRenderer:
    def test_default_template(self, tmp_path):
        renderer = PathRenderer(tmp_path)
        fields = {
            "artist": "Daft Punk",
            "album_artist": "Daft Punk",
            "album": "Discovery",
            "title": "One More Time",
            "track_number": "1/14",
        }
        result = renderer.render(fields, extension="mp3")
        assert result == tmp_path / "Daft Punk" / "Discovery" / "01 - One More Time.mp3"

    def test_missing_fields_fall_back_to_unknown(self, tmp_path):
        renderer = PathRenderer(tmp_path)
        result = renderer.render({}, extension="flac")
        assert (
            result
            == tmp_path / "Unknown Artist" / "Unknown Album" / "00 - Unknown Title.flac"
        )

    def test_album_artist_falls_back_to_artist(self, tmp_path):
        renderer = PathRenderer(tmp_path)
        fields = {"artist": "Some Artist", "album": "Some Album", "title": "Track"}
        result = renderer.render(fields, extension="mp3")
        assert "Some Artist" in result.parts

    def test_collision_gets_numbered_suffix(self, tmp_path):
        renderer = PathRenderer(tmp_path)
        fields = {
            "artist": "A",
            "album": "B",
            "title": "Same Title",
            "track_number": "1",
        }
        first = renderer.render(fields, extension="mp3")
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"fake")

        second = renderer.render(fields, extension="mp3")
        assert second != first
        assert second.name == "01 - Same Title (2).mp3"

    def test_directory_casing_stays_consistent_within_a_run(self, tmp_path):
        renderer = PathRenderer(tmp_path)
        first = renderer.render(
            {"artist": "Artist Name", "album": "Album", "title": "Track One"},
            extension="mp3",
        )
        second = renderer.render(
            {"artist": "artist name", "album": "Album", "title": "Track Two"},
            extension="mp3",
        )
        assert first.parent == second.parent

    def test_custom_template(self, tmp_path):
        renderer = PathRenderer(tmp_path, template="{genre}/{artist}/{title}")
        result = renderer.render(
            {"artist": "A", "title": "T", "genre": "Techno"}, extension="wav"
        )
        assert result == tmp_path / "Techno" / "A" / "T.wav"


class TestPathRendererSanitizesFieldValues:
    def test_slash_in_a_field_does_not_create_extra_directories(self, tmp_path):
        renderer = PathRenderer(tmp_path)
        fields = {
            "album_artist": "Andrew Weatherall",
            "album": "ANDREW WEATHERALL / TWO LONE SWORDSMEN / SABRES OF PARADISE",
            "title": "Conquistador",
            "track_number": "63",
        }
        result = renderer.render(fields, extension="mp3")
        assert result == (
            tmp_path
            / "Andrew Weatherall"
            / "ANDREW WEATHERALL _ TWO LONE SWORDSMEN _ SABRES OF PARADISE"
            / "63 - Conquistador.mp3"
        )

    def test_period_in_title_is_not_mistaken_for_an_extension(self, tmp_path):
        renderer = PathRenderer(tmp_path)
        fields = {
            "album_artist": "Andrew Weatherall",
            "album": "Etc",
            "title": "Conquistador (Sabres of Paradise No.3 Mix)",
            "track_number": "63",
        }
        result = renderer.render(fields, extension="mp3")
        assert result.name == "63 - Conquistador (Sabres of Paradise No.3 Mix).mp3"
