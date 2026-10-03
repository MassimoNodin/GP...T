from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from dataclasses import asdict

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

# Candidate ranking requires a separate review of the exact registered model
# contents. Validation status alone does not grant this authority. No bundled
# model has that approval yet.
CORNER_CANDIDATE_RANKING_APPROVALS: Mapping[
    tuple[str, int], Mapping[str, object]
] = MappingProxyType({})


def list_track_models() -> list[dict[str, object]]:
    return [_metadata(model) for model in TRACK_MODEL_REGISTRY.values()]


def resolve_track_model(model_id: str, revision: int) -> TrackModel:
    model = TRACK_MODEL_REGISTRY.get((model_id, revision))
    if model is None:
        raise ValueError("unknown_track_model_revision")
    return model


def corner_candidate_ranking_approval(model: TrackModel) -> dict[str, object]:
    """Return explicit approval evidence for this exact registered revision."""
    key = (model.model_id, model.revision)
    registered_model = TRACK_MODEL_REGISTRY.get(key)
    fingerprint = _model_fingerprint(model)
    registered = registered_model is not None and registered_model == model
    raw_approval = CORNER_CANDIDATE_RANKING_APPROVALS.get(key)
    provenance = raw_approval.get("approval_provenance") if raw_approval else None
    approved = (
        registered
        and model.validation_status == "validated"
        and raw_approval is not None
        and raw_approval.get("model_content_sha256") == fingerprint
        and isinstance(provenance, Mapping)
        and bool(provenance)
    )
    reason = (
        None
        if approved
        else "track_model_revision_not_registered"
        if not registered
        else "track_model_not_validated"
        if model.validation_status != "validated"
        else "track_model_not_approved_for_candidate_ranking"
    )
    return {
        "model_id": model.model_id,
        "revision": model.revision,
        "registered": registered,
        "approved_for_candidate_ranking": approved,
        "validation_status": model.validation_status,
        "model_content_sha256": fingerprint,
        "approval_provenance": dict(provenance) if isinstance(provenance, Mapping) else None,
        "reason": reason,
    }


def _model_fingerprint(model: TrackModel) -> str:
    payload = json.dumps(
        asdict(model), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
