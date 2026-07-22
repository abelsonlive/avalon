"""Downloads and caches the Essentia pretrained models avalon uses.

Each model's input/output tensor names and class label order come from its
`.json` sidecar at run time rather than being hardcoded: node names vary
per model (e.g. genre_discogs400 uses a different input node than the
mood/character heads), and -- more importantly -- class order is *not*
consistent (`mood_sad` is `["non_sad", "sad"]` while `mood_happy` is
`["happy", "non_happy"]`). Trusting the declared schema avoids silently
inverted probabilities.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import requests

from avalon.constants import MODEL_CACHE_DIRNAME

logger = logging.getLogger(__name__)

_BASE_URL = "https://essentia.upf.edu/models"
_EMBEDDING_SUBDIR = "feature-extractors/discogs-effnet"
_EMBEDDING_STEM = "discogs-effnet-bs64-1"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One classifier head. `kind` drives how essentia_analyzer reads its
    output vector:
      binary      -> single probability of `positive_label`
      categorical -> whichever of `labels` scores highest, plus its confidence
      multilabel  -> independent (sigmoid) scores, no single "positive" class
    """

    name: str
    subdir: str
    kind: str
    positive_label: str | None = None


CLASSIFIER_HEADS: tuple[ModelSpec, ...] = (
    ModelSpec("danceability", "danceability", "binary", positive_label="danceable"),
    ModelSpec("mood_acoustic", "mood_acoustic", "binary", positive_label="acoustic"),
    ModelSpec(
        "mood_aggressive", "mood_aggressive", "binary", positive_label="aggressive"
    ),
    ModelSpec(
        "mood_electronic", "mood_electronic", "binary", positive_label="electronic"
    ),
    ModelSpec("mood_happy", "mood_happy", "binary", positive_label="happy"),
    ModelSpec("mood_sad", "mood_sad", "binary", positive_label="sad"),
    ModelSpec("mood_relaxed", "mood_relaxed", "binary", positive_label="relaxed"),
    ModelSpec("mood_party", "mood_party", "binary", positive_label="party"),
    ModelSpec(
        "voice_instrumental", "voice_instrumental", "binary", positive_label="voice"
    ),
    ModelSpec("tonal_atonal", "tonal_atonal", "binary", positive_label="tonal"),
    ModelSpec("gender", "gender", "categorical"),
    ModelSpec("timbre", "timbre", "categorical"),
    ModelSpec("genre_discogs400", "genre_discogs400", "multilabel"),
    ModelSpec("mtg_jamendo_moodtheme", "mtg_jamendo_moodtheme", "multilabel"),
)


@dataclass(frozen=True, slots=True)
class ModelMeta:
    """Parsed `.json` sidecar: local file path plus the schema bits needed
    to run inference generically."""

    pb_path: str
    input_name: str
    output_name: str
    classes: tuple[str, ...]


def _cache_dir() -> Path:
    path = Path.home() / ".cache" / MODEL_CACHE_DIRNAME / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str, dest: Path) -> None:
    if dest.exists():
        return
    logger.info("Downloading model file %s", url)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(response.content)
    tmp.rename(dest)


def _fetch(subdir: str, filename_stem: str) -> tuple[Path, dict]:
    cache = _cache_dir()
    pb_dest = cache / f"{filename_stem}.pb"
    json_dest = cache / f"{filename_stem}.json"
    base = f"{_BASE_URL}/{subdir}/{filename_stem}"
    _download(f"{base}.pb", pb_dest)
    _download(f"{base}.json", json_dest)
    return pb_dest, json.loads(json_dest.read_text())


def _output_by_purpose(outputs: list[dict], purpose: str) -> str:
    for out in outputs:
        if out.get("output_purpose") == purpose:
            return out["name"]
    return outputs[0]["name"]


def get_embedding_model() -> tuple[str, str]:
    """Downloads (if needed) the shared discogs-effnet embedding extractor.

    Returns (pb_path, embedding_output_node_name).
    """
    pb_path, meta = _fetch(_EMBEDDING_SUBDIR, _EMBEDDING_STEM)
    output_name = _output_by_purpose(meta["schema"]["outputs"], "embeddings")
    return str(pb_path), output_name


def get_classifier(spec: ModelSpec) -> ModelMeta:
    filename_stem = f"{spec.name}-discogs-effnet-1"
    pb_path, meta = _fetch(f"classification-heads/{spec.subdir}", filename_stem)
    schema = meta["schema"]
    return ModelMeta(
        pb_path=str(pb_path),
        input_name=schema["inputs"][0]["name"],
        output_name=_output_by_purpose(schema["outputs"], "predictions"),
        classes=tuple(meta["classes"]),
    )


def prefetch_all() -> None:
    """Downloads every model avalon needs, up front (~26.5MB total)."""
    get_embedding_model()
    for spec in CLASSIFIER_HEADS:
        get_classifier(spec)
