from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping

from .comparison import calculate_channel_differences, calculate_delta_time
from .corners import analyze_corner_regions
from .resampling import (
    ANALYSIS_VERSION,
    ResamplingConfig,
    TraceSample,
    common_distance_grid,
    resample_trace,
)
from ..storage.query import StoredAttemptTrace, load_attempt_trace
from ..tracks.loader import load_track_model
from ..tracks.model import TrackModel


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


def compare_attempts(
    database_path: str | Path,
    target_attempt_key: str,
    reference_attempt_key: str,
    *,
    config: ResamplingConfig = ResamplingConfig(),
    track_model: TrackModel | str | Path | None = None,
) -> dict[str, object]:
    """Compare two explicitly selected, stored, completed Time Trial attempts."""
    if target_attempt_key == reference_attempt_key:
        raise ValueError("target and reference must be different lap attempts")
    target = load_attempt_trace(database_path, target_attempt_key)
    reference = load_attempt_trace(database_path, reference_attempt_key)
    if target is None:
        raise ValueError(f"target attempt {target_attempt_key!r} was not found or is unavailable")
    if reference is None:
        raise ValueError(
            f"reference attempt {reference_attempt_key!r} was not found or is unavailable"
        )
    _require_completed(target)
    _require_completed(reference)
    target_context, target_signature = _stable_time_trial_context(target)
    reference_context, reference_signature = _stable_time_trial_context(reference)
    if target_signature != reference_signature:
        raise ValueError("attempts have incompatible track, format, or Time Trial settings")

    target_samples = tuple(TraceSample.from_record(row) for row in target.samples)
    reference_samples = tuple(TraceSample.from_record(row) for row in reference.samples)
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

    result = {
        "analysis_version": ANALYSIS_VERSION,
        "config": config.to_dict(),
        "target": _attempt_summary(target),
        "reference": _attempt_summary(reference),
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
    if track_model is not None:
        model = (
            track_model
            if isinstance(track_model, TrackModel)
            else load_track_model(track_model)
        )
        _require_track_model_compatible(model, target_context)
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


def _require_completed(attempt: StoredAttemptTrace) -> None:
    if attempt.disposition != "completed":
        raise ValueError(
            f"attempt {attempt.attempt_key!r} is {attempt.disposition}; only completed laps can be compared"
        )


def _stable_time_trial_context(
    attempt: StoredAttemptTrace,
) -> tuple[Mapping[str, object], tuple[object, ...]]:
    if not attempt.context_segments:
        raise ValueError(f"attempt {attempt.attempt_key!r} has no recorded session context")
    contexts: list[Mapping[str, object]] = []
    for _, context in attempt.context_segments:
        if context is None:
            raise ValueError(
                f"attempt {attempt.attempt_key!r} has unknown context; comparison is unavailable"
            )
        mode_fields = ("session_type", "game_mode", "rule_set")
        if any(context.get(field) in (None, "unknown") for field in mode_fields):
            raise ValueError(
                f"attempt {attempt.attempt_key!r} has unknown or unfamiliar session mode"
            )
        if (
            context["session_type"] != "time_trial"
            or context["game_mode"] != "time_trial"
            or context["rule_set"] != "time_trial"
        ):
            raise ValueError(
                f"attempt {attempt.attempt_key!r} is not a known Time Trial attempt"
            )
        if any(context.get(field) is None for field in _COMPATIBILITY_FIELDS):
            raise ValueError(
                f"attempt {attempt.attempt_key!r} has incomplete track or mode context"
            )
        track_length = context["track_length_m"]
        if not isinstance(track_length, (int, float)) or not math.isfinite(track_length) or track_length <= 0:
            raise ValueError(f"attempt {attempt.attempt_key!r} has an invalid track length")
        if not isinstance(context["track_name"], str) or not context["track_name"].strip():
            raise ValueError(f"attempt {attempt.attempt_key!r} has an unknown track")
        contexts.append(context)

    signature = tuple(contexts[0][field] for field in _COMPATIBILITY_FIELDS)
    if any(
        tuple(context[field] for field in _COMPATIBILITY_FIELDS) != signature
        for context in contexts[1:]
    ):
        raise ValueError(
            f"attempt {attempt.attempt_key!r} changes track or Time Trial settings during the lap"
        )
    return contexts[-1], signature


def _require_track_model_compatible(
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
