from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .loader import load_track_model
from .model import TrackModel


_MODEL_FILES = ("melbourne_f1_25_tt_draft_v1.json",)


def _load_registry() -> Mapping[tuple[str, int], TrackModel]:
    data_directory = Path(__file__).with_name("data")
    models = {
        (model.model_id, model.revision): model
        for model in (
            load_track_model(data_directory / filename) for filename in _MODEL_FILES
        )
    }
    if len(models) != len(_MODEL_FILES):
        raise RuntimeError("duplicate track model ID and revision in registry")
    return MappingProxyType(models)


TRACK_MODEL_REGISTRY = _load_registry()


def list_track_models() -> list[dict[str, object]]:
    return [_metadata(model) for model in TRACK_MODEL_REGISTRY.values()]


def resolve_track_model(model_id: str, revision: int) -> TrackModel:
    model = TRACK_MODEL_REGISTRY.get((model_id, revision))
    if model is None:
        raise ValueError("unknown_track_model_revision")
    return model


def _metadata(model: TrackModel) -> dict[str, object]:
    return {
        "model_id": model.model_id,
        "revision": model.revision,
        "packet_format": model.packet_format,
        "track_id": model.track_id,
        "track_name": model.track_name,
        "layout_id": model.layout_id,
        "track_length_m": model.track_length_m,
        "validation_status": model.validation_status,
        "provenance": model.provenance,
        "region_count": len(model.corners),
    }
