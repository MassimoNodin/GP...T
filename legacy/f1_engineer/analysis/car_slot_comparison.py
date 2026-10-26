from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping, Sequence

from .comparison import calculate_channel_differences, calculate_delta_time
from .comparison_window import DistanceWindow
from .distance_support import requested_window_coverage
from .interval_delta import evaluate_interval_delta, reserve_interval_evaluation_work
from .resampling import (
    ResamplingConfig,
    TraceSample,
    common_distance_grid,
    estimate_resampling_work,
    resample_trace,
)
from .service import _load_bounded_attempt_trace, stable_practice_qualifying_context
from ..storage.query import (
    MAX_CAR_LAP_ANALYSIS_ROWS,
    MAX_CAR_LAP_OBSERVATION_ATTEMPT_KEY_BYTES,
    MAX_OBSERVATION_PREVIEW_SOURCE_BYTES,
    load_attempt_association_scope,
    load_car_lap_observation_source,
)


PLAYER_SLOT_COMPARISON_POLICY = "pq-player-slot-window-diagnostic-v1"
MAX_PLAYER_SLOT_WINDOW_M = 2_000.0
MAX_PLAYER_SLOT_GRID_POINTS = 100_000
MAX_PLAYER_SLOT_ANALYSIS_WORK = 32_000_000
MAX_PLAYER_SLOT_RESPONSE_BYTES = 2 * 1024 * 1024


def compare_player_slot_window(
    database_path: str | Path,
    target_attempt_key: str,
    slot_car_index: int,
    slot_attempt_key: str,
    window: DistanceWindow,
    *,
    config: ResamplingConfig = ResamplingConfig(),
) -> dict[str, object]:
    """Compare a player lap with one explicitly selected owned slot lap."""
    _validate_attempt_key(target_attempt_key, "target")
    _validate_attempt_key(slot_attempt_key, "slot")
    if type(slot_car_index) is not int or not 0 <= slot_car_index <= 23:
        raise ValueError("player_slot_comparison_car_index_invalid")
    if target_attempt_key == slot_attempt_key:
        raise ValueError("player_slot_comparison_attempts_must_differ")
    if config.grid_step_m != 1.0:
        raise ValueError("player_slot_comparison_requires_one_metre_grid")
    if window.end_m - window.start_m > MAX_PLAYER_SLOT_WINDOW_M:
        raise ValueError("player_slot_comparison_window_too_wide")

    target = _load_bounded_attempt_trace(
        database_path,
        target_attempt_key,
        reason_prefix="player_slot_comparison_target_source",
    )
    if target is None:
        raise ValueError("player_slot_comparison_target_unavailable")
    if target.disposition != "completed":
        raise ValueError("player_slot_comparison_target_not_completed")
    if target.game_valid is not True:
        raise ValueError("player_slot_comparison_target_validity_unknown_or_invalid")
    if not target.start_observed:
        raise ValueError("player_slot_comparison_target_start_unobserved")
    if target.pit_encountered:
        raise ValueError("player_slot_comparison_target_pit_encountered")
    if target.source_sample_count is None or target.source_bytes_read is None:
        raise ValueError("player_slot_comparison_target_source_limits_unavailable")

    target_scope = load_attempt_association_scope(database_path, target_attempt_key)
    if target_scope is None or target_scope.get("association_scope_assessable") is not True:
        raise ValueError("player_slot_comparison_target_lifecycle_scope_unassessable")
    if (
        target_scope.get("run_id") != target.run_id
        or str(target_scope.get("session_uid")) != str(target.session_uid)
    ):
        raise ValueError("player_slot_comparison_target_scope_mismatch")
    remaining_source_bytes = MAX_OBSERVATION_PREVIEW_SOURCE_BYTES - target.source_bytes_read
    remaining_rows = MAX_CAR_LAP_ANALYSIS_ROWS - target.source_sample_count
    if remaining_source_bytes <= 0:
        raise ValueError("player_slot_comparison_shared_source_bytes_limit_exceeded")
    if remaining_rows <= 0:
        raise ValueError("player_slot_comparison_shared_analysis_rows_limit_exceeded")

    slot_source = load_car_lap_observation_source(
        database_path,
        target.run_id,
        target.session_uid,
        slot_car_index,
        slot_attempt_key,
        source_bytes_limit=remaining_source_bytes,
        retained_row_limit=remaining_rows,
    )
    if slot_source is None or slot_source.get("status") != "available":
        raise ValueError("player_slot_comparison_slot_source_unavailable")
    slot_attempt = _mapping(slot_source.get("attempt"))
    observations = _mapping(slot_source.get("observations"))
    slot_tenure = _mapping(slot_attempt.get("tenure")) if slot_attempt else None
    raw_slot_rows = observations.get("items") if observations else None
    raw_chunks = slot_source.get("source_chunks")
    if (
        slot_attempt is None
        or observations is None
        or slot_tenure is None
        or not isinstance(raw_slot_rows, list)
        or not raw_slot_rows
        or not isinstance(raw_chunks, list)
        or not raw_chunks
    ):
        raise ValueError("player_slot_comparison_slot_source_invalid")
    if (
        slot_attempt.get("attempt_key") != slot_attempt_key
        or slot_attempt.get("car_index") != slot_car_index
        or slot_source.get("run_id") != target.run_id
        or str(slot_source.get("session_uid")) != str(target.session_uid)
        or slot_attempt.get("disposition") != "completed"
        or slot_attempt.get("game_valid") is not True
        or slot_attempt.get("start_observed") is not True
        or slot_attempt.get("pit_encountered") is not False
        or not isinstance(slot_attempt.get("lap_time_ms"), int)
        or isinstance(slot_attempt.get("lap_time_ms"), bool)
        or slot_attempt["lap_time_ms"] <= 0
        or target.car_index == slot_car_index
    ):
        raise ValueError("player_slot_comparison_slot_attempt_not_admitted")

    target_packet_format = target_scope.get("packet_format")
    target_epoch = target_scope.get("association_epoch")
    slot_packet_format = slot_attempt.get("packet_format")
    slot_epoch = slot_attempt.get("lifecycle_epoch")
    if (
        type(target_packet_format) is not int
        or type(target_epoch) is not int
        or target_packet_format != slot_packet_format
        or target_epoch != slot_epoch
        or slot_tenure.get("ordinal") != slot_attempt.get("tenure_ordinal")
    ):
        raise ValueError("player_slot_comparison_lifecycle_scope_mismatch")

    target_context, target_signature = stable_practice_qualifying_context(
        target.context_segments, target_attempt_key
    )
    raw_contexts = slot_attempt.get("context_segments")
    if not isinstance(raw_contexts, list) or not raw_contexts:
        raise ValueError("player_slot_comparison_slot_context_unavailable")
    slot_context_segments: list[tuple[int, Mapping[str, object] | None]] = []
    for segment in raw_contexts:
        segment_mapping = _mapping(segment)
        if (
            segment_mapping is None
            or type(segment_mapping.get("from_frame_identifier")) is not int
            or (segment_mapping.get("context") is not None
                and not isinstance(segment_mapping.get("context"), Mapping))
        ):
            raise ValueError("player_slot_comparison_slot_context_invalid")
        context = segment_mapping.get("context")
        slot_context_segments.append(
            (
                int(segment_mapping["from_frame_identifier"]),
                context if isinstance(context, Mapping) else None,
            )
        )
    slot_context, slot_signature = stable_practice_qualifying_context(
        tuple(slot_context_segments), slot_attempt_key
    )
    if target_signature != slot_signature:
        raise ValueError("player_slot_comparison_context_incompatible")
    track_length = target_context["track_length_m"]
    if (
        not isinstance(track_length, (int, float))
        or isinstance(track_length, bool)
        or not math.isfinite(track_length)
        or track_length <= 0
    ):
        raise ValueError("player_slot_comparison_track_length_invalid")
    window.validate_track_length(float(track_length))
    full_grid_count = math.floor(float(track_length) / config.grid_step_m) + 1
    if full_grid_count > MAX_PLAYER_SLOT_GRID_POINTS:
        raise ValueError("player_slot_comparison_grid_limit_exceeded")

    target_samples = tuple(TraceSample.from_record(row) for row in target.samples)
    slot_samples: list[TraceSample] = []
    previous_frame_ordinal: int | None = None
    for raw_row in raw_slot_rows:
        row = _mapping(raw_row)
        if row is None:
            raise ValueError("player_slot_comparison_slot_row_invalid")
        frame_ordinal = row.get("frame_ordinal")
        if (
            type(frame_ordinal) is not int
            or row.get("session_uid") != str(target.session_uid)
            or row.get("car_index") != slot_car_index
            or row.get("header_player_car_index") != target.car_index
            or row.get("packet_format") != target_packet_format
            or row.get("lifecycle_epoch") != target_epoch
        ):
            raise ValueError("player_slot_comparison_slot_row_scope_mismatch")
        if previous_frame_ordinal is not None and frame_ordinal <= previous_frame_ordinal:
            raise ValueError("player_slot_comparison_slot_row_order_invalid")
        previous_frame_ordinal = frame_ordinal
        slot_samples.append(TraceSample.from_record(row))
    if not target_samples or not slot_samples:
        raise ValueError("player_slot_comparison_source_rows_unavailable")
    if len(target_samples) + len(slot_samples) > MAX_CAR_LAP_ANALYSIS_ROWS:
        raise ValueError("player_slot_comparison_shared_analysis_rows_limit_exceeded")
    slot_source_bytes = slot_source.get("source_bytes_read")
    if type(slot_source_bytes) is not int or slot_source_bytes < 0:
        raise ValueError("player_slot_comparison_slot_source_bytes_invalid")
    total_source_bytes = target.source_bytes_read + slot_source_bytes
    if total_source_bytes > MAX_OBSERVATION_PREVIEW_SOURCE_BYTES:
        raise ValueError("player_slot_comparison_shared_source_bytes_limit_exceeded")
    reported_slot_rows = observations.get("verified_row_count")
    if type(reported_slot_rows) is not int or reported_slot_rows != len(slot_samples):
        raise ValueError("player_slot_comparison_slot_row_count_mismatch")

    work = estimate_resampling_work(
        len(target_samples), full_grid_count, config
    ) + estimate_resampling_work(len(slot_samples), full_grid_count, config)
    if work > MAX_PLAYER_SLOT_ANALYSIS_WORK:
        raise ValueError("player_slot_comparison_analysis_work_limit_exceeded")
    grid = common_distance_grid(
        target_samples,
        slot_samples,
        track_length_m=float(track_length),
        step_m=1.0,
    )
    if len(grid) > MAX_PLAYER_SLOT_GRID_POINTS:
        raise ValueError("player_slot_comparison_grid_limit_exceeded")
    target_resampled = resample_trace(
        target_samples, grid, config, track_length_m=float(track_length)
    )
    slot_resampled = resample_trace(
        slot_samples, grid, config, track_length_m=float(track_length)
    )
    delta = calculate_delta_time(target_resampled, slot_resampled)
    reserve_interval_evaluation_work(
        1,
        target_samples,
        slot_samples,
        target_resampled,
        slot_resampled,
        delta,
    )
    channel_differences = calculate_channel_differences(
        target_resampled,
        slot_resampled,
        channels=("speed_mps", "brake", "throttle"),
    )
    interval = _evaluate_window_support(
        target_samples,
        slot_samples,
        target_resampled,
        slot_resampled,
        delta,
        window,
        config,
    )
    window_indexes = [
        index
        for index, distance in enumerate(grid)
        if window.start_m <= distance < window.end_m
    ]
    charts = _chart_payload(
        grid,
        window_indexes,
        target_resampled,
        slot_resampled,
        delta,
        channel_differences,
    )
    facts = _measurement_facts(
        target_samples,
        slot_samples,
        window,
        interval,
    )
    if len(facts) > 5:
        raise ValueError("player_slot_comparison_fact_limit_exceeded")

    capture = _mapping(slot_source.get("capture")) or {}
    attempt_value = _mapping(slot_source.get("attempt")) or {}
    result: dict[str, object] = {
        "schema_version": 1,
        "analysis_version": PLAYER_SLOT_COMPARISON_POLICY,
        "comparison_policy": PLAYER_SLOT_COMPARISON_POLICY,
        "diagnostic_only": True,
        "reference_eligible": False,
        "ranking_eligible": False,
        "coaching_eligible": False,
        "window_m": window.to_dict(),
        "direction": "player_minus_recorded_slot",
        "target": {
            "attempt_key": target.attempt_key,
            "run_id": target.run_id,
            "session_uid": target.session_uid,
            "car_index": target.car_index,
            "packet_format": target_packet_format,
            "lifecycle_epoch": target_epoch,
            "association_scope_assessable": True,
            "trace_sha256": target.trace_sha256,
            "trace_schema_version": target.trace_schema_version,
            "source_row_count": len(target_samples),
            "lap_time_ms": target.lap_time_ms,
        },
        "recorded_slot": {
            "attempt_key": slot_attempt_key,
            "run_id": target.run_id,
            "session_uid": target.session_uid,
            "car_index": slot_car_index,
            "packet_format": slot_packet_format,
            "lifecycle_epoch": slot_epoch,
            "tenure": dict(slot_tenure),
            "lap_number": attempt_value.get("lap_number"),
            "lap_time_ms": attempt_value.get("lap_time_ms"),
            "source_row_count": len(slot_samples),
            "source_chunks": raw_chunks,
        },
        "track": {
            "track_id": target_context["track_id"],
            "track_name": target_context["track_name"],
            "track_length_m": track_length,
        },
        "capture": {
            "complete": capture.get("complete"),
            "footer_status": capture.get("footer_status"),
            "replay_quality": capture.get("replay_quality"),
        },
        "condition_limitations": [
            "recorded_slot_has_no_stable_driver_identity",
            "slot_fuel_condition_unassessed",
            "slot_tyre_condition_unassessed",
            "slot_damage_condition_unassessed",
        ],
        "window_support": interval,
        "distance_m": [grid[index] for index in window_indexes],
        "charts": charts,
        "measured_facts": facts,
        "source_read_bytes_total": total_source_bytes,
        "analysis_work_estimate": work,
    }
    result["response_bytes"] = 0
    try:
        response_bytes = len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        )
        result["response_bytes"] = response_bytes
        response_bytes = len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("player_slot_comparison_result_invalid") from exc
    if response_bytes > MAX_PLAYER_SLOT_RESPONSE_BYTES:
        raise ValueError("player_slot_comparison_response_limit_exceeded")
    return result


def _validate_attempt_key(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_CAR_LAP_OBSERVATION_ATTEMPT_KEY_BYTES
    ):
        raise ValueError(f"player_slot_comparison_{name}_attempt_key_invalid")


def _evaluate_window_support(
    target_samples: Sequence[TraceSample],
    slot_samples: Sequence[TraceSample],
    target_resampled: object,
    slot_resampled: object,
    delta: object,
    window: DistanceWindow,
    config: ResamplingConfig,
) -> dict[str, object]:
    interval = evaluate_interval_delta(
        target_samples,
        slot_samples,
        target_resampled,
        slot_resampled,
        delta,
        window.start_m,
        window.end_m,
        config=config,
    )
    target_spans = tuple(
        span
        for span in target_resampled.excluded_spans
        if span.end_distance_m > window.start_m and span.start_distance_m < window.end_m
    )
    slot_spans = tuple(
        span
        for span in slot_resampled.excluded_spans
        if span.end_distance_m > window.start_m and span.start_distance_m < window.end_m
    )
    return {
        "interval_convention": "[start_m, end_m)",
        "status": interval.status,
        "target_time_coverage": interval.target_time_coverage,
        "recorded_slot_time_coverage": interval.reference_time_coverage,
        "shared_time_coverage": interval.shared_time_coverage,
        "interval_connected_supported_time": interval.interval_connected_supported_time,
        "entry_delta_s": interval.start_delta_s,
        "exit_delta_s": interval.end_delta_s,
        "delta_change_s": interval.delta_change_s,
        "unavailable_reasons": list(interval.unavailable_reasons),
        "target_speed_coverage": requested_window_coverage(
            target_resampled, window.start_m, window.end_m, config.grid_step_m, channel="speed_mps"
        ),
        "recorded_slot_speed_coverage": requested_window_coverage(
            slot_resampled, window.start_m, window.end_m, config.grid_step_m, channel="speed_mps"
        ),
        "target_brake_coverage": requested_window_coverage(
            target_resampled, window.start_m, window.end_m, config.grid_step_m, channel="brake"
        ),
        "recorded_slot_brake_coverage": requested_window_coverage(
            slot_resampled, window.start_m, window.end_m, config.grid_step_m, channel="brake"
        ),
        "target_throttle_coverage": requested_window_coverage(
            target_resampled, window.start_m, window.end_m, config.grid_step_m, channel="throttle"
        ),
        "recorded_slot_throttle_coverage": requested_window_coverage(
            slot_resampled, window.start_m, window.end_m, config.grid_step_m, channel="throttle"
        ),
        "unsupported_span_count": len(target_spans) + len(slot_spans),
    }


def _chart_payload(
    grid: Sequence[float],
    indexes: Sequence[int],
    target: object,
    slot: object,
    delta: object,
    differences: Mapping[str, object],
) -> dict[str, object]:
    charts: dict[str, object] = {
        "lap_time_delta_s": {
            "player_minus_slot": [delta.values_s[index] for index in indexes],
            "supported": [delta.mask[index] for index in indexes],
            "coverage": sum(delta.mask[index] for index in indexes) / max(1, len(indexes)),
        }
    }
    channel_specs = (
        ("speed_mps", "speed_kph", 3.6, "km/h"),
        ("brake", "brake_pct", 100.0, "%"),
        ("throttle", "throttle_pct", 100.0, "%"),
    )
    for source_name, chart_name, scale, unit in channel_specs:
        difference = differences[source_name]
        target_values = target.values[source_name]
        slot_values = slot.values[source_name]
        target_mask = target.masks[source_name]
        slot_mask = slot.masks[source_name]
        charts[chart_name] = {
            "unit": unit,
            "player": [
                None if target_values[index] is None else float(target_values[index]) * scale
                for index in indexes
            ],
            "recorded_slot": [
                None if slot_values[index] is None else float(slot_values[index]) * scale
                for index in indexes
            ],
            "player_minus_slot": [
                None
                if difference.values[index] is None
                else float(difference.values[index]) * scale
                for index in indexes
            ],
            "supported": [
                target_mask[index] and slot_mask[index] for index in indexes
            ],
            "coverage": sum(difference.mask[index] for index in indexes)
            / max(1, len(indexes)),
        }
    return charts


def _measurement_facts(
    target_samples: Sequence[TraceSample],
    slot_samples: Sequence[TraceSample],
    window: DistanceWindow,
    interval: Mapping[str, object],
) -> list[dict[str, object]]:
    delta_status = "observed" if interval.get("interval_connected_supported_time") is True else "unavailable"
    facts: list[dict[str, object]] = [
        {
            "id": "window_time_delta_change",
            "status": delta_status,
            "value": interval.get("delta_change_s") if delta_status == "observed" else None,
            "unit": "s",
        }
    ]
    facts.extend(
        (
            _extreme_fact("player_minimum_speed", target_samples, window, "speed_mps", "min", scale=3.6, unit="km/h"),
            _extreme_fact("recorded_slot_minimum_speed", slot_samples, window, "speed_mps", "min", scale=3.6, unit="km/h"),
            _extreme_fact("player_peak_brake", target_samples, window, "brake", "max", scale=100.0, unit="%"),
            _extreme_fact("recorded_slot_peak_brake", slot_samples, window, "brake", "max", scale=100.0, unit="%"),
        )
    )
    return facts[:5]


def _extreme_fact(
    fact_id: str,
    samples: Sequence[TraceSample],
    window: DistanceWindow,
    channel: str,
    direction: str,
    *,
    scale: float,
    unit: str,
) -> dict[str, object]:
    candidates = [
        sample
        for sample in samples
        if sample.distance_m is not None
        and window.start_m <= sample.distance_m < window.end_m
        and (value := getattr(sample, channel)) is not None
        and math.isfinite(float(value))
    ]
    if not candidates:
        return {"id": fact_id, "status": "unavailable_no_samples", "value": None, "unit": unit}
    selected = (
        min(candidates, key=lambda sample: float(getattr(sample, channel)))
        if direction == "min"
        else max(candidates, key=lambda sample: float(getattr(sample, channel)))
    )
    value = getattr(selected, channel)
    return {
        "id": fact_id,
        "status": "observed",
        "value": float(value) * scale,
        "unit": unit,
        "distance_m": selected.distance_m,
        "frame_identifier": selected.frame_identifier,
        "session_time_s": selected.session_time_s,
    }


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None
