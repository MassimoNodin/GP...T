from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..analysis.region_service import (
    RegionReportUnavailable,
    _region_warnings,
    stable_region_context,
)
from ..analysis.source_limits import (
    ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
    ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
)
from ..storage.query import (
    load_attempt_draft_authoring_snapshot,
)
from .model import CornerDefinition, MAX_TRACK_MODEL_REGIONS, TrackModel


MAX_DRAFT_MODEL_REQUEST_BYTES = 64 * 1024
MAX_DRAFT_MODEL_OUTPUT_BYTES = 1024 * 1024
MAX_DRAFT_MODEL_IDENTIFIER_LENGTH = 128
MAX_DRAFT_MODEL_LABEL_LENGTH = 96


def build_draft_track_model(
    database_path: str | Path,
    source_attempt_key: str,
    *,
    model_id: str,
    revision: int,
    layout_id: str,
    regions: object,
) -> dict[str, object]:
    """Build a validated draft distance model from one explicitly selected attempt."""
    if not isinstance(source_attempt_key, str) or not source_attempt_key.strip():
        raise ValueError("source_attempt_key_required")
    if len(source_attempt_key) > 256:
        raise ValueError("source_attempt_key_too_long")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id_required")
    if len(model_id) > MAX_DRAFT_MODEL_IDENTIFIER_LENGTH:
        raise ValueError("model_id_too_long")
    if not isinstance(layout_id, str) or not layout_id.strip():
        raise ValueError("caller_declared_layout_id_required")
    if len(layout_id) > MAX_DRAFT_MODEL_IDENTIFIER_LENGTH:
        raise ValueError("layout_id_too_long")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("revision_must_be_positive")
    try:
        request_bytes = len(
            json.dumps(
                {
                    "source_attempt_key": source_attempt_key,
                    "model_id": model_id,
                    "revision": revision,
                    "layout_id": layout_id,
                    "regions": regions,
                },
                separators=(",", ":"),
                allow_nan=True,
            ).encode("utf-8")
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("draft_model_request_invalid") from exc
    if request_bytes > MAX_DRAFT_MODEL_REQUEST_BYTES:
        raise ValueError("draft_model_request_size_limit_exceeded")
    parsed_regions = _parse_regions(regions)

    attempt, source_metadata = load_attempt_draft_authoring_snapshot(
        database_path,
        source_attempt_key,
        max_context_segments=ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
        max_context_bytes=ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
    )
    if attempt is None:
        raise ValueError("draft_model_source_attempt_unavailable")
    if source_metadata is None:
        raise ValueError("draft_model_source_metadata_unavailable")
    try:
        context, context_mode = stable_region_context(
            attempt.context_segments, attempt.attempt_key
        )
    except RegionReportUnavailable as exc:
        raise ValueError(exc.reason_code) from exc
    except ValueError as exc:
        reason_code = getattr(exc, "reason_code", None)
        raise ValueError(reason_code or str(exc)) from exc

    source = _source_provenance(attempt, source_metadata, context, context_mode)
    if any(
        source[key] is not True
        for key in (
            "scope_matches_attempt",
            "attempt_metadata_matches_attempt",
            "trace_metadata_matches_attempt",
            "context_timeline_matches_attempt",
            "context_matches_attempt",
        )
    ):
        raise ValueError("draft_model_source_metadata_changed_during_assessment")
    provenance = (
        f"User-authored draft distance regions from attempt {attempt.attempt_key}; "
        f"capture sha256 {source['capture_sha256'] or 'unavailable'}; "
        "game-distance origin normalized to zero. Circuit geometry and corner identity "
        "have not been verified."
    )
    model = TrackModel(
        model_id=model_id.strip(),
        revision=revision,
        packet_format=int(context["packet_format"]),
        track_id=int(context["track_id"]),
        track_name=str(context["track_name"]),
        layout_id=layout_id.strip(),
        track_length_m=float(context["track_length_m"]),
        distance_origin_m=0.0,
        provenance=provenance,
        validation_status="draft",
        corners=parsed_regions,
    )
    model_document = _model_document(model)
    encoded_model = json.dumps(
        model_document, separators=(",", ":"), sort_keys=True, allow_nan=False
    ).encode("utf-8")
    if len(encoded_model) > MAX_DRAFT_MODEL_OUTPUT_BYTES:
        raise ValueError("draft_model_output_size_limit_exceeded")

    warning_run_summary = _warning_run_summary(source_metadata)
    warnings = _region_warnings(attempt, context_mode, warning_run_summary)
    _append_metadata_limit_warnings(warnings, source_metadata)
    if source["footer_status"] != "complete" and not any(
        warning["code"] in {"capture_incomplete", "capture_completion_unknown"}
        for warning in warnings
    ):
        warnings.append(
            {
                "code": "capture_completion_unknown",
                "text": "Capture footer completion could not be confirmed.",
            }
        )
    return {
        "status": "draft_model_built",
        "verification_scope": "user-authored distance windows only; no circuit geometry or corner identity verified",
        "model": model_document,
        "source": source,
        "warnings": warnings[:16],
        "authoring_limits": {
            "regions": MAX_TRACK_MODEL_REGIONS,
            "context_segments": ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
            "context_bytes": ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
            "request_bytes": MAX_DRAFT_MODEL_REQUEST_BYTES,
            "output_bytes": MAX_DRAFT_MODEL_OUTPUT_BYTES,
            "distance_origin_m": 0,
        },
        "catalog_installation": "not_performed",
    }


def _parse_regions(value: object) -> tuple[CornerDefinition, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("regions_must_be_an_array")
    if not 1 <= len(value) <= MAX_TRACK_MODEL_REGIONS:
        raise ValueError("region_count_limit_exceeded")
    allowed = {
        "identifier",
        "label",
        "start_distance_m",
        "end_distance_m",
        "braking_search_window_m",
        "turn_in_search_window_m",
        "throttle_pickup_window_m",
    }
    regions: list[CornerDefinition] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError(f"region_{index}_must_be_an_object")
        if set(raw) - allowed:
            raise ValueError(f"region_{index}_contains_unsupported_fields")
        identifier = raw.get("identifier")
        label = raw.get("label")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"region_{index}_identifier_required")
        if len(identifier) > MAX_DRAFT_MODEL_IDENTIFIER_LENGTH:
            raise ValueError(f"region_{index}_identifier_too_long")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"region_{index}_label_required")
        if len(label) > MAX_DRAFT_MODEL_LABEL_LENGTH:
            raise ValueError(f"region_{index}_label_too_long")
        regions.append(
            CornerDefinition(
                identifier=identifier.strip(),
                label=label.strip(),
                start_distance_m=_finite_number(raw.get("start_distance_m"), f"region_{index}_start_distance_m"),
                end_distance_m=_finite_number(raw.get("end_distance_m"), f"region_{index}_end_distance_m"),
                braking_search_window_m=_optional_window(
                    raw.get("braking_search_window_m"),
                    f"region_{index}_braking_search_window_m",
                ),
                turn_in_search_window_m=_optional_window(
                    raw.get("turn_in_search_window_m"),
                    f"region_{index}_turn_in_search_window_m",
                ),
                throttle_pickup_window_m=_optional_window(
                    raw.get("throttle_pickup_window_m"),
                    f"region_{index}_throttle_pickup_window_m",
                ),
            )
        )
    return tuple(regions)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}_must_be_numeric")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name}_must_be_finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name}_must_be_finite")
    return result


def _optional_window(value: object, name: str) -> tuple[float, float] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name}_must_contain_two_distances")
    if len(value) != 2:
        raise ValueError(f"{name}_must_contain_two_distances")
    return _finite_number(value[0], name), _finite_number(value[1], name)


def _model_document(model: TrackModel) -> dict[str, object]:
    corners: list[dict[str, object]] = []
    for corner in model.corners:
        item: dict[str, object] = {
            "identifier": corner.identifier,
            "label": corner.label,
            "start_distance_m": corner.start_distance_m,
            "end_distance_m": corner.end_distance_m,
        }
        for key, value in (
            ("braking_search_window_m", corner.braking_search_window_m),
            ("turn_in_search_window_m", corner.turn_in_search_window_m),
            ("throttle_pickup_window_m", corner.throttle_pickup_window_m),
        ):
            if value is not None:
                item[key] = list(value)
        corners.append(item)
    return {
        "schema_version": 1,
        "model_id": model.model_id,
        "revision": model.revision,
        "packet_format": model.packet_format,
        "track_id": model.track_id,
        "track_name": model.track_name,
        "layout_id": model.layout_id,
        "track_length_m": model.track_length_m,
        "distance_origin_m": 0,
        "provenance": model.provenance,
        "validation_status": "draft",
        "corners": corners,
    }


def _source_provenance(
    attempt: Any,
    metadata: Mapping[str, object],
    context: Mapping[str, object],
    context_mode: str,
) -> dict[str, object]:
    scope = metadata.get("scope")
    scope = scope if isinstance(scope, Mapping) else {}
    capture = metadata.get("capture")
    capture = capture if isinstance(capture, Mapping) else {}
    completion = capture.get("completion")
    completion = completion if isinstance(completion, Mapping) else {}
    trace = metadata.get("trace_metadata")
    trace = trace if isinstance(trace, Mapping) else {}
    attempt_metadata = metadata.get("attempt")
    attempt_metadata = attempt_metadata if isinstance(attempt_metadata, Mapping) else {}
    context_segments = attempt.context_segments
    initial_context = context_segments[0][1] if context_segments else None
    return {
        "attempt_key": attempt.attempt_key,
        "run_id": attempt.run_id,
        "session_uid": str(attempt.session_uid),
        "car_index": attempt.car_index,
        "attempt_number": attempt.attempt_number,
        "disposition": attempt.disposition,
        "game_valid": attempt.game_valid,
        "context_mode": context_mode,
        "packet_format": context.get("packet_format"),
        "track_id": context.get("track_id"),
        "track_name": context.get("track_name"),
        "track_length_m": context.get("track_length_m"),
        "source_trace_sha256": trace.get("sha256") or attempt.trace_sha256,
        "source_trace_schema_version": trace.get("schema_version") or attempt.trace_schema_version,
        "source_trace_checksum_verified": False,
        "source_trace_row_count": trace.get("row_count"),
        "capture_sha256": capture.get("sha256"),
        "capture_complete": capture.get("complete"),
        "footer_status": completion.get("status"),
        "recording_counters": {
            name: completion.get(name)
            for name in (
                "queue_dropped",
                "unpersisted_on_shutdown",
                "socket_errors",
            )
        },
        "replay_counters": _replay_counters(metadata),
        "scope_matches_attempt": (
            scope.get("run_id") == attempt.run_id
            and str(scope.get("session_uid")) == str(attempt.session_uid)
            and scope.get("car_index") == attempt.car_index
            and scope.get("packet_format") == context.get("packet_format")
        ),
        "attempt_metadata_matches_attempt": _attempt_metadata_matches(
            attempt, attempt_metadata
        ),
        "trace_metadata_matches_attempt": (
            trace.get("ready") is True
            and trace.get("sha256") == attempt.trace_sha256
            and trace.get("schema_version") == attempt.trace_schema_version
            and trace.get("row_count") == attempt.source_sample_count
        ),
        "context_timeline_matches_attempt": (
            metadata.get("context_segment_count") == len(context_segments)
            and metadata.get("context") == initial_context
        ),
        "context_matches_attempt": _context_identity_matches(
            metadata.get("context"), context
        ),
    }


def _attempt_metadata_matches(
    attempt: Any, metadata: Mapping[str, object]
) -> bool:
    return all(
        metadata.get(metadata_field) == getattr(attempt, attempt_field)
        for metadata_field, attempt_field in (
            ("attempt_key", "attempt_key"),
            ("attempt_number", "attempt_number"),
            ("disposition", "disposition"),
            ("lap_time_ms", "lap_time_ms"),
            ("game_valid", "game_valid"),
            ("start_observed", "start_observed"),
            ("pit_encountered", "pit_encountered"),
            ("superseded", "superseded"),
            ("lifecycle_assessed", "lifecycle_assessed"),
            ("reference_eligible", "reference_eligible"),
        )
    )


def _context_identity_matches(
    source_context: object, stable_context: Mapping[str, object]
) -> bool:
    if not isinstance(source_context, Mapping):
        return False
    fields = ("packet_format", "track_id", "track_name", "track_length_m")
    return all(source_context.get(field) == stable_context.get(field) for field in fields)


def _replay_counters(metadata: Mapping[str, object]) -> dict[str, object]:
    processing = metadata.get("processing")
    processing = processing if isinstance(processing, Mapping) else {}
    metrics = processing.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    quality = metrics.get("capture_quality")
    quality = quality if isinstance(quality, Mapping) else {}
    return {
        name: quality.get(name)
        for name in (
            "import_late_packets_ignored",
            "import_frame_overflow_packets_dropped",
        )
    }


def _warning_run_summary(metadata: Mapping[str, object]) -> dict[str, object]:
    capture = metadata.get("capture")
    capture = capture if isinstance(capture, Mapping) else {}
    completion = capture.get("completion")
    completion = completion if isinstance(completion, Mapping) else {}
    return {
        "capture": {
            "complete": capture.get("complete"),
            "footer_status": completion.get("status"),
            "recording_counters": {
                name: completion.get(name)
                for name in (
                    "queue_dropped",
                    "unpersisted_on_shutdown",
                    "socket_errors",
                )
            },
        },
        "processing": {"replay_counters": _replay_counters(metadata)},
    }


def _append_metadata_limit_warnings(
    warnings: list[dict[str, str]], metadata: Mapping[str, object]
) -> None:
    limits = metadata.get("metadata_limits")
    limits = limits if isinstance(limits, Mapping) else {}
    if limits.get("capture_completion_truncated") is True:
        warnings.append(
            {
                "code": "capture_completion_metadata_truncated",
                "text": "Capture footer details exceed the source metadata limit.",
            }
        )
    if limits.get("processing_metrics_truncated") is True:
        warnings.append(
            {
                "code": "replay_frame_counters_unknown",
                "text": "Replay-loss counters exceed the source metadata limit.",
            }
        )
