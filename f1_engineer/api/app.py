from __future__ import annotations

import hmac
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Generic, Literal, TypeVar

from fastapi import FastAPI, Header, Path as ApiPath, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..analysis.reference_selection import ReferenceKind, ReferenceRequest, select_reference
from ..analysis.session_best import assess_session_best
from ..analysis.comparison_window import optional_distance_window
from ..analysis.engineer_query import query_engineer_evidence
from ..analysis.observation_set import build_observation_set
from ..analysis.paired_region_service import (
    PairedRegionReportUnavailable,
    compare_attempt_regions,
)
from ..analysis.quality import inspect_attempt_quality
from ..analysis.region_service import RegionReportUnavailable, load_attempt_region_report
from ..analysis.service import compare_attempts
from ..analysis.trajectory import TrajectoryPreviewUnavailable
from ..analysis.trajectory_comparison import TrajectoryComparisonUnavailable
from ..analysis.trajectory_comparison_service import compare_observed_trajectories
from ..analysis.trajectory_service import load_observed_trajectory_preview
from ..analysis.trace_chart_service import (
    TraceChartUnavailable,
    load_attempt_trace_chart_preview,
)
from ..tracks.draft_authoring import (
    MAX_DRAFT_MODEL_REQUEST_BYTES,
    build_draft_track_model,
)
from ..storage.import_jobs import list_recording_sources
from ..storage.importer import (
    DEFAULT_DATABASE,
    MAX_LAP_ATTEMPT_PAGE_OFFSET,
    list_lap_attempt_page,
    list_laps,
    list_sessions,
)
from ..storage.query import (
    list_car_observation_inventory,
    load_attempt_timing_evidence,
    load_car_observation_preview,
)
from ..storage.run_summaries import (
    ArchiveFilterLimitExceeded,
    DEFAULT_ATTEMPT_PAGE_SIZE,
    DEFAULT_LIFECYCLE_EVENT_PAGE_SIZE,
    DEFAULT_RUN_PAGE_SIZE,
    DEFAULT_SESSION_PAGE_SIZE,
    MAX_CHILD_PAGE_SIZE,
    MAX_PAGE_OFFSET,
    MAX_RUN_PAGE_SIZE,
    RunArchiveFilters,
    get_processing_run_detail,
    list_processing_run_summaries,
    list_processing_run_lifecycle_events,
)
from ..storage.database import DatabaseSchemaError
from ..tracks.model import TrackModel
from ..tracks.registry import (
    TrackModelCatalog,
    load_track_model_catalog,
    list_track_models as list_registered_track_models,
)
from .import_controller import ImportController
from .replay_controller import ReplayController
from .recording_controller import RecordingController

REPLAY_CONTROL_CONFLICT_REASONS = {
    "replay_not_playing",
    "replay_not_paused",
    "replay_step_in_progress",
}


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
    origin: Literal["packaged", "local_draft", "reviewed"]
    content_sha256: str
    source_filename: str
    source_kind: Literal["package_artifact", "diagnostic_draft", "review_bundle"]
    model_content_sha256: str
    bundle_content_sha256: str | None
    approved_for_candidate_ranking: bool
    review: dict[str, Any] | None


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


class AttemptSummaryQueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Literal["attempt_summary"]
    target_attempt_key: str = Field(min_length=1, max_length=256)


class RegionComparisonQueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Literal["region_comparison"]
    target_attempt_key: str = Field(min_length=1, max_length=256)
    reference_attempt_key: str = Field(min_length=1, max_length=256)
    comparison_policy: Literal["time_trial", "practice_qualifying"]
    track_model_id: str = Field(min_length=1, max_length=256)
    track_model_revision: int = Field(ge=1)
    region_identifier: str = Field(min_length=1, max_length=256)


EngineerQueryBody = Annotated[
    AttemptSummaryQueryBody | RegionComparisonQueryBody,
    Field(discriminator="intent"),
]


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


class LiveCarStatusRecord(BaseModel):
    status: Literal["waiting", "fresh", "stale", "unsupported", "unavailable"]
    reason: str | None
    age_ms: int | None
    session_uid: str | None = None
    frame_identifier: int | None = None
    packet_format: int | None = None
    player_car_index: int | None = None
    fuel_in_tank_reported: float | None = None
    fuel_remaining_laps: float | None = None
    actual_tyre_compound: int | None = None
    visual_tyre_compound: int | None = None
    tyre_age_laps: int | None = None
    front_brake_bias_percent: int | None = None
    pit_limiter_active: bool | None = None
    validation_flags: list[str] = Field(default_factory=list)


class LiveLapTimingRecord(BaseModel):
    status: Literal["waiting", "fresh", "stale", "unsupported", "unavailable"]
    reason: str | None
    age_ms: int | None
    session_uid: str | None = None
    frame_identifier: int | None = None
    packet_format: int | None = None
    player_car_index: int | None = None
    lap_number: int | None = None
    current_lap_time_ms: int | None = None
    current_sector: int | None = None
    previous_lap_time_ms: int | None = None
    sector1_time_ms: int | None = None
    sector2_time_ms: int | None = None
    validation_flags: list[str] = Field(default_factory=list)


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
    live_car_status: LiveCarStatusRecord | None = None
    live_lap_timing: LiveLapTimingRecord | None = None


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


class ReplayStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    speed: float = Field(default=1.0, allow_inf_nan=False)


class ReplayRecord(BaseModel):
    source_kind: Literal["replay"]
    playback_id: str
    capture_id: str
    capture_name: str
    speed: float
    state: Literal[
        "starting",
        "playing",
        "pausing",
        "paused",
        "resuming",
        "stepping",
        "stopping",
        "stopped",
        "completed",
        "failed",
    ]
    elapsed_ms: int
    datagrams_delivered: int
    capture_complete: bool | None
    capture_completion: dict[str, Any] | None
    source_stable: bool | None
    latest_context: dict[str, Any] | None
    live_telemetry: LiveTelemetryRecord
    live_car_status: LiveCarStatusRecord
    live_lap_timing: LiveLapTimingRecord
    failure_reason: str | None


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
    start_frame_ordinal: int | None
    end_frame_ordinal: int | None
    superseded: bool | None
    lifecycle_assessed: bool
    start_observed: bool
    pit_encountered: bool
    sample_count: int
    trace_row_count: int
    trace_schema_version: int
    trace_sha256: str
    context: dict[str, Any] | None
    quality: dict[str, Any]
    exclusion_reasons: list[str]
    timing_evidence: dict[str, Any]
    player_participant_context: dict[str, Any] | None = None
    player_car_setup_context: dict[str, Any] | None = None


class SelectedLapAttempt(BaseModel):
    requested_attempt_key: str
    attempt: LapRecord | None


class LapAttemptPage(BaseModel):
    run_id: str
    session_uid: str
    items: list[LapRecord]
    total: int
    limit: int
    offset: int
    selected_attempts: list[SelectedLapAttempt]


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


class DraftRegionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    identifier: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=96)
    start_distance_m: int | float
    end_distance_m: int | float
    braking_search_window_m: tuple[int | float, int | float] | None = None
    turn_in_search_window_m: tuple[int | float, int | float] | None = None
    throttle_pickup_window_m: tuple[int | float, int | float] | None = None


class DraftTrackModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_attempt_key: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    layout_id: str = Field(min_length=1, max_length=128)
    regions: list[DraftRegionBody] = Field(min_length=1, max_length=64)


def _draft_track_model_request_schema() -> dict[str, Any]:
    schema = DraftTrackModelBody.model_json_schema()
    definitions = schema.pop("$defs", {})
    region_schema = definitions.get("DraftRegionBody")
    if isinstance(region_schema, dict):
        schema["properties"]["regions"]["items"] = region_schema
    return schema


class RunArchiveFilterValidationError(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.reason = f"invalid_archive_filter_{field}"


def _run_archive_filters(request: Request) -> RunArchiveFilters:
    fields = (
        "q",
        "packet_format",
        "track_id",
        "session_category",
        "started_from",
        "started_through",
    )
    values: dict[str, str | None] = {}
    for field in fields:
        entries = request.query_params.getlist(field)
        if len(entries) > 1:
            raise RunArchiveFilterValidationError(field)
        value = entries[0].strip() if entries else ""
        values[field] = value or None

    query = values["q"]
    if query is not None:
        if re.fullmatch(r"[0-9a-fA-F]{3,64}", query) is None:
            raise RunArchiveFilterValidationError("q")
        query = query.lower()

    packet_format_value = values["packet_format"]
    track_id_value = values["track_id"]
    if (packet_format_value is None) != (track_id_value is None):
        raise RunArchiveFilterValidationError("track_context")
    packet_format: int | None = None
    track_id: int | None = None
    if packet_format_value is not None and track_id_value is not None:
        if packet_format_value not in {"2025", "2026"}:
            raise RunArchiveFilterValidationError("packet_format")
        if re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", track_id_value) is None:
            raise RunArchiveFilterValidationError("track_id")
        parsed_track_id = int(track_id_value)
        if parsed_track_id > 127:
            raise RunArchiveFilterValidationError("track_id")
        packet_format = int(packet_format_value)
        track_id = parsed_track_id

    category_value = values["session_category"]
    categories = {"time_trial", "practice", "qualifying", "race", "unknown"}
    category = category_value.lower() if category_value is not None else None
    if category is not None and category not in categories:
        raise RunArchiveFilterValidationError("session_category")

    dates: dict[str, str | None] = {}
    for field in ("started_from", "started_through"):
        value = values[field]
        if value is not None:
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
                raise RunArchiveFilterValidationError(field)
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise RunArchiveFilterValidationError(field) from exc
        dates[field] = value
    if (
        dates["started_from"] is not None
        and dates["started_through"] is not None
        and dates["started_from"] > dates["started_through"]
    ):
        raise RunArchiveFilterValidationError("date_range")

    return RunArchiveFilters(
        q=query,
        packet_format=packet_format,
        track_id=track_id,
        session_category=category,
        started_from=dates["started_from"],
        started_through=dates["started_through"],
    )


def create_app(
    database_path: str | Path = DEFAULT_DATABASE,
    *,
    recordings_root: str | Path = "recordings",
    control_token: str | None = None,
    recording_host: str = "0.0.0.0",
    recording_port: int = 20777,
    recording_queue_size: int = 8192,
    track_models_root: str | Path | None = None,
    reviewed_track_models_root: str | Path | None = None,
) -> FastAPI:
    """Create a local API bound to operator-configured storage and recording roots."""
    configured_database_path = Path(database_path).expanduser().resolve()
    configured_recordings_root = Path(recordings_root).expanduser().resolve()
    selected_track_models_root = (
        track_models_root
        if track_models_root is not None
        else os.environ.get("F1_ENGINEER_TRACK_MODELS_ROOT") or None
    )
    selected_reviewed_track_models_root = (
        reviewed_track_models_root
        if reviewed_track_models_root is not None
        else os.environ.get("F1_ENGINEER_REVIEWED_TRACK_MODELS_ROOT") or None
    )
    track_model_catalog: TrackModelCatalog = load_track_model_catalog(
        selected_track_models_root, selected_reviewed_track_models_root
    )
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
    replay_controller = ReplayController(
        configured_database_path,
        configured_recordings_root,
        import_controller,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import_controller.start()
        recording_controller.start()
        replay_controller.start()
        app.state.import_controller = import_controller
        app.state.recording_controller = recording_controller
        app.state.replay_controller = replay_controller
        try:
            yield
        finally:
            replay_controller.close()
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
        request: Request,
        limit: int = Query(default=DEFAULT_RUN_PAGE_SIZE, ge=1, le=MAX_RUN_PAGE_SIZE),
        offset: int = Query(default=0, ge=0, le=MAX_PAGE_OFFSET),
    ) -> APIResponse[dict[str, Any]] | JSONResponse:
        try:
            filters = _run_archive_filters(request)
        except RunArchiveFilterValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "api_version": "v1",
                    "status": "unavailable",
                    "data": None,
                    "reason": exc.reason,
                },
            )
        try:
            page = list_processing_run_summaries(
                configured_database_path,
                limit=limit,
                offset=offset,
                filters=filters,
            )
        except ArchiveFilterLimitExceeded:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="archive_filter_limit_exceeded"
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(page)
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
        lifecycle_event_limit: int = Query(
            default=DEFAULT_LIFECYCLE_EVENT_PAGE_SIZE, ge=1, le=MAX_CHILD_PAGE_SIZE
        ),
        lifecycle_event_offset: int = Query(default=0, ge=0, le=MAX_PAGE_OFFSET),
    ) -> APIResponse[dict[str, Any]]:
        result = get_processing_run_detail(
            configured_database_path,
            run_id,
            session_limit=session_limit,
            session_offset=session_offset,
            attempt_limit=attempt_limit,
            attempt_offset=attempt_offset,
            lifecycle_event_limit=lifecycle_event_limit,
            lifecycle_event_offset=lifecycle_event_offset,
        )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="processing_run_unavailable"
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(result)
        )

    @app.get(
        "/api/v1/processing-runs/{run_id}/lifecycle-events",
        response_model=APIResponse[dict[str, Any]],
    )
    def processing_run_lifecycle_events(
        run_id: str,
        limit: int = Query(default=DEFAULT_LIFECYCLE_EVENT_PAGE_SIZE, ge=1, le=MAX_CHILD_PAGE_SIZE),
        offset: int = Query(default=0, ge=0, le=MAX_PAGE_OFFSET),
    ) -> APIResponse[dict[str, Any]]:
        result = list_processing_run_lifecycle_events(
            configured_database_path, run_id, limit=limit, offset=offset
        )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="processing_run_unavailable"
            )
        return APIResponse[dict[str, Any]](data=_stringify_session_uids(result))

    @app.get(
        "/api/v1/processing-runs/{run_id}/sessions/{session_uid}/cars",
        response_model=APIResponse[dict[str, Any]],
    )
    def car_observation_inventory(
        run_id: str,
        session_uid: str,
        limit: int = Query(default=24, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
    ) -> APIResponse[dict[str, Any]]:
        try:
            result = list_car_observation_inventory(
                configured_database_path,
                run_id,
                session_uid,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason=str(exc)
            )
        except OSError:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="car_observation_inventory_unavailable"
            )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="car_observation_inventory_unavailable"
            )
        return APIResponse[dict[str, Any]](data=_stringify_session_uids(result))

    @app.get(
        "/api/v1/processing-runs/{run_id}/sessions/{session_uid}/cars/{car_index}/observations",
        response_model=APIResponse[dict[str, Any]],
    )
    def car_observation_preview(
        run_id: str,
        session_uid: str,
        car_index: int = ApiPath(ge=0, le=23),
        limit: int = Query(default=200, ge=1, le=500),
        offset: int = Query(default=0, ge=0, le=100_000),
    ) -> APIResponse[dict[str, Any]]:
        try:
            result = load_car_observation_preview(
                configured_database_path,
                run_id,
                session_uid,
                car_index,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason=str(exc)
            )
        except OSError:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="car_observation_preview_unavailable"
            )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="car_observation_preview_unavailable"
            )
        return APIResponse[dict[str, Any]](data=_stringify_session_uids(result))

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
        "/api/v1/processing-runs/{run_id}/sessions/{session_uid}/lap-attempts",
        response_model=APIResponse[LapAttemptPage],
    )
    def comparison_lap_attempts(
        run_id: str,
        session_uid: str,
        limit: int = Query(default=50, ge=1, le=MAX_CHILD_PAGE_SIZE),
        offset: int = Query(default=0, ge=0, le=MAX_LAP_ATTEMPT_PAGE_OFFSET),
        selected_target_attempt_key: str | None = Query(
            default=None, min_length=1, max_length=256
        ),
        selected_reference_attempt_key: str | None = Query(
            default=None, min_length=1, max_length=256
        ),
    ) -> APIResponse[LapAttemptPage]:
        selected_keys = tuple(
            key
            for key in (selected_target_attempt_key, selected_reference_attempt_key)
            if key is not None
        )
        result = list_lap_attempt_page(
            configured_database_path,
            run_id=run_id,
            session_uid=session_uid,
            limit=limit,
            offset=offset,
            selected_attempt_keys=selected_keys,
        )
        return APIResponse[LapAttemptPage](
            data=_stringify_session_uids(result)
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
        "/api/v1/attempts/{attempt_key}/timing",
        response_model=APIResponse[dict[str, Any]],
    )
    def attempt_timing(attempt_key: str) -> APIResponse[dict[str, Any]]:
        result = load_attempt_timing_evidence(
            configured_database_path, attempt_key
        )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="attempt_timing_evidence_unavailable"
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
            model_entry = track_model_catalog.resolve_entry(
                track_model_id, track_model_revision
            )
            result = load_attempt_region_report(
                configured_database_path,
                attempt_key,
                model_entry.model,
                model_metadata=model_entry.metadata(),
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
            data=list_registered_track_models(track_model_catalog)
        )

    @app.post(
        "/api/v1/track-models/draft",
        response_model=APIResponse[dict[str, Any]],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": _draft_track_model_request_schema()
                    }
                },
            }
        },
    )
    async def draft_track_model(
        request: Request,
    ) -> APIResponse[dict[str, Any]]:
        declared_length = request.headers.get("content-length")
        if declared_length is not None:
            try:
                if int(declared_length) > MAX_DRAFT_MODEL_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "api_version": "v1",
                            "status": "unavailable",
                            "data": None,
                            "reason": "draft_model_request_size_limit_exceeded",
                        },
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "api_version": "v1",
                        "status": "unavailable",
                        "data": None,
                        "reason": "invalid_content_length",
                    },
                )
        body_bytes = bytearray()
        async for chunk in request.stream():
            if len(body_bytes) + len(chunk) > MAX_DRAFT_MODEL_REQUEST_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "api_version": "v1",
                        "status": "unavailable",
                        "data": None,
                        "reason": "draft_model_request_size_limit_exceeded",
                    },
                )
            body_bytes.extend(chunk)
        try:
            body = DraftTrackModelBody.model_validate_json(bytes(body_bytes))
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc
        if len(body.model_dump_json().encode("utf-8")) > MAX_DRAFT_MODEL_REQUEST_BYTES:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="draft_model_request_size_limit_exceeded",
            )
        try:
            result = await run_in_threadpool(
                build_draft_track_model,
                configured_database_path,
                body.source_attempt_key,
                model_id=body.model_id,
                revision=body.revision,
                layout_id=body.layout_id,
                regions=[region.model_dump() for region in body.regions],
            )
        except DatabaseSchemaError:
            raise
        except (OSError, sqlite3.Error):
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="draft_model_source_unavailable",
            )
        except ValueError as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason=str(exc),
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(result)
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

    @app.get(
        "/api/v1/replays/current",
        response_model=APIResponse[ReplayRecord],
    )
    def current_replay() -> APIResponse[ReplayRecord] | JSONResponse:
        try:
            playback = replay_controller.current()
        except ValueError as exc:
            return _api_error(503, str(exc))
        return APIResponse[ReplayRecord](data=playback)

    @app.post(
        "/api/v1/replays/start",
        response_model=APIResponse[ReplayRecord],
        status_code=202,
    )
    def start_replay(
        request: ReplayStartRequest,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[ReplayRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "replay_control_not_authorized")
        try:
            playback = replay_controller.start_replay(
                request.capture_id, speed=request.speed
            )
        except ValueError as exc:
            reason = str(exc)
            status_code = (
                409
                if reason == "another_local_operation_is_in_progress"
                else 404
                if reason == "capture_id_unavailable"
                else 422
                if reason == "replay_speed_unsupported"
                else 503
            )
            return _api_error(status_code, reason)
        return APIResponse[ReplayRecord](data=playback)

    @app.post(
        "/api/v1/replays/{playback_id}/stop",
        response_model=APIResponse[ReplayRecord],
        status_code=202,
    )
    def stop_replay(
        playback_id: str,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[ReplayRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "replay_control_not_authorized")
        try:
            playback = replay_controller.stop_replay(playback_id)
        except ValueError as exc:
            reason = str(exc)
            status_code = 404 if reason == "playback_unavailable" else 503
            return _api_error(status_code, reason)
        return APIResponse[ReplayRecord](data=playback)

    @app.post(
        "/api/v1/replays/{playback_id}/pause",
        response_model=APIResponse[ReplayRecord],
        status_code=202,
    )
    def pause_replay(
        playback_id: str,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[ReplayRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "replay_control_not_authorized")
        try:
            playback = replay_controller.pause_replay(playback_id)
        except ValueError as exc:
            reason = str(exc)
            status_code = (
                404
                if reason == "playback_unavailable"
                else 409
                if reason in REPLAY_CONTROL_CONFLICT_REASONS
                else 503
            )
            return _api_error(status_code, reason)
        return APIResponse[ReplayRecord](data=playback)

    @app.post(
        "/api/v1/replays/{playback_id}/resume",
        response_model=APIResponse[ReplayRecord],
        status_code=202,
    )
    def resume_replay(
        playback_id: str,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[ReplayRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "replay_control_not_authorized")
        try:
            playback = replay_controller.resume_replay(playback_id)
        except ValueError as exc:
            reason = str(exc)
            status_code = (
                404
                if reason == "playback_unavailable"
                else 409
                if reason in REPLAY_CONTROL_CONFLICT_REASONS
                else 503
            )
            return _api_error(status_code, reason)
        return APIResponse[ReplayRecord](data=playback)

    @app.post(
        "/api/v1/replays/{playback_id}/step",
        response_model=APIResponse[ReplayRecord],
        status_code=202,
    )
    def step_replay(
        playback_id: str,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[ReplayRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "replay_control_not_authorized")
        try:
            playback = replay_controller.step_replay(playback_id)
        except ValueError as exc:
            reason = str(exc)
            status_code = (
                404
                if reason == "playback_unavailable"
                else 409
                if reason in REPLAY_CONTROL_CONFLICT_REASONS
                else 503
            )
            return _api_error(status_code, reason)
        return APIResponse[ReplayRecord](data=playback)

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

    @app.get(
        "/api/v1/compare/trajectories",
        response_model=APIResponse[dict[str, Any]],
    )
    def compare_trajectories(
        target_attempt_key: str = Query(min_length=1),
        reference_attempt_key: str = Query(min_length=1),
        comparison_policy: Literal["time_trial", "practice_qualifying"] = "time_trial",
        position_probe_m: float | None = Query(default=None),
    ) -> APIResponse[dict[str, Any]]:
        try:
            comparison_options: dict[str, object] = {
                "policy": comparison_policy,
            }
            if position_probe_m is not None:
                comparison_options["position_probe_m"] = position_probe_m
            result = compare_observed_trajectories(
                configured_database_path,
                target_attempt_key,
                reference_attempt_key,
                **comparison_options,
            )
        except DatabaseSchemaError:
            raise
        except TrajectoryComparisonUnavailable as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason=exc.reason_code
            )
        except OSError:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="attempt_trace_unavailable"
            )
        except ValueError as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason=str(exc)
            )
        return APIResponse[dict[str, Any]](data=_stringify_session_uids(result))

    @app.get("/api/v1/compare/laps", response_model=APIResponse[dict[str, Any]])
    def compare_laps(
        target_attempt_key: str = Query(min_length=1),
        reference_attempt_key: str = Query(min_length=1),
        comparison_policy: Literal["time_trial", "practice_qualifying"] = "time_trial",
        track_model_id: str | None = Query(default=None, min_length=1),
        track_model_revision: int | None = Query(default=None, ge=1),
        window_start_m: float | None = Query(default=None),
        window_end_m: float | None = Query(default=None),
    ) -> APIResponse[dict[str, Any]]:
        if (track_model_id is None) != (track_model_revision is None):
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="track_model_id_and_revision_must_be_selected_together",
            )
        try:
            model_entry = (
                track_model_catalog.resolve_entry(track_model_id, track_model_revision)
                if track_model_id is not None and track_model_revision is not None
                else None
            )
            if model_entry is not None and model_entry.origin == "local_draft":
                raise ValueError("local_draft_track_model_not_available_for_comparison")
            track_model: TrackModel | None = model_entry.model if model_entry else None
            distance_window = optional_distance_window(window_start_m, window_end_m)
            result = compare_attempts(
                configured_database_path,
                target_attempt_key,
                reference_attempt_key,
                track_model=track_model,
                track_model_catalog=track_model_catalog,
                policy=comparison_policy,
                distance_window=distance_window,
            )
        except DatabaseSchemaError:
            raise
        except OSError:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="attempt_trace_unavailable",
            )
        except ValueError as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason=str(exc),
            )
        return APIResponse[dict[str, Any]](data=_stringify_session_uids(result))

    @app.get("/api/v1/compare/regions", response_model=APIResponse[dict[str, Any]])
    def compare_regions(
        target_attempt_key: str = Query(min_length=1),
        reference_attempt_key: str = Query(min_length=1),
        comparison_policy: Literal["time_trial", "practice_qualifying"] = "time_trial",
        track_model_id: str | None = Query(default=None, min_length=1),
        track_model_revision: int | None = Query(default=None, ge=1),
    ) -> APIResponse[dict[str, Any]]:
        if track_model_id is None or track_model_revision is None:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="track_model_id_and_revision_must_be_selected_together",
            )
        try:
            model_entry = track_model_catalog.resolve_entry(
                track_model_id, track_model_revision
            )
            result = compare_attempt_regions(
                configured_database_path,
                target_attempt_key,
                reference_attempt_key,
                model_entry.model,
                model_metadata=model_entry.metadata(),
                policy=comparison_policy,
            )
        except DatabaseSchemaError:
            raise
        except PairedRegionReportUnavailable as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason=exc.reason_code
            )
        except OSError:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="attempt_trace_unavailable"
            )
        except ValueError as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason=str(exc)
            )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="attempt_trace_unavailable"
            )
        return APIResponse[dict[str, Any]](data=_stringify_session_uids(result))

    @app.post(
        "/api/v1/engineer/query",
        response_model=APIResponse[dict[str, Any]],
    )
    def engineer_query(request: EngineerQueryBody) -> APIResponse[dict[str, Any]]:
        try:
            result = query_engineer_evidence(
                configured_database_path,
                request.model_dump(),
                track_model_catalog=track_model_catalog,
            )
        except DatabaseSchemaError:
            raise
        except (OSError, sqlite3.Error):
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="engineer_query_source_unavailable"
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(result)
        )

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

    @app.get(
        "/api/v1/analysis/session-best",
        response_model=APIResponse[dict[str, Any]],
    )
    def session_best_overview(
        anchor_attempt_key: str = Query(min_length=1),
    ) -> APIResponse[dict[str, Any]]:
        try:
            result = assess_session_best(
                configured_database_path,
                anchor_attempt_key,
            )
        except DatabaseSchemaError:
            raise
        except (OSError, sqlite3.Error):
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="session_best_source_unavailable"
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(result.to_dict())
        )

    @app.get(
        "/api/v1/analysis/observation-set",
        response_model=APIResponse[dict[str, Any]],
    )
    def observation_set(
        attempt_key: list[str] = Query(default=[], alias="attempt_key", max_length=8),
        comparison_policy: Literal["time_trial", "practice_qualifying"] = "time_trial",
        window_start_m: float | None = Query(default=None),
        window_end_m: float | None = Query(default=None),
    ) -> APIResponse[dict[str, Any]]:
        try:
            distance_window = optional_distance_window(window_start_m, window_end_m)
            if distance_window is None:
                return APIResponse[dict[str, Any]](
                    status="unavailable", reason="observation_set_window_required"
                )
            result = build_observation_set(
                configured_database_path,
                attempt_key,
                distance_window,
                policy=comparison_policy,
            )
        except DatabaseSchemaError:
            raise
        except (OSError, sqlite3.Error):
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="observation_set_source_unavailable"
            )
        except ValueError as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason=str(exc)
            )
        return APIResponse[dict[str, Any]](data=_stringify_session_uids(result))

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
