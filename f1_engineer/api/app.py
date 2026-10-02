from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Generic, Literal, TypeVar

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..analysis.reference_selection import ReferenceKind, ReferenceRequest, select_reference
from ..analysis.service import compare_attempts
from ..storage.importer import DEFAULT_DATABASE, list_laps, list_sessions
from ..storage.database import DatabaseSchemaError
from ..tracks.model import TrackModel
from ..tracks.registry import (
    list_track_models as list_registered_track_models,
    resolve_track_model,
)


PayloadT = TypeVar("PayloadT")


class APIResponse(BaseModel, Generic[PayloadT]):
    api_version: Literal["v1"] = "v1"
    status: Literal["ok", "unavailable"] = "ok"
    data: PayloadT | None = None
    reason: str | None = None


class SessionRecord(BaseModel):
    session_key: str
    run_id: str
    session_uid: str
    packet_format: int
    context: dict[str, Any] | None
    run_status: str
    capture_quality: dict[str, Any] | None
    capture_sha256: str
    pipeline_version: str
    started_at_utc: str
    finished_at_utc: str | None
    lap_attempts: int


class TrackModelRecord(BaseModel):
    model_id: str
    revision: int
    packet_format: int
    track_id: int
    track_name: str
    layout_id: str
    track_length_m: float
    validation_status: str
    provenance: str
    region_count: int


class LapRecord(BaseModel):
    attempt_key: str
    run_id: str
    session_uid: str
    car_index: int
    attempt_number: int
    lap_number: int
    disposition: str
    lap_time_ms: int | None
    game_valid: bool | None
    reference_eligible: bool
    start_observed: bool
    pit_encountered: bool
    sample_count: int
    trace_row_count: int
    trace_schema_version: int
    trace_sha256: str
    context: dict[str, Any] | None
    quality: dict[str, Any]
    exclusion_reasons: list[str]


class ReferenceCandidate(BaseModel):
    attempt_key: str
    attempt_number: int
    lap_time_ms: int | None
    eligible: bool
    selected: bool
    trace_sha256: str | None
    exclusion_reasons: list[str]


class ReferenceSelectionData(BaseModel):
    reference_kind: str
    status: str
    policy_version: str
    target: dict[str, Any]
    scope: dict[str, Any] | None
    selected_reference: dict[str, Any] | None
    candidates: list[ReferenceCandidate]
    reasons: list[str]


def create_app(database_path: str | Path = DEFAULT_DATABASE) -> FastAPI:
    """Create an API bound to one operator-configured database file."""
    configured_database_path = Path(database_path).expanduser().resolve()
    app = FastAPI(
        title="F1 Race Engineer API",
        version="1.0.0",
        description="Read-only historical F1 telemetry and lap analysis.",
    )

    @app.exception_handler(FileNotFoundError)
    @app.exception_handler(DatabaseSchemaError)
    @app.exception_handler(sqlite3.DatabaseError)
    def database_unavailable(_request: Any, _exception: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "api_version": "v1",
                "status": "unavailable",
                "data": None,
                "reason": "configured_database_unavailable",
            },
        )

    @app.get("/api/v1/sessions", response_model=APIResponse[list[SessionRecord]])
    def sessions() -> APIResponse[list[SessionRecord]]:
        return APIResponse[list[SessionRecord]](
            data=_stringify_session_uids(list_sessions(configured_database_path))
        )

    @app.get("/api/v1/laps", response_model=APIResponse[list[LapRecord]])
    def laps(
        run_id: str | None = Query(default=None),
        session_uid: str | None = Query(default=None),
    ) -> APIResponse[list[LapRecord]]:
        return APIResponse[list[LapRecord]](
            data=_stringify_session_uids(
                list_laps(
                    configured_database_path,
                    run_id=run_id,
                    session_uid=session_uid,
                )
            )
        )

    @app.get(
        "/api/v1/track-models",
        response_model=APIResponse[list[TrackModelRecord]],
    )
    def track_models() -> APIResponse[list[TrackModelRecord]]:
        return APIResponse[list[TrackModelRecord]](
            data=list_registered_track_models()
        )

    @app.get("/api/v1/compare/laps", response_model=APIResponse[dict[str, Any]])
    def compare_laps(
        target_attempt_key: str = Query(min_length=1),
        reference_attempt_key: str = Query(min_length=1),
        track_model_id: str | None = Query(default=None, min_length=1),
        track_model_revision: int | None = Query(default=None, ge=1),
    ) -> APIResponse[dict[str, Any]]:
        if (track_model_id is None) != (track_model_revision is None):
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="track_model_id_and_revision_must_be_selected_together",
            )
        try:
            track_model: TrackModel | None = (
                resolve_track_model(track_model_id, track_model_revision)
                if track_model_id is not None and track_model_revision is not None
                else None
            )
            result = compare_attempts(
                configured_database_path,
                target_attempt_key,
                reference_attempt_key,
                track_model=track_model,
            )
        except DatabaseSchemaError:
            raise
        except ValueError as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason=str(exc),
            )
        return APIResponse[dict[str, Any]](data=_stringify_session_uids(result))

    @app.get(
        "/api/v1/references/session-best",
        response_model=APIResponse[ReferenceSelectionData],
    )
    def session_best_reference(
        target_attempt_key: str = Query(min_length=1),
    ) -> APIResponse[ReferenceSelectionData]:
        result = select_reference(
            configured_database_path,
            ReferenceRequest(
                target_attempt_key=target_attempt_key,
                reference_kind=ReferenceKind.SESSION_BEST,
            ),
        )
        return APIResponse[ReferenceSelectionData](
            data=_stringify_session_uids(result.to_dict())
        )

    return app


def _stringify_session_uids(value: Any) -> Any:
    """Keep unsigned 64-bit session identities exact across JSON/JavaScript."""
    if isinstance(value, dict):
        return {
            key: str(item) if key == "session_uid" and item is not None
            else _stringify_session_uids(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_stringify_session_uids(item) for item in value]
    return value
