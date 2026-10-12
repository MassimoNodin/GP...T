from __future__ import annotations

import json

from fastapi import FastAPI, Path, Query
from fastapi.responses import JSONResponse

from ..processing.evidence import EvidenceStore
from ..tracks.registry import TrackModelCatalog, TrackModelEntry


def _regions(entry: TrackModelEntry) -> dict[str, object]:
    model = entry.model
    return {
        **entry.metadata(),
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
        "regions": [
            {
                "id": corner.identifier,
                "label": corner.label,
                "start_distance_m": corner.start_distance_m + model.distance_origin_m,
                "end_distance_m": corner.end_distance_m + model.distance_origin_m,
                "nominal_apex_m": (
                    None if corner.nominal_apex_m is None
                    else corner.nominal_apex_m + model.distance_origin_m
                ),
                "direction": corner.direction,
                "complex_id": corner.complex_id,
            }
            for corner in model.corners
        ],
    }


def _unavailable(reason: str, status_code: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={
        "api_version": "v1", "status": "unavailable", "data": None, "reason": reason,
    })


def register_track_region_routes(
    app: FastAPI, *, catalog: TrackModelCatalog, evidence_store: EvidenceStore,
) -> None:
    @app.get("/api/v1/track-models/{model_id}/regions")
    def track_regions(
        model_id: str = Path(min_length=1, max_length=128),
        revision: int = Query(ge=1),
    ):
        try:
            entry = catalog.resolve_entry(model_id, revision)
        except ValueError:
            return _unavailable("unknown_track_model_revision", 404)
        return {"data": _regions(entry)}

    @app.get("/api/v2/session-evidence/sessions/{session_id}/track-models")
    def session_track_models(session_id: str = Path(min_length=1, max_length=128)):
        with evidence_store.connect() as database:
            session = database.execute("SELECT context FROM sessions WHERE id=?", (session_id,)).fetchone()
        if session is None:
            return _unavailable("session_not_found", 404)
        try:
            context = json.loads(session["context"] or "{}")
            track_id = context.get("track_id")
            packet_format = context.get("packet_format")
            length = context.get("track_length_m")
        except (ValueError, TypeError, AttributeError):
            return _unavailable("session_track_context_unavailable")
        if track_id is None or packet_format is None or length is None:
            return _unavailable("session_track_context_unavailable")
        entries = [
            entry for entry in catalog.entries.values()
            if entry.model.track_id == track_id and entry.model.packet_format == packet_format
            and entry.model.track_length_m == length
        ]
        entries.sort(key=lambda entry: (entry.model.model_id, entry.model.revision))
        return {"data": {
            "session_id": session_id,
            "models": [_regions(entry) for entry in entries],
            "reason": None if entries else "compatible_track_model_unavailable",
        }}
