from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..processing.evidence import EvidenceStore, EvidenceUnavailable
from ..processing.lap_enrichment import LapEnrichmentBusy, LapEnrichmentLimits, LapEnrichmentService


def _error(status_code: int, reason: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={
        "api_version": "v1", "status": "unavailable", "data": None, "reason": reason,
    })


def register_lap_enrichment_routes(
    app: FastAPI,
    *,
    evidence_store: EvidenceStore,
    service: LapEnrichmentService | None = None,
    limits: LapEnrichmentLimits | None = None,
) -> LapEnrichmentService:
    """Expose read-side sector and tyre enrichment for v2 session-evidence player attempts."""
    enrichment = service or LapEnrichmentService(evidence_store, limits=limits)

    @app.get("/api/v2/session-evidence/sessions/{session_id}/laps")
    def session_evidence_laps(session_id: str):
        if not 1 <= len(session_id) <= 128:
            return _error(404, "session_not_found")
        try:
            return {"data": enrichment.laps(session_id)}
        except LapEnrichmentBusy:
            return _error(409, "lap_enrichment_busy")
        except EvidenceUnavailable as exception:
            return _error(404 if str(exception) == "session_not_found" else 422, str(exception))

    return enrichment
