from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

from .loader import load_track_model_with_checksum
from .model import TrackModel


_MODEL_FILES = ("melbourne_f1_25_tt_draft_v1.json",)
MAX_LOCAL_TRACK_MODELS = 16
MAX_LOCAL_TRACK_MODEL_DIRECTORY_ENTRIES = 128

ModelOrigin = Literal["packaged", "local_draft"]


@dataclass(frozen=True, slots=True)
class TrackModelEntry:
    model: TrackModel
    origin: ModelOrigin
    content_sha256: str
    source_filename: str

    def metadata(self) -> dict[str, object]:
        return {
            **_metadata(self.model),
            "origin": self.origin,
            "content_sha256": self.content_sha256,
            "source_filename": self.source_filename,
        }


@dataclass(frozen=True, slots=True)
class TrackModelCatalog:
    """Immutable startup snapshot of packaged and explicitly configured models."""

    entries: Mapping[tuple[str, int], TrackModelEntry]

    def resolve_entry(self, model_id: str, revision: int) -> TrackModelEntry:
        entry = self.entries.get((model_id, revision))
        if entry is None:
            raise ValueError("unknown_track_model_revision")
        return entry

    def list_metadata(self) -> list[dict[str, object]]:
        return [
            self.entries[key].metadata()
            for key in sorted(
                self.entries,
                key=lambda item: (
                    self.entries[item].model.track_name.casefold(),
                    self.entries[item].model.model_id,
                    item[1],
                ),
            )
        ]


def _load_packaged_entries() -> Mapping[tuple[str, int], TrackModelEntry]:
    data_directory = Path(__file__).with_name("data")
    entries: dict[tuple[str, int], TrackModelEntry] = {}
    for filename in _MODEL_FILES:
        model, content_sha256 = load_track_model_with_checksum(data_directory / filename)
        key = (model.model_id, model.revision)
        if key in entries:
            raise RuntimeError("duplicate track model ID and revision in registry")
        entries[key] = TrackModelEntry(
            model=model,
            origin="packaged",
            content_sha256=content_sha256,
            source_filename=filename,
        )
    return MappingProxyType(entries)


_PACKAGED_ENTRIES = _load_packaged_entries()
PACKAGED_TRACK_MODEL_CATALOG = TrackModelCatalog(_PACKAGED_ENTRIES)

# Candidate ranking requires separate review of the exact registered model
# contents. Local models are intentionally absent from this packaged registry.
TRACK_MODEL_REGISTRY: Mapping[tuple[str, int], TrackModel] = MappingProxyType(
    {key: entry.model for key, entry in _PACKAGED_ENTRIES.items()}
)
CORNER_CANDIDATE_RANKING_APPROVALS: Mapping[
    tuple[str, int], Mapping[str, object]
] = MappingProxyType({})


def load_track_model_catalog(
    local_root: str | Path | None = None,
) -> TrackModelCatalog:
    """Build an immutable catalog, optionally adding local draft JSON models.

    The configured directory is flat and server-selected. Request parameters
    can choose only an opaque model ID and revision, never a file path.
    """
    if local_root is None:
        return PACKAGED_TRACK_MODEL_CATALOG
    try:
        root = Path(local_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("local_track_model_root_unavailable") from exc
    if not root.is_dir():
        raise ValueError("local_track_model_root_must_be_a_directory")

    candidates: list[Path] = []
    try:
        for count, candidate in enumerate(root.iterdir(), start=1):
            if count > MAX_LOCAL_TRACK_MODEL_DIRECTORY_ENTRIES:
                raise ValueError("local_track_model_directory_entry_limit_exceeded")
            if candidate.suffix.casefold() == ".json":
                candidates.append(candidate)
    except OSError as exc:
        raise ValueError("local_track_model_root_unavailable") from exc
    if len(candidates) > MAX_LOCAL_TRACK_MODELS:
        raise ValueError("local_track_model_count_limit_exceeded")

    entries = dict(_PACKAGED_ENTRIES)
    packaged_ids = {model_id for model_id, _revision in entries}
    local_keys: set[tuple[str, int]] = set()
    for candidate in sorted(candidates, key=lambda path: path.name.casefold()):
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError("local_track_model_file_unavailable") from exc
        if not resolved.is_relative_to(root):
            raise ValueError("local_track_model_path_outside_root")
        if not resolved.is_file():
            raise ValueError("local_track_model_path_must_be_a_file")
        model, content_sha256 = load_track_model_with_checksum(resolved)
        if model.validation_status != "draft":
            raise ValueError("local_track_model_must_remain_draft")
        if model.model_id in packaged_ids:
            raise ValueError("local_track_model_shadows_packaged_model")
        key = (model.model_id, model.revision)
        if key in entries or key in local_keys:
            raise ValueError("duplicate_track_model_id_and_revision")
        local_keys.add(key)
        entries[key] = TrackModelEntry(
            model=model,
            origin="local_draft",
            content_sha256=content_sha256,
            source_filename=candidate.name,
        )
    return TrackModelCatalog(MappingProxyType(entries))


def list_track_models(
    catalog: TrackModelCatalog | None = None,
) -> list[dict[str, object]]:
    return (catalog or PACKAGED_TRACK_MODEL_CATALOG).list_metadata()


def resolve_track_model(model_id: str, revision: int) -> TrackModel:
    """Resolve only packaged models for paired-comparison call sites."""
    entry = PACKAGED_TRACK_MODEL_CATALOG.resolve_entry(model_id, revision)
    return entry.model


def corner_candidate_ranking_approval(model: TrackModel) -> dict[str, object]:
    """Return explicit approval evidence for this exact packaged revision."""
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
