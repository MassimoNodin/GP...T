from __future__ import annotations

from copy import deepcopy

from f1_engineer.analysis.comparison import calculate_delta_time
from f1_engineer.analysis.corner_comparison_brief import (
    build_corner_comparison_brief,
)
from f1_engineer.analysis.driving_pattern_assessment import (
    build_driving_pattern_assessment,
)
from f1_engineer.analysis.corners import analyze_corner_regions
from f1_engineer.analysis.resampling import (
    ResamplingConfig,
    TraceSample,
    resample_trace,
)
from f1_engineer.tracks.model import CornerDefinition, TrackModel


_TARGET = {
    "attempt_key": "run:42:0:3",
    "run_id": "run",
    "trace_sha256": "3" * 64,
    "disposition": "completed",
}
_REFERENCE = {
    "attempt_key": "run:42:0:1",
    "run_id": "run",
    "trace_sha256": "1" * 64,
    "disposition": "completed",
}
_PROVENANCE = {
    "target": {
        "attempt_key": _TARGET["attempt_key"],
        "run_id": _TARGET["run_id"],
        "trace_sha256": _TARGET["trace_sha256"],
    },
    "reference": {
        "attempt_key": _REFERENCE["attempt_key"],
        "run_id": _REFERENCE["run_id"],
        "trace_sha256": _REFERENCE["trace_sha256"],
    },
    "model": {
        "model_id": "reviewed-model",
        "revision": 1,
        "validation_status": "validated",
        "registered": True,
        "approved_for_candidate_ranking": True,
        "model_content_sha256": "a" * 64,
        "approval_provenance": {"review_record": "synthetic-approval"},
    },
    "reference_selection": {
        "reference_kind": "session_best",
        "status": "selected",
        "policy_version": "tt-session-best-v2-lifecycle",
        "scope": {"run_id": "run", "session_uid": "42", "car_index": 0},
        "selected_reference": {
            "attempt_key": _REFERENCE["attempt_key"],
            "trace_sha256": _REFERENCE["trace_sha256"],
        },
        "reasons": [],
    },
}
_CONNECTED = {
    "target_resampled_time_connected": True,
    "reference_resampled_time_connected": True,
    "shared_delta_time_connected": True,
    "target_source_session_time_connected": True,
    "reference_source_session_time_connected": True,
    "interval_connected_supported_time": True,
    "target_time_coverage": 1.0,
    "reference_time_coverage": 1.0,
    "shared_time_coverage": 1.0,
    "entry_delta_s": 0.1,
    "exit_delta_s": 0.25,
}


def _event(channel: str, threshold: float, distance: float, bracket: list[float]) -> dict[str, object]:
    return {
        "channel": channel,
        "threshold": threshold,
        "start_distance_m": distance,
        "start_distance_bracket_m": bracket,
        "left_censored": False,
    }


def _attempt(*, offset: float) -> dict[str, object]:
    brake_distance = 100.0 + offset
    throttle_distance = 130.0 + offset
    return {
        "braking": {
            "status": "detected",
            "event_name": "brake_onset",
            "distance_m": brake_distance,
            "event_count": 1,
            "events_truncated": False,
            "rejected_short_event_count": 0,
            "unsupported_break_count": 0,
            "events": [_event("brake", 0.1, brake_distance, [brake_distance - 1, brake_distance])],
        },
        "minimum_speed": {
            "status": "observed_minimum_complete_window",
            "speed_kph": 89.0 - offset,
            "distance_m": 120.0 + offset,
            "supported_grid_coverage": 1.0,
        },
        "throttle_pickup": {
            "0.5": {
                "status": "detected",
                "event_name": "throttle_0.5_onset",
                "distance_m": throttle_distance,
                "event_count": 1,
                "events_truncated": False,
                "rejected_short_event_count": 0,
                "unsupported_break_count": 0,
                "events": [_event("throttle", 0.5, throttle_distance, [throttle_distance - 1, throttle_distance])],
            }
        },
        "event_channel_coverage": {"brake": 1.0, "throttle": 1.0},
        "exit_speeds": [
            {"offset_m": 0.0, "distance_m": 150.0, "status": "supported", "speed_kph": 180.0 - offset}
        ],
    }


def _region() -> dict[str, object]:
    return {
        "identifier": "turn-1",
        "label": "Turn 1",
        "analysis_window_m": [90.0, 160.0],
        "diagnostic_only": False,
        "target": _attempt(offset=4.0),
        "reference": _attempt(offset=0.0),
        "delta_change": {
            "status": "supported_region_delta_change",
            "delta_change_s": 0.15,
            **_CONNECTED,
        },
    }


def _comparison() -> dict[str, object]:
    return {
        "comparison_policy": "time_trial",
        "target": deepcopy(_TARGET),
        "reference": deepcopy(_REFERENCE),
        "corner_loss_candidates": {
            "analysis_version": "corner-loss-candidates-v1",
            "policy_version": "tt-session-best-connected-regions-v1",
            "status": "ranked",
            "coaching_eligible": False,
            "source": {**deepcopy(_PROVENANCE), "capture_evidence": "passed_session_best_policy"},
            "ranked_candidates": [
                {
                    "rank": 1,
                    "region_id": "turn-1",
                    "region_label": "Turn 1",
                    "analysis_window_m": [90.0, 160.0],
                    "recorded_time_difference_s": 0.15,
                    "connected_support": deepcopy(_CONNECTED),
                }
            ],
        },
        "corner_analysis": {
            "diagnostic_only": False,
            "model": {
                "model_id": "reviewed-model",
                "revision": 1,
                "validation_status": "validated",
            },
            "source": {
                "target": deepcopy(_PROVENANCE["target"]),
                "reference": deepcopy(_PROVENANCE["reference"]),
            },
            "regions": [_region()],
        },
    }


def test_summarizes_interval_and_four_supported_control_facts_with_provenance() -> None:
    result = build_corner_comparison_brief(_comparison())

    assert result["status"] == "available"
    assert result["coaching_eligible"] is False
    assert len(result["regions"]) == 1
    summary = result["regions"][0]
    assert summary["rank"] == 1
    assert summary["region_id"] == "turn-1"
    assert [fact["kind"] for fact in summary["facts"]] == [
        "recorded_interval_time_difference",
        "braking_threshold_onset_difference",
        "observed_minimum_speed_difference",
        "throttle_50_threshold_onset_difference",
        "configured_exit_anchor_speed_difference",
    ]
    assert summary["facts"][1]["value"] == 4.0
    assert summary["facts"][1]["difference_bounds_m"] == [3.0, 5.0]
    assert summary["facts"][3]["threshold_percent"] == 50.0
    assert summary["facts"][4]["distance_m"] == 150.0
    assert summary["provenance"]["model"]["model_content_sha256"] == "a" * 64
    assert "cause" not in result["text"]
    assert "advice" not in result["text"]
    assert summary["omitted_measurement_count"] == 0


def _driving_pattern_comparison() -> dict[str, object]:
    comparison = _comparison()
    region = comparison["corner_analysis"]["regions"][0]
    braking = region["target"]["braking"]
    braking["distance_m"] = 96.0
    braking["events"][0]["start_distance_m"] = 96.0
    braking["events"][0]["start_distance_bracket_m"] = [95.0, 96.0]
    comparison["corner_comparison_brief"] = build_corner_comparison_brief(comparison)
    return comparison


def test_assesses_the_braking_pattern_without_creating_coaching() -> None:
    result = build_driving_pattern_assessment(_driving_pattern_comparison())

    assert result["analysis_version"] == "driving-pattern-assessment-v1"
    assert result["status"] == "available"
    assert result["validation_status"] == "experimental"
    assert result["diagnostic_only"] is True
    assert result["coaching_eligible"] is False
    assert result["coaching_admission"]["eligible"] is False
    assert result["action"] is None
    region = result["regions"][0]
    assert region["status"] == "matched"
    assert region["reasons"] == []
    assert region["evidence"]["brake_onset"]["target_minus_reference_bounds_m"] == [
        -5.0,
        -3.0,
    ]
    assert region["evidence"]["minimum_speed"]["target_minus_reference_kph"] == -4.0
    assert region["evidence"]["exit_speed"]["target_minus_reference_kph"] == -4.0
    assert region["action"] is None
    assert region["coaching_eligible"] is False
    assert "cause" not in str(region).lower()


def test_later_braking_contradicts_pattern_and_overlapping_brackets_are_unavailable() -> None:
    later = _comparison()
    later["corner_comparison_brief"] = build_corner_comparison_brief(later)
    contradicted = build_driving_pattern_assessment(later)["regions"][0]
    assert contradicted["status"] == "contradicted"
    assert "target_brake_onset_not_earlier" in contradicted["reasons"]

    overlapping = _driving_pattern_comparison()
    braking = overlapping["corner_analysis"]["regions"][0]["target"]["braking"]
    braking["distance_m"] = 100.0
    braking["events"][0]["start_distance_m"] = 100.0
    braking["events"][0]["start_distance_bracket_m"] = [99.0, 101.0]
    overlapping["corner_comparison_brief"] = build_corner_comparison_brief(overlapping)
    unresolved = build_driving_pattern_assessment(overlapping)["regions"][0]
    assert unresolved["status"] == "unavailable"
    assert "brake_onset_order_unresolved" in unresolved["reasons"]


def test_measurement_omissions_exit_advantage_and_zero_boundary_are_explicit() -> None:
    incomplete = _driving_pattern_comparison()
    incomplete["corner_analysis"]["regions"][0]["target"]["minimum_speed"]["status"] = (
        "observed_minimum_partial_window"
    )
    incomplete["corner_comparison_brief"] = build_corner_comparison_brief(incomplete)
    unavailable = build_driving_pattern_assessment(incomplete)["regions"][0]
    assert unavailable["status"] == "unavailable"
    assert any(reason.startswith("minimum_speed_measurement_omitted:") for reason in unavailable["reasons"])

    exit_advantage = _driving_pattern_comparison()
    exit_advantage["corner_analysis"]["regions"][0]["target"]["exit_speeds"][0][
        "speed_kph"
    ] = 185.0
    exit_advantage["corner_comparison_brief"] = build_corner_comparison_brief(exit_advantage)
    contradicted = build_driving_pattern_assessment(exit_advantage)["regions"][0]
    assert contradicted["status"] == "contradicted"
    assert "target_exit_speed_advantage_observed" in contradicted["reasons"]

    equal_onset = _driving_pattern_comparison()
    for side in ("target", "reference"):
        event = equal_onset["corner_analysis"]["regions"][0][side]["braking"]["events"][0]
        event["start_distance_m"] = 99.0
        event["start_distance_bracket_m"] = [99.0, 99.0]
    equal_onset["corner_analysis"]["regions"][0]["target"]["braking"][
        "distance_m"
    ] = 99.0
    equal_onset["corner_analysis"]["regions"][0]["reference"]["braking"][
        "distance_m"
    ] = 99.0
    equal_onset["corner_comparison_brief"] = build_corner_comparison_brief(equal_onset)
    boundary = build_driving_pattern_assessment(equal_onset)["regions"][0]
    assert boundary["status"] == "contradicted"
    assert "target_brake_onset_not_earlier" in boundary["reasons"]


def test_left_censoring_is_unavailable_while_right_censoring_keeps_observed_onset() -> None:
    right_censored = _driving_pattern_comparison()
    right_censored["corner_analysis"]["regions"][0]["target"]["braking"]["events"][0][
        "right_censored"
    ] = True
    right_censored["corner_comparison_brief"] = build_corner_comparison_brief(right_censored)
    assert build_driving_pattern_assessment(right_censored)["regions"][0]["status"] == "matched"

    left_censored = _driving_pattern_comparison()
    left_censored["corner_analysis"]["regions"][0]["target"]["braking"]["events"][0][
        "left_censored"
    ] = True
    left_censored["corner_comparison_brief"] = build_corner_comparison_brief(left_censored)
    assessment = build_driving_pattern_assessment(left_censored)["regions"][0]
    assert assessment["status"] == "unavailable"
    assert any(reason.startswith("braking_measurement_omitted:") for reason in assessment["reasons"])


def test_malformed_or_duplicate_facts_cannot_match() -> None:
    malformed = _driving_pattern_comparison()
    facts = malformed["corner_comparison_brief"]["regions"][0]["facts"]
    brake = next(fact for fact in facts if fact["kind"] == "braking_threshold_onset_difference")
    brake["difference_bounds_m"] = [2.0, -2.0]
    assessment = build_driving_pattern_assessment(malformed)["regions"][0]
    assert assessment["status"] == "unavailable"
    assert "brake_onset_evidence_invalid" in assessment["reasons"]

    duplicate = _driving_pattern_comparison()
    facts = duplicate["corner_comparison_brief"]["regions"][0]["facts"]
    brake = next(fact for fact in facts if fact["kind"] == "braking_threshold_onset_difference")
    facts.append(deepcopy(brake))
    assessment = build_driving_pattern_assessment(duplicate)["regions"][0]
    assert assessment["status"] == "unavailable"
    assert "braking_measurement_omitted" in assessment["reasons"]


def test_connected_support_and_interval_boundary_arithmetic_are_required() -> None:
    disconnected = _driving_pattern_comparison()
    candidate = disconnected["corner_loss_candidates"]["ranked_candidates"][0]
    region = disconnected["corner_comparison_brief"]["regions"][0]
    candidate["connected_support"]["shared_delta_time_connected"] = False
    region["connected_support"]["shared_delta_time_connected"] = False
    interval = next(
        fact
        for fact in region["facts"]
        if fact["kind"] == "recorded_interval_time_difference"
    )
    interval["connected_support"]["shared_delta_time_connected"] = False
    result = build_driving_pattern_assessment(disconnected)
    assert result["status"] == "abstained"
    assert result["regions"] == []

    missing_coverage = _driving_pattern_comparison()
    candidate = missing_coverage["corner_loss_candidates"]["ranked_candidates"][0]
    region = missing_coverage["corner_comparison_brief"]["regions"][0]
    candidate["connected_support"]["target_time_coverage"] = None
    region["connected_support"]["target_time_coverage"] = None
    interval = next(
        fact
        for fact in region["facts"]
        if fact["kind"] == "recorded_interval_time_difference"
    )
    interval["connected_support"]["target_time_coverage"] = None
    unsupported = build_driving_pattern_assessment(missing_coverage)
    assert unsupported["status"] == "abstained"
    assert unsupported["regions"] == []

    inconsistent = _driving_pattern_comparison()
    interval = next(
        fact
        for fact in inconsistent["corner_comparison_brief"]["regions"][0]["facts"]
        if fact["kind"] == "recorded_interval_time_difference"
    )
    interval["boundary_delta_evidence"]["exit_target_minus_reference_s"] = 0.5
    assessment = build_driving_pattern_assessment(inconsistent)["regions"][0]
    assert assessment["status"] == "unavailable"
    assert "recorded_interval_time_evidence_invalid" in assessment["reasons"]


def test_region_assessment_limit_is_three() -> None:
    comparison = _driving_pattern_comparison()
    ranked = comparison["corner_loss_candidates"]["ranked_candidates"]
    ranked.extend({**deepcopy(ranked[0]), "rank": index} for index in (2, 3, 4))

    result = build_driving_pattern_assessment(comparison)

    assert result["status"] == "abstained"
    assert result["regions"] == []
    assert result["gate_reasons"] == ["ranked_candidate_limit_exceeded"]


def test_provenance_mismatch_abstains_before_rule_assessment() -> None:
    comparison = _driving_pattern_comparison()
    comparison["corner_loss_candidates"]["source"]["target"]["trace_sha256"] = "f" * 64

    result = build_driving_pattern_assessment(comparison)

    assert result["status"] == "abstained"
    assert result["regions"] == []
    assert "target_corner_provenance_mismatch" in result["gate_reasons"]
    assert result["coaching_eligible"] is False
    assert result["action"] is None


def test_overlapping_threshold_brackets_report_unresolved_order() -> None:
    comparison = _comparison()
    region = comparison["corner_analysis"]["regions"][0]
    region["target"]["braking"]["events"][0]["start_distance_bracket_m"] = [99.0, 104.0]
    region["reference"]["braking"]["events"][0]["start_distance_bracket_m"] = [98.0, 101.0]

    result = build_corner_comparison_brief(comparison)

    brake_fact = result["regions"][0]["facts"][1]
    assert brake_fact["difference_bounds_m"] == [-2.0, 6.0]
    assert "overlapping onset brackets" in brake_fact["text"]


def test_right_censored_continuation_keeps_a_supported_observed_onset() -> None:
    comparison = _comparison()
    event = comparison["corner_analysis"]["regions"][0]["target"]["braking"]
    event["events"][0]["right_censored"] = True

    result = build_corner_comparison_brief(comparison)

    assert result["status"] == "available"
    assert result["regions"][0]["facts"][1]["kind"] == (
        "braking_threshold_onset_difference"
    )


def test_actual_corner_analysis_output_supplies_unique_onset_evidence() -> None:
    config = ResamplingConfig()

    def make_samples(*, target: bool) -> tuple[TraceSample, ...]:
        brake_start = 10 if target else 12
        throttle_start = 32 if target else 34
        interval = 0.051 if target else 0.05
        samples = []
        for distance in range(61):
            speed = (
                20.0
                if target and distance == 30
                else 22.0
                if not target and distance == 31
                else 40.0
            )
            samples.append(
                TraceSample(
                    frame_identifier=distance + 1,
                    distance_m=float(distance),
                    time_s=distance * interval,
                    speed_mps=speed,
                    throttle=0.75 if throttle_start <= distance <= 49 else 0.0,
                    brake=0.2 if brake_start <= distance <= 17 else 0.0,
                    steering=0.0,
                    gear=4,
                    drs_active=False,
                    session_time_s=1.0 + distance * interval,
                )
            )
        return tuple(samples)

    target_samples = make_samples(target=True)
    reference_samples = make_samples(target=False)
    grid = tuple(float(distance) for distance in range(61))
    target_resampled = resample_trace(
        target_samples, grid, config, track_length_m=60.0
    )
    reference_resampled = resample_trace(
        reference_samples, grid, config, track_length_m=60.0
    )
    delta = calculate_delta_time(target_resampled, reference_resampled)
    model = TrackModel(
        model_id="reviewed-model",
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Melbourne",
        layout_id="synthetic",
        track_length_m=60.0,
        distance_origin_m=0.0,
        provenance="synthetic accepted corner model",
        validation_status="validated",
        corners=(
            CornerDefinition(
                identifier="turn-1",
                label="Turn 1",
                start_distance_m=0.0,
                end_distance_m=60.0,
                braking_search_window_m=(5.0, 25.0),
                throttle_pickup_window_m=(25.0, 55.0),
                exit_distance_m=55.0,
            ),
        ),
    )
    analysis = analyze_corner_regions(
        target_samples,
        reference_samples,
        target_resampled,
        reference_resampled,
        delta,
        model,
        config=config,
        target_reference_eligible=True,
        reference_reference_eligible=True,
    )
    analysis["source"] = {
        "target": deepcopy(_PROVENANCE["target"]),
        "reference": deepcopy(_PROVENANCE["reference"]),
    }
    analyzed_region = analysis["regions"][0]
    interval = analyzed_region["delta_change"]
    connected = {
        key: interval[key]
        for key in (
            *_CONNECTED.keys(),
        )
    }
    candidate = {
        "rank": 1,
        "region_id": "turn-1",
        "region_label": "Turn 1",
        "analysis_window_m": [0.0, 60.0],
        "recorded_time_difference_s": interval["delta_change_s"],
        "connected_support": connected,
    }
    comparison = _comparison()
    comparison["corner_analysis"] = analysis
    comparison["corner_loss_candidates"]["source"]["model"]["model_id"] = (
        "reviewed-model"
    )
    comparison["corner_loss_candidates"]["ranked_candidates"] = [candidate]
    comparison["corner_loss_candidates"]["omitted_candidate_count"] = 0

    result = build_corner_comparison_brief(comparison)

    assert result["status"] == "available"
    facts = result["regions"][0]["facts"]
    assert [fact["kind"] for fact in facts] == [
        "recorded_interval_time_difference",
        "braking_threshold_onset_difference",
        "observed_minimum_speed_difference",
        "throttle_50_threshold_onset_difference",
        "configured_exit_anchor_speed_difference",
    ]


def test_ambiguous_or_censored_events_omit_only_their_measurement() -> None:
    for change in (
        {"event_count": 2},
        {"events":[_event("brake", 0.1, 104.0, [103.0, 104.0]), _event("brake", 0.1, 110.0, [109.0, 110.0])]},
        {"events":[{**_event("brake", 0.1, 104.0, [103.0, 104.0]), "left_censored": True}]},
    ):
        comparison = _comparison()
        event = comparison["corner_analysis"]["regions"][0]["target"]["braking"]
        event.update(change)
        result = build_corner_comparison_brief(comparison)

        summary = result["regions"][0]
        assert result["status"] == "available"
        assert len(summary["facts"]) == 4
        assert summary["facts"][0]["kind"] == "recorded_interval_time_difference"
        assert summary["omitted_measurement_count"] == 1
        assert summary["omitted_measurements"][0]["metric"] == "braking"


def test_partial_channels_omit_control_facts_and_keep_interval_time() -> None:
    comparison = _comparison()
    region = comparison["corner_analysis"]["regions"][0]
    region["target"]["event_channel_coverage"]["brake"] = 0.99
    region["target"]["minimum_speed"]["status"] = "observed_minimum_partial_window"
    region["target"]["exit_speeds"][0]["status"] = "unsupported_or_outside_observed_range"

    result = build_corner_comparison_brief(comparison)

    summary = result["regions"][0]
    assert len(summary["facts"]) == 2
    assert summary["facts"][0]["kind"] == "recorded_interval_time_difference"
    assert {item["metric"] for item in summary["omitted_measurements"]} == {
        "braking",
        "minimum_speed",
        "exit_speed",
    }
    assert summary["omitted_measurement_count"] == 3


def test_ranking_gate_or_provenance_mismatch_abstains() -> None:
    comparison = _comparison()
    comparison["corner_loss_candidates"]["status"] = "abstained"
    comparison["corner_loss_candidates"]["ranked_candidates"] = []
    comparison["corner_loss_candidates"]["gate_reasons"] = ["track_model_not_validated"]

    result = build_corner_comparison_brief(comparison)

    assert result["status"] == "abstained"
    assert result["regions"] == []
    assert result["gate_reasons"] == ["track_model_not_validated"]
    assert result["coaching_eligible"] is False

    mismatch = _comparison()
    mismatch["reference"]["trace_sha256"] = "f" * 64
    result = build_corner_comparison_brief(mismatch)
    assert result["status"] == "abstained"
    assert result["gate_reasons"] == ["reference_corner_provenance_mismatch"]


def test_unsupported_policy_model_approval_and_ranking_version_fail_closed() -> None:
    changes = (
        ("comparison_policy", "practice_qualifying", "unsupported_comparison_policy"),
        ("model_approval", False, "corner_model_approval_provenance_invalid"),
        ("ranking_version", False, "corner_ranking_version_unsupported"),
    )
    for kind, value, reason in changes:
        comparison = _comparison()
        if kind == "comparison_policy":
            comparison["comparison_policy"] = value
        elif kind == "model_approval":
            comparison["corner_loss_candidates"]["source"]["model"]["approved_for_candidate_ranking"] = value
        else:
            comparison["corner_loss_candidates"]["analysis_version"] = "future-version"

        result = build_corner_comparison_brief(comparison)

        assert result["status"] == "abstained"
        assert result["gate_reasons"] == [reason]


def test_ranked_candidate_and_source_region_must_match_exactly() -> None:
    comparison = _comparison()
    comparison["corner_loss_candidates"]["ranked_candidates"][0]["recorded_time_difference_s"] = 0.2

    result = build_corner_comparison_brief(comparison)

    assert result["status"] == "abstained"
    assert result["gate_reasons"] == ["ranked_region_difference_mismatch"]
