from __future__ import annotations

import math
from typing import Any, Protocol

from ..processing.evidence import EvidenceStore, EvidenceUnavailable, digest, encode
from .comparison import calculate_channel_differences, calculate_delta_time
from .events import detect_sustained_threshold_events
from .resampling import ResamplingConfig, TraceSample, resample_trace


POLICY_VERSION = "observed-session-distance-v1"
MAX_GRID_POINTS = 5001
CONFIG = ResamplingConfig(grid_step_m=5.0)


class EvidenceProvider(Protocol):
    def evidence(self, attempt_id: str, *, session: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...


def measure_completed_laps(provider: EvidenceProvider, session: str, target_id: str,
                           reference_id: str) -> dict[str, Any]:
    target, target_rows = provider.evidence(target_id, session=session)
    reference, reference_rows = provider.evidence(reference_id, session=session)
    for attempt in (target, reference):
        if not attempt["driver"] or attempt["readiness"]["state"] != "published":
            raise EvidenceUnavailable("attempt_not_measurement_ready")
    if target["manifest"]["format"] != reference["manifest"]["format"]:
        raise EvidenceUnavailable("incompatible_packet_formats")
    geometry = set()
    for attempt in (target, reference):
        for segment in attempt["payload"]["context_segments"]:
            context = segment["context"]
            if context is not None and context["track_id"] >= 0 and context["track_length_m"] > 0:
                geometry.add((context["track_id"], context["track_length_m"]))
    if len(geometry) > 1:
        raise EvidenceUnavailable("incompatible_track_distance_regions")
    traces = [tuple(TraceSample.from_record(row) for row in rows) for rows in (target_rows, reference_rows)]
    distances = [[sample.distance_m for sample in trace if sample.distance_m is not None and sample.distance_m >= 0]
                 for trace in traces]
    if any(not values for values in distances):
        raise EvidenceUnavailable("distance_coverage_unavailable")
    start = math.ceil(max(min(values) for values in distances) / CONFIG.grid_step_m) * CONFIG.grid_step_m
    end = math.floor(min(max(values) for values in distances) / CONFIG.grid_step_m) * CONFIG.grid_step_m
    points = int((end - start) / CONFIG.grid_step_m) + 1
    if points < 2 or points > MAX_GRID_POINTS:
        raise EvidenceUnavailable("distance_grid_unavailable_or_over_budget")
    grid = tuple(start + index * CONFIG.grid_step_m for index in range(points))
    sampled = [resample_trace(trace, grid, CONFIG) for trace in traces]
    qualifications = sorted(set(
        target["manifest"]["qualifications"] + reference["manifest"]["qualifications"]
        + ["fuel_and_tyre_conditions_not_established", "later_braking_is_not_automatically_better",
           "numeric_distance_regions_not_named_corners", "measurement_not_reference_ranking"]
    ))
    if any(attempt["payload"]["game_valid"] is not True for attempt in (target, reference)):
        qualifications.append("invalid_or_unknown_game_validity")
    if any(attempt["payload"]["pit_encountered"] for attempt in (target, reference)):
        qualifications.append("pit_encountered")
    if any(not attempt["payload"]["context_segments"] or any(
        segment["context"] is None or segment["context"]["track_id"] < 0
        or segment["context"]["track_length_m"] <= 0
        for segment in attempt["payload"]["context_segments"]
    ) for attempt in (target, reference)):
        qualifications.append("track_geometry_not_fully_established")
    if target["manifest"]["epoch"] != reference["manifest"]["epoch"]:
        qualifications.append("different_lifecycle_epochs")
    return {
        "policy": POLICY_VERSION, "session": session,
        "resampling_config": CONFIG.to_dict(),
        "target": {"revision": target_id, "manifest_hash": digest(encode(target["manifest"])),
                   "driver": target["driver"], "readiness": target["readiness"]},
        "reference": {"revision": reference_id, "manifest_hash": digest(encode(reference["manifest"])),
                      "driver": reference["driver"], "readiness": reference["readiness"]},
        "distance_m": list(grid),
        "lap_time_difference_ms": (
            target["payload"]["lap_time_ms"] - reference["payload"]["lap_time_ms"]
            if target["payload"]["lap_time_ms"] and reference["payload"]["lap_time_ms"] else None
        ),
        "delta_time": calculate_delta_time(*sampled).to_dict(),
        "channels": {name: value.to_dict() for name, value in calculate_channel_differences(*sampled).items()},
        "braking_zones": _braking_zones(traces, start, end),
        "coverage": [dict(trace.coverage) for trace in sampled],
        "excluded_spans": [[span.to_dict() for span in trace.excluded_spans] for trace in sampled],
        "qualifications": qualifications,
    }


def _braking_zones(traces: list[tuple[TraceSample, ...]], start: float, end: float) -> list[dict[str, Any]]:
    detections = [detect_sustained_threshold_events(
        trace, channel="brake", threshold=0.1, search_window_m=(start, end + CONFIG.grid_step_m),
        minimum_duration_s=0.1, max_gap_time_s=CONFIG.max_bracket_time_s,
        max_gap_distance_m=CONFIG.max_bracket_distance_m,
    ) for trace in traces]
    events = [detection.sustained_events for detection in detections]
    regions: list[tuple[float, float]] = []
    for event in sorted((*events[0], *events[1]), key=lambda value: value.start_distance_m):
        lower = max(start, event.start_distance_m - CONFIG.max_bracket_distance_m)
        upper = min(end + CONFIG.grid_step_m, event.end_distance_m + CONFIG.max_bracket_distance_m)
        if regions and lower <= regions[-1][1]:
            regions[-1] = (regions[-1][0], max(regions[-1][1], upper))
        else:
            regions.append((lower, upper))
    result = []
    for lower, upper in regions:
        matches = [[event for event in side if lower <= event.start_distance_m < upper] for side in events]
        supported = all(len(side) == 1 and not side[0].left_censored and side[0].start_distance_bracket_m is not None
                        for side in matches)
        brackets = [side[0].start_distance_bracket_m if len(side) == 1 else None for side in matches]
        difference = None
        interval = None
        if supported:
            difference = matches[0][0].start_distance_m - matches[1][0].start_distance_m
            interval = [brackets[0][0] - brackets[1][1], brackets[0][1] - brackets[1][0]]
        result.append({"region_m": [lower, upper], "threshold": 0.1, "minimum_duration_s": 0.1,
                       "supported": supported, "onset_brackets_m": [list(bracket) if bracket else None for bracket in brackets],
                       "target_minus_reference_m": difference, "difference_interval_m": interval,
                       "limitation": "sampled_first_meaningful_application; ambiguous_or_missing_onsets_omitted"})
    return result


def compare_session_laps(store: EvidenceStore, session: str, target_id: str, reference_id: str) -> dict[str, Any]:
    report = measure_completed_laps(store, session, target_id, reference_id)
    with store.connect() as database:
        database.execute("BEGIN IMMEDIATE")
        for side in ("target", "reference"):
            current = database.execute(
                "SELECT state,reason,sequence FROM dispositions WHERE attempt=? ORDER BY sequence DESC LIMIT 1",
                (report[side]["revision"],),
            ).fetchone()
            if current is None or dict(current) != report[side]["readiness"]:
                raise EvidenceUnavailable("evidence_changed_during_comparison")
        report_id = digest(encode(report))
        report["id"] = report_id
        database.execute("INSERT OR IGNORE INTO reports VALUES (?,?,?)", (report_id, session, encode(report)))
    return report
