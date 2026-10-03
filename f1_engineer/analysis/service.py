from __future__ import annotations

import math
from enum import Enum
from pathlib import Path
from typing import Mapping

from .comparison import calculate_channel_differences, calculate_delta_time
from .comparison_window import DistanceWindow, analyze_comparison_window
from .corners import analyze_corner_regions
from .quality import OBSERVED_CONDITION_TRACE_COLUMNS, summarize_observed_conditions
from .resampling import (
    ANALYSIS_VERSION,
    ResamplingConfig,
    TraceSample,
    common_distance_grid,
    resample_trace,
)
from ..sessions.context import GameMode, SessionType
from ..storage.query import (
    ANALYSIS_TRACE_COLUMNS,
    AttemptTraceReadLimitError,
    StoredAttemptTrace,
    load_attempt_trace,
)
from ..storage.run_summaries import get_processing_run_summary
from ..tracks.loader import load_track_model
from ..tracks.model import TrackModel
from .source_limits import (
    ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
    ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
    ANALYSIS_SOURCE_TRACE_BYTE_LIMIT,
    ANALYSIS_SOURCE_TRACE_ROW_LIMIT,
)


_COMPATIBILITY_FIELDS = (
    "packet_format",
    "track_id",
    "track_name",
    "track_length_m",
    "session_type",
    "game_mode",
    "rule_set",
    "formula_id",
    "equal_car_performance_id",
    "steering_assist_id",
    "braking_assist_id",
    "gearbox_assist_id",
)
_AUTO_REFERENCE_CONDITION_FIELDS = (
    "weather_id",
    "weather_name",
    "track_temperature_c",
    "air_temperature_c",
)
_PRACTICE_QUALIFYING_SESSION_TYPES = frozenset(
    session_type.value
    for session_type in SessionType
    if session_type.value.startswith(("practice_", "qualifying_", "sprint_shootout_"))
    or session_type.value
    in {
        SessionType.SHORT_PRACTICE.value,
        SessionType.SHORT_QUALIFYING.value,
        SessionType.ONE_SHOT_QUALIFYING.value,
        SessionType.SHORT_SPRINT_SHOOTOUT.value,
        SessionType.ONE_SHOT_SPRINT_SHOOTOUT.value,
    }
)
_KNOWN_GAME_MODES = frozenset(
    mode.value for mode in GameMode if mode is not GameMode.TIME_TRIAL
)
_PRACTICE_QUALIFYING_POLICY_VERSION = "pq-same-session-diagnostic-v1"
_PRACTICE_QUALIFYING_GRID_POINT_LIMIT = 100_000
_COMPARISON_TRACE_COLUMNS = list(
    dict.fromkeys((*ANALYSIS_TRACE_COLUMNS, *OBSERVED_CONDITION_TRACE_COLUMNS))
)


class ComparisonPolicy(str, Enum):
    TIME_TRIAL = "time_trial"
    PRACTICE_QUALIFYING = "practice_qualifying"


class TimeTrialContextError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def compare_attempts(
    database_path: str | Path,
    target_attempt_key: str,
    reference_attempt_key: str,
    *,
    config: ResamplingConfig = ResamplingConfig(),
    track_model: TrackModel | str | Path | None = None,
    policy: ComparisonPolicy | str = ComparisonPolicy.TIME_TRIAL,
    distance_window: DistanceWindow | None = None,
) -> dict[str, object]:
    """Compare explicitly selected stored attempts under a mode-specific policy."""
    try:
        policy = ComparisonPolicy(policy)
    except ValueError as exc:
        raise ValueError("unsupported_comparison_policy") from exc
    if target_attempt_key == reference_attempt_key:
        raise ValueError("target and reference must be different lap attempts")
    if policy is ComparisonPolicy.PRACTICE_QUALIFYING and track_model is not None:
        raise ValueError(
            "track_model_region_analysis_unsupported_for_practice_qualifying"
        )
    if policy is ComparisonPolicy.PRACTICE_QUALIFYING or distance_window is not None:
        source_reason_prefix = (
            "practice_qualifying_source"
            if policy is ComparisonPolicy.PRACTICE_QUALIFYING
            else "comparison_window_source"
        )
        target = _load_bounded_attempt_trace(
            database_path, target_attempt_key, reason_prefix=source_reason_prefix
        )
        reference = _load_bounded_attempt_trace(
            database_path, reference_attempt_key, reason_prefix=source_reason_prefix
        )
    else:
        target = load_attempt_trace(
            database_path, target_attempt_key, columns=_COMPARISON_TRACE_COLUMNS
        )
        reference = load_attempt_trace(
            database_path, reference_attempt_key, columns=_COMPARISON_TRACE_COLUMNS
        )
    if target is None:
        raise ValueError(f"target attempt {target_attempt_key!r} was not found or is unavailable")
    if reference is None:
        raise ValueError(
            f"reference attempt {reference_attempt_key!r} was not found or is unavailable"
        )
    _require_completed(target)
    _require_completed(reference)
    if policy is ComparisonPolicy.PRACTICE_QUALIFYING:
        _require_same_session_player(target, reference)
        _require_practice_qualifying_attempt(target)
        _require_practice_qualifying_attempt(reference)
        target_context, target_signature = stable_practice_qualifying_context(
            target.context_segments, target.attempt_key
        )
        reference_context, reference_signature = stable_practice_qualifying_context(
            reference.context_segments, reference.attempt_key
        )
        if target_signature != reference_signature:
            raise ValueError(
                "practice_qualifying_attempts_have_incompatible_context"
            )
        _require_bounded_grid(
            target_context["track_length_m"],
            config.grid_step_m,
            reason_prefix="practice_qualifying",
        )
    else:
        target_context, target_signature = stable_time_trial_context(
            target.context_segments, target.attempt_key
        )
        reference_context, reference_signature = stable_time_trial_context(
            reference.context_segments, reference.attempt_key
        )
    if target_signature != reference_signature:
        raise ValueError("attempts have incompatible track, format, or Time Trial settings")

    target_samples = tuple(TraceSample.from_record(row) for row in target.samples)
    reference_samples = tuple(TraceSample.from_record(row) for row in reference.samples)
    if distance_window is not None:
        distance_window.validate_track_length(target_context["track_length_m"])
        if policy is ComparisonPolicy.TIME_TRIAL:
            _require_bounded_grid(
                target_context["track_length_m"],
                config.grid_step_m,
                reason_prefix="comparison_window",
            )
    track_length_m = float(target_context["track_length_m"])
    distance_grid = common_distance_grid(
        target_samples,
        reference_samples,
        track_length_m=track_length_m,
        step_m=config.grid_step_m,
    )
    target_resampled = resample_trace(
        target_samples, distance_grid, config, track_length_m=track_length_m
    )
    reference_resampled = resample_trace(
        reference_samples, distance_grid, config, track_length_m=track_length_m
    )
    delta = calculate_delta_time(target_resampled, reference_resampled)
    channel_differences = calculate_channel_differences(
        target_resampled, reference_resampled
    )

    official_lap_time_difference_s: float | None = None
    if target.lap_time_ms is not None and reference.lap_time_ms is not None:
        official_lap_time_difference_s = (target.lap_time_ms - reference.lap_time_ms) / 1000.0

    processing_run_evidence = _processing_run_evidence(
        database_path, target.run_id, reference.run_id
    )
    observed_conditions = {
        "target": summarize_observed_conditions(
            target.samples,
            trace_schema_version=target.trace_schema_version,
            context_segments=target.context_segments,
        ),
        "reference": summarize_observed_conditions(
            reference.samples,
            trace_schema_version=reference.trace_schema_version,
            context_segments=reference.context_segments,
        ),
    }

    result = {
        "analysis_version": ANALYSIS_VERSION,
        "comparison_policy": policy.value,
        "comparison_policy_version": (
            _PRACTICE_QUALIFYING_POLICY_VERSION
            if policy is ComparisonPolicy.PRACTICE_QUALIFYING
            else "tt-explicit-compatible-v1"
        ),
        "diagnostic_only": policy is ComparisonPolicy.PRACTICE_QUALIFYING
        or target.game_valid is not True
        or reference.game_valid is not True
        or not target.reference_eligible
        or not reference.reference_eligible
        or target.superseded is True
        or reference.superseded is True
        or not target.lifecycle_assessed
        or not reference.lifecycle_assessed,
        "policy_limitations": (
            [
                "fuel_load_uncontrolled",
                "tyre_condition_uncontrolled",
                "traffic_uncontrolled",
                "cooldown_intent_uncontrolled",
            ]
            if policy is ComparisonPolicy.PRACTICE_QUALIFYING
            else []
        ),
        "config": config.to_dict(),
        "target": _attempt_summary(target),
        "reference": _attempt_summary(reference),
        "processing_run_evidence": processing_run_evidence,
        "observed_conditions": observed_conditions,
        "track": {
            "track_id": target_context["track_id"],
            "track_name": target_context["track_name"],
            "track_length_m": target_context["track_length_m"],
        },
        "distance_m": list(distance_grid),
        "target_trace": target_resampled.to_dict(),
        "reference_trace": reference_resampled.to_dict(),
        "delta_s": list(delta.values_s),
        "delta_mask": list(delta.mask),
        "channel_differences": {
            channel: result.to_dict() for channel, result in channel_differences.items()
        },
        "channel_difference_direction": "target_minus_reference",
        "official_lap_time_difference_s": official_lap_time_difference_s,
        "observed_range_delta": delta.to_dict(),
        "quality": {
            "target_trace": dict(target.quality),
            "reference_trace": dict(reference.quality),
            "delta_time_coverage": delta.coverage,
            "target_resampling_coverage": dict(target_resampled.coverage),
            "reference_resampling_coverage": dict(reference_resampled.coverage),
            "channel_difference_coverage": {
                channel: result.coverage for channel, result in channel_differences.items()
            },
            "target_excluded_spans": [
                span.to_dict() for span in target_resampled.excluded_spans
            ],
            "reference_excluded_spans": [
                span.to_dict() for span in reference_resampled.excluded_spans
            ],
        },
    }
    if distance_window is not None:
        result["comparison_window"] = analyze_comparison_window(
            target_samples,
            reference_samples,
            target_resampled,
            reference_resampled,
            delta,
            distance_window,
            config=config,
        )
    if track_model is not None:
        model = (
            track_model
            if isinstance(track_model, TrackModel)
            else load_track_model(track_model)
        )
        require_track_model_compatible(model, target_context)
        result["corner_analysis"] = analyze_corner_regions(
            target_samples,
            reference_samples,
            target_resampled,
            reference_resampled,
            delta,
            model,
            config=config,
            target_reference_eligible=target.reference_eligible,
            reference_reference_eligible=reference.reference_eligible,
        )
    return result


def _load_bounded_attempt_trace(
    database_path: str | Path,
    attempt_key: str,
    *,
    reason_prefix: str = "practice_qualifying_source",
) -> StoredAttemptTrace | None:
    try:
        return load_attempt_trace(
            database_path,
            attempt_key,
            columns=_COMPARISON_TRACE_COLUMNS,
            max_trace_bytes=ANALYSIS_SOURCE_TRACE_BYTE_LIMIT,
            max_trace_rows=ANALYSIS_SOURCE_TRACE_ROW_LIMIT,
            max_context_segments=ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
            max_context_bytes=ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
        )
    except AttemptTraceReadLimitError as exc:
        raise ValueError(
            f"{reason_prefix}_{exc.limit_kind}_limit_exceeded"
        ) from exc


def _processing_run_evidence(
    database_path: str | Path, target_run_id: str, reference_run_id: str
) -> dict[str, object | None]:
    if not Path(database_path).is_file():
        return {"target": None, "reference": None}
    target = get_processing_run_summary(database_path, target_run_id)
    reference = (
        target
        if target_run_id == reference_run_id
        else get_processing_run_summary(database_path, reference_run_id)
    )
    return {"target": target, "reference": reference}


def _require_same_session_player(
    target: StoredAttemptTrace, reference: StoredAttemptTrace
) -> None:
    if target.run_id != reference.run_id:
        raise ValueError("practice_qualifying_attempts_must_share_processing_run")
    if target.session_uid != reference.session_uid:
        raise ValueError("practice_qualifying_attempts_must_share_session")
    if target.car_index != reference.car_index:
        raise ValueError("practice_qualifying_attempts_must_share_player")


def _require_practice_qualifying_attempt(attempt: StoredAttemptTrace) -> None:
    if (
        not isinstance(attempt.lap_time_ms, int)
        or isinstance(attempt.lap_time_ms, bool)
        or attempt.lap_time_ms <= 0
    ):
        raise ValueError("practice_qualifying_attempt_requires_positive_lap_time")
    if not attempt.start_observed:
        raise ValueError("practice_qualifying_attempt_start_unobserved")
    if attempt.pit_encountered:
        raise ValueError("practice_qualifying_attempt_encountered_pit")


def stable_practice_qualifying_context(
    context_segments: tuple[tuple[int, Mapping[str, object] | None], ...],
    attempt_key: str,
) -> tuple[Mapping[str, object], tuple[object, ...]]:
    if not context_segments:
        raise ValueError("practice_qualifying_unknown_context")
    contexts: list[Mapping[str, object]] = []
    for _, context in context_segments:
        if context is None:
            raise ValueError("practice_qualifying_unknown_context")
        session_type = context.get("session_type")
        game_mode = context.get("game_mode")
        rule_set = context.get("rule_set")
        if (
            session_type in (None, "unknown")
            or game_mode in (None, "unknown")
            or rule_set in (None, "unknown")
        ):
            raise ValueError("practice_qualifying_unknown_mode")
        if session_type not in _PRACTICE_QUALIFYING_SESSION_TYPES:
            raise ValueError("practice_qualifying_unsupported_session_type")
        if game_mode not in _KNOWN_GAME_MODES or rule_set != "practice_qualifying":
            raise ValueError("practice_qualifying_unsupported_mode_or_ruleset")
        if any(context.get(field) is None for field in _COMPATIBILITY_FIELDS):
            raise ValueError("practice_qualifying_incomplete_context")
        track_length = context["track_length_m"]
        if (
            not isinstance(track_length, (int, float))
            or not math.isfinite(track_length)
            or track_length <= 0
        ):
            raise ValueError("practice_qualifying_invalid_track_length")
        if not isinstance(context["track_name"], str) or not context["track_name"].strip():
            raise ValueError("practice_qualifying_unknown_track")
        contexts.append(context)

    signature = tuple(contexts[0][field] for field in _COMPATIBILITY_FIELDS)
    if any(
        tuple(context[field] for field in _COMPATIBILITY_FIELDS) != signature
        for context in contexts[1:]
    ):
        raise ValueError(
            f"practice_qualifying_context_changed_during_attempt:{attempt_key}"
        )
    return contexts[-1], signature


def _require_bounded_grid(
    track_length_m: object,
    grid_step_m: float,
    *,
    reason_prefix: str = "practice_qualifying",
) -> None:
    if (
        not isinstance(track_length_m, (int, float))
        or isinstance(track_length_m, bool)
        or not math.isfinite(track_length_m)
        or track_length_m <= 0
    ):
        raise ValueError(f"{reason_prefix}_invalid_track_length")
    if not math.isfinite(grid_step_m) or grid_step_m <= 0:
        raise ValueError(f"{reason_prefix}_invalid_grid_step")
    ratio = (track_length_m + 1e-9) / grid_step_m
    if not math.isfinite(ratio) or ratio >= _PRACTICE_QUALIFYING_GRID_POINT_LIMIT:
        raise ValueError(f"{reason_prefix}_resampling_grid_limit_exceeded")
    point_count = math.floor(ratio) + 1
    if point_count > _PRACTICE_QUALIFYING_GRID_POINT_LIMIT:
        raise ValueError(f"{reason_prefix}_resampling_grid_limit_exceeded")


def _require_completed(attempt: StoredAttemptTrace) -> None:
    if attempt.disposition != "completed":
        raise ValueError(
            f"attempt {attempt.attempt_key!r} is {attempt.disposition}; only completed laps can be compared"
        )


def _stable_time_trial_context(
    attempt: StoredAttemptTrace,
) -> tuple[Mapping[str, object], tuple[object, ...]]:
    return stable_time_trial_context(attempt.context_segments, attempt.attempt_key)


def stable_time_trial_context(
    context_segments: tuple[tuple[int, Mapping[str, object] | None], ...],
    attempt_key: str,
    *,
    require_conditions: bool = False,
) -> tuple[Mapping[str, object], tuple[object, ...]]:
    if not context_segments:
        raise TimeTrialContextError(
            "unknown_context", f"attempt {attempt_key!r} has no recorded session context"
        )
    contexts: list[Mapping[str, object]] = []
    for _, context in context_segments:
        if context is None:
            raise TimeTrialContextError(
                "unknown_context",
                f"attempt {attempt_key!r} has unknown context; comparison is unavailable",
            )
        mode_fields = ("session_type", "game_mode", "rule_set")
        if any(context.get(field) in (None, "unknown") for field in mode_fields):
            raise TimeTrialContextError(
                "unknown_mode", f"attempt {attempt_key!r} has unknown or unfamiliar session mode"
            )
        if (
            context["session_type"] != "time_trial"
            or context["game_mode"] != "time_trial"
            or context["rule_set"] != "time_trial"
        ):
            raise TimeTrialContextError(
                "unsupported_mode", f"attempt {attempt_key!r} is not a known Time Trial attempt"
            )
        required_fields = (
            *_COMPATIBILITY_FIELDS,
            *(_AUTO_REFERENCE_CONDITION_FIELDS if require_conditions else ()),
        )
        if any(context.get(field) is None for field in required_fields):
            missing_conditions = require_conditions and any(
                context.get(field) is None for field in _AUTO_REFERENCE_CONDITION_FIELDS
            )
            reason = "unknown_conditions" if missing_conditions else "incomplete_context"
            message = (
                "unknown weather or temperature conditions"
                if missing_conditions
                else "incomplete track or mode context"
            )
            raise TimeTrialContextError(
                reason, f"attempt {attempt_key!r} has {message}"
            )
        track_length = context["track_length_m"]
        if not isinstance(track_length, (int, float)) or not math.isfinite(track_length) or track_length <= 0:
            raise TimeTrialContextError(
                "invalid_track_length", f"attempt {attempt_key!r} has an invalid track length"
            )
        if not isinstance(context["track_name"], str) or not context["track_name"].strip():
            raise TimeTrialContextError(
                "unknown_track", f"attempt {attempt_key!r} has an unknown track"
            )
        contexts.append(context)

    signature_fields = (
        *_COMPATIBILITY_FIELDS,
        *(_AUTO_REFERENCE_CONDITION_FIELDS if require_conditions else ()),
    )
    signature = tuple(contexts[0][field] for field in signature_fields)
    if any(
        tuple(context[field] for field in signature_fields) != signature
        for context in contexts[1:]
    ):
        raise TimeTrialContextError(
            "context_changed",
            f"attempt {attempt_key!r} changes track or Time Trial settings during the lap",
        )
    return contexts[-1], signature


def require_track_model_compatible(
    model: TrackModel, context: Mapping[str, object]
) -> None:
    if model.packet_format != context["packet_format"]:
        raise ValueError("track model packet format does not match the attempts")
    if model.track_id != context["track_id"]:
        raise ValueError("track model ID does not match the attempts")
    if model.track_name.casefold() != str(context["track_name"]).casefold():
        raise ValueError("track model name does not match the attempts")
    context_length = context["track_length_m"]
    if not isinstance(context_length, (int, float)) or not math.isclose(
        model.track_length_m, float(context_length), rel_tol=0.0, abs_tol=1.0
    ):
        raise ValueError("track model length does not match the attempts")


def _attempt_summary(attempt: StoredAttemptTrace) -> dict[str, object]:
    return {
        "attempt_key": attempt.attempt_key,
        "run_id": attempt.run_id,
        "session_uid": attempt.session_uid,
        "car_index": attempt.car_index,
        "disposition": attempt.disposition,
        "lap_time_ms": attempt.lap_time_ms,
        "game_valid": attempt.game_valid,
        "reference_eligible": attempt.reference_eligible,
        "superseded": attempt.superseded,
        "lifecycle_assessed": attempt.lifecycle_assessed,
        "lifecycle_exclusions": (
            ["superseded_by_flashback"]
            if attempt.superseded is True
            else ["lifecycle_evidence_unassessed"]
            if not attempt.lifecycle_assessed or attempt.superseded is None
            else []
        ),
        "exclusion_reasons": list(attempt.exclusion_reasons),
        "trace_sha256": attempt.trace_sha256,
        "trace_schema_version": attempt.trace_schema_version,
        "source_sample_count": len(attempt.samples),
        "session_context_segments": [
            {
                "from_frame_identifier": frame,
                "context": dict(context) if context is not None else None,
            }
            for frame, context in attempt.context_segments
        ],
    }
