from __future__ import annotations

from fastapi import FastAPI, Path
from fastapi.responses import JSONResponse

from ..processing.evidence import EvidenceStore, EvidenceUnavailable
from ..processing.session_summary import SessionSummaryService

_STATUS = {"session_not_found": 404, "summary_busy": 409}


def register_session_summary_routes(
    app: FastAPI, *, evidence_store: EvidenceStore, service: SessionSummaryService | None = None
) -> SessionSummaryService:
    """Expose the read-only session timeline and summary for v2 evidence sessions."""
    summaries = service or SessionSummaryService(evidence_store)

    @app.get("/api/v2/session-evidence/sessions/{session_id}/summary")
    def session_evidence_summary(session_id: str = Path(max_length=128)):
        try:
            return {"data": summaries.summary(session_id)}
        except EvidenceUnavailable as exception:
            reason = str(exception)
            return JSONResponse(
                status_code=_STATUS.get(reason, 422),
                content={"api_version": "v1", "status": "unavailable", "data": None, "reason": reason},
            )

    return summaries
