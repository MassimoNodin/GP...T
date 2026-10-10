from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Generic, Literal, TypeVar

from fastapi import FastAPI, Header, Path as ApiPath, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..analysis.reference_selection import ReferenceKind, ReferenceRequest, select_reference
from ..analysis.session_comparison import compare_session_laps
from ..processing.evidence import EvidenceStore, EvidenceUnavailable
from ..processing.legacy import archived_session_page
from ..processing.runtime import LiveSessionRuntime
from ..analysis.ai_admission import AIGateLease, PROCESS_ENGINEER_AI_GATE
from ..analysis.local_speech import (
    LocalSpeechRuntime,
    LocalSpeechUnavailable,
    SPEECH_MAX_AUDIO_BYTES,
    SPEECH_MAX_RESPONSE_BYTES,
    validate_canonical_wav,
)
from ..analysis.session_best import assess_session_best
from ..analysis.comparison_window import optional_distance_window
from ..analysis.engineer_query import query_engineer_evidence
from ..analysis.engineer_ask_service import answer_engineer_question
from ..analysis.ollama_runtime import OllamaRuntime, OllamaUnavailable
from ..analysis.observation_set import build_observation_set
from ..analysis.paired_region_service import (
    PairedRegionReportUnavailable,
    compare_attempt_regions,
)
from ..analysis.quality import inspect_attempt_quality_web
from ..analysis.region_service import RegionReportUnavailable, load_attempt_region_report
from ..analysis.service import compare_attempts
from ..analysis.car_slot_comparison import compare_player_slot_window
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
from ..storage.import_jobs import (
    RecordingCatalogUnavailable,
    list_recording_sources,
    list_recording_sources_page,
)
from ..storage.importer import (
    DEFAULT_DATABASE,
    MAX_LAP_ATTEMPT_PAGE_OFFSET,
    list_lap_attempt_page,
    list_laps,
    list_sessions,
)
from ..storage.query import (
    load_car_lap_inventory_page,
    load_car_lap_observation_page,
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
from ..storage.artifacts import (
    RunArtifactInventoryUnavailable,
    list_processing_run_artifacts,
)
from ..storage.usage import measure_storage_usage
from ..storage.recording_download import (
    DEFAULT_MAX_RECORDING_DOWNLOAD_BYTES,
    RecordingDownloadError,
    RecordingDownloadService,
)
from ..storage.recording_upload import (
    DEFAULT_MAX_RECORDING_UPLOAD_BYTES,
    RecordingUploadError,
    RecordingUploadService,
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
from .recording_download import (
    RecordingDownloadResponse,
    recording_download_content_disposition,
)
from .recording_upload import RecordingUploadTransferError, receive_recording_upload

REPLAY_CONTROL_CONFLICT_REASONS = {
    "replay_not_playing",
    "replay_not_paused",
    "replay_step_in_progress",
}
_ENGINEER_SPEECH_REQUEST_ID = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
)
_ENGINEER_SPEECH_SHA256 = re.compile(r"^[a-f0-9]{64}$")


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


class RecordingSourceCatalogRecord(RecordingSourceRecord):
    download_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class RecordingSourceFiltersRecord(BaseModel):
    query: str = Field(max_length=128)
    latest_job_status: Literal[
        "all", "none", "queued", "running", "complete", "failed", "interrupted", "cancelled"
    ]
    availability: Literal["all", "available", "missing"]
    selected_capture_id: str | None


class RecordingSourcePageRecord(BaseModel):
    items: list[RecordingSourceCatalogRecord] = Field(max_length=50)
    selected_capture: RecordingSourceCatalogRecord | None
    total_count: int = Field(ge=0, le=10_000)
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0, le=100_000)
    has_more: bool
    filters: RecordingSourceFiltersRecord


class StorageScopeRecord(BaseModel):
    status: Literal["available", "unavailable"]
    logical_bytes: int | None
    regular_file_count: int | None
    excluded_entry_count: int | None
    reason: str | None


class StorageVolumeRecord(BaseModel):
    status: Literal["available", "unavailable"]
    total_bytes: int | None
    free_bytes: int | None
    reason: str | None


class StorageScopesRecord(BaseModel):
    database: StorageScopeRecord
    finalized_captures: StorageScopeRecord
    recorder_staging: StorageScopeRecord
    imported_traces: StorageScopeRecord


class StorageVolumesRecord(BaseModel):
    database_location: StorageVolumeRecord
    recordings_location: StorageVolumeRecord


class StorageUsageRecord(BaseModel):
    measurement_started_at_utc: str
    measurement_completed_at_utc: str
    measurement_note: str
    scopes: StorageScopesRecord
    volumes: StorageVolumesRecord


class TelemetryServiceRecord(BaseModel):
    observed_at_utc: str = Field(max_length=40)
    udp_bind_host: str | None = Field(max_length=256)
    udp_port: int | None = Field(ge=1, le=65535)
    receive_queue_size: int | None = Field(ge=1, le=1_000_000)
    configuration_available: bool
    controller_ready: bool
    operation_reservation: Literal[
        "idle", "recording", "import", "replay", "upload", "unavailable"
    ]
    unavailable_reason: Literal[
        "telemetry_configuration_unsupported", "recording_controller_unavailable"
    ] | None


class ImportProgressRecord(BaseModel):
    phase: str
    packets_processed: int
    bytes_read: int
    total_bytes: int


class ImportJobRecord(BaseModel):
    job_id: str
    capture_id: str
    status: Literal["queued", "running", "complete", "failed", "interrupted", "cancelled"]
    phase: str
    attempt_count: int
    created_at_utc: str
    updated_at_utc: str
    started_at_utc: str | None
    finished_at_utc: str | None
    result: dict[str, Any] | None
    failure_reason: str | None
    progress: ImportProgressRecord | None = None
    source_root_namespace: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_metadata_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ImportQueueJobRecord(BaseModel):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    capture_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal["queued", "running"]
    phase: str = Field(max_length=64)
    attempt_count: int = Field(ge=1)
    created_at_utc: str = Field(max_length=40)
    updated_at_utc: str = Field(max_length=40)
    queue_position: int | None = Field(default=None, ge=1, le=16)


class ImportQueueRecord(BaseModel):
    waiting_count: int = Field(ge=0, le=16)
    running_job: ImportQueueJobRecord | None
    waiting_jobs: list[ImportQueueJobRecord] = Field(max_length=16)
    blocking_reservation: Literal["idle", "recording", "import", "replay", "upload"]


class ImportJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    queue_if_busy: bool = False


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


class EngineerAskBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=1_024)
    selection: EngineerQueryBody


class LiveTelemetryRecord(BaseModel):
    status: Literal["waiting", "fresh", "stale", "unsupported", "unavailable"]
    reason: str | None
    age_ms: int | None
    source_epoch: str | None = Field(default=None, max_length=64)
    session_uid: str | None = None
    frame_identifier: int | None = None
    packet_format: int | None = None
    player_car_index: int | None = None
    session_time_s: float | None = Field(default=None, ge=0, le=86_400)
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
    engine_temperature_c: Annotated[
        int, Field(strict=True, ge=0, le=65_535)
    ] | None = None
    brake_temperature_c: tuple[
        Annotated[int, Field(strict=True, ge=0, le=65_535)],
        Annotated[int, Field(strict=True, ge=0, le=65_535)],
        Annotated[int, Field(strict=True, ge=0, le=65_535)],
        Annotated[int, Field(strict=True, ge=0, le=65_535)],
    ] | None = None
    tyre_surface_temperature_c: tuple[
        Annotated[int, Field(strict=True, ge=0, le=255)],
        Annotated[int, Field(strict=True, ge=0, le=255)],
        Annotated[int, Field(strict=True, ge=0, le=255)],
        Annotated[int, Field(strict=True, ge=0, le=255)],
    ] | None = None
    tyre_inner_temperature_c: tuple[
        Annotated[int, Field(strict=True, ge=0, le=255)],
        Annotated[int, Field(strict=True, ge=0, le=255)],
        Annotated[int, Field(strict=True, ge=0, le=255)],
        Annotated[int, Field(strict=True, ge=0, le=255)],
    ] | None = None


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


class LiveCarDamageRecord(BaseModel):
    status: Literal["waiting", "fresh", "stale", "unsupported", "unavailable"]
    reason: str | None
    age_ms: int | None
    observation_count: int = Field(ge=0, le=2_147_483_647)
    session_uid: str | None = None
    frame_identifier: int | None = None
    packet_format: int | None = None
    player_car_index: int | None = None
    session_time_s: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    tyre_wear_percent: tuple[
        Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)] | None,
        Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)] | None,
        Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)] | None,
        Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)] | None,
    ] | None = None
    tyre_damage_percent: tuple[
        Annotated[int, Field(strict=True, ge=0, le=100)] | None,
        Annotated[int, Field(strict=True, ge=0, le=100)] | None,
        Annotated[int, Field(strict=True, ge=0, le=100)] | None,
        Annotated[int, Field(strict=True, ge=0, le=100)] | None,
    ] | None = None
    brake_damage_percent: tuple[
        Annotated[int, Field(strict=True, ge=0, le=100)] | None,
        Annotated[int, Field(strict=True, ge=0, le=100)] | None,
        Annotated[int, Field(strict=True, ge=0, le=100)] | None,
        Annotated[int, Field(strict=True, ge=0, le=100)] | None,
    ] | None = None
    front_left_wing_damage_percent: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None
    front_right_wing_damage_percent: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None
    rear_wing_damage_percent: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None
    engine_damage_percent: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None
    validation_flags: list[str] = Field(default_factory=list, max_length=16)


class LiveCarSetupRecord(BaseModel):
    status: Literal["waiting", "fresh", "stale", "unsupported", "unavailable"]
    reason: str | None
    age_ms: int | None
    observation_count: int = Field(ge=0, le=2_147_483_647)
    session_uid: str | None = None
    frame_identifier: int | None = None
    packet_format: int | None = None
    player_car_index: int | None = None
    session_time_s: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    front_wing: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    rear_wing: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    on_throttle_differential: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    off_throttle_differential: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    front_camber: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    rear_camber: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    front_toe: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    rear_toe: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    front_suspension: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    rear_suspension: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    front_anti_roll_bar: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    rear_anti_roll_bar: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    front_suspension_height: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    rear_suspension_height: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    brake_pressure_percent: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    brake_bias_percent: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    engine_braking_percent: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    rear_left_tyre_pressure_psi: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    rear_right_tyre_pressure_psi: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    front_left_tyre_pressure_psi: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    front_right_tyre_pressure_psi: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    ballast: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    fuel_load: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    next_front_wing_value: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    validation_flags: list[str] = Field(default_factory=list, max_length=16)


class LiveSessionConditionsRecord(BaseModel):
    status: Literal["waiting", "fresh", "stale", "unsupported", "unavailable"]
    reason: str | None
    age_ms: int | None
    observation_count: int = Field(ge=0, le=2_147_483_647)
    session_uid: str | None = None
    frame_identifier: int | None = None
    packet_format: int | None = None
    session_time_s: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    weather_id: Annotated[int, Field(strict=True, ge=0, le=255)] | None = None
    weather_name: str | None = Field(default=None, max_length=32)
    air_temperature_c: Annotated[int, Field(strict=True, ge=-128, le=127)] | None = None
    track_temperature_c: Annotated[int, Field(strict=True, ge=-128, le=127)] | None = None
    validation_flags: list[str] = Field(default_factory=list, max_length=4)


class LiveMotionRecord(BaseModel):
    status: Literal["waiting", "fresh", "stale", "unsupported", "unavailable"]
    reason: str | None
    age_ms: int | None
    observation_count: int = Field(ge=0, le=2_147_483_647)
    session_uid: str | None = None
    frame_identifier: int | None = None
    packet_format: int | None = None
    player_car_index: int | None = None
    session_time_s: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    world_position_m: tuple[
        Annotated[float, Field(strict=True, allow_inf_nan=False)],
        Annotated[float, Field(strict=True, allow_inf_nan=False)],
        Annotated[float, Field(strict=True, allow_inf_nan=False)],
    ] | None = None
    world_velocity_mps: tuple[
        Annotated[float, Field(strict=True, allow_inf_nan=False)],
        Annotated[float, Field(strict=True, allow_inf_nan=False)],
        Annotated[float, Field(strict=True, allow_inf_nan=False)],
    ] | None = None
    validation_flags: list[str] = Field(default_factory=list, max_length=2)


class LiveSessionHistoryLapRecord(BaseModel):
    lap_index: Annotated[int, Field(strict=True, ge=0, le=99)]
    lap_number: Annotated[int, Field(strict=True, ge=1, le=100)]
    lap_time_ms: Annotated[int, Field(strict=True, ge=0, le=4_294_967_295)]
    lap_time_available: bool
    lap_time_unavailable_reason: str | None = None
    sector1_time_ms: Annotated[int, Field(strict=True, ge=0, le=15_365_535)] | None
    sector1_time_ms_part: Annotated[int, Field(strict=True, ge=0, le=65_535)]
    sector1_time_minutes_part: Annotated[int, Field(strict=True, ge=0, le=255)]
    sector1_time_available: bool
    sector1_time_unavailable_reason: str | None = None
    sector2_time_ms: Annotated[int, Field(strict=True, ge=0, le=15_365_535)] | None
    sector2_time_ms_part: Annotated[int, Field(strict=True, ge=0, le=65_535)]
    sector2_time_minutes_part: Annotated[int, Field(strict=True, ge=0, le=255)]
    sector2_time_available: bool
    sector2_time_unavailable_reason: str | None = None
    sector3_time_ms: Annotated[int, Field(strict=True, ge=0, le=15_365_535)] | None
    sector3_time_ms_part: Annotated[int, Field(strict=True, ge=0, le=65_535)]
    sector3_time_minutes_part: Annotated[int, Field(strict=True, ge=0, le=255)]
    sector3_time_available: bool
    sector3_time_unavailable_reason: str | None = None
    validity_flags: Annotated[int, Field(strict=True, ge=0, le=255)]
    lap_valid: bool
    sector1_valid: bool
    sector2_valid: bool
    sector3_valid: bool
    unknown_validity_bits: Annotated[int, Field(strict=True, ge=0, le=255)]


class LiveSessionHistoryRecord(BaseModel):
    status: Literal["waiting", "fresh", "stale", "unsupported", "unavailable"]
    reason: str | None
    age_ms: int | None
    observation_count: int = Field(ge=0, le=2_147_483_647)
    session_uid: str | None = None
    packet_format: int | None = None
    player_car_index: int | None = None
    frame_identifier: int | None = None
    source_frame_identifier: int | None = None
    session_time_s: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    populated_row_count: Annotated[int, Field(strict=True, ge=0, le=100)] = 0
    omitted_row_count: Annotated[int, Field(strict=True, ge=0, le=90)] = 0
    rows: list[LiveSessionHistoryLapRecord] = Field(default_factory=list, max_length=10)


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
    live_car_damage: LiveCarDamageRecord | None = None
    live_car_setup: LiveCarSetupRecord | None = None
    live_session_conditions: LiveSessionConditionsRecord | None = None
    live_motion: LiveMotionRecord | None = None
    live_session_history: LiveSessionHistoryRecord | None = None


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
    group_id: str | None = None
    segment_ordinal: int | None = Field(default=None, ge=1, le=256)


class RecordingGroupRecord(BaseModel):
    group_id: str
    status: Literal[
        "starting", "recording", "pausing", "paused", "resuming", "stopping",
        "complete", "failed", "interrupted",
    ]
    bind_host: str
    bind_port: int
    transition_revision: int = Field(ge=0)
    created_at_utc: str
    updated_at_utc: str
    started_at_utc: str | None
    finished_at_utc: str | None
    current_recording_id: str | None
    last_recording_id: str | None
    segment_count: int = Field(ge=1, le=256)
    summary: dict[str, Any] | None
    failure_reason: str | None


class RecordingGroupEventRecord(BaseModel):
    event_ordinal: int = Field(ge=0)
    event_kind: Literal[
        "start_requested", "acquisition_started", "pause_requested",
        "pause_acknowledged", "resume_requested", "stop_requested",
        "complete", "failed", "interrupted",
    ]
    event_at_utc: str
    recording_id: str | None
    transition_revision: int = Field(ge=0)
    details: dict[str, Any] | None


class RecordingGroupSegmentPage(BaseModel):
    items: list[RecordingJobRecord]
    total_count: int = Field(ge=0, le=256)
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0, le=256)
    has_more: bool


class RecordingGroupTransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0)
    expected_recording_id: str = Field(pattern=r"^[a-f0-9]{32}$")


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
    live_car_damage: LiveCarDamageRecord | None = None
    live_car_setup: LiveCarSetupRecord | None = None
    live_session_conditions: LiveSessionConditionsRecord | None = None
    live_motion: LiveMotionRecord | None = None
    live_session_history: LiveSessionHistoryRecord | None = None


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


class RunArtifactQueryValidationError(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.reason = f"invalid_processing_run_artifact_{field}"


class RecordingSourcePageQueryValidationError(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.reason = f"invalid_recording_catalog_{field}"


def _recording_source_page_query(
    request: Request,
) -> tuple[int, int, str, str, str, str | None]:
    allowed = {
        "limit",
        "offset",
        "q",
        "latest_job_status",
        "availability",
        "selected_capture_id",
    }
    values: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key not in allowed:
            raise RecordingSourcePageQueryValidationError("query_parameter")
        if key in values:
            raise RecordingSourcePageQueryValidationError("repeated_parameter")
        values[key] = value

    raw_limit = values.get("limit", "25")
    if re.fullmatch(r"[0-9]{1,2}", raw_limit) is None:
        raise RecordingSourcePageQueryValidationError("limit")
    limit = int(raw_limit)
    if not 1 <= limit <= 50:
        raise RecordingSourcePageQueryValidationError("limit")

    raw_offset = values.get("offset", "0")
    if re.fullmatch(r"[0-9]{1,6}", raw_offset) is None:
        raise RecordingSourcePageQueryValidationError("offset")
    offset = int(raw_offset)
    if offset > 100_000:
        raise RecordingSourcePageQueryValidationError("offset")

    query = values.get("q", "")
    if len(query) > 128 or any(
        ord(character) < 32 or ord(character) == 127 for character in query
    ):
        raise RecordingSourcePageQueryValidationError("q")
    latest_job_status = values.get("latest_job_status", "all")
    if latest_job_status not in {
        "all",
        "none",
        "queued",
        "running",
        "complete",
        "failed",
        "interrupted",
    }:
        raise RecordingSourcePageQueryValidationError("latest_job_status")
    availability = values.get("availability", "all")
    if availability not in {"all", "available", "missing"}:
        raise RecordingSourcePageQueryValidationError("availability")
    selected_capture_id = values.get("selected_capture_id")
    if selected_capture_id is not None and re.fullmatch(
        r"[a-f0-9]{32}", selected_capture_id
    ) is None:
        raise RecordingSourcePageQueryValidationError("selected_capture_id")
    return (
        limit,
        offset,
        query,
        latest_job_status,
        availability,
        selected_capture_id,
    )


def _run_artifact_query(request: Request) -> tuple[str, int, int]:
    allowed = {"kind", "limit", "offset"}
    values: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key not in allowed:
            raise RunArtifactQueryValidationError("query_parameter")
        if key in values:
            raise RunArtifactQueryValidationError("repeated_parameter")
        values[key] = value

    kind = values.get("kind", "all")
    if kind not in {"all", "player_trace", "car_observation_chunk"}:
        raise RunArtifactQueryValidationError("kind")
    raw_limit = values.get("limit", "50")
    if re.fullmatch(r"[0-9]{1,3}", raw_limit) is None:
        raise RunArtifactQueryValidationError("limit")
    limit = int(raw_limit)
    if not 1 <= limit <= 50:
        raise RunArtifactQueryValidationError("limit")
    raw_offset = values.get("offset", "0")
    if re.fullmatch(r"[0-9]{1,6}", raw_offset) is None:
        raise RunArtifactQueryValidationError("offset")
    offset = int(raw_offset)
    if not 0 <= offset <= 100_000:
        raise RunArtifactQueryValidationError("offset")
    return kind, limit, offset


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
    require_auth: bool = False,
    recording_host: str = "0.0.0.0",
    recording_port: int = 20777,
    recording_queue_size: int = 8192,
    max_recording_download_bytes: int = DEFAULT_MAX_RECORDING_DOWNLOAD_BYTES,
    max_recording_upload_bytes: int = DEFAULT_MAX_RECORDING_UPLOAD_BYTES,
    track_models_root: str | Path | None = None,
    reviewed_track_models_root: str | Path | None = None,
    engineer_runtime: OllamaRuntime | None = None,
    automatic_acquisition: bool = True,
    evidence_database_path: str | Path | None = None,
) -> FastAPI:
    """Create a local API bound to operator-configured storage and recording roots."""
    if require_auth and (not control_token or len(control_token) < 32):
        raise ValueError("authenticated API requires a control token of at least 32 characters")
    configured_database_path = Path(database_path).expanduser().resolve()
    evidence_store = EvidenceStore(evidence_database_path or configured_database_path.with_name(configured_database_path.stem + "-evidence.sqlite3"))
    live_runtime = LiveSessionRuntime(evidence_store, host=recording_host, port=recording_port,
                                     queue_size=min(recording_queue_size, 1024)) if automatic_acquisition else None
    comparison_admission = threading.BoundedSemaphore(2)
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
    recording_download_service = RecordingDownloadService(
        configured_database_path,
        configured_recordings_root,
        max_download_bytes=max_recording_download_bytes,
    )
    recording_upload_service = RecordingUploadService(
        configured_database_path,
        configured_recordings_root,
        import_controller,
        max_upload_bytes=max_recording_upload_bytes,
    )
    ollama_runtime = engineer_runtime or OllamaRuntime(
        configured_database_path.parent / ".f1-engineer-ollama-model.json"
    )
    local_speech_runtime = LocalSpeechRuntime(
        configured_database_path.parent / ".f1-engineer-whisper-pin.json",
        configured_database_path.parent / ".f1-engineer-speech",
    )
    engineer_ask_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import_controller.start()
        recording_upload_service.start()
        recording_controller.start()
        replay_controller.start()
        if live_runtime:
            await run_in_threadpool(live_runtime.start)
        import_controller.enable_dispatch()
        await run_in_threadpool(local_speech_runtime.cleanup_stale_temporary_files)
        app.state.import_controller = import_controller
        app.state.recording_controller = recording_controller
        app.state.replay_controller = replay_controller
        app.state.recording_upload_service = recording_upload_service
        app.state.ollama_runtime = ollama_runtime
        app.state.local_speech_runtime = local_speech_runtime
        app.state.live_session_runtime = live_runtime
        app.state.evidence_store = evidence_store
        try:
            yield
        finally:
            if live_runtime:
                await run_in_threadpool(live_runtime.close)
            import_controller.stop_dispatch()
            await run_in_threadpool(recording_upload_service.close)
            replay_controller.close()
            recording_controller.close()
            import_controller.close()

    app = FastAPI(
        title="F1 Race Engineer API",
        version="1.0.0",
        description="Local telemetry recording, historical analysis, and explicit capture imports.",
        lifespan=lifespan,
    )

    if require_auth:
        @app.middleware("http")
        async def authenticate_request(request: Request, call_next):
            if not _authorized(request.headers.get("authorization"), control_token):
                return _api_error(403, "api_not_authorized")
            return await call_next(request)

    @app.get("/api/v2/session-evidence/status")
    def session_evidence_status():
        return live_runtime.status() if live_runtime else {"state": "disabled"}

    @app.get("/api/v2/session-evidence/sessions")
    def evidence_sessions(limit: int = Query(default=100, ge=1, le=100), after: str = Query(default="", max_length=128)):
        return {"data": evidence_store.sessions(limit=limit, after=after)}

    @app.get("/api/v2/session-evidence/legacy-sessions")
    def legacy_evidence_sessions(limit: int = Query(default=100, ge=1, le=100), after: str = Query(default="", max_length=128)):
        return {"data": archived_session_page(configured_database_path, limit=limit, after=after)}

    @app.get("/api/v2/session-evidence/sessions/{session_id}/attempts")
    def evidence_attempts(session_id: str, limit: int = Query(default=100, ge=1, le=100), after: str = Query(default="", max_length=128)):
        return {"data": evidence_store.attempts(session_id, limit=limit, after=after)}

    @app.post("/api/v2/session-evidence/sessions/{session_id}/compare")
    def evidence_comparison(session_id: str, target: str = Query(max_length=128), reference: str = Query(max_length=128),
                            comparison_policy: Literal["observed_session_distance", "practice_qualifying"] = Query(default="observed_session_distance"),
                            authorization: str | None = Header(default=None)):
        if not _authorized(authorization, control_token):
            return _api_error(403, "comparison_control_not_authorized")
        if not comparison_admission.acquire(blocking=False):
            return _api_error(409, "comparison_read_budget_busy")
        try:
            return {"data": compare_session_laps(
                evidence_store, session_id, target, reference, policy=comparison_policy
            )}
        except EvidenceUnavailable as exception:
            return _api_error(422, str(exception))
        finally:
            comparison_admission.release()

    @app.get("/api/v2/session-evidence/sessions/{session_id}/attempts/{revision_id}")
    def evidence_trace(session_id: str, revision_id: str):
        if not comparison_admission.acquire(blocking=False):
            return _api_error(409, "comparison_read_budget_busy")
        try:
            metadata, samples = evidence_store.evidence(revision_id, session=session_id)
            return {"data": {"attempt": metadata, "samples": samples}}
        except EvidenceUnavailable as exception:
            return _api_error(422, str(exception))
        finally:
            comparison_admission.release()

    @app.get("/api/v2/session-evidence/comparisons/{comparison_id}")
    def evidence_comparison_history(comparison_id: str):
        try:
            return {"data": evidence_store.report(comparison_id)}
        except EvidenceUnavailable as exception:
            return _api_error(404, str(exception))

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
        "/api/v1/processing-runs/{run_id}/artifacts",
        response_model=APIResponse[dict[str, Any]],
    )
    def processing_run_artifacts(
        run_id: str, request: Request
    ) -> APIResponse[dict[str, Any]] | JSONResponse:
        try:
            kind, limit, offset = _run_artifact_query(request)
        except RunArtifactQueryValidationError as exc:
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
            result = list_processing_run_artifacts(
                configured_database_path,
                run_id,
                kind=kind,
                limit=limit,
                offset=offset,
            )
        except RunArtifactInventoryUnavailable as exc:
            if exc.reason_code == "processing_run_id_invalid":
                return JSONResponse(
                    status_code=422,
                    content={
                        "api_version": "v1",
                        "status": "unavailable",
                        "data": None,
                        "reason": exc.reason_code,
                    },
                )
            return APIResponse[dict[str, Any]](
                status="unavailable", reason=exc.reason_code
            )
        except FileNotFoundError:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="configured_database_unavailable"
            )
        if result is None:
            return JSONResponse(
                status_code=404,
                content={
                    "api_version": "v1",
                    "status": "unavailable",
                    "data": None,
                    "reason": "processing_run_unavailable",
                },
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

    @app.get(
        "/api/v1/processing-runs/{run_id}/sessions/{session_uid}/cars/{car_index}/lap-inventory",
        response_model=APIResponse[dict[str, Any]],
    )
    def car_lap_inventory(
        run_id: str,
        session_uid: str,
        car_index: int = ApiPath(ge=0, le=23),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
    ) -> APIResponse[dict[str, Any]]:
        try:
            result = load_car_lap_inventory_page(
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
                status="unavailable", reason="car_lap_inventory_unavailable"
            )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="car_lap_inventory_unavailable"
            )
        return APIResponse[dict[str, Any]](data=_stringify_session_uids(result))

    @app.get(
        "/api/v1/processing-runs/{run_id}/sessions/{session_uid}/cars/{car_index}/lap-attempts/{attempt_key}/observations",
        response_model=APIResponse[dict[str, Any]],
    )
    def car_lap_observations(
        run_id: str,
        session_uid: str,
        car_index: int = ApiPath(ge=0, le=23),
        attempt_key: str = ApiPath(min_length=1, max_length=512),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
    ) -> APIResponse[dict[str, Any]]:
        try:
            result = load_car_lap_observation_page(
                configured_database_path,
                run_id,
                session_uid,
                car_index,
                attempt_key,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason=str(exc)
            )
        except OSError:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="car_lap_observation_page_unavailable"
            )
        if result is None:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason="car_lap_observation_page_unavailable"
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
            result = inspect_attempt_quality_web(
                configured_database_path, attempt_key
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
        "/api/v1/recording-sources/page",
        response_model=APIResponse[RecordingSourcePageRecord],
    )
    def recording_sources_page(
        request: Request,
    ) -> APIResponse[RecordingSourcePageRecord] | JSONResponse:
        try:
            (
                limit,
                offset,
                query,
                latest_job_status,
                availability,
                selected_capture_id,
            ) = _recording_source_page_query(request)
        except RecordingSourcePageQueryValidationError as exc:
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
            page = list_recording_sources_page(
                configured_database_path,
                configured_recordings_root,
                limit=limit,
                offset=offset,
                query=query,
                latest_job_status=latest_job_status,
                availability=availability,
                selected_capture_id=selected_capture_id,
            )
        except RecordingCatalogUnavailable as exc:
            return APIResponse[RecordingSourcePageRecord](
                status="unavailable", reason=exc.reason
            )
        except (OSError, sqlite3.DatabaseError, ValueError):
            return APIResponse[RecordingSourcePageRecord](
                status="unavailable", reason="recording_catalog_unavailable"
            )
        return APIResponse[RecordingSourcePageRecord](data=page)

    @app.post("/api/v1/recording-sources/upload")
    async def upload_recording_source(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Response:
        def error(status_code: int, reason: str) -> JSONResponse:
            return JSONResponse(
                status_code=status_code,
                content={
                    "api_version": "v1",
                    "status": "unavailable",
                    "data": None,
                    "reason": reason,
                },
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )

        if not _authorized(authorization, control_token):
            return error(401, "local_control_unauthorized")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
            return error(415, "recording_upload_content_type_invalid")
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            return error(415, "recording_upload_content_encoding_unsupported")

        declared_length: int | None = None
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            if not raw_length.isascii() or not raw_length.isdigit() or len(raw_length) > 20:
                return error(400, "recording_upload_length_invalid")
            declared_length = int(raw_length)
            if declared_length < 1:
                return error(400, "recording_upload_length_invalid")
            if declared_length > recording_upload_service.max_upload_bytes:
                return error(413, "recording_upload_size_limit")

        encoded_filename = request.headers.get("x-capture-upload-filename")
        if encoded_filename is None:
            return error(422, "recording_upload_filename_invalid")
        try:
            opened = await run_in_threadpool(
                recording_upload_service.begin_upload,
                encoded_filename,
                expected_bytes=declared_length,
            )
        except RecordingUploadError as exc:
            return error(exc.status_code, exc.reason)
        except (OSError, ValueError):
            return error(503, "recording_upload_service_unavailable")

        try:
            data = await receive_recording_upload(request, opened)
            return JSONResponse(
                status_code=201,
                content={
                    "api_version": "v1",
                    "status": "ok",
                    "data": data,
                    "reason": None,
                },
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except RecordingUploadError as exc:
            return error(exc.status_code, exc.reason)
        except RecordingUploadTransferError as exc:
            return error(exc.status_code, exc.reason)
        except Exception:
            return error(503, "recording_upload_transfer_failed")

    @app.get("/api/v1/recording-sources/{capture_id}/download")
    def download_recording_source(
        capture_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Response:
        def error(status_code: int, reason: str) -> JSONResponse:
            return JSONResponse(
                status_code=status_code,
                content={
                    "api_version": "v1",
                    "status": "unavailable",
                    "data": None,
                    "reason": reason,
                },
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )

        if not _authorized(authorization, control_token):
            return error(401, "local_control_unauthorized")
        if re.fullmatch(r"[a-f0-9]{32}", capture_id) is None:
            return error(422, "recording_download_capture_id_invalid")
        if "range" in request.headers:
            return error(416, "recording_download_range_unsupported")

        version: str | None = None
        for key, value in request.query_params.multi_items():
            if key != "version":
                return error(422, "recording_download_query_parameter_invalid")
            if version is not None:
                return error(422, "recording_download_query_parameter_repeated")
            version = value
        if version is None or re.fullmatch(r"[a-f0-9]{64}", version) is None:
            return error(422, "recording_download_version_invalid")

        try:
            opened = recording_download_service.open_download(capture_id, version)
        except RecordingDownloadError as exc:
            return error(exc.status_code, exc.reason)
        except (OSError, sqlite3.DatabaseError, ValueError):
            return error(503, "recording_download_service_unavailable")

        try:
            content_disposition = recording_download_content_disposition(
                opened.display_name
            )
        except UnicodeEncodeError:
            opened.close()
            return error(503, "recording_download_filename_unavailable")
        headers = {
            "Content-Disposition": content_disposition,
            "Content-Length": str(opened.byte_size),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        return RecordingDownloadResponse(opened, headers=headers)

    @app.get(
        "/api/v1/storage/usage",
        response_model=APIResponse[StorageUsageRecord],
    )
    def storage_usage() -> APIResponse[StorageUsageRecord]:
        return APIResponse[StorageUsageRecord](
            data=measure_storage_usage(
                configured_database_path, configured_recordings_root
            )
        )

    @app.get(
        "/api/v1/telemetry/service",
        response_model=APIResponse[TelemetryServiceRecord],
    )
    def telemetry_service(
        request: Request, response: Response
    ) -> APIResponse[TelemetryServiceRecord] | JSONResponse:
        response.headers["Cache-Control"] = "no-store"
        if request.query_params:
            return _api_error(422, "telemetry_service_query_parameters_unsupported")

        host = recording_controller.host
        port = recording_controller.port
        queue_size = recording_controller.queue_size
        configuration_available = (
            isinstance(host, str)
            and 0 < len(host) <= 256
            and isinstance(port, int)
            and not isinstance(port, bool)
            and 1 <= port <= 65535
            and isinstance(queue_size, int)
            and not isinstance(queue_size, bool)
            and 1 <= queue_size <= 1_000_000
        )
        controller_ready = recording_controller.ready
        reservation = (
            import_controller.current_operation_reservation
            if controller_ready
            else None
        )
        if reservation not in {None, "recording", "import", "replay", "upload"}:
            reservation = None

        unavailable_reason = (
            "telemetry_configuration_unsupported"
            if not configuration_available
            else "recording_controller_unavailable"
            if not controller_ready
            else None
        )
        data = TelemetryServiceRecord(
            observed_at_utc=datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            udp_bind_host=host if configuration_available else None,
            udp_port=port if configuration_available else None,
            receive_queue_size=queue_size if configuration_available else None,
            configuration_available=configuration_available,
            controller_ready=controller_ready,
            operation_reservation=(
                (reservation or "idle") if controller_ready else "unavailable"
            ),
            unavailable_reason=unavailable_reason,
        )
        response = APIResponse[TelemetryServiceRecord](data=data)
        if len(response.model_dump_json().encode("utf-8")) > 4096:
            return _api_error(503, "telemetry_service_response_unavailable")
        return response

    @app.post(
        "/api/v1/recording-groups/start",
        response_model=APIResponse[RecordingGroupRecord],
        status_code=202,
    )
    def start_recording_group(
        authorization: str | None = Header(default=None),
    ) -> APIResponse[RecordingGroupRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "recording_control_not_authorized")
        if live_runtime and live_runtime.state not in {"failed", "stopped"}:
            return _api_error(409, "automatic_session_acquisition_owns_udp")
        try:
            group = recording_controller.start_recording_group()
        except ValueError as exc:
            reason = str(exc)
            status_code = (
                409
                if reason
                in {
                    "another_local_operation_is_in_progress",
                    "recording_controller_busy",
                    "recording_group_transition_conflict",
                }
                else 503
            )
            return _api_error(status_code, reason)
        return APIResponse[RecordingGroupRecord](data=group)

    @app.get(
        "/api/v1/recording-groups/current",
        response_model=APIResponse[RecordingGroupRecord],
    )
    def current_recording_group() -> APIResponse[RecordingGroupRecord] | JSONResponse:
        try:
            group = recording_controller.current_group()
        except ValueError as exc:
            return _api_error(503, str(exc))
        return APIResponse[RecordingGroupRecord](data=group)

    @app.get(
        "/api/v1/recording-groups/{group_id}",
        response_model=APIResponse[RecordingGroupRecord],
    )
    def recording_group(group_id: str) -> APIResponse[RecordingGroupRecord] | JSONResponse:
        try:
            group = recording_controller.group(group_id)
        except ValueError as exc:
            return _api_error(503, str(exc))
        if group is None:
            return _api_error(404, "recording_group_unavailable")
        return APIResponse[RecordingGroupRecord](data=group)

    @app.get(
        "/api/v1/recording-groups/{group_id}/segments",
        response_model=APIResponse[RecordingGroupSegmentPage],
    )
    def recording_group_segments(
        group_id: str,
        limit: int = Query(default=50, ge=1, le=50),
        offset: int = Query(default=0, ge=0, le=256),
    ) -> APIResponse[RecordingGroupSegmentPage] | JSONResponse:
        try:
            page = recording_controller.group_segments(
                group_id, limit=limit, offset=offset
            )
        except ValueError as exc:
            reason = str(exc)
            return _api_error(
                404 if reason == "recording_group_unavailable" else 422,
                reason,
            )
        return APIResponse[RecordingGroupSegmentPage](data=page)

    @app.get(
        "/api/v1/recording-groups/{group_id}/events",
        response_model=APIResponse[list[RecordingGroupEventRecord]],
    )
    def recording_group_events(
        group_id: str,
    ) -> APIResponse[list[RecordingGroupEventRecord]] | JSONResponse:
        try:
            events = recording_controller.group_events(group_id)
        except ValueError as exc:
            reason = str(exc)
            return _api_error(
                404 if reason == "recording_group_unavailable" else 503,
                reason,
            )
        return APIResponse[list[RecordingGroupEventRecord]](data=events)

    @app.post(
        "/api/v1/recording-groups/{group_id}/pause",
        response_model=APIResponse[RecordingGroupRecord],
        status_code=202,
    )
    def pause_recording_group(
        group_id: str,
        body: RecordingGroupTransitionBody,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[RecordingGroupRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "recording_control_not_authorized")
        try:
            group = recording_controller.pause_recording_group(
                group_id,
                expected_revision=body.expected_revision,
                expected_recording_id=body.expected_recording_id,
            )
        except ValueError as exc:
            reason = str(exc)
            return _api_error(
                404 if reason == "recording_group_unavailable" else
                409 if reason == "recording_group_transition_conflict" else 503,
                reason,
            )
        return APIResponse[RecordingGroupRecord](data=group)

    @app.post(
        "/api/v1/recording-groups/{group_id}/resume",
        response_model=APIResponse[RecordingGroupRecord],
        status_code=202,
    )
    def resume_recording_group(
        group_id: str,
        body: RecordingGroupTransitionBody,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[RecordingGroupRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "recording_control_not_authorized")
        try:
            group = recording_controller.resume_recording_group(
                group_id,
                expected_revision=body.expected_revision,
                expected_recording_id=body.expected_recording_id,
            )
        except ValueError as exc:
            reason = str(exc)
            return _api_error(
                404 if reason == "recording_group_unavailable" else
                409 if reason in {
                    "recording_group_transition_conflict",
                    "recording_group_segment_limit_reached",
                    "another_local_operation_is_in_progress",
                } else 503,
                reason,
            )
        return APIResponse[RecordingGroupRecord](data=group)

    @app.post(
        "/api/v1/recording-groups/{group_id}/stop",
        response_model=APIResponse[RecordingGroupRecord],
        status_code=202,
    )
    def stop_recording_group(
        group_id: str,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[RecordingGroupRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "recording_control_not_authorized")
        try:
            group = recording_controller.stop_recording_group(group_id)
        except ValueError as exc:
            reason = str(exc)
            return _api_error(
                404 if reason == "recording_group_unavailable" else 503,
                reason,
            )
        return APIResponse[RecordingGroupRecord](data=group)

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
        if live_runtime and live_runtime.state not in {"failed", "stopped"}:
            return _api_error(409, "automatic_session_acquisition_owns_udp")
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
            job = import_controller.submit(
                request.capture_id, queue_if_busy=request.queue_if_busy
            )
        except ValueError as exc:
            reason = str(exc)
            status_code = (
                409
                if reason
                in {
                    "another_import_is_in_progress",
                    "another_local_operation_is_in_progress",
                    "import_queue_full",
                    "import_queue_capacity_inconsistent",
                    "capture_source_changed_refresh_catalog",
                }
                else 503
            )
            return _api_error(status_code, reason)
        return APIResponse[ImportJobRecord](data=job)

    @app.get(
        "/api/v1/import-jobs/queue",
        response_model=APIResponse[ImportQueueRecord],
    )
    def import_job_queue() -> APIResponse[ImportQueueRecord] | JSONResponse:
        try:
            snapshot = import_controller.queue_snapshot()
        except ValueError as exc:
            return _api_error(503, str(exc))
        return APIResponse[ImportQueueRecord](data=snapshot)

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
                    "import_queue_full",
                    "import_queue_capacity_inconsistent",
                    "import_capture_already_active",
                    "capture_source_changed_refresh_catalog",
                }
                else 503
            )
            return _api_error(status_code, reason)
        return APIResponse[ImportJobRecord](data=job)

    @app.post(
        "/api/v1/import-jobs/{job_id}/cancel",
        response_model=APIResponse[ImportJobRecord],
    )
    def cancel_import(
        job_id: str,
        authorization: str | None = Header(default=None),
    ) -> APIResponse[ImportJobRecord] | JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(403, "import_control_not_authorized")
        try:
            job, cancelled = import_controller.cancel(job_id)
        except ValueError as exc:
            return _api_error(503, str(exc))
        if job is None:
            return _api_error(404, "import_job_unavailable")
        if not cancelled:
            return _api_error(409, "import_job_not_waiting")
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

    @app.get(
        "/api/v1/compare/player-slot-window",
        response_model=APIResponse[dict[str, Any]],
    )
    def compare_player_slot_window_api(
        target_attempt_key: str = Query(min_length=1, max_length=512),
        slot_car_index: int = Query(ge=0, le=23),
        slot_attempt_key: str = Query(min_length=1, max_length=512),
        window_start_m: float = Query(),
        window_end_m: float = Query(),
    ) -> APIResponse[dict[str, Any]]:
        try:
            window = optional_distance_window(window_start_m, window_end_m)
            if window is None:
                raise ValueError("player_slot_comparison_window_required")
            result = compare_player_slot_window(
                configured_database_path,
                target_attempt_key,
                slot_car_index,
                slot_attempt_key,
                window,
            )
        except DatabaseSchemaError:
            raise
        except OSError:
            return APIResponse[dict[str, Any]](
                status="unavailable",
                reason="player_slot_comparison_source_unavailable",
            )
        except ValueError as exc:
            return APIResponse[dict[str, Any]](
                status="unavailable", reason=str(exc)
            )
        return APIResponse[dict[str, Any]](
            data=_stringify_session_uids(result)
        )

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

    @app.get("/api/v1/engineer/runtime", response_model=APIResponse[dict[str, Any]])
    async def engineer_runtime_status(request: Request) -> APIResponse[dict[str, Any]] | JSONResponse:
        if request.query_params:
            return _api_error(400, "engineer_runtime_query_not_allowed")
        status = await ollama_runtime.status()
        return APIResponse[dict[str, Any]](data=status)

    @app.get("/api/v1/engineer/transcribe", response_model=APIResponse[dict[str, Any]])
    async def engineer_speech_runtime_status(
        request: Request,
    ) -> APIResponse[dict[str, Any]] | JSONResponse:
        if request.query_params:
            return _api_error(400, "engineer_speech_query_not_allowed")
        status = await run_in_threadpool(local_speech_runtime.status)
        return APIResponse[dict[str, Any]](data=status)

    @app.post("/api/v1/engineer/ask")
    async def engineer_ask(
        request: Request,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(401, "engineer_ask_unauthorized")
        if request.query_params:
            return _api_error(400, "engineer_ask_query_not_allowed")
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return _api_error(415, "engineer_ask_json_required")
        deadline = asyncio.get_running_loop().time() + 60.0
        lock_acquired = False
        gate_lease: AIGateLease | None = None
        try:
            async with asyncio.timeout_at(deadline):
                try:
                    raw_body = await _read_engineer_ask_body(request)
                except ValueError:
                    return _api_error(400, "engineer_ask_content_length_invalid")
                except (OSError, RuntimeError):
                    return _api_error(400, "engineer_ask_request_incomplete")
                try:
                    body = EngineerAskBody.model_validate_json(raw_body, strict=True)
                    question_bytes = body.question.encode("utf-8", errors="strict")
                except (ValidationError, UnicodeError, RecursionError, ValueError):
                    return _api_error(422, "engineer_ask_request_invalid")
                if not body.question.strip():
                    return _api_error(422, "engineer_ask_question_empty")
                if len(question_bytes) > 1_024:
                    return _api_error(413, "engineer_ask_question_limit_exceeded")
                if engineer_ask_lock.locked():
                    return _api_error(409, "engineer_ask_busy")

                gate_lease = PROCESS_ENGINEER_AI_GATE.try_acquire()
                if gate_lease is None:
                    return _api_error(409, "engineer_ask_busy")
                await engineer_ask_lock.acquire()
                lock_acquired = True
                data = await _run_until_client_disconnect(
                    answer_engineer_question(
                        configured_database_path,
                        body.selection.model_dump(),
                        body.question.strip(),
                        runtime=ollama_runtime,
                        track_model_catalog=track_model_catalog,
                        gate_lease=gate_lease,
                    ),
                    request,
                    gate_lease=gate_lease,
                )
                response = JSONResponse(
                    status_code=200,
                    content={
                        "api_version": "v1",
                        "status": "ok",
                        "data": _stringify_session_uids(data),
                        "reason": None,
                    },
                    headers={"Cache-Control": "no-store"},
                )
                if len(response.body) > 40 * 1_024:
                    return _api_error(502, "engineer_ask_response_limit_exceeded")
                return response
        except TimeoutError:
            return _api_error(504, "engineer_ask_deadline_exceeded")
        except _EngineerAskBodyLimitExceeded:
            return _api_error(413, "engineer_ask_request_limit_exceeded")
        except _EngineerClientDisconnected:
            return _api_error(499, "engineer_ask_client_disconnected")
        except OllamaUnavailable as exc:
            if exc.reason == "analysis_busy":
                return _api_error(409, "engineer_ask_busy")
            return _api_error(503, "engineer_ask_unavailable")
        except DatabaseSchemaError:
            raise
        except (OSError, sqlite3.Error):
            return _api_error(503, "engineer_ask_source_unavailable")
        except (TypeError, KeyError, ValueError):
            return _api_error(422, "engineer_ask_selection_unavailable")
        except RuntimeError:
            return _api_error(503, "engineer_ask_unavailable")
        finally:
            if gate_lease is not None:
                gate_lease.close()
            if lock_acquired:
                engineer_ask_lock.release()

    @app.post("/api/v1/engineer/transcribe")
    async def engineer_speech_transcription(
        request: Request,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
        request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
        expected_audio_sha256: Annotated[str | None, Header(alias="X-Audio-SHA256")] = None,
    ) -> JSONResponse:
        if not _authorized(authorization, control_token):
            return _api_error(401, "engineer_speech_unauthorized")
        if request.query_params:
            return _api_error(400, "engineer_speech_query_not_allowed")
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "audio/wav":
            return _api_error(415, "engineer_speech_wav_required")
        if (
            not isinstance(request_id, str)
            or not _ENGINEER_SPEECH_REQUEST_ID.fullmatch(request_id)
            or not isinstance(expected_audio_sha256, str)
            or not _ENGINEER_SPEECH_SHA256.fullmatch(expected_audio_sha256)
        ):
            return _api_error(422, "engineer_speech_correlation_invalid")
        content_length = request.headers.get("content-length")
        if content_length is not None:
            if not re.fullmatch(r"[0-9]{1,10}", content_length):
                return _api_error(400, "engineer_speech_content_length_invalid")
            if int(content_length) > SPEECH_MAX_AUDIO_BYTES:
                return _api_error(413, "engineer_speech_audio_limit_exceeded")

        deadline = asyncio.get_running_loop().time() + 60.0
        gate_lease: AIGateLease | None = None
        try:
            async with asyncio.timeout_at(deadline):
                wav = await _read_limited_body(request, SPEECH_MAX_AUDIO_BYTES)
                validate_canonical_wav(wav)
                actual_audio_sha256 = hashlib.sha256(wav).hexdigest()
                if not hmac.compare_digest(actual_audio_sha256, expected_audio_sha256):
                    return _api_error(422, "engineer_speech_audio_digest_mismatch")
                gate_lease = PROCESS_ENGINEER_AI_GATE.try_acquire()
                if gate_lease is None:
                    return _api_error(409, "engineer_ask_busy")
                transcription = await _run_until_client_disconnect(
                    local_speech_runtime.transcribe(wav, gate_lease=gate_lease),
                    request,
                    gate_lease=gate_lease,
                )
                response = JSONResponse(
                    status_code=200,
                    content={
                        "api_version": "v1",
                        "status": "ok",
                        "data": {
                            "schema_version": 1,
                            "request_id": request_id,
                            "audio_sha256": transcription.audio_sha256,
                            "transcript": transcription.transcript,
                            "language": "en",
                            "verified": False,
                            "runtime": {
                                "name": "whisper.cpp",
                                "version": transcription.runtime_version,
                                "model_name": transcription.model_name,
                                "model_sha256": transcription.model_sha256,
                                "runtime_id": transcription.runtime_id,
                                "placement": "CPU",
                            },
                        },
                        "reason": None,
                    },
                    headers={"Cache-Control": "no-store"},
                )
                if len(response.body) > SPEECH_MAX_RESPONSE_BYTES:
                    return _api_error(502, "engineer_speech_response_limit_exceeded")
                return response
        except LocalSpeechUnavailable as exc:
            return _api_error(_engineer_speech_error_status(exc.reason), exc.reason)
        except TimeoutError:
            return _api_error(504, "engineer_speech_deadline_exceeded")
        except _EngineerRequestBodyLimitExceeded:
            return _api_error(413, "engineer_speech_audio_limit_exceeded")
        except ValueError:
            return _api_error(400, "engineer_speech_content_length_invalid")
        except _EngineerClientDisconnected:
            return _api_error(499, "engineer_speech_client_disconnected")
        except (OSError, RuntimeError):
            return _api_error(503, "engineer_speech_unavailable")
        finally:
            if gate_lease is not None:
                gate_lease.close()

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
        and hmac.compare_digest(supplied.encode("utf-8"), control_token.encode("utf-8"))
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


def _engineer_speech_error_status(reason: str) -> int:
    if "limit_exceeded" in reason:
        return 413
    if reason.startswith("audio_") or reason == "engineer_speech_audio_digest_mismatch":
        return 422
    if reason == "transcription_timeout":
        return 504
    return 503


class _EngineerRequestBodyLimitExceeded(Exception):
    pass


class _EngineerAskBodyLimitExceeded(_EngineerRequestBodyLimitExceeded):
    pass


class _EngineerClientDisconnected(Exception):
    pass


async def _read_engineer_ask_body(request: Request) -> bytes:
    return await _read_limited_body(
        request,
        8 * 1_024,
        limit_exception=_EngineerAskBodyLimitExceeded,
    )


async def _read_limited_body(
    request: Request,
    maximum: int,
    *,
    limit_exception: type[Exception] = _EngineerRequestBodyLimitExceeded,
) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        if not re.fullmatch(r"[0-9]{1,10}", declared):
            raise ValueError("invalid_content_length")
        if int(declared) > maximum:
            raise limit_exception
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > maximum:
            raise limit_exception
        chunks.append(chunk)
    return b"".join(chunks)


async def _run_until_client_disconnect(
    awaitable: Any,
    request: Request,
    *,
    gate_lease: AIGateLease,
) -> Any:
    task = asyncio.create_task(awaitable)
    drain_task: asyncio.Task[None] | None = None

    def cancel_and_drain() -> asyncio.Task[None]:
        nonlocal drain_task
        if drain_task is None:
            task.cancel()
            drain_task = asyncio.create_task(_consume_cancelled_task(task))
        return drain_task

    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=0.2)
            if done:
                return await task
            if await request.is_disconnected():
                drain = cancel_and_drain()
                gate_lease.retain_until(drain)
                raise _EngineerClientDisconnected
    except asyncio.CancelledError:
        drain = cancel_and_drain()
        gate_lease.retain_until(drain)
        raise


async def _consume_cancelled_task(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except BaseException:
        pass


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
