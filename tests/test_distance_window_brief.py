from __future__ import annotations

from copy import deepcopy

import pytest

from f1_engineer.analysis.distance_window_brief import build_distance_window_brief


def _event(
    channel: str,
    threshold: float,
    bracket: tuple[float, float],
    *,
    left_censored: bool = False,
    right_censored: bool = False,
) -> dict[str, object]:
    return {
        "channel": channel,
        "threshold": threshold,
        "start_distance_m": bracket[1],
        "start_distance_bracket_m": list(bracket),
        "start_session_time_s": 12.5,
        "left_censored": left_censored,
        "right_censored": right_censored,
    }


def _detection(
    channel: str,
    threshold: float,
    event: dict[str, object],
    *,
    event_count: int = 1,
    truncated: bool = False,
    rejected_short: int = 0,
    unsupported_breaks: int = 0,
    left_censored: int = 0,
) -> dict[str, object]:
    return {
        "status": "detected" if left_censored == 0 else "left_censored",
        "threshold": threshold,
        "events": [event],
        "event_count": event_count,
        "events_truncated": truncated,
        "left_censored_event_count": left_censored,
        "right_censored_event_count": int(event["right_censored"] is True),
        "rejected_short_event_count": rejected_short,
        "unsupported_break_count": unsupported_breaks,
    }


def _attempt(
    side: str,
    *,
    game_valid: bool | None = True,
    coverage: dict[str, float] | None = None,
) -> dict[str, object]:
    frame = 11 if side == "target" else 21
    distance = 150.0
    return {
        "attempt_key": f"{side}-attempt",
        "run_id": f"{side}-run",
        "trace_sha256": ("a" if side == "target" else "b") * 64,
        "disposition": "completed",
        "game_valid": game_valid,
        "superseded": False,
        "lifecycle_assessed": True,
        "lifecycle_exclusions": [],
        "minimum_speed": {
            "status": "observed",
            "speed_kph": 132.0 if side == "target" else 138.5,
            "anchor": {
                "frame_identifier": frame,
                "session_time_s": 12.5,
                "lap_distance_m": distance,
            },
        },
        "peak_brake": {
            "status": "observed",
            "value": 0.8 if side == "target" else 0.6,
            "anchor": {
                "frame_identifier": frame + 1,
                "session_time_s": 12.6,
                "lap_distance_m": distance + 1,
            },
        },
        "coverage": coverage
        or {"speed_mps": 1.0, "brake": 1.0, "throttle": 1.0},
        "threshold_events": {
            "brake_10_percent": _detection(
                "brake", 0.1, _event("brake", 0.1, (148.0, 151.0))
            ),
            "throttle_50_percent": _detection(
                "throttle", 0.5, _event("throttle", 0.5, (170.0, 174.0))
            ),
        },
    }


def _run(run_id: str, *, complete: bool = True) -> dict[str, object]:
    return {
        "run_id": run_id,
        "capture": {
            "complete": complete,
            "recording_counters": {
                "queue_dropped": 0,
                "unpersisted_on_shutdown": 0,
                "socket_errors": 0,
            },
        },
        "processing": {
            "replay_counters": {
                "import_late_packets_ignored": 0,
                "import_frame_overflow_packets_dropped": 0,
            }
        },
    }


def _comparison() -> dict[str, object]:
    return {
        "comparison_policy": "time_trial",
        "diagnostic_only": False,
        "target": _attempt("target"),
        "reference": _attempt("reference"),
        "processing_run_evidence": {
            "target": _run("target-run"),
            "reference": _run("reference-run"),
        },
        "comparison_window": {
            "window_m": {"start_m": 100.0, "end_m": 200.0},
            "target": _attempt("target"),
            "reference": _attempt("reference"),
            "delta": {
                "status": "supported",
                "interval_connected_supported_time": True,
                "delta_change_s": -0.25,
            },
        },
    }


def test_brief_reports_five_bounded_measured_facts_and_source_provenance() -> None:
    brief = build_distance_window_brief(_comparison())

    assert brief["status"] == "available"
    assert brief["diagnostic_only"] is True
    assert brief["coaching_eligible"] is False
    assert len(brief["facts"]) == brief["limits"]["fact_limit"] == 5
    facts = brief["facts"]
    assert [fact["kind"] for fact in facts] == [
        "connected_interval_time_difference",
        "observed_minimum_speed_difference",
        "peak_brake_input_difference",
        "brake_10_percent_onset_difference",
        "throttle_50_percent_onset_difference",
    ]
    assert facts[0]["value"] == pytest.approx(-0.25)
    assert facts[1]["value"] == pytest.approx(-6.5)
    assert facts[2]["value"] == pytest.approx(20.0)
    assert facts[2]["unit"] == "percentage_points"
    assert "80.0% vs 60.0%" in facts[2]["text"]
    assert facts[1]["provenance"]["target"]["trace_sha256"] == "a" * 64
    assert brief["provenance"]["window_m"] == {"start_m": 100.0, "end_m": 200.0}


def test_onset_fact_preserves_overlapping_brackets_and_rounds_bounds_outward() -> None:
    comparison = _comparison()
    target = comparison["comparison_window"]["target"]
    reference = comparison["comparison_window"]["reference"]
    target_event = target["threshold_events"]["brake_10_percent"]["events"][0]
    reference_event = reference["threshold_events"]["brake_10_percent"]["events"][0]
    target_event.update(
        start_distance_m=100.04,
        start_distance_bracket_m=[100.00, 100.04],
    )
    reference_event.update(
        start_distance_m=100.04,
        start_distance_bracket_m=[100.00, 100.04],
    )

    brief = build_distance_window_brief(comparison)
    fact = next(
        fact
        for fact in brief["facts"]
        if fact["kind"] == "brake_10_percent_onset_difference"
    )

    assert fact["target_minus_reference_start_bracket_m"] == pytest.approx(
        [-0.04, 0.04]
    )
    assert "100.0–100.1 m (target)" in fact["text"]
    assert "bounded to −0.1 to +0.1 m" in fact["text"]


def test_right_censored_onset_is_supported_and_disclosed() -> None:
    comparison = _comparison()
    target = comparison["comparison_window"]["target"]
    event = target["threshold_events"]["throttle_50_percent"]["events"][0]
    event["right_censored"] = True
    target["threshold_events"]["throttle_50_percent"]["right_censored_event_count"] = 1

    brief = build_distance_window_brief(comparison)
    fact = next(
        fact
        for fact in brief["facts"]
        if fact["kind"] == "throttle_50_percent_onset_difference"
    )

    assert fact["right_censored"] == {"target": True, "reference": False}
    assert "target event continued to the end of observed support" in fact["text"]


@pytest.mark.parametrize(
    ("update", "expected_code"),
    [
        (
            {"event_count": 2},
            "brake_10_percent_not_unique_supported_event",
        ),
        (
            {"events_truncated": True},
            "brake_10_percent_not_unique_supported_event",
        ),
        (
            {"unsupported_break_count": 1},
            "brake_10_percent_not_unique_supported_event",
        ),
        (
            {"rejected_short_event_count": 1},
            "brake_10_percent_not_unique_supported_event",
        ),
        (
            {"left_censored_event_count": 1},
            "brake_10_percent_not_unique_supported_event",
        ),
    ],
)
def test_unsupported_brake_onset_is_omitted_with_a_reason(update, expected_code) -> None:
    comparison = _comparison()
    detection = comparison["comparison_window"]["target"]["threshold_events"][
        "brake_10_percent"
    ]
    detection.update(update)
    if "left_censored_event_count" in update:
        detection["status"] = "left_censored"

    brief = build_distance_window_brief(comparison)

    assert not any(
        fact["kind"] == "brake_10_percent_onset_difference"
        for fact in brief["facts"]
    )
    assert expected_code in {item["code"] for item in brief["limitations"]}


def test_incomplete_coverage_and_bad_source_anchors_omit_observation_facts() -> None:
    comparison = _comparison()
    comparison["comparison_window"]["target"]["coverage"]["speed_mps"] = 0.99
    comparison["comparison_window"]["reference"]["peak_brake"]["anchor"][
        "frame_identifier"
    ] = None

    brief = build_distance_window_brief(comparison)
    kinds = {fact["kind"] for fact in brief["facts"]}

    assert "observed_minimum_speed_difference" not in kinds
    assert "peak_brake_input_difference" not in kinds
    codes = {item["code"] for item in brief["limitations"]}
    assert "minimum_speed_incomplete_coverage" in codes
    assert "peak_brake_source_anchor_unavailable" in codes


def test_attempt_capture_and_partially_known_loss_warnings_are_retained() -> None:
    comparison = _comparison()
    comparison["diagnostic_only"] = True
    comparison["comparison_policy"] = "practice_qualifying"
    comparison["target"]["game_valid"] = False
    comparison["target"]["superseded"] = True
    comparison["target"]["lifecycle_assessed"] = False
    comparison["target"]["lifecycle_exclusions"] = ["superseded_by_flashback"]
    comparison["processing_run_evidence"]["target"] = {
        "run_id": "target-run",
        "capture": {
            "complete": False,
            "recording_counters": {
                "queue_dropped": 4,
                "unpersisted_on_shutdown": None,
                "socket_errors": None,
            },
        },
        "processing": {
            "replay_counters": {
                "import_late_packets_ignored": 2,
                "import_frame_overflow_packets_dropped": None,
            }
        },
    }

    brief = build_distance_window_brief(comparison)
    warning_text = " ".join(item["text"] for item in brief["warnings"])

    assert "game marked the lap invalid" in warning_text
    assert "superseded by lifecycle evidence" in warning_text
    assert "lifecycle evidence is unassessed" in warning_text
    assert "capture footer is incomplete" in warning_text
    assert "at least 4 known loss or socket-error events" in warning_text
    assert "at least 2 known frame-admission exclusions or drops" in warning_text
    assert "coaching_eligible" in brief and brief["coaching_eligible"] is False


def test_unsupported_mode_and_attempt_pair_are_unavailable() -> None:
    comparison = _comparison()
    comparison["comparison_policy"] = "race"
    comparison["comparison_window"]["target"]["disposition"] = "partial"

    brief = build_distance_window_brief(comparison)

    assert brief["status"] == "unavailable"
    assert brief["facts"] == []
    assert {
        "unsupported_comparison_pair",
    }.issubset({item["code"] for item in brief["limitations"]})


def test_missing_interval_support_is_never_summarized() -> None:
    comparison = _comparison()
    comparison["comparison_window"]["delta"]["interval_connected_supported_time"] = False

    brief = build_distance_window_brief(comparison)

    assert not any(
        fact["kind"] == "connected_interval_time_difference"
        for fact in brief["facts"]
    )
    assert "interval_time_unavailable" in {
        item["code"] for item in brief["limitations"]
    }


def test_builder_does_not_mutate_comparison_input() -> None:
    comparison = _comparison()
    original = deepcopy(comparison)

    build_distance_window_brief(comparison)

    assert comparison == original
