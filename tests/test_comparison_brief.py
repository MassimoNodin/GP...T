from __future__ import annotations

from copy import deepcopy

import pytest

from f1_engineer.analysis.comparison_brief import build_comparison_brief


def _comparison() -> dict[str, object]:
    target_timing = {
        "status": "matched",
        "sector1_time_ms": 30_100,
        "sector2_time_ms": 20_000,
        "sector3_time_ms": 40_000,
        "sector1_valid": True,
        "sector2_valid": True,
        "sector3_valid": True,
        "sector_sum_residual_ms": 0,
    }
    reference_timing = {
        "status": "matched",
        "sector1_time_ms": 30_000,
        "sector2_time_ms": 20_000,
        "sector3_time_ms": 40_000,
        "sector1_valid": True,
        "sector2_valid": True,
        "sector3_valid": True,
        "sector_sum_residual_ms": 0,
    }
    counters = {
        "received": 100,
        "recorded": 100,
        "recovered_datagrams": 0,
        "queue_dropped": 0,
        "unpersisted_on_shutdown": 0,
        "socket_errors": 0,
    }
    replay = {
        "import_late_packets_ignored": 0,
        "import_frame_overflow_packets_dropped": 0,
    }
    return {
        "comparison_policy": "time_trial",
        "diagnostic_only": False,
        "target": {
            "attempt_key": "target-attempt",
            "run_id": "target-run",
            "trace_sha256": "a" * 64,
            "disposition": "completed",
            "lap_time_ms": 90_100,
            "game_valid": True,
            "reference_eligible": True,
            "superseded": False,
            "lifecycle_assessed": True,
            "lifecycle_exclusions": [],
        },
        "reference": {
            "attempt_key": "reference-attempt",
            "run_id": "reference-run",
            "trace_sha256": "b" * 64,
            "disposition": "completed",
            "lap_time_ms": 90_000,
            "game_valid": True,
            "reference_eligible": True,
            "superseded": False,
            "lifecycle_assessed": True,
            "lifecycle_exclusions": [],
        },
        "official_lap_time_difference_s": 0.1,
        "reported_timing_evidence": {
            "target": target_timing,
            "reference": reference_timing,
        },
        "sector_timing_difference_ms": {
            "status": "matched",
            "sectors": {
                "sector1": {
                    "status": "matched_values",
                    "target_time_ms": 30_100,
                    "reference_time_ms": 30_000,
                    "target_minus_reference_ms": 100,
                    "target_valid": True,
                    "reference_valid": True,
                },
                "sector2": {
                    "status": "matched_values",
                    "target_time_ms": 20_000,
                    "reference_time_ms": 20_000,
                    "target_minus_reference_ms": 0,
                    "target_valid": True,
                    "reference_valid": True,
                },
                "sector3": {
                    "status": "matched_values",
                    "target_time_ms": 40_000,
                    "reference_time_ms": 40_000,
                    "target_minus_reference_ms": 0,
                    "target_valid": True,
                    "reference_valid": True,
                },
            },
            "target_sector_sum_residual_ms": 0,
            "reference_sector_sum_residual_ms": 0,
        },
        "processing_run_evidence": {
            "target": {
                "capture": {"complete": True, "recording_counters": counters},
                "processing": {"replay_counters": replay},
            },
            "reference": {
                "capture": {"complete": True, "recording_counters": counters},
                "processing": {"replay_counters": replay},
            },
        },
        "quality": {"delta_time_coverage": 1.0},
    }


@pytest.mark.parametrize(
    ("difference", "expected_phrase"),
    [
        (1.2344, "Target was 1.234 s slower"),
        (-1.2344, "Target was 1.234 s faster"),
        (0.0004, "equal official lap times to 0.001 s"),
    ],
)
def test_official_delta_direction_and_rounding_are_neutral_and_deterministic(
    difference: float, expected_phrase: str
) -> None:
    comparison = _comparison()
    target = comparison["target"]
    reference = comparison["reference"]
    assert isinstance(target, dict) and isinstance(reference, dict)
    target["lap_time_ms"] = 90_000 + round(difference * 1000)
    reference["lap_time_ms"] = 90_000
    comparison["official_lap_time_difference_s"] = (
        target["lap_time_ms"] - reference["lap_time_ms"]
    ) / 1000

    brief = build_comparison_brief(comparison)

    assert brief["status"] == "available"
    assert expected_phrase in brief["text"]
    fact = brief["facts"][0]
    assert fact["kind"] == "official_lap_time_difference"
    assert fact["unit"] == "s"
    assert fact["provenance"]["target"]["attempt_key"] == "target-attempt"
    assert fact["provenance"]["reference"]["trace_sha256"] == "b" * 64


def test_valid_sector_facts_include_validity_and_residuals() -> None:
    brief = build_comparison_brief(_comparison())

    facts = brief["facts"]
    assert [fact["kind"] for fact in facts] == [
        "official_lap_time_difference",
        "reported_sector_difference",
        "reported_sector_difference",
        "reported_sector_difference",
        "reported_sector_sum_residual",
    ]
    assert facts[1]["validity"] == {"target": True, "reference": True}
    assert facts[1]["unit"] == "ms"
    assert facts[1]["value"] == 100
    assert "sum exactly to the lap time" in facts[4]["text"]
    assert facts[1]["source_fields"]["target"] == (
        "reported_timing_evidence.target.sector1_time_ms"
    )


def test_invalid_or_unavailable_sector_timing_does_not_create_sector_facts() -> None:
    comparison = _comparison()
    timing = comparison["reported_timing_evidence"]
    difference = comparison["sector_timing_difference_ms"]
    assert isinstance(timing, dict) and isinstance(difference, dict)
    target = timing["target"]
    sectors = difference["sectors"]
    assert isinstance(target, dict) and isinstance(sectors, dict)
    target["sector2_valid"] = False
    sectors["sector2"]["target_valid"] = False
    brief = build_comparison_brief(comparison)

    assert [fact["kind"] for fact in brief["facts"]].count(
        "reported_sector_difference"
    ) == 2
    assert not any(fact["kind"] == "reported_sector_sum_residual" for fact in brief["facts"])
    assert any(item["code"] == "sector_validity_not_supported" for item in brief["limitations"])

    for status in ("conflicting", "truncated", "unavailable"):
        unsupported = deepcopy(_comparison())
        timing = unsupported["reported_timing_evidence"]
        assert isinstance(timing, dict)
        assert isinstance(timing["target"], dict)
        timing["target"]["status"] = status
        brief = build_comparison_brief(unsupported)
        assert not any(
            fact["kind"].startswith("reported_sector") for fact in brief["facts"]
        )


def test_window_fact_requires_connected_supported_evidence() -> None:
    comparison = _comparison()
    comparison["comparison_window"] = {
        "window_m": {"start_m": 500, "end_m": 1200},
        "delta": {
            "status": "supported",
            "interval_connected_supported_time": True,
            "delta_change_s": -0.25,
        },
    }
    brief = build_comparison_brief(comparison)
    window_fact = brief["facts"][-1]
    assert window_fact["kind"] == "supported_distance_window_delta_change"
    assert "decreased by 0.250 s" in window_fact["text"]

    comparison["comparison_window"]["delta"]["interval_connected_supported_time"] = False
    brief = build_comparison_brief(comparison)
    assert not any(
        fact["kind"] == "supported_distance_window_delta_change"
        for fact in brief["facts"]
    )
    assert any(item["code"] == "selected_window_unsupported" for item in brief["limitations"])


def test_capture_lifecycle_coverage_and_practice_limits_are_bounded() -> None:
    comparison = _comparison()
    comparison["comparison_policy"] = "practice_qualifying"
    comparison["diagnostic_only"] = True
    comparison["policy_limitations"] = ["fuel_load_uncontrolled"]
    comparison["quality"] = {"delta_time_coverage": 0.5}
    evidence = comparison["processing_run_evidence"]
    assert isinstance(evidence, dict)
    evidence["target"] = {
        "capture": {
            "complete": False,
            "recording_counters": {
                "queue_dropped": 2,
                "unpersisted_on_shutdown": 0,
                "socket_errors": 0,
            },
        },
        "processing": {
            "replay_counters": {
                "import_late_packets_ignored": 3,
                "import_frame_overflow_packets_dropped": 0,
            }
        },
    }
    target = comparison["target"]
    reference = comparison["reference"]
    assert isinstance(target, dict)
    assert isinstance(reference, dict)
    target["game_valid"] = False
    target["superseded"] = True
    target["lifecycle_exclusions"] = ["superseded_by_flashback"]
    target["lifecycle_assessed"] = False
    reference["game_valid"] = False
    reference["superseded"] = True
    reference["lifecycle_exclusions"] = ["lifecycle_evidence_unassessed"]
    reference["lifecycle_assessed"] = False
    timing = comparison["reported_timing_evidence"]
    differences = comparison["sector_timing_difference_ms"]
    assert isinstance(timing, dict) and isinstance(differences, dict)
    assert isinstance(timing["target"], dict) and isinstance(differences["sectors"], dict)
    for number in (1, 2, 3):
        timing["target"][f"sector{number}_valid"] = False
        differences["sectors"][f"sector{number}"]["target_valid"] = False
    comparison["comparison_window"] = {
        "window_m": {"start_m": 100, "end_m": 200},
        "delta": {
            "status": "unsupported_interior",
            "interval_connected_supported_time": False,
            "delta_change_s": None,
        },
    }
    brief = build_comparison_brief(comparison)

    codes = {item["code"] for item in brief["limitations"]}
    assert "capture_incomplete" in codes
    assert "recording_losses" in codes
    assert "replay_losses" in codes
    assert "delta_coverage_partial" in codes
    assert "practice_qualifying_conditions_uncontrolled" in codes
    assert len(brief["limitations"]) <= 8
    assert brief["limits"]["omitted_limitation_count"] > 0
    assert brief["diagnostic_only"] is True


@pytest.mark.parametrize("policy", ["race", "unknown", None])
def test_unsupported_modes_never_receive_a_success_brief(policy: object) -> None:
    comparison = _comparison()
    comparison["comparison_policy"] = policy

    brief = build_comparison_brief(comparison)

    assert brief["status"] == "unavailable"
    assert brief["facts"] == []


def test_partial_attempts_cannot_receive_a_success_brief() -> None:
    comparison = _comparison()
    target = comparison["target"]
    assert isinstance(target, dict)
    target["disposition"] = "partial"

    brief = build_comparison_brief(comparison)

    assert brief["status"] == "unavailable"
    assert brief["facts"] == []
