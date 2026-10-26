from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

from .loader import load_track_model_with_checksum
from .fingerprint import (
    TRACK_MODEL_FINGERPRINT_VERSION,
    track_model_fingerprint,
)
from .model import TrackModel
from .reviewed_bundle import (
    REVIEWED_TRACK_MODEL_SCOPE,
    ReviewProvenance,
    load_reviewed_track_models,
)


_MODEL_FILES = ("melbourne_f1_25_tt_draft_v1.json",)
MAX_LOCAL_TRACK_MODELS = 16
MAX_LOCAL_TRACK_MODEL_DIRECTORY_ENTRIES = 128

ModelOrigin = Literal["packaged", "local_draft", "reviewed"]


@dataclass(frozen=True, slots=True)
class TrackModelEntry:
    model: TrackModel
    origin: ModelOrigin
    content_sha256: str
    source_filename: str
    model_content_sha256: str
    review: ReviewProvenance | None = None

    def metadata(self) -> dict[str, object]:
        reviewed_approved = (
            self.origin == "reviewed"
            and self.model.validation_status == "validated"
            and self.review is not None
            and self.review.scope == REVIEWED_TRACK_MODEL_SCOPE
            and self.review.model_fingerprint_version == "track-model-canonical-v1"
            and self.model_content_sha256 == track_model_fingerprint(self.model)
        )
        packaged_approval = CORNER_CANDIDATE_RANKING_APPROVALS.get(
            (self.model.model_id, self.model.revision)
        ) if self.origin == "packaged" else None
        packaged_provenance = (
            packaged_approval.get("approval_provenance")
            if packaged_approval is not None
            else None
        )
        approved = reviewed_approved or (
            self.origin == "packaged"
            and self.model.validation_status == "validated"
            and packaged_approval is not None
            and packaged_approval.get("model_content_sha256") == self.model_content_sha256
            and isinstance(packaged_provenance, Mapping)
            and bool(packaged_provenance)
        )
        return {
            **_metadata(self.model),
            "origin": self.origin,
            "content_sha256": self.content_sha256,
            "source_filename": self.source_filename,
            "source_kind": {
                "packaged": "package_artifact",
                "local_draft": "diagnostic_draft",
                "reviewed": "review_bundle",
            }[self.origin],
            "model_content_sha256": self.model_content_sha256,
            "bundle_content_sha256": self.content_sha256 if self.origin == "reviewed" else None,
            "approved_for_candidate_ranking": approved,
            "review": self.review.metadata() if self.review is not None else None,
        }


@dataclass(frozen=True, slots=True)
class TrackModelCatalog:
    """Immutable startup snapshot of packaged, draft, and reviewed models."""

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
            model_content_sha256=track_model_fingerprint(model),
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
    reviewed_root: str | Path | None = None,
) -> TrackModelCatalog:
    """Build an immutable catalog, optionally adding drafts and review bundles.

    Both configured directories are flat and operator-selected. Request
    parameters can choose only an opaque model ID and revision, never a path.
    """
    if local_root is None and reviewed_root is None:
        return PACKAGED_TRACK_MODEL_CATALOG
    entries = dict(_PACKAGED_ENTRIES)
    packaged_ids = {model_id for model_id, _revision in entries}
    if local_root is not None:
        for entry in _load_local_draft_entries(local_root):
            if entry.model.model_id in packaged_ids:
                raise ValueError("local_track_model_shadows_packaged_model")
            _admit_unique(entries, entry)
    if reviewed_root is not None:
        for reviewed in load_reviewed_track_models(reviewed_root):
            entry = TrackModelEntry(
                model=reviewed.model,
                origin="reviewed",
                content_sha256=reviewed.bundle_content_sha256,
                source_filename=reviewed.source_filename,
                model_content_sha256=reviewed.model_content_sha256,
                review=reviewed.review,
            )
            _admit_unique(entries, entry)
    return TrackModelCatalog(MappingProxyType(entries))


def _load_local_draft_entries(root_path: str | Path) -> tuple[TrackModelEntry, ...]:
    try:
        root = Path(root_path).expanduser().resolve(strict=True)
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

    loaded: list[TrackModelEntry] = []
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
        loaded.append(
            TrackModelEntry(
                model=model,
                origin="local_draft",
                content_sha256=content_sha256,
                source_filename=candidate.name,
                model_content_sha256=track_model_fingerprint(model),
            )
        )
    return tuple(loaded)


def _admit_unique(
    entries: dict[tuple[str, int], TrackModelEntry], entry: TrackModelEntry
) -> None:
    key = (entry.model.model_id, entry.model.revision)
    if key in entries:
        raise ValueError("duplicate_track_model_id_and_revision")
    entries[key] = entry


def list_track_models(
    catalog: TrackModelCatalog | None = None,
) -> list[dict[str, object]]:
    return (catalog or PACKAGED_TRACK_MODEL_CATALOG).list_metadata()


def resolve_track_model(model_id: str, revision: int) -> TrackModel:
    """Resolve only packaged models for paired-comparison call sites."""
    entry = PACKAGED_TRACK_MODEL_CATALOG.resolve_entry(model_id, revision)
    return entry.model


def corner_candidate_ranking_approval(
    model: TrackModel, catalog: TrackModelCatalog | None = None
) -> dict[str, object]:
    """Return review evidence for this exact model in the supplied snapshot."""
    key = (model.model_id, model.revision)
    fingerprint = track_model_fingerprint(model)
    entry = catalog.entries.get(key) if catalog is not None else None
    if catalog is None:
        registered_model = TRACK_MODEL_REGISTRY.get(key)
        registered = registered_model is not None and registered_model == model
        entry = _PACKAGED_ENTRIES.get(key) if registered else None
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
    else:
        registered = entry is not None and entry.model == model
        if registered and entry is not None and entry.origin == "reviewed":
            provenance = entry.review.metadata() if entry.review is not None else None
            approved = (
                model.validation_status == "validated"
                and entry.model_content_sha256 == fingerprint
                and entry.review is not None
                and entry.review.scope == REVIEWED_TRACK_MODEL_SCOPE
                and entry.review.model_fingerprint_version == TRACK_MODEL_FINGERPRINT_VERSION
            )
        elif registered and entry is not None and entry.origin == "packaged":
            raw_approval = CORNER_CANDIDATE_RANKING_APPROVALS.get(key)
            provenance = raw_approval.get("approval_provenance") if raw_approval else None
            approved = (
                model.validation_status == "validated"
                and raw_approval is not None
                and raw_approval.get("model_content_sha256") == fingerprint
                and isinstance(provenance, Mapping)
                and bool(provenance)
            )
        else:
            provenance = None
            approved = False
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
        "origin": entry.origin if entry is not None and registered else None,
        "source_kind": (
            entry.metadata()["source_kind"] if entry is not None and registered else None
        ),
        "content_sha256": entry.content_sha256 if entry is not None and registered else None,
        "bundle_content_sha256": (
            entry.content_sha256
            if entry is not None and registered and entry.origin == "reviewed"
            else None
        ),
        "review": entry.review.metadata()
        if entry is not None and registered and entry.review is not None
        else None,
        "approved_for_candidate_ranking": approved,
        "validation_status": model.validation_status,
        "model_content_sha256": fingerprint,
        "approval_provenance": dict(provenance) if isinstance(provenance, Mapping) else None,
        "reason": reason,
    }


def _model_fingerprint(model: TrackModel) -> str:
    """Backward-compatible name for the canonical model fingerprint."""
    return track_model_fingerprint(model)


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
