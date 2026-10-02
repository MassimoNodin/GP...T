from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping, Sequence

from .continuity import MAX_SESSION_TIME_GAP_S, session_time_discontinuity
from .resampling import ResampledTrace, ResamplingConfig, TraceSample, resample_trace
from ..storage.query import StoredAttemptTrace, load_attempt_trace, load_reference_inventory


QUALITY_REPORT_VERSION = 1
QUALITY_ANALYSIS_VERSION = "attempt-telemetry-quality-v1"
QUALITY_TRACE_COLUMNS = [
    "frame_identifier",
    "session_time_s",
    "lap_distance_m",
    "current_lap_time_ms",
    "speed_mps",
    "throttle",
    "brake",
    "steering",
    "gear",
    "drs_active",
    "car_telemetry_available",
    "motion_available",
    "world_position_x_m",
    "world_position_y_m",
    "world_position_z_m",
    "world_velocity_x_mps",
    "world_velocity_y_mps",
    "world_velocity_z_mps",
]
QUALITY_RESAMPLING = ResamplingConfig()
_FRAME_MASK = 0xFFFFFFFF
_SERIAL_HALF_RANGE = 0x80000000
_MAX_GRID_POINTS = 100_000
_MAX_EXAMPLES = 20
_CHANNELS = (
    "time_s",
    "speed_mps",
    "throttle",
    "brake",
    "steering",
    "gear",
    "drs_active",
)
_CHANNEL_FIELDS = {
    "time_s": "current_lap_time_ms",
    "speed_mps": "speed_mps",
    "throttle": "throttle",
    "brake": "brake",
    "steering": "steering",
    "gear": "gear",
    "drs_active": "drs_active",
}


def inspect_attempt_quality(
    database_path: str | Path, attempt_key: str
) -> dict[str, object] | None:
    """Return standalone quality evidence for any persisted attempt with a trace."""
    attempt = load_attempt_trace(
        database_path, attempt_key, columns=QUALITY_TRACE_COLUMNS
    )
    if attempt is None:
        return None
    inventory = load_reference_inventory(database_path, attempt_key)
    if inventory is None:
        return None

    samples = attempt.samples
    trace_samples = tuple(TraceSample.from_record(sample) for sample in samples)
    context_segments = [
        {
            "from_frame_identifier": frame,
            "context": _json_safe(context) if context is not None else None,
        }
        for frame, context in attempt.context_segments
    ]
    report_track_length, track_length_source, track_length_reason = _track_length(
        attempt.context_segments
    )
    observed_min, observed_max = _observed_distance_range(
        samples, report_track_length
    )
    continuity = _continuity_report(samples)

    grid, grid_reason = _distance_grid(
        report_track_length, observed_min, observed_max
    )
    resampled = None
    resampling_reason = grid_reason
    if grid:
        try:
            resampled = resample_trace(
                trace_samples,
                grid,
                QUALITY_RESAMPLING,
                track_length_m=report_track_length,
            )
        except ValueError as exc:
            resampling_reason = str(exc)

    channels = {
        channel: _availability(samples, field, special=channel == "time_s")
        for channel, field in _CHANNEL_FIELDS.items()
    }
    if attempt.trace_schema_version == 1:
        channels["motion_available"] = {
            "status": "unavailable_in_trace_schema",
            "available_samples": None,
            "sample_count": len(samples),
            "missing_samples": None,
        }
    else:
        channels["motion_available"] = _availability(
            samples, "motion_available", true_only=True
        )
    channels["world_position"] = _vector_availability(
        samples,
        ("world_position_x_m", "world_position_y_m", "world_position_z_m"),
        schema_version=attempt.trace_schema_version,
    )
    channels["world_velocity"] = _vector_availability(
        samples,
        ("world_velocity_x_mps", "world_velocity_y_mps", "world_velocity_z_mps"),
        schema_version=attempt.trace_schema_version,
    )

    distance_support = _distance_support(
        channels=_CHANNELS,
        resampled=resampled,
        grid=grid,
        observed_min=observed_min,
        observed_max=observed_max,
        track_length_m=report_track_length,
        unavailable_reason=resampling_reason,
    )
    capture_quality = dict(inventory.processing_quality)
    raw_completion = inventory.capture_completion
    completion = raw_completion if isinstance(raw_completion, Mapping) else None
    recording_fields = (
        "elapsed_ms",
        "received",
        "queued",
        "recorded",
        "queue_dropped",
        "socket_errors",
        "unpersisted_on_shutdown",
    )
    recording_counters, recording_counter_status = _counter_evidence(
        completion, recording_fields
    )
    footer_status = completion.get("status") if completion is not None else None
    footer_status_evidence = (
        "missing"
        if completion is None or "status" not in completion
        else "available"
        if isinstance(footer_status, str) and footer_status.strip()
        else "invalid"
    )
    if not isinstance(footer_status, str) or not footer_status.strip():
        footer_status = None
    footer_evidence_status = (
        "unavailable"
        if raw_completion is None
        else "available"
        if completion is not None
        else "invalid_shape"
    )
    recording = {
        "capture_complete": inventory.capture_complete,
        "footer_available": completion is not None,
        "footer_evidence_status": footer_evidence_status,
        "footer_status": footer_status,
        "footer_status_evidence": footer_status_evidence,
        "counters": recording_counters,
        "counter_evidence": recording_counter_status,
    }
    observer_fields = ("late_packets_ignored", "frame_overflow_packets_dropped")
    observer_counters, observer_counter_status = _counter_evidence(
        completion, observer_fields
    )
    assembly_fields = (
        "import_late_packets_ignored",
        "import_frame_overflow_packets_dropped",
    )
    assembly_counters, assembly_counter_status = _counter_evidence(
        capture_quality, assembly_fields
    )
    return {
        "report_version": QUALITY_REPORT_VERSION,
        "analysis_version": QUALITY_ANALYSIS_VERSION,
        "identity": {
            "attempt_key": attempt.attempt_key,
            "run_id": attempt.run_id,
            "session_uid": attempt.session_uid,
            "car_index": attempt.car_index,
            "attempt_number": attempt.attempt_number,
            "trace_sha256": attempt.trace_sha256,
            "trace_schema_version": attempt.trace_schema_version,
            "trace_row_count": len(samples),
            "trace_checksum_verified": True,
        },
        "attempt": {
            "disposition": attempt.disposition,
            "lap_time_ms": attempt.lap_time_ms,
            "game_valid": attempt.game_valid,
            "reference_eligible": attempt.reference_eligible,
            "start_observed": attempt.start_observed,
            "pit_encountered": attempt.pit_encountered,
            "exclusion_reasons": list(attempt.exclusion_reasons),
        },
        "context": {
            "segments": context_segments,
            "session_types": _json_safe(
                _distinct_context_values(attempt.context_segments, "session_type")
            ),
            "game_modes": _json_safe(
                _distinct_context_values(attempt.context_segments, "game_mode")
            ),
            "track_names": _json_safe(
                _distinct_context_values(attempt.context_segments, "track_name")
            ),
        },
        "evidence": {
            "recording": recording,
            "capture_decode": {
                "available": bool(capture_quality),
                "capture_wide_counters": _json_safe(capture_quality),
            },
            "replay_assembly": {
                "available": all(
                    value == "available" for value in assembly_counter_status.values()
                ),
                "counters": assembly_counters,
                "counter_evidence": assembly_counter_status,
            },
            "recording_observer": {
                "available": all(
                    value == "available" for value in observer_counter_status.values()
                ),
                "source_scope": "raw_capture_processing_before_import",
                "counters": observer_counters,
                "counter_evidence": observer_counter_status,
            },
            "attempt_trace": _json_safe(attempt.quality),
        },
        "channels": channels,
        "continuity": continuity,
        "distance_support": {
            "resolution_m": QUALITY_RESAMPLING.grid_step_m,
            "resampling_config": QUALITY_RESAMPLING.to_dict(),
            "track_length_m": report_track_length,
            "track_length_source": track_length_source,
            "track_length_unavailable_reason": track_length_reason,
            "observed_distance_range_m": (
                [observed_min, observed_max]
                if observed_min is not None and observed_max is not None
                else None
            ),
            **distance_support,
        },
    }


def _availability(
    samples: Sequence[Mapping[str, object]], field: str, *, true_only: bool = False,
    special: bool = False,
) -> dict[str, object]:
    if special:
        present = sum(_finite_number(sample.get(field)) is not None for sample in samples)
    elif true_only:
        present = sum(sample.get(field) is True for sample in samples)
    else:
        present = sum(sample.get(field) is not None for sample in samples)
    return {
        "status": _availability_status(present, len(samples)),
        "available_samples": present,
        "sample_count": len(samples),
        "missing_samples": len(samples) - present,
    }


def _vector_availability(
    samples: Sequence[Mapping[str, object]],
    fields: Sequence[str],
    *,
    schema_version: int,
) -> dict[str, object]:
    if schema_version == 1:
        return {
            "status": "unavailable_in_trace_schema",
            "available_samples": None,
            "sample_count": len(samples),
            "missing_samples": None,
        }
    present = sum(
        all(_finite_number(sample.get(field)) is not None for field in fields)
        for sample in samples
    )
    return {
        "status": _availability_status(present, len(samples)),
        "available_samples": present,
        "sample_count": len(samples),
        "missing_samples": len(samples) - present,
    }


def _availability_status(present: int, total: int) -> str:
    if total == 0 or present == 0:
        return "unavailable"
    if present == total:
        return "available"
    return "partial"


def _track_length(
    segments: Sequence[tuple[int, Mapping[str, object] | None]],
) -> tuple[float | None, str | None, str | None]:
    values = {
        number
        for _, context in segments
        if context is not None
        and (number := _finite_number(context.get("track_length_m"))) is not None
        and number > 0
    }
    if not values:
        return None, None, "track_length_not_reported_in_attempt_context"
    if len(values) > 1:
        return None, None, "track_length_changed_during_attempt"
    return values.pop(), "attempt_session_context", None


def _distinct_context_values(
    segments: Sequence[tuple[int, Mapping[str, object] | None]], field: str
) -> list[object]:
    values: list[object] = []
    for _, context in segments:
        value = context.get(field) if context is not None else None
        if value is not None and value not in values:
            values.append(value)
    return values


def _observed_distance_range(
    samples: Sequence[Mapping[str, object]], track_length_m: float | None
) -> tuple[float | None, float | None]:
    distances = [
        distance
        for sample in samples
        if (distance := _finite_number(sample.get("lap_distance_m"))) is not None
        and distance >= 0
        and (track_length_m is None or distance <= track_length_m)
    ]
    return (min(distances), max(distances)) if distances else (None, None)


def _distance_grid(
    track_length_m: float | None,
    observed_min: float | None,
    observed_max: float | None,
) -> tuple[tuple[float, ...], str | None]:
    if track_length_m is not None:
        final = math.floor(track_length_m)
        if final + 1 > _MAX_GRID_POINTS:
            return (), "track_length_exceeds_quality_grid_limit"
        return tuple(float(value) for value in range(final + 1)), None
    if observed_min is None or observed_max is None:
        return (), "observed_lap_distance_unavailable"
    first = math.ceil(observed_min)
    final = math.floor(observed_max)
    if first > final:
        return (), "observed_distance_range_below_grid_resolution"
    if final - first + 1 > _MAX_GRID_POINTS:
        return (), "observed_distance_range_exceeds_quality_grid_limit"
    return tuple(float(value) for value in range(first, final + 1)), None


def _distance_support(
    *,
    channels: Sequence[str],
    resampled: ResampledTrace | None,
    grid: Sequence[float],
    observed_min: float | None,
    observed_max: float | None,
    track_length_m: float | None,
    unavailable_reason: str | None,
) -> dict[str, object]:
    if resampled is None:
        return {
            "status": "unavailable",
            "reason": unavailable_reason or "distance_resampling_unavailable",
            "channels": {
                channel: {"observed_range_coverage": None, "full_track_coverage": None}
                for channel in channels
            },
        }

    result = resampled
    values: dict[str, object] = {}
    observed_indices = [
        index
        for index, distance in enumerate(grid)
        if observed_min is not None
        and observed_max is not None
        and observed_min - 1e-7 <= distance <= observed_max + 1e-7
    ]
    for channel in channels:
        mask = result.masks[channel]
        observed_coverage = (
            sum(mask[index] for index in observed_indices) / len(observed_indices)
            if observed_indices
            else None
        )
        values[channel] = {
            "observed_range_coverage": observed_coverage,
            "observed_grid_point_count": len(observed_indices),
            "full_track_coverage": (
                result.coverage[channel] if track_length_m is not None else None
            ),
            "full_track_grid_point_count": len(grid) if track_length_m is not None else None,
        }
    return {
        "status": "available",
        "reason": None,
        "channels": values,
    }


def _continuity_report(samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    frame_gaps = 0
    largest_frame_gap = 0
    frame_order_discontinuities = 0
    lap_clock_gaps = 0
    lap_clock_regressions = 0
    largest_lap_clock_gap = 0.0
    distance_gaps = 0
    distance_regressions = 0
    largest_distance_gap = 0.0
    session_time_gaps = 0
    session_time_regressions = 0
    largest_session_time_gap = 0.0
    examples: list[dict[str, object]] = []

    def example(reason: str, previous: Mapping[str, object], current: Mapping[str, object], delta: float | int | None) -> None:
        if len(examples) >= _MAX_EXAMPLES:
            return
        examples.append({
            "reason": reason,
            "from_frame_identifier": previous.get("frame_identifier"),
            "to_frame_identifier": current.get("frame_identifier"),
            "delta": delta,
        })

    for previous, current in zip(samples, samples[1:]):
        previous_frame = previous.get("frame_identifier")
        current_frame = current.get("frame_identifier")
        if isinstance(previous_frame, int) and isinstance(current_frame, int):
            frame_delta = (current_frame - previous_frame) & _FRAME_MASK
            if 1 < frame_delta < _SERIAL_HALF_RANGE:
                frame_gaps += 1
                largest_frame_gap = max(largest_frame_gap, frame_delta - 1)
                example("frame_gap", previous, current, frame_delta - 1)
            elif frame_delta == 0 or frame_delta >= _SERIAL_HALF_RANGE:
                frame_order_discontinuities += 1
                example("frame_order_discontinuity", previous, current, frame_delta)

        previous_clock = _finite_number(previous.get("current_lap_time_ms"))
        current_clock = _finite_number(current.get("current_lap_time_ms"))
        if previous_clock is not None and current_clock is not None:
            clock_gap = (current_clock - previous_clock) / 1000.0
            if clock_gap < 0:
                lap_clock_regressions += 1
                example("lap_clock_regression", previous, current, clock_gap)
            else:
                largest_lap_clock_gap = max(largest_lap_clock_gap, clock_gap)
                if clock_gap > QUALITY_RESAMPLING.max_bracket_time_s + 1e-9:
                    lap_clock_gaps += 1
                    example("lap_clock_gap", previous, current, clock_gap)

        previous_distance = _finite_number(previous.get("lap_distance_m"))
        current_distance = _finite_number(current.get("lap_distance_m"))
        if previous_distance is not None and current_distance is not None:
            distance_gap = current_distance - previous_distance
            if distance_gap < 0:
                distance_regressions += 1
                example("distance_regression", previous, current, distance_gap)
            else:
                largest_distance_gap = max(largest_distance_gap, distance_gap)
                if distance_gap > QUALITY_RESAMPLING.max_bracket_distance_m:
                    distance_gaps += 1
                    example("distance_gap", previous, current, distance_gap)

        previous_session_time = _finite_number(previous.get("session_time_s"))
        current_session_time = _finite_number(current.get("session_time_s"))
        if previous_session_time is not None and current_session_time is not None:
            session_gap = current_session_time - previous_session_time
            time_break = session_time_discontinuity(
                previous_session_time,
                current_session_time,
                max_gap_s=MAX_SESSION_TIME_GAP_S,
            )
            if time_break == "session_time_regression":
                session_time_regressions += 1
                example("session_time_regression", previous, current, session_gap)
            else:
                largest_session_time_gap = max(largest_session_time_gap, session_gap, 0.0)
                if time_break == "session_time_gap":
                    session_time_gaps += 1
                    example("session_time_gap", previous, current, session_gap)

    return {
        "sample_adjacency_count": max(0, len(samples) - 1),
        "frame_gaps": {
            "count": frame_gaps,
            "largest_missing_frame_count": largest_frame_gap,
            "order_discontinuity_count": frame_order_discontinuities,
        },
        "lap_clock": {
            "gap_threshold_s": QUALITY_RESAMPLING.max_bracket_time_s,
            "gap_count": lap_clock_gaps,
            "regression_count": lap_clock_regressions,
            "largest_nonnegative_gap_s": largest_lap_clock_gap,
        },
        "session_time": {
            "gap_threshold_s": MAX_SESSION_TIME_GAP_S,
            "gap_count": session_time_gaps,
            "regression_count": session_time_regressions,
            "largest_nonnegative_gap_s": largest_session_time_gap,
        },
        "lap_distance": {
            "gap_threshold_m": QUALITY_RESAMPLING.max_bracket_distance_m,
            "gap_count": distance_gaps,
            "regression_count": distance_regressions,
            "largest_nonnegative_gap_m": largest_distance_gap,
        },
        "examples": examples,
        "examples_truncated": (
            frame_gaps + frame_order_discontinuities + lap_clock_gaps
            + lap_clock_regressions + distance_gaps + distance_regressions
            + session_time_gaps + session_time_regressions > len(examples)
        ),
    }


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _counter_evidence(
    source: Mapping[str, object] | None, fields: Sequence[str]
) -> tuple[dict[str, int | None], dict[str, str]]:
    values: dict[str, int | None] = {}
    evidence: dict[str, str] = {}
    for field in fields:
        if source is None or field not in source:
            values[field] = None
            evidence[field] = "missing"
            continue
        value = source[field]
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values[field] = value
            evidence[field] = "available"
        else:
            values[field] = None
            evidence[field] = "invalid"
    return values, evidence


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return None
