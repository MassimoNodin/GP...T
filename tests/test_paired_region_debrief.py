from __future__ import annotations

from f1_engineer.analysis.paired_region_debrief import (
    DIAGNOSTIC_REGION_DEBRIEF_VERSION,
    MAX_DIAGNOSTIC_DEBRIEF_TEXT_LENGTH,
    build_diagnostic_region_debrief,
)


def _supported_differences() -> dict[str, dict[str, object]]:
    return {
        "connected_interval_time": {
            "status": "supported",
            "value": 0.042,
            "unit": "s",
        },
        "minimum_speed": {
            "status": "supported",
            "target_value": 194.5,
            "reference_value": 197.6,
            "value": -3.1,
            "unit": "km/h",
        },
        "brake_10_percent_onset": {
            "status": "supported",
            "target_start_bracket_m": [8.0, 8.5],
            "reference_start_bracket_m": [10.5, 11.0],
            "target_minus_reference_start_bracket_m": [-3.0, -2.0],
            "right_censored": {"target": True, "reference": False},
            "unit": "m",
        },
        "throttle_50_percent_onset": {
            "status": "supported",
            "target_start_bracket_m": [12.0, 13.0],
            "reference_start_bracket_m": [12.5, 13.5],
            "target_minus_reference_start_bracket_m": [-1.5, 0.5],
            "right_censored": {"target": False, "reference": True},
            "unit": "m",
        },
        "exit_speed": {
            "status": "supported",
            "distance_m": 17.0,
            "target_value": 190.0,
            "reference_value": 192.3,
            "value": -2.3,
            "unit": "km/h",
        },
    }


def test_debrief_emits_supported_facts_in_fixed_order_and_preserves_event_bounds() -> None:
    report = build_diagnostic_region_debrief(
        _supported_differences(), {"exit_distance_m": 17.0}
    )

    assert report["schema_version"] == 1
    assert report["analysis_version"] == DIAGNOSTIC_REGION_DEBRIEF_VERSION
    assert report["diagnostic_only"] is True
    assert report["coaching_eligible"] is False
    assert report["ranking_eligible"] is False
    facts = report["facts"]
    assert [fact["kind"] for fact in facts] == [
        "connected_interval_time",
        "minimum_speed",
        "brake_10_percent_onset",
        "throttle_50_percent_onset",
        "exit_speed",
    ]
    assert "0.042 s longer" in facts[0]["text"]
    assert "3.10 km/h lower" in facts[1]["text"]
    assert "target 8.0–8.5 m" in facts[2]["text"]
    assert "target minus reference -3.0–-2.0 m" in facts[2]["text"]
    assert "target event continued to observed support end" in facts[2]["text"]
    assert "target minus reference -1.5–0.5 m" in facts[3]["text"]
    assert "reference event continued to observed support end" in facts[3]["text"]
    assert "configured exit (17.0 m)" in facts[4]["text"]
    assert report["omissions"] == []
    assert all(len(fact["text"]) <= MAX_DIAGNOSTIC_DEBRIEF_TEXT_LENGTH for fact in facts)


def test_debrief_records_explicit_omissions_for_disconnected_or_unavailable_evidence() -> None:
    differences = _supported_differences()
    differences["connected_interval_time"] = {
        "status": "unavailable",
        "unavailable_reason": "interval_time_disconnected",
    }
    differences["minimum_speed"] = {
        "status": "unavailable",
        "unavailable_reason": "target_minimum_speed_window_incomplete",
    }
    differences["brake_10_percent_onset"] = {
        "status": "unavailable",
        "unavailable_reason": "target_brake_coverage_incomplete",
    }
    differences["throttle_50_percent_onset"] = {
        "status": "unavailable",
        "unavailable_reason": "search_window_unconfigured",
    }
    differences["exit_speed"] = {
        "status": "unavailable",
        "unavailable_reason": "exit_distance_unconfigured",
    }

    report = build_diagnostic_region_debrief(differences, {"exit_distance_m": None})

    assert report["facts"] == []
    omissions = report["omissions"]
    assert [item["kind"] for item in omissions] == [
        "connected_interval_time",
        "minimum_speed",
        "brake_10_percent_onset",
        "throttle_50_percent_onset",
        "exit_speed",
    ]
    assert omissions[0]["reason_code"] == "interval_time_disconnected"
    assert "interval time disconnected" in omissions[0]["text"]
    assert omissions[3]["reason_code"] == "search_window_unconfigured"
    assert all(len(item["text"]) <= MAX_DIAGNOSTIC_DEBRIEF_TEXT_LENGTH for item in omissions)


def test_debrief_does_not_emit_a_configured_exit_fact_from_another_anchor() -> None:
    differences = _supported_differences()
    differences["exit_speed"]["distance_m"] = 16.0

    report = build_diagnostic_region_debrief(
        differences, {"exit_distance_m": 17.0}
    )

    assert [fact["kind"] for fact in report["facts"]] == [
        "connected_interval_time",
        "minimum_speed",
        "brake_10_percent_onset",
        "throttle_50_percent_onset",
    ]
    assert report["omissions"][-1]["reason_code"] == "supported_measurement_malformed"
