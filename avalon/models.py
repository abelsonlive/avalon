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

    bpm: float
    bpm_confidence: float
    key: str
    scale: str
    camelot: str | None
    key_strength: float
    loudness: float
    dynamic_complexity: float
    mood_aggressive: float
    mood_happy: float
    mood_sad: float
    mood_relaxed: float
    mood_party: float
    danceability: float
    mood_acoustic: float
    mood_electronic: float
    voice_probability: float
    gender: str | None
    gender_confidence: float
    tonal_probability: float
    timbre: str
    timbre_confidence: float
    genres: list[Label] = field(default_factory=list)
    mood_themes: list[Label] = field(default_factory=list)

    schema_version: int = 1

    @property
    def top_genre(self) -> str | None:
        return self.genres[0].name if self.genres else None


@dataclass(slots=True)
class TrackIdentity:
    """Result of --identify: MusicBrainz/AcoustID/Discogs reconciliation.

    Always constructed (never partially built) once an identify attempt
    *completes* -- including an all-`None` result when nothing matched, so
    that outcome is itself recorded and not endlessly retried. A raised
    exception (network error, etc.), as opposed to a completed no-match, is
    the only case that should prevent construction of this at all."""

    musicbrainz_recording_id: str | None = None
    musicbrainz_release_id: str | None = None
    musicbrainz_artist_id: str | None = None
    discogs_release_id: str | None = None
    acoustid_id: str | None = None
    match_confidence: float = 0.0
    isrc: str | None = None
    release_date: str | None = None
    release_country: str | None = None
    label: str | None = None
    catalog_number: str | None = None
    genre: str | None = None

    schema_version: int = 1


@dataclass(slots=True)
class ProcessResult:
    """Outcome of running the pipeline on one file."""

    source_path: str
    output_path: str
    analyzed: bool
    converted: bool
    identified: bool = False
    skipped_reason: str | None = None
    error: str | None = None
