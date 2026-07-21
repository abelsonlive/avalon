"""Runs the full Essentia extraction roster against audio files.

One embedding pass (discogs-effnet) feeds every classifier head, so
breadth is cheap -- this is the "extract as much information as we can
from essentia" implementation, not a curated subset.

Loading a TensorFlow graph has real fixed overhead (~1-2s each); with 14
classifier heads plus the embedding extractor, reloading all of them for
every file measured at ~25s/track. Loading them once and reusing the
loaded graphs across files drops that to ~1.1s/track (measured), which is
the difference between a usable batch tool and one that takes days on a
real library. `EssentiaAnalyzer` is therefore a reusable object, not a
stateless function: construct one per CLI run and call `.analyze()` per
file.
"""

from __future__ import annotations

import logging
import os

import numpy as np

from avalon.analysis import model_cache
from avalon.constants import to_camelot
from avalon.models import Label, TrackAnalysis

logger = logging.getLogger(__name__)

_ANALYSIS_SAMPLE_RATE = 44100
_EMBEDDING_SAMPLE_RATE = 16000
_TOP_N_BY_MODEL = {"genre_discogs400": 3, "mtg_jamendo_moodtheme": 5}


def _clean_label(raw: str) -> str:
    """ "Electronic---Techno" -> "Electronic / Techno" for readability."""
    return raw.replace("---", " / ")


class EssentiaAnalyzer:
    """Loads every model once; call `analyze(path)` per file."""

    def __init__(self) -> None:
        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

        import essentia
        import essentia.standard as es

        essentia.log.warningActive = False
        essentia.log.infoActive = False

        self._es = es
        embedding_pb, embedding_output = model_cache.get_embedding_model()
        self._embedding_algo = es.TensorflowPredictEffnetDiscogs(
            graphFilename=embedding_pb, output=embedding_output
        )
        self._classifiers: dict[str, tuple[object, model_cache.ModelMeta]] = {}
        for spec in model_cache.CLASSIFIER_HEADS:
            meta = model_cache.get_classifier(spec)
            algo = es.TensorflowPredict2D(
                graphFilename=meta.pb_path,
                input=meta.input_name,
                output=meta.output_name,
            )
            self._classifiers[spec.name] = (algo, meta)

    def analyze(self, path: str) -> TrackAnalysis:
        es = self._es

        rhythm_audio = es.MonoLoader(filename=path, sampleRate=_ANALYSIS_SAMPLE_RATE)()
        bpm, _beats, bpm_confidence, _estimates, _intervals = es.RhythmExtractor2013(
            method="multifeature"
        )(rhythm_audio)
        key, scale, key_strength = es.KeyExtractor()(rhythm_audio)
        dynamic_complexity, loudness = es.DynamicComplexity()(rhythm_audio)

        embedding_audio = es.MonoLoader(
            filename=path, sampleRate=_EMBEDDING_SAMPLE_RATE, resampleQuality=4
        )()
        embeddings = self._embedding_algo(embedding_audio)

        binary_probs: dict[str, float] = {}
        categorical: dict[str, tuple[str, float]] = {}
        multilabel: dict[str, list[Label]] = {}

        for spec in model_cache.CLASSIFIER_HEADS:
            algo, meta = self._classifiers[spec.name]
            track_scores = np.mean(algo(embeddings), axis=0)

            if spec.kind == "binary":
                binary_probs[spec.name] = float(
                    track_scores[meta.classes.index(spec.positive_label)]
                )
            elif spec.kind == "categorical":
                idx = int(np.argmax(track_scores))
                categorical[spec.name] = (meta.classes[idx], float(track_scores[idx]))
            elif spec.kind == "multilabel":
                top_n = _TOP_N_BY_MODEL[spec.name]
                order = np.argsort(track_scores)[::-1][:top_n]
                multilabel[spec.name] = [
                    Label(
                        name=_clean_label(meta.classes[i]),
                        confidence=float(track_scores[i]),
                    )
                    for i in order
                ]
            else:
                raise ValueError(f"Unknown model kind: {spec.kind!r}")

        gender_label, gender_confidence = categorical["gender"]
        timbre_label, timbre_confidence = categorical["timbre"]

        return TrackAnalysis(
            bpm=float(bpm),
            bpm_confidence=float(bpm_confidence),
            key=key,
            scale=scale,
            camelot=to_camelot(key, scale),
            key_strength=float(key_strength),
            loudness=float(loudness),
            dynamic_complexity=float(dynamic_complexity),
            mood_aggressive=binary_probs["mood_aggressive"],
            mood_happy=binary_probs["mood_happy"],
            mood_sad=binary_probs["mood_sad"],
            mood_relaxed=binary_probs["mood_relaxed"],
            mood_party=binary_probs["mood_party"],
            danceability=binary_probs["danceability"],
            mood_acoustic=binary_probs["mood_acoustic"],
            mood_electronic=binary_probs["mood_electronic"],
            voice_probability=binary_probs["voice_instrumental"],
            gender=gender_label,
            gender_confidence=gender_confidence,
            tonal_probability=binary_probs["tonal_atonal"],
            timbre=timbre_label,
            timbre_confidence=timbre_confidence,
            genres=multilabel["genre_discogs400"],
            mood_themes=multilabel["mtg_jamendo_moodtheme"],
        )
