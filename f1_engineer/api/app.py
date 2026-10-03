from __future__ import annotations

import hmac
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..analysis.reference_selection import ReferenceKind, ReferenceRequest, select_reference
from ..analysis.quality import inspect_attempt_quality
from ..analysis.region_service import RegionReportUnavailable, load_attempt_region_report
from ..analysis.service import compare_attempts
from ..analysis.trajectory import TrajectoryPreviewUnavailable
from ..analysis.trajectory_service import load_observed_trajectory_preview
from ..analysis.trace_chart_service import (
    TraceChartUnavailable,
    load_attempt_trace_chart_preview,
)
from ..storage.import_jobs import list_recording_sources
from ..storage.importer import DEFAULT_DATABASE, list_laps, list_sessions
from ..storage.run_summaries import (
    DEFAULT_ATTEMPT_PAGE_SIZE,
    DEFAULT_RUN_PAGE_SIZE,
    DEFAULT_SESSION_PAGE_SIZE,
    MAX_CHILD_PAGE_SIZE,
    MAX_PAGE_OFFSET,
    MAX_RUN_PAGE_SIZE,
    get_processing_run_detail,
    list_processing_run_summaries,
)
from ..storage.database import DatabaseSchemaError
from ..tracks.model import TrackModel
from ..tracks.registry import (
    list_track_models as list_registered_track_models,
    resolve_track_model,
)
from .import_controller import ImportController
from .recording_controller import RecordingController


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


class RecordingSourceRecord(BaseModel):
    capture_id: str
    display_name: str
    byte_size: int
    modified_at_utc: str
    latest_job_id: str | None
    latest_job_status: str | None
    latest_job_run_id: str | None
    available: bool


class ImportProgressRecord(BaseModel):
    phase: str
    packets_processed: int
    bytes_read: int
    total_bytes: int


class ImportJobRecord(BaseModel):
    job_id: str
    capture_id: str
    status: Literal["queued", "running", "complete", "failed", "interrupted"]
    phase: str
    attempt_count: int
    created_at_utc: str
    updated_at_utc: str
    started_at_utc: str | None
    finished_at_utc: str | None
    result: dict[str, Any] | None
    failure_reason: str | None
    progress: ImportProgressRecord | None = None


class ImportJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class LiveTelemetryRecord(BaseModel):
    status: Literal["waiting", "fresh", "stale", "unsupported", "unavailable"]
    reason: str | None
    age_ms: int | None
    session_uid: str | None = None
    frame_identifier: int | None = None
    packet_format: int | None = None
    player_car_index: int | None = None
    lap_number: int | None = None
    lap_time_ms: int | None = None
    game_invalid: bool | None = None
    pit_status_id: int | None = None
    driver_status_id: int | None = None
    speed_kph: float | None = None
    gear: int | None = None
    engine_rpm: int | None = None
    throttle: float | None = None
    brake: float | None = None


class RecordingProgressRecord(BaseModel):
    state: str
    elapsed_ms: int
    received: int
    queued: int
    recorded: int
    queue_dropped: int
    socket_errors: int
    latest_context: dict[str, Any] | None
    live_telemetry: LiveTelemetryRecord


class RecordingJobRecord(BaseModel):
    recording_id: str
    status: Literal[
        "starting", "recording", "stopping", "complete", "failed", "interrupted"
    ]
    bind_host: str
    bind_port: int
    created_at_utc: str
    updated_at_utc: str
    started_at_utc: str | None
    finished_at_utc: str | None
    summary: dict[str, Any] | None
    failure_reason: str | None
    published: bool
    progress: RecordingProgressRecord | None = None


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


def create_app(
    database_path: str | Path = DEFAULT_DATABASE,
    *,
    recordings_root: str | Path = "recordings",
    control_token: str | None = None,
    recording_host: str = "0.0.0.0",
    recording_port: int = 20777,
    recording_queue_size: int = 8192,
) -> FastAPI:
    """Create a local API bound to operator-configured storage and recording roots."""
    configured_database_path = Path(database_path).expanduser().resolve()
    configured_recordings_root = Path(recordings_root).expanduser().resolve()
    import_controller = ImportController(
        configured_database_path, configured_recordings_root
    )
    recording_controller = RecordingController(
        configured_database_path,
        configured_recordings_root,
        import_controller,
        host=recording_host,
        port=recording_port,
        queue_size=recording_queue_size,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import_controller.start()
        recording_controller.start()
        app.state.import_controller = import_controller
        app.state.recording_controller = recording_controller
        try:
            yield
        finally:
            recording_controller.close()
            import_controller.close()

    app = FastAPI(
        title="F1 Race Engineer API",
        version="1.0.0",
        description="Local telemetry recording, historical analysis, and explicit capture imports.",
        lifespan=lifespan,
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

    @app.get("/api/v1/processing-runs", response_model=APIResponse[dict[str, Any]])
    def processing_runs(
        limit: int = Query(default=DEFAULT_RUN_PAGE_SIZE, ge=1, le=MAX_RUN_PAGE_SIZE),
        offset: int = Query(default=0, ge=0, le=MAX_PAGE_OFFSET),
    ) -> APIResponse[dict[str, Any]]:
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(
                list_processing_run_summaries(
                    configured_database_path, limit=limit, offset=offset
                )
            )
        )

    @app.get(
        "/api/v1/processing-runs/{run_id}",
        response_model=APIResponse[dict[str, Any]],
    )
    def processing_run_detail(
        run_id: str,
        session_limit: int = Query(
            default=DEFAULT_SESSION_PAGE_SIZE, ge=1, le=MAX_CHILD_PAGE_SIZE
        ),
        session_offset: int = Query(default=0, ge=0, le=MAX_PAGE_OFFSET),
        attempt_limit: int = Query(
            default=DEFAULT_ATTEMPT_PAGE_SIZE, ge=1, le=MAX_CHILD_PAGE_SIZE
        ),
        attempt_offset: int = Query(default=0, ge=0, le=MAX_PAGE_OFFSET),
    ) -> APIResponse[dict[str, Any]]:
        result = get_processing_run_detail(
            configured_database_path,
            run_id,
            session_limit=session_limit,
            session_offset=session_offset,
            attempt_limit=attempt_limit,
            attempt_offset=attempt_offset,
        )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="processing_run_unavailable"
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(result)
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
        "/api/v1/attempts/{attempt_key}/quality",
        response_model=APIResponse[dict[str, Any]],
    )
    def attempt_quality(attempt_key: str) -> APIResponse[dict[str, Any]]:
        try:
            result = inspect_attempt_quality(configured_database_path, attempt_key)
        except (ValueError, OSError) as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason=(
                    "attempt_trace_unavailable"
                    if isinstance(exc, OSError)
                    else str(exc)
                ),
            )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="attempt_trace_unavailable",
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(result)
        )

    @app.get(
        "/api/v1/attempts/{attempt_key}/traces",
        response_model=APIResponse[dict[str, Any]],
    )
    def attempt_traces(attempt_key: str) -> APIResponse[dict[str, Any]]:
        try:
            result = load_attempt_trace_chart_preview(
                configured_database_path, attempt_key
            )
        except TraceChartUnavailable as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason=exc.reason_code,
            )
        except (ValueError, OSError):
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="attempt_trace_unavailable",
            )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="attempt_trace_unavailable",
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(result)
        )

    @app.get(
        "/api/v1/attempts/{attempt_key}/trajectory",
        response_model=APIResponse[dict[str, Any]],
    )
    def attempt_trajectory(attempt_key: str) -> APIResponse[dict[str, Any]]:
        try:
            result = load_observed_trajectory_preview(
                configured_database_path, attempt_key
            )
        except TrajectoryPreviewUnavailable as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason=exc.reason_code,
            )
        except (ValueError, OSError):
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="attempt_trace_unavailable",
            )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="attempt_trace_unavailable",
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(result)
        )

    @app.get(
        "/api/v1/attempts/{attempt_key}/regions",
        response_model=APIResponse[dict[str, Any]],
    )
    def attempt_regions(
        attempt_key: str,
        track_model_id: str | None = Query(default=None, min_length=1),
        track_model_revision: int | None = Query(default=None, ge=1),
    ) -> APIResponse[dict[str, Any]]:
        if track_model_id is None or track_model_revision is None:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="track_model_id_and_revision_must_be_selected_together",
            )
        try:
            track_model = resolve_track_model(track_model_id, track_model_revision)
            result = load_attempt_region_report(
                configured_database_path,
                attempt_key,
                track_model,
            )
        except RegionReportUnavailable as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason=exc.reason_code,
            )
        except (ValueError, OSError) as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason=(
                    "attempt_trace_unavailable"
                    if isinstance(exc, OSError)
                    else str(exc)
                ),
            )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="attempt_trace_unavailable",
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(result)
        )

    @app.get(
        "/api/v1/track-models",
        response_model=APIResponse[list[TrackModelRecord]],
    )
    def track_models() -> APIResponse[list[TrackModelRecord]]:
        return APIResponse[list[TrackModelRecord]](
            data=list_registered_track_models()
        )

    @app.get(
        "/api/v1/recording-sources",
        response_model=APIResponse[list[RecordingSourceRecord]],
    )
    def recording_sources() -> APIResponse[list[RecordingSourceRecord]]:
        return APIResponse[list[RecordingSourceRecord]](
            data=list_recording_sources(
                configured_database_path, configured_recordings_root
            )
        )

    @app.get(
        "/api/v1/recordings/current",
        response_model=APIResponse[RecordingJobRecord],
    )
    def current_recording() -> APIResponse[RecordingJobRecord] | JSONResponse:
        try:
            job = recording_controller.current()
        except ValueError as exc:
            return _api_error(503, str(exc))
        return APIResponse[RecordingJobRecord](data=job)

    @app.post(
        "/api/v1/recordings/start",
        response_model=APIResponse[RecordingJobRecord],
        status_code=202,
    )
    def start_recording(
        authorization: str | None = Header(default=None),
    ) -> APIResponse[RecordingJobRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "recording_control_not_authorized")
        try:
            job = recording_controller.start_recording()
        except ValueError as exc:
            reason = str(exc)
            status_code = (
                409
                if reason
                in {
                    "another_local_operation_is_in_progress",
                    "recording_controller_busy",
                }
                else 503
            )
            return _api_error(status_code, reason)
        return APIResponse[RecordingJobRecord](data=job)

    @app.post(
        "/api/v1/recordings/{recording_id}/stop",
        response_model=APIResponse[RecordingJobRecord],
        status_code=202,
    )
    def stop_recording(
        recording_id: str,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[RecordingJobRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "recording_control_not_authorized")
        try:
            job = recording_controller.stop_recording(recording_id)
        except ValueError as exc:
            reason = str(exc)
            status_code = 404 if reason == "recording_unavailable" else 503
            return _api_error(status_code, reason)
        return APIResponse[RecordingJobRecord](data=job)

    @app.post(
        "/api/v1/import-jobs",
        response_model=APIResponse[ImportJobRecord],
        status_code=202,
    )
    def create_import(
        request: ImportJobRequest,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[ImportJobRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "import_control_not_authorized")
        try:
            job = import_controller.submit(request.capture_id)
        except ValueError as exc:
            reason = str(exc)
            status_code = (
                409
                if reason
                in {
                    "another_import_is_in_progress",
                    "another_local_operation_is_in_progress",
                }
                else 503
            )
            return _api_error(status_code, reason)
        return APIResponse[ImportJobRecord](data=job)

    @app.get(
        "/api/v1/import-jobs/{job_id}",
        response_model=APIResponse[ImportJobRecord],
    )
    def import_job(job_id: str) -> APIResponse[ImportJobRecord] | JSONResponse:
        job = import_controller.get(job_id)
        if job is None:
            return _api_error(404, "import_job_unavailable")
        return APIResponse[ImportJobRecord](data=job)

    @app.post(
        "/api/v1/import-jobs/{job_id}/retry",
        response_model=APIResponse[ImportJobRecord],
        status_code=202,
    )
    def retry_import(
        job_id: str,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[ImportJobRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "import_control_not_authorized")
        try:
            job = import_controller.retry(job_id)
        except ValueError as exc:
            reason = str(exc)
            status_code = (
                409
                if reason
                in {
                    "another_import_is_in_progress",
                    "another_local_operation_is_in_progress",
                    "import_job_not_retryable",
                }
                else 503
            )
            return _api_error(status_code, reason)
        return APIResponse[ImportJobRecord](data=job)

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


def _authorized(authorization: str | None, control_token: str | None) -> bool:
    if not authorization or not control_token:
        return False
    scheme, separator, supplied = authorization.partition(" ")
    return bool(
        separator
        and scheme.lower() == "bearer"
        and hmac.compare_digest(supplied, control_token)
    )


def _api_error(status_code: int, reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "api_version": "v1",
            "status": "unavailable",
            "data": None,
            "reason": reason,
        },
    )


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
