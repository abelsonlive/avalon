"""Core dataclasses shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Label:
    """One entry of a multi-label prediction (genre, mood/theme)."""

    name: str
    confidence: float


@dataclass(slots=True)
class TrackAnalysis:
    """Every descriptor essentia extracted for one track."""

    # Rhythm
    bpm: float
    bpm_confidence: float
    # Tonal
    key: str
    scale: str
    camelot: str | None
    key_strength: float
    # Loudness / dynamics (plain DSP, not ML)
    loudness: float
    dynamic_complexity: float
    # Mood (probability of the named concept, 0-1)
    mood_aggressive: float
    mood_happy: float
    mood_sad: float
    mood_relaxed: float
    mood_party: float
    # Character
    danceability: float
    mood_acoustic: float
    mood_electronic: float
    voice_probability: float  # P(has vocals), 0-1
    gender: (
        str | None
    )  # "male" / "female" -- only meaningful when voice_probability is high
    gender_confidence: float
    tonal_probability: float  # P(tonal) vs atonal, 0-1
    timbre: str  # "bright" / "dark"
    timbre_confidence: float
    # Multi-label
    genres: list[Label] = field(default_factory=list)  # top-3, by confidence
    mood_themes: list[Label] = field(default_factory=list)  # top-5, by confidence

    schema_version: int = 1

    @property
    def top_genre(self) -> str | None:
        return self.genres[0].name if self.genres else None


@dataclass(slots=True)
class TrackIdentity:
    """Phase 2 placeholder -- MusicBrainz/AcoustID/Discogs IDs. Not populated in v1."""

    musicbrainz_recording_id: str | None = None
    musicbrainz_release_id: str | None = None
    musicbrainz_artist_id: str | None = None
    discogs_release_id: str | None = None


@dataclass(slots=True)
class ProcessResult:
    """Outcome of running the pipeline on one file."""

    source_path: str
    output_path: str
    analyzed: bool
    converted: bool
    skipped_reason: str | None = None
    error: str | None = None
