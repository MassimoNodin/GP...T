from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping, Sequence

from .continuity import MAX_SESSION_TIME_GAP_S, session_time_discontinuity
from .source_limits import (
    ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
    ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
    ANALYSIS_SOURCE_TRACE_BYTE_LIMIT,
    ANALYSIS_SOURCE_TRACE_ROW_LIMIT,
)
from ..storage.query import (
    ANALYSIS_TRACE_COLUMNS,
    AttemptTraceReadLimitError,
    StoredAttemptTrace,
    load_attempt_trace,
)


TRACE_CHART_REPORT_VERSION = 1
TRACE_CHART_POINT_LIMIT = 2_000
TRACE_CHART_RUN_LIMIT = 256
TRACE_CHART_SOURCE_ROW_LIMIT = ANALYSIS_SOURCE_TRACE_ROW_LIMIT
TRACE_CHART_SOURCE_BYTE_LIMIT = ANALYSIS_SOURCE_TRACE_BYTE_LIMIT
TRACE_CHART_SOURCE_CONTEXT_SEGMENT_LIMIT = ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT
TRACE_CHART_SOURCE_CONTEXT_BYTE_LIMIT = ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT

_UINT32_MASK = 0xFFFFFFFF
_UINT32_HALF_RANGE = 0x80000000
_CHANNELS = (
    {
        "identifier": "speed",
        "label": "Speed",
        "source_field": "speed_mps",
        "source_unit": "m/s",
        "unit": "km/h",
        "multiplier": 3.6,
    },
    {
        "identifier": "throttle",
        "label": "Throttle",
        "source_field": "throttle",
        "source_unit": "ratio",
        "unit": "percent",
        "multiplier": 100.0,
    },
    {
        "identifier": "brake",
        "label": "Brake",
        "source_field": "brake",
        "source_unit": "ratio",
        "unit": "percent",
        "multiplier": 100.0,
    },
    {
        "identifier": "steering",
        "label": "Steering input",
        "source_field": "steering",
        "source_unit": "ratio",
        "unit": "normalized",
        "multiplier": 1.0,
    },
)


class TraceChartUnavailable(ValueError):
    """A bounded observed-trace chart preview cannot be produced."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class _ChannelRuns:
    def __init__(self, definition: Mapping[str, object], run_limit: int) -> None:
        self.definition = definition
        self.run_limit = run_limit
        self.runs: list[dict[str, object]] = []
        self.current_indices: list[int] | None = None
        self.current_count = 0
        self.current_break_reasons: list[str] = []
        self.pending_break_reasons: list[str] = []
        self.source_run_count = 0
        self.observed_sample_count = 0
        self.missing_value_sample_count = 0
        self.invalid_anchor_sample_count = 0
        self.minimum_value: float | None = None
        self.maximum_value: float | None = None

    def break_run(self, reasons: Sequence[str]) -> None:
        self._finish_run()
        for reason in reasons:
            if reason not in self.pending_break_reasons:
                self.pending_break_reasons.append(reason)

    def invalidate_anchor(self, reasons: Sequence[str]) -> None:
        self.invalid_anchor_sample_count += 1
        self.break_run(reasons)

    def observe(self, sample_index: int, sample: Mapping[str, object]) -> None:
        source_field = self.definition["source_field"]
        value = _finite_number(sample.get(str(source_field)))
        if value is None:
            self.missing_value_sample_count += 1
            self.break_run(("missing_channel_value",))
            return

        multiplier = float(self.definition["multiplier"])
        chart_value = value * multiplier
        if not math.isfinite(chart_value):
            self.missing_value_sample_count += 1
            self.break_run(("invalid_channel_value",))
            return

        self.observed_sample_count += 1
        self.minimum_value = (
            chart_value if self.minimum_value is None else min(self.minimum_value, chart_value)
        )
        self.maximum_value = (
            chart_value if self.maximum_value is None else max(self.maximum_value, chart_value)
        )
        if self.current_count == 0:
            self.current_break_reasons = list(self.pending_break_reasons)
            if not self.current_break_reasons:
                self.current_break_reasons = ["start"]
            self.pending_break_reasons.clear()
            self.current_indices = (
                [] if self.source_run_count < self.run_limit else None
            )
        if self.current_indices is not None:
            self.current_indices.append(sample_index)
        self.current_count += 1

    def _finish_run(self) -> None:
        if self.current_count == 0:
            self.current_indices = None
            return
        if self.current_indices is not None:
            self.runs.append(
                {
                    "sample_indices": self.current_indices,
                    "sample_count": self.current_count,
                    "break_before_reasons": self.current_break_reasons,
                }
            )
        self.source_run_count += 1
        self.current_indices = None
        self.current_count = 0
        self.current_break_reasons = []


def build_attempt_trace_chart_preview(
    attempt: StoredAttemptTrace,
    *,
    point_limit: int = TRACE_CHART_POINT_LIMIT,
    run_limit: int = TRACE_CHART_RUN_LIMIT,
) -> dict[str, object]:
    """Build source-ordered and bounded speed/control traces for one attempt."""
    if point_limit < 1 or run_limit < 1:
        raise ValueError("trace chart limits must be positive")

    channel_runs = {
        str(definition["identifier"]): _ChannelRuns(definition, run_limit)
        for definition in _CHANNELS
    }
    previous_frame: int | None = None
    previous_time: float | None = None
    for sample_index, sample in enumerate(attempt.samples):
        frame = _frame_identifier(sample.get("frame_identifier"))
        session_time = _finite_number(sample.get("session_time_s"))
        invalid_anchor_reasons: list[str] = []
        if frame is None:
            invalid_anchor_reasons.append("invalid_frame_anchor")
        if session_time is None or session_time < 0:
            invalid_anchor_reasons.append("invalid_session_time_anchor")
        if invalid_anchor_reasons:
            for state in channel_runs.values():
                state.invalidate_anchor(invalid_anchor_reasons)
            previous_frame = None
            previous_time = None
            continue

        assert frame is not None and session_time is not None
        discontinuities: list[str] = []
        if previous_frame is not None:
            reason = _frame_discontinuity(previous_frame, frame)
            if reason is not None:
                discontinuities.append(reason)
        if previous_time is not None:
            reason = session_time_discontinuity(previous_time, session_time)
            if reason is not None:
                discontinuities.append(reason)
        if discontinuities:
            for state in channel_runs.values():
                state.break_run(discontinuities)

        for state in channel_runs.values():
            state.observe(sample_index, sample)
        previous_frame = frame
        previous_time = session_time

    for state in channel_runs.values():
        state._finish_run()

    channels: dict[str, object] = {}
    any_observed_samples = False
    for definition in _CHANNELS:
        identifier = str(definition["identifier"])
        state = channel_runs[identifier]
        channel_report = _render_channel(
            state,
            attempt.samples,
            point_limit=point_limit,
            run_limit=run_limit,
        )
        channels[identifier] = channel_report
        any_observed_samples = any_observed_samples or state.observed_sample_count > 0

    context_segments = _context_segments(attempt.context_segments)
    game_modes = _unique_context_values(context_segments, "game_mode")
    session_types = _unique_context_values(context_segments, "session_type")
    track_names = _unique_context_values(context_segments, "track_name")
    source = {
        "attempt_key": attempt.attempt_key,
        "run_id": attempt.run_id,
        "session_uid": attempt.session_uid,
        "car_index": attempt.car_index,
        "attempt_number": attempt.attempt_number,
        "disposition": attempt.disposition,
        "lap_time_ms": attempt.lap_time_ms,
        "game_valid": attempt.game_valid,
        "reference_eligible": attempt.reference_eligible,
        "start_observed": attempt.start_observed,
        "pit_encountered": attempt.pit_encountered,
        "exclusion_reasons": list(attempt.exclusion_reasons),
        "trace_sha256": attempt.trace_sha256,
        "trace_schema_version": attempt.trace_schema_version,
        "trace_row_count": len(attempt.samples),
        "trace_checksum_verified": True,
    }
    return {
        "report_version": TRACE_CHART_REPORT_VERSION,
        "artifact_kind": "single_attempt_player_trace_preview",
        "diagnostic_only": True,
        "status": "observed" if any_observed_samples else "no_chartable_samples",
        "source": source,
        "context": {
            "segments": context_segments,
            "game_modes": game_modes or ["unknown"],
            "session_types": session_types or ["unknown"],
            "track_names": track_names or ["unknown"],
        },
        "channels": channels,
        "preview": {
            "point_limit_per_channel": point_limit,
            "run_limit_per_channel": run_limit,
            "source_row_limit": TRACE_CHART_SOURCE_ROW_LIMIT,
            "source_byte_limit": TRACE_CHART_SOURCE_BYTE_LIMIT,
            "source_context_segment_limit": TRACE_CHART_SOURCE_CONTEXT_SEGMENT_LIMIT,
            "source_context_byte_limit": TRACE_CHART_SOURCE_CONTEXT_BYTE_LIMIT,
            "thinning_method": "evenly_spaced_per_run_with_endpoints",
        },
        "continuity_policy": {
            "frame_delta": "exactly one, with uint32 wrap allowed",
            "maximum_session_time_gap_s": MAX_SESSION_TIME_GAP_S,
            "session_time_tolerance": "one float32 ULP at the larger endpoint magnitude",
            "missing_channel_values": "break only that channel's displayed run",
            "coordinate": "stored session_time_s; source sample order preserved",
        },
    }


def load_attempt_trace_chart_preview(
    database_path: str | Path, attempt_key: str
) -> dict[str, object] | None:
    """Load and checksum-verify one attempt under the dashboard source limits."""
    try:
        attempt = load_attempt_trace(
            database_path,
            attempt_key,
            columns=ANALYSIS_TRACE_COLUMNS,
            max_trace_bytes=TRACE_CHART_SOURCE_BYTE_LIMIT,
            max_trace_rows=TRACE_CHART_SOURCE_ROW_LIMIT,
            max_context_segments=TRACE_CHART_SOURCE_CONTEXT_SEGMENT_LIMIT,
            max_context_bytes=TRACE_CHART_SOURCE_CONTEXT_BYTE_LIMIT,
        )
    except AttemptTraceReadLimitError as exc:
        raise TraceChartUnavailable(
            f"trace_chart_source_{exc.limit_kind}_limit_exceeded"
        ) from exc
    if attempt is None:
        return None
    return build_attempt_trace_chart_preview(attempt)


def _render_channel(
    state: _ChannelRuns,
    samples: Sequence[Mapping[str, object]],
    *,
    point_limit: int,
    run_limit: int,
) -> dict[str, object]:
    candidate_runs = state.runs[:run_limit]
    quotas: list[int] = []
    selected_runs: list[dict[str, object]] = []
    remaining_points = point_limit
    for run in candidate_runs:
        indices = run["sample_indices"]
        if not isinstance(indices, list):
            raise ValueError("trace chart run is malformed")
        minimum = min(2, len(indices))
        if remaining_points < minimum:
            break
        selected_runs.append(run)
        quotas.append(minimum)
        remaining_points -= minimum

    capacities = [
        int(run["sample_count"]) - quota
        for run, quota in zip(selected_runs, quotas)
    ]
    total_capacity = sum(capacities)
    extra_points = min(remaining_points, total_capacity)
    if extra_points and total_capacity:
        shares = [extra_points * capacity / total_capacity for capacity in capacities]
        grants = [min(capacity, int(share)) for capacity, share in zip(capacities, shares)]
        granted = sum(grants)
        for index, grant in enumerate(grants):
            quotas[index] += grant
        remaining_extras = extra_points - granted
        order = sorted(
            range(len(capacities)),
            key=lambda index: (-(shares[index] - int(shares[index])), index),
        )
        for index in order:
            if remaining_extras == 0:
                break
            if grants[index] < capacities[index]:
                quotas[index] += 1
                grants[index] += 1
                remaining_extras -= 1

    rendered_runs: list[dict[str, object]] = []
    rendered_point_count = 0
    rendered_source_sample_count = 0
    for run_index, (run, retained_count) in enumerate(zip(selected_runs, quotas)):
        indices = run["sample_indices"]
        if not isinstance(indices, list):
            raise ValueError("trace chart run is malformed")
        rendered_indices = _evenly_spaced_indices(len(indices), retained_count)
        points = [
            _chart_point(samples[indices[point_index]], state.definition)
            for point_index in rendered_indices
        ]
        rendered_source_sample_count += int(run["sample_count"])
        rendered_point_count += len(points)
        rendered_runs.append(
            {
                "run_index": run_index,
                "break_before_reasons": list(run["break_before_reasons"]),
                "source_sample_count": int(run["sample_count"]),
                "rendered_point_count": len(points),
                "start_anchor": _chart_anchor(points[0]),
                "end_anchor": _chart_anchor(points[-1]),
                "points": points,
            }
        )

    source_sample_count = len(samples)
    return {
        "label": state.definition["label"],
        "source_field": state.definition["source_field"],
        "source_unit": state.definition["source_unit"],
        "unit": state.definition["unit"],
        "source_sample_count": source_sample_count,
        "observed_sample_count": state.observed_sample_count,
        "unsupported_sample_count": max(0, source_sample_count - state.observed_sample_count),
        "missing_value_sample_count": state.missing_value_sample_count,
        "invalid_anchor_sample_count": state.invalid_anchor_sample_count,
        "observed_value_range": (
            [state.minimum_value, state.maximum_value]
            if state.minimum_value is not None and state.maximum_value is not None
            else None
        ),
        "source_run_count": state.source_run_count,
        "rendered_run_count": len(rendered_runs),
        "omitted_run_count": max(0, state.source_run_count - len(rendered_runs)),
        "rendered_point_count": rendered_point_count,
        "omitted_point_count": max(0, state.observed_sample_count - rendered_point_count),
        "thinned_sample_count": max(
            0, rendered_source_sample_count - rendered_point_count
        ),
        "segments": rendered_runs,
    }


def _chart_point(
    sample: Mapping[str, object], definition: Mapping[str, object]
) -> dict[str, object]:
    frame = _frame_identifier(sample.get("frame_identifier"))
    session_time = _finite_number(sample.get("session_time_s"))
    source_value = _finite_number(sample.get(str(definition["source_field"])))
    if frame is None or session_time is None or source_value is None:
        raise ValueError("trace chart point lost a validated source anchor")
    return {
        "frame_identifier": frame,
        "session_time_s": session_time,
        "lap_distance_m": _finite_number(sample.get("lap_distance_m")),
        "value": source_value * float(definition["multiplier"]),
    }


def _chart_anchor(point: Mapping[str, object]) -> dict[str, object]:
    return {
        "frame_identifier": point["frame_identifier"],
        "session_time_s": point["session_time_s"],
        "lap_distance_m": point["lap_distance_m"],
    }


def _frame_identifier(value: object) -> int | None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > _UINT32_MASK
    ):
        return None
    return value


def _frame_discontinuity(previous: int, current: int) -> str | None:
    delta = (current - previous) & _UINT32_MASK
    if delta == 1:
        return None
    if delta == 0:
        return "duplicate_frame"
    if delta < _UINT32_HALF_RANGE:
        return "frame_gap"
    return "frame_order_discontinuity"


def _finite_number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _evenly_spaced_indices(point_count: int, retained_count: int) -> list[int]:
    if retained_count >= point_count:
        return list(range(point_count))
    if retained_count <= 1:
        return [0] if point_count else []
    last = point_count - 1
    denominator = retained_count - 1
    return [
        (2 * index * last + denominator) // (2 * denominator)
        for index in range(retained_count)
    ]


def _context_segments(
    segments: Sequence[tuple[int, Mapping[str, object] | None]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for frame, context in segments:
        result.append(
            {
                "from_frame_identifier": frame,
                "game_mode": _context_text(context, "game_mode"),
                "session_type": _context_text(context, "session_type"),
                "track_name": _context_text(context, "track_name"),
            }
        )
    return result


def _context_text(
    context: Mapping[str, object] | None, field: str
) -> str | None:
    if context is None:
        return None
    value = context.get(field)
    return value if isinstance(value, str) and value else None


def _unique_context_values(
    segments: Sequence[Mapping[str, object]], field: str
) -> list[str]:
    values = {
        value
        for segment in segments
        if isinstance((value := segment.get(field)), str)
    }
    if any(not isinstance(segment.get(field), str) for segment in segments):
        values.add("unknown")
    return sorted(
        values
    )
