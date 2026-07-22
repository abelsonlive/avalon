from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Label:
    name: str
    confidence: float


@dataclass(slots=True)
class TrackAnalysis:
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
class ProcessResult:
    source_path: str
    output_path: str
    analyzed: bool
    converted: bool
    skipped_reason: str | None = None
    error: str | None = None
