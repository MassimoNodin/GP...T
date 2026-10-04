from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from .comparison_window import (
    DistanceWindow,
    analyze_single_attempt_window,
)
from .resampling import ResamplingConfig, TraceSample, resample_trace
from .service import (
    ComparisonPolicy,
    TimeTrialContextError,
    stable_practice_qualifying_context,
    stable_time_trial_context,
)
from ..storage.query import (
    ANALYSIS_TRACE_COLUMNS,
    AttemptTraceResourceEstimate,
    AttemptTraceReadLimitError,
    StoredAttemptTrace,
    load_attempt_trace,
    load_attempt_trace_resource_estimates,
)
from ..storage.database import Database
from .source_limits import (
    ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
    ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
    ANALYSIS_SOURCE_TRACE_BYTE_LIMIT,
    ANALYSIS_SOURCE_TRACE_ROW_LIMIT,
)


OBSERVATION_SET_VERSION = "selected-window-observation-set-v1"
MAX_OBSERVATION_SET_ATTEMPTS = 8
MIN_OBSERVATION_SET_ATTEMPTS = 2
OBSERVATION_SET_GRID_POINT_LIMIT = 100_000
MIN_AGGREGATE_CONTRIBUTORS = 2
_FULL_COVERAGE_TOLERANCE = 1e-9


def build_observation_set(
    database_path: str | Path,
    attempt_keys: Sequence[str],
    window: DistanceWindow,
    *,
    policy: ComparisonPolicy | str = ComparisonPolicy.TIME_TRIAL,
    config: ResamplingConfig = ResamplingConfig(),
) -> dict[str, object]:
    """Summarize repeated, explicitly selected single-attempt window observations."""
    try:
        policy = ComparisonPolicy(policy)
    except ValueError as exc:
        raise ValueError("unsupported_comparison_policy") from exc
    keys = tuple(attempt_keys)
    if not MIN_OBSERVATION_SET_ATTEMPTS <= len(keys) <= MAX_OBSERVATION_SET_ATTEMPTS:
        raise ValueError("observation_set_attempt_count_out_of_range")
    if any(not isinstance(key, str) or not key.strip() for key in keys):
        raise ValueError("observation_set_attempt_key_invalid")
    if len(set(keys)) != len(keys):
        raise ValueError("observation_set_attempt_keys_must_be_unique")

    try:
        resources = load_attempt_trace_resource_estimates(
            database_path,
            list(keys),
            max_context_segments=ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
            max_context_bytes=ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
        )
    except AttemptTraceReadLimitError as exc:
        raise ValueError(f"observation_set_source_{exc.limit_kind}_limit_exceeded") from exc
    if len(resources) != len(keys):
        raise ValueError("observation_set_attempt_not_found_or_unavailable")

    ordered_resources = tuple(
        sorted(resources, key=lambda item: (item.attempt_number, item.attempt_key))
    )
    first = ordered_resources[0]
    for resource in ordered_resources[1:]:
        if resource.run_id != first.run_id:
            raise ValueError("observation_set_attempts_must_share_processing_run")
        if resource.session_uid != first.session_uid:
            raise ValueError("observation_set_attempts_must_share_session")
        if resource.car_index != first.car_index:
            raise ValueError("observation_set_attempts_must_share_player")

    contexts: dict[str, Mapping[str, object]] = {}
    reference_signature: tuple[object, ...] | None = None
    for resource in ordered_resources:
        try:
            if policy is ComparisonPolicy.TIME_TRIAL:
                context, signature = stable_time_trial_context(
                    resource.context_segments, resource.attempt_key
                )
            else:
                context, signature = stable_practice_qualifying_context(
                    resource.context_segments, resource.attempt_key
                )
        except TimeTrialContextError as exc:
            raise ValueError(f"observation_set_{exc.reason_code}") from exc
        except ValueError as exc:
            raise ValueError(f"observation_set_{exc}") from exc
        if reference_signature is None:
            reference_signature = signature
        elif signature != reference_signature:
            raise ValueError("observation_set_attempts_have_incompatible_context")
        contexts[resource.attempt_key] = context

    track_length = _positive_finite(contexts[first.attempt_key].get("track_length_m"))
    if track_length is None:
        raise ValueError("observation_set_invalid_track_length")
    try:
        window.validate_track_length(track_length)
    except ValueError as exc:
        raise ValueError("observation_set_distance_window_exceeds_track_length") from exc

    final_grid_index = math.floor((track_length + 1e-9) / config.grid_step_m)
    grid_point_count = final_grid_index + 1
    aggregate_grid_points = grid_point_count * len(ordered_resources)
    if aggregate_grid_points > OBSERVATION_SET_GRID_POINT_LIMIT:
        raise ValueError("observation_set_resampling_grid_limit_exceeded")
    distance_grid = tuple(
        index * config.grid_step_m for index in range(grid_point_count)
    )

    total_trace_bytes = 0
    total_trace_rows = 0
    for resource in ordered_resources:
        if not resource.trace_ready:
            continue
        if resource.trace_size_bytes is None:
            continue
        if resource.trace_row_count is None:
            continue
        total_trace_bytes += resource.trace_size_bytes
        total_trace_rows += resource.trace_row_count
    if total_trace_bytes > ANALYSIS_SOURCE_TRACE_BYTE_LIMIT:
        raise ValueError("observation_set_source_bytes_limit_exceeded")
    if total_trace_rows > ANALYSIS_SOURCE_TRACE_ROW_LIMIT:
        raise ValueError("observation_set_source_rows_limit_exceeded")

    attempt_rows: list[dict[str, object]] = []
    scalar_candidates: dict[str, list[tuple[dict[str, object], float]]] = {
        "minimum_speed": [],
        "peak_brake": [],
    }
    scalar_exclusions: dict[str, Counter[str]] = {
        "minimum_speed": Counter(),
        "peak_brake": Counter(),
    }
    used_trace_bytes = 0
    used_trace_rows = 0
    used_context_segments = 0
    used_context_bytes = 0

    for resource in ordered_resources:
        row = _attempt_row(resource)
        exclusion_reasons = _aggregate_exclusion_reasons(resource, policy)
        row["aggregate_exclusion_reasons"] = exclusion_reasons
        row["warnings"] = _attempt_warnings(resource, policy)

        if not resource.trace_ready:
            row["analysis_status"] = "unavailable"
            row["analysis_reason"] = "attempt_trace_not_ready"
            attempt_rows.append(row)
            _record_scalar_exclusions(
                scalar_exclusions, exclusion_reasons, "attempt_trace_not_ready"
            )
            continue
        if resource.trace_size_bytes is None or resource.trace_row_count is None:
            row["analysis_status"] = "unavailable"
            row["analysis_reason"] = "attempt_trace_resource_unavailable"
            attempt_rows.append(row)
            _record_scalar_exclusions(
                scalar_exclusions, exclusion_reasons, "attempt_trace_resource_unavailable"
            )
            continue

        remaining_bytes = ANALYSIS_SOURCE_TRACE_BYTE_LIMIT - used_trace_bytes
        remaining_rows = ANALYSIS_SOURCE_TRACE_ROW_LIMIT - used_trace_rows
        remaining_context_segments = (
            ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT - used_context_segments
        )
        remaining_context_bytes = (
            ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT - used_context_bytes
        )
        # Recheck each remaining budget immediately before loading and decoding this trace.
        if resource.trace_size_bytes > remaining_bytes:
            raise ValueError("observation_set_source_bytes_limit_exceeded")
        if resource.trace_row_count > remaining_rows:
            raise ValueError("observation_set_source_rows_limit_exceeded")
        if resource.context_segment_count > remaining_context_segments:
            raise ValueError("observation_set_source_context_segments_limit_exceeded")
        if resource.context_bytes > remaining_context_bytes:
            raise ValueError("observation_set_source_context_bytes_limit_exceeded")

        try:
            attempt = load_attempt_trace(
                database_path,
                resource.attempt_key,
                columns=ANALYSIS_TRACE_COLUMNS,
                max_trace_bytes=min(remaining_bytes, resource.trace_size_bytes),
                max_trace_rows=min(remaining_rows, resource.trace_row_count),
                max_context_segments=min(
                    remaining_context_segments, resource.context_segment_count
                ),
                max_context_bytes=min(remaining_context_bytes, resource.context_bytes),
            )
        except AttemptTraceReadLimitError as exc:
            raise ValueError(
                f"observation_set_source_{exc.limit_kind}_limit_exceeded"
            ) from exc
        except OSError:
            attempt = None
            row["analysis_status"] = "unavailable"
            row["analysis_reason"] = "attempt_trace_unavailable"
        except (ValueError, TypeError):
            attempt = None
            row["analysis_status"] = "unavailable"
            row["analysis_reason"] = "attempt_trace_integrity_unavailable"

        used_trace_bytes += resource.trace_size_bytes
        used_trace_rows += resource.trace_row_count
        used_context_segments += resource.context_segment_count
        used_context_bytes += resource.context_bytes
        if attempt is None:
            row.setdefault("analysis_status", "unavailable")
            row.setdefault("analysis_reason", "attempt_unavailable_after_preflight")
            attempt_rows.append(row)
            _record_scalar_exclusions(
                scalar_exclusions,
                exclusion_reasons,
                str(row["analysis_reason"]),
            )
            continue
        if not _attempt_matches_preflight_snapshot(attempt, resource):
            row["analysis_status"] = "unavailable"
            row["analysis_reason"] = "attempt_metadata_changed_during_read"
            attempt_rows.append(row)
            _record_scalar_exclusions(
                scalar_exclusions,
                exclusion_reasons,
                "attempt_metadata_changed_during_read",
            )
            continue

        if attempt.context_segments != resource.context_segments:
            row["analysis_status"] = "unavailable"
            row["analysis_reason"] = "attempt_context_changed_during_read"
            attempt_rows.append(row)
            _record_scalar_exclusions(
                scalar_exclusions, exclusion_reasons, "attempt_context_changed_during_read"
            )
            continue

        try:
            samples = tuple(TraceSample.from_record(record) for record in attempt.samples)
            resampled = resample_trace(
                samples,
                distance_grid,
                config,
                track_length_m=track_length,
            )
            observations = analyze_single_attempt_window(
                samples, resampled, window, config=config
            )
        except (ValueError, TypeError):
            row["analysis_status"] = "unavailable"
            row["analysis_reason"] = "attempt_trace_samples_invalid"
            attempt_rows.append(row)
            _record_scalar_exclusions(
                scalar_exclusions, exclusion_reasons, "attempt_trace_samples_invalid"
            )
            continue

        row["analysis_status"] = "available"
        row["analysis_reason"] = None
        row["measurements"] = observations
        attempt_rows.append(row)
        _collect_scalar_candidate(
            scalar_candidates["minimum_speed"],
            scalar_exclusions["minimum_speed"],
            row,
            observations,
            field="minimum_speed",
            value_key="speed_kph",
            channel="speed_mps",
            start_m=window.start_m,
            end_m=window.end_m,
            exclusion_reasons=exclusion_reasons,
        )
        _collect_scalar_candidate(
            scalar_candidates["peak_brake"],
            scalar_exclusions["peak_brake"],
            row,
            observations,
            field="peak_brake",
            value_key="value",
            channel="brake",
            multiplier=100.0,
            start_m=window.start_m,
            end_m=window.end_m,
            exclusion_reasons=exclusion_reasons,
        )

    aggregates = {
        "minimum_speed": _aggregate_metric(
            "minimum_speed", "km/h", scalar_candidates, scalar_exclusions
        ),
        "peak_brake": _aggregate_metric(
            "peak_brake", "percentage_points", scalar_candidates, scalar_exclusions
        ),
    }


    capture = _load_capture_evidence(database_path, first.run_id)
    for row in attempt_rows:
        row["capture_evidence"] = capture
        _append_capture_warnings(row, capture)

    return {
        "schema_version": 1,
        "artifact_kind": "selected_window_observation_set",
        "analysis_version": OBSERVATION_SET_VERSION,
        "status": "available",
        "comparison_policy": policy.value,
        "diagnostic_only": True,
        "coaching_eligible": False,
        "consistency_claim": False,
        "interval_convention": "[start_m, end_m)",
        "window_m": window.to_dict(),
        "track": {
            "track_id": contexts[first.attempt_key].get("track_id"),
            "track_name": contexts[first.attempt_key].get("track_name"),
            "track_length_m": track_length,
        },
        "scope": {
            "run_id": first.run_id,
            "session_uid": first.session_uid,
            "car_index": first.car_index,
        },
        "attempts": attempt_rows,
        "aggregates": aggregates,
        "warnings": _set_warnings(policy, capture),
        "limits": {
            "minimum_attempt_count": MIN_OBSERVATION_SET_ATTEMPTS,
            "maximum_attempt_count": MAX_OBSERVATION_SET_ATTEMPTS,
            "aggregate_grid_point_limit": OBSERVATION_SET_GRID_POINT_LIMIT,
            "aggregate_grid_points_used": aggregate_grid_points,
            "trace_bytes_used": used_trace_bytes,
            "trace_rows_used": used_trace_rows,
            "context_segments_used": used_context_segments,
            "context_bytes_used": used_context_bytes,
            "attempt_row_limit": MAX_OBSERVATION_SET_ATTEMPTS,
        },
    }


def _attempt_matches_preflight_snapshot(
    attempt: StoredAttemptTrace,
    resource: AttemptTraceResourceEstimate,
) -> bool:
    """Require the decoded trace and eligibility metadata to match preflight."""
    return (
        attempt.attempt_key == resource.attempt_key
        and attempt.run_id == resource.run_id
        and attempt.session_uid == resource.session_uid
        and attempt.car_index == resource.car_index
        and attempt.attempt_number == resource.attempt_number
        and attempt.disposition == resource.disposition
        and attempt.lap_time_ms == resource.lap_time_ms
        and attempt.game_valid == resource.game_valid
        and attempt.start_observed == resource.start_observed
        and attempt.pit_encountered == resource.pit_encountered
        and attempt.superseded == resource.superseded
        and attempt.lifecycle_assessed == resource.lifecycle_assessed
        and attempt.exclusion_reasons == resource.exclusion_reasons
        and attempt.trace_sha256 == resource.trace_sha256
        and attempt.trace_schema_version == resource.trace_schema_version
        and attempt.source_sample_count == resource.trace_row_count
        and resource.trace_ready is True
    )


def _attempt_row(resource: AttemptTraceResourceEstimate) -> dict[str, object]:
    return {
        "attempt_key": resource.attempt_key,
        "attempt_number": resource.attempt_number,
        "run_id": resource.run_id,
        "session_uid": resource.session_uid,
        "car_index": resource.car_index,
        "disposition": resource.disposition,
        "lap_time_ms": resource.lap_time_ms,
        "game_valid": resource.game_valid,
        "superseded": resource.superseded,
        "lifecycle_assessed": resource.lifecycle_assessed,
        "attempt_exclusion_reasons": list(resource.exclusion_reasons[:8]),
        "trace_sha256": resource.trace_sha256,
        "trace_schema_version": resource.trace_schema_version,
        "source_sample_count": resource.trace_row_count,
    }


def _aggregate_exclusion_reasons(
    resource: AttemptTraceResourceEstimate, policy: ComparisonPolicy
) -> list[str]:
    reasons: list[str] = []
    if resource.disposition != "completed":
        reasons.append("attempt_not_completed")
    if resource.superseded is True:
        reasons.append("superseded_by_lifecycle_evidence")
    if resource.lifecycle_assessed is not True or resource.superseded is None:
        reasons.append("lifecycle_evidence_unassessed")
    if policy is ComparisonPolicy.PRACTICE_QUALIFYING:
        if (
            not isinstance(resource.lap_time_ms, int)
            or isinstance(resource.lap_time_ms, bool)
            or resource.lap_time_ms <= 0
        ):
            reasons.append("practice_qualifying_positive_lap_time_unavailable")
        if not resource.start_observed:
            reasons.append("practice_qualifying_start_unobserved")
        if resource.pit_encountered:
            reasons.append("practice_qualifying_pit_encountered")
    return reasons


def _attempt_warnings(
    resource: AttemptTraceResourceEstimate, policy: ComparisonPolicy
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if resource.game_valid is False:
        warnings.append({"code": "game_invalid", "text": "The game marked this lap invalid."})
    elif resource.game_valid is None:
        warnings.append({"code": "game_validity_unknown", "text": "Game validity is unknown."})
    if resource.superseded is True:
        warnings.append({"code": "superseded", "text": "Lifecycle evidence supersedes this attempt."})
    if not resource.lifecycle_assessed or resource.superseded is None:
        warnings.append({"code": "lifecycle_unassessed", "text": "Lifecycle evidence is unassessed."})
    if policy is ComparisonPolicy.PRACTICE_QUALIFYING:
        warnings.append({
            "code": "practice_qualifying_conditions_uncontrolled",
            "text": "Fuel, tyres, traffic, and cooldown intent are uncontrolled.",
        })
    return warnings[:8]


def _collect_scalar_candidate(
    candidates: list[tuple[dict[str, object], float]],
    exclusions: Counter[str],
    row: dict[str, object],
    observations: Mapping[str, object],
    *,
    field: str,
    value_key: str,
    channel: str,
    start_m: float,
    end_m: float,
    exclusion_reasons: list[str],
    multiplier: float = 1.0,
) -> None:
    measurement = observations.get(field)
    measurement = measurement if isinstance(measurement, Mapping) else {}
    coverage = observations.get("coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    numeric_coverage = _finite_number(coverage.get(channel))
    raw_value = _finite_number(measurement.get(value_key))
    anchor = measurement.get("anchor")
    anchor = anchor if isinstance(anchor, Mapping) else {}
    frame = anchor.get("frame_identifier")
    anchor_time = _finite_number(anchor.get("session_time_s"))
    anchor_distance = _finite_number(anchor.get("lap_distance_m"))
    observation_valid = (
        measurement.get("status") == "observed"
        and raw_value is not None
        and raw_value >= 0
        and (field != "peak_brake" or raw_value <= 1.0)
    )
    coverage_valid = numeric_coverage is not None and math.isclose(
        numeric_coverage, 1.0, rel_tol=0, abs_tol=_FULL_COVERAGE_TOLERANCE
    )
    anchor_valid = (
        isinstance(frame, int)
        and not isinstance(frame, bool)
        and 0 <= frame <= 0xFFFFFFFF
        and anchor_time is not None
        and anchor_time >= 0
        and anchor_distance is not None
        and start_m <= anchor_distance < end_m
    )
    if not observation_valid or not coverage_valid or not anchor_valid:
        if not observation_valid:
            exclusions["observation_unavailable_or_invalid"] += 1
        if not coverage_valid:
            exclusions["incomplete_channel_coverage"] += 1
        if not anchor_valid:
            exclusions["source_anchor_invalid"] += 1
        for reason in exclusion_reasons:
            exclusions[reason] += 1
        return
    if exclusion_reasons:
        for reason in exclusion_reasons:
            exclusions[reason] += 1
        return
    assert raw_value is not None
    value = raw_value * multiplier
    candidates.append((row, value))


def _aggregate_metric(
    key: str,
    unit: str,
    candidates: Mapping[str, list[tuple[dict[str, object], float]]],
    exclusions: Mapping[str, Counter[str]],
) -> dict[str, object]:
    included = candidates[key]
    values = [value for _row, value in included]
    enough = len(values) >= MIN_AGGREGATE_CONTRIBUTORS
    minimum = min(values) if enough else None
    maximum = max(values) if enough else None
    return {
        "status": "supported" if enough else "insufficient_contributors",
        "required_contributor_count": MIN_AGGREGATE_CONTRIBUTORS,
        "contributor_count": len(values),
        "unit": unit,
        "minimum": minimum,
        "maximum": maximum,
        "range": (maximum - minimum) if enough else None,
        "contributors": [
            {"attempt_key": row["attempt_key"], "value": value}
            for row, value in included
        ],
        "exclusion_counts": [
            {"reason": reason, "attempt_count": count}
            for reason, count in sorted(exclusions[key].items())[:8]
        ],
    }


def _record_scalar_exclusions(
    exclusions: Mapping[str, Counter[str]],
    attempt_reasons: list[str],
    fallback: str,
) -> None:
    reasons = attempt_reasons or [fallback]
    for counts in exclusions.values():
        for reason in reasons:
            counts[reason] += 1


def _load_capture_evidence(database_path: str | Path, run_id: str) -> dict[str, object]:
    # Keep capture evidence compact; detailed run totals would scan unrelated attempts.
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT c.complete, c.completion_json, r.metrics_json
                 FROM processing_runs r LEFT JOIN captures c USING(capture_sha256)
                WHERE r.run_id = ?""",
            (run_id,),
        ).fetchone()
    if row is None:
        return {"complete": None, "footer_status": None, "recording_counters": {}, "replay_counters": {}}
    completion = json.loads(row["completion_json"]) if row["completion_json"] else {}
    metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
    capture_quality = metrics.get("capture_quality", {})
    if not isinstance(completion, Mapping):
        completion = {}
    if not isinstance(capture_quality, Mapping):
        capture_quality = {}
    recording_keys = ("received", "recorded", "recovered_datagrams", "queue_dropped", "unpersisted_on_shutdown", "socket_errors")
    replay_keys = ("import_late_packets_ignored", "import_frame_overflow_packets_dropped")
    return {
        "complete": None if row["complete"] is None else bool(row["complete"]),
        "footer_status": completion.get("status") if isinstance(completion.get("status"), str) else None,
        "recording_counters": _counter_values(completion, recording_keys),
        "replay_counters": _counter_values(capture_quality, replay_keys),
    }


def _counter_values(source: Mapping[str, object], keys: tuple[str, ...]) -> dict[str, int | None]:
    return {
        key: value if isinstance((value := source.get(key)), int) and not isinstance(value, bool) and value >= 0 else None
        for key in keys
    }


def _append_capture_warnings(row: dict[str, object], capture: Mapping[str, object]) -> None:
    warnings = row.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    complete = capture.get("complete")
    if complete is False:
        warnings.append({"code": "capture_incomplete", "text": "The source capture footer is incomplete."})
    elif complete is not True:
        warnings.append({"code": "capture_completion_unknown", "text": "Capture completion is unknown."})
    recording = capture.get("recording_counters")
    recording = recording if isinstance(recording, Mapping) else {}
    watched = ("queue_dropped", "unpersisted_on_shutdown", "socket_errors")
    values = [recording.get(key) for key in watched]
    known_loss = any(isinstance(value, int) and value > 0 for value in values)
    unknown_loss = any(value is None for value in values)
    if known_loss:
        text = "Recording loss or socket errors were reported."
        if unknown_loss:
            text += " Other loss counters are unknown."
        warnings.append({"code": "recording_loss_reported", "text": text})
    elif unknown_loss:
        warnings.append({"code": "recording_loss_counters_unknown", "text": "Some recording loss counters are unknown."})
    replay = capture.get("replay_counters")
    replay = replay if isinstance(replay, Mapping) else {}
    replay_values = [
        replay.get("import_late_packets_ignored"),
        replay.get("import_frame_overflow_packets_dropped"),
    ]
    known_replay = any(isinstance(value, int) and value > 0 for value in replay_values)
    unknown_replay = any(value is None for value in replay_values)
    if known_replay:
        text = "Replay excluded late or overflowed frames."
        if unknown_replay:
            text += " Other replay counters are unknown."
        warnings.append({"code": "replay_frame_exclusions_reported", "text": text})
    elif unknown_replay:
        warnings.append({"code": "replay_frame_counters_unknown", "text": "Some replay frame counters are unknown."})
    row["warnings"] = warnings[:8]


def _set_warnings(policy: ComparisonPolicy, capture: Mapping[str, object]) -> list[dict[str, str]]:
    warnings = []
    if policy is ComparisonPolicy.PRACTICE_QUALIFYING:
        warnings.append({
            "code": "practice_qualifying_conditions_uncontrolled",
            "text": "Fuel, tyres, traffic, and cooldown intent are uncontrolled.",
        })
    if capture.get("complete") is False:
        warnings.append({"code": "capture_incomplete", "text": "The source capture footer is incomplete."})
    elif capture.get("complete") is not True:
        warnings.append({"code": "capture_completion_unknown", "text": "Capture completion is unknown."})
    recording = capture.get("recording_counters")
    recording = recording if isinstance(recording, Mapping) else {}
    recording_values = [recording.get(key) for key in ("queue_dropped", "unpersisted_on_shutdown", "socket_errors")]
    known_loss = any(isinstance(value, int) and value > 0 for value in recording_values)
    unknown_loss = any(value is None for value in recording_values)
    if known_loss:
        text = "Recording loss or socket errors were reported."
        if unknown_loss:
            text += " Other loss counters are unknown."
        warnings.append({"code": "recording_loss_reported", "text": text})
    elif unknown_loss:
        warnings.append({"code": "recording_loss_counters_unknown", "text": "Some recording loss counters are unknown."})
    replay = capture.get("replay_counters")
    replay = replay if isinstance(replay, Mapping) else {}
    replay_values = [replay.get(key) for key in ("import_late_packets_ignored", "import_frame_overflow_packets_dropped")]
    known_replay = any(isinstance(value, int) and value > 0 for value in replay_values)
    unknown_replay = any(value is None for value in replay_values)
    if known_replay:
        text = "Replay excluded late or overflowed frames."
        if unknown_replay:
            text += " Other replay counters are unknown."
        warnings.append({"code": "replay_frame_exclusions_reported", "text": text})
    elif unknown_replay:
        warnings.append({"code": "replay_frame_counters_unknown", "text": "Some replay frame counters are unknown."})
    return warnings[:8]


def _positive_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
