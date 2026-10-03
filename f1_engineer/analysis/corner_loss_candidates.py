from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


CORNER_LOSS_CANDIDATES_SCHEMA_VERSION = 1
CORNER_LOSS_CANDIDATES_ANALYSIS_VERSION = "corner-loss-candidates-v1"
CORNER_LOSS_CANDIDATES_POLICY_VERSION = "tt-session-best-connected-regions-v1"
EXPECTED_REFERENCE_SELECTION_POLICY_VERSION = "tt-session-best-v2-lifecycle"
MAX_CORNER_LOSS_CANDIDATES = 3
MAX_CORNER_REGION_ASSESSMENTS = 64
MAX_REASONS_PER_REGION = 8
DISPLAY_RESOLUTION_SECONDS = 0.001

_CONNECTED_SUPPORT_FIELDS = (
    "target_resampled_time_connected",
    "reference_resampled_time_connected",
    "shared_delta_time_connected",
    "target_source_session_time_connected",
    "reference_source_session_time_connected",
    "interval_connected_supported_time",
)


def build_corner_loss_candidates(
    *,
    comparison_policy: str,
    target: Mapping[str, object],
    reference: Mapping[str, object],
    corner_analysis: Mapping[str, object] | None,
    reference_selection: Mapping[str, object] | None,
    selected_model: Mapping[str, object] | None,
    model_approval: Mapping[str, object] | None,
    target_context_supported: bool,
    reference_selection_error: str | None = None,
) -> dict[str, object]:
    """Build bounded recorded interval-difference measurements, never advice."""
    gate_reasons: list[str] = []
    if comparison_policy != "time_trial":
        gate_reasons.append("unsupported_comparison_policy")

    gate_reasons.extend(_target_gate_reasons(target, target_context_supported))
    gate_reasons.extend(
        _reference_gate_reasons(
            target,
            reference,
            reference_selection,
            reference_selection_error,
        )
    )
    gate_reasons.extend(
        _model_gate_reasons(corner_analysis, selected_model, model_approval)
    )

    regions = _regions(corner_analysis)
    raw_region_count = _raw_region_count(corner_analysis)
    malformed_region_count = max(0, raw_region_count - len(regions))
    if malformed_region_count:
        gate_reasons.append("corner_region_assessment_invalid")
    if len(regions) > MAX_CORNER_REGION_ASSESSMENTS:
        gate_reasons.append("region_assessment_limit_exceeded")
    if corner_analysis is None:
        gate_reasons.append("corner_analysis_unavailable")
    else:
        if corner_analysis.get("diagnostic_only") is not False:
            gate_reasons.append("corner_analysis_diagnostic_only")
        source_reasons = _corner_source_gate_reasons(
            corner_analysis, target, reference
        )
        gate_reasons.extend(source_reasons)
        complex_reasons = _complex_gate_reasons(regions)
        gate_reasons.extend(complex_reasons)

    gate_reasons = list(dict.fromkeys(gate_reasons))
    all_gates_pass = not gate_reasons

    assessments: list[dict[str, object]] = []
    qualified: list[dict[str, object]] = []
    connected_count = 0
    unsupported_count = 0
    diagnostic_count = 0
    nonpositive_count = 0
    below_resolution_count = 0
    assessment_limit = min(len(regions), MAX_CORNER_REGION_ASSESSMENTS)

    for region in regions[:assessment_limit]:
        assessment, candidate, connected, unsupported = _assess_region(
            region,
            allow_candidate=all_gates_pass,
        )
        assessments.append(assessment)
        if connected:
            connected_count += 1
        if unsupported:
            unsupported_count += 1
        if "corner_delta_diagnostic_only" in assessment["reasons"]:
            diagnostic_count += 1
        if "non_positive_difference" in assessment["reasons"]:
            nonpositive_count += 1
        if "below_display_resolution" in assessment["reasons"]:
            below_resolution_count += 1
        if candidate is not None:
            qualified.append(candidate)

    qualified.sort(
        key=lambda item: (
            -float(item["recorded_time_difference_s"]),
            float(item["analysis_window_m"][0]),  # type: ignore[index]
            str(item["region_id"]),
        )
    )
    ranked = [
        {"rank": index, **candidate}
        for index, candidate in enumerate(
            qualified[:MAX_CORNER_LOSS_CANDIDATES], start=1
        )
    ]

    if gate_reasons or connected_count == 0:
        status = "abstained"
        if not gate_reasons:
            gate_reasons.append("no_supported_connected_regions")
    elif ranked:
        status = "ranked"
    else:
        status = "no_positive_supported_differences"

    excluded_assessments = [item for item in assessments if item["reasons"]]
    model_regions_omitted = malformed_region_count + max(
        0, len(regions) - assessment_limit
    )
    all_regions_assessed = (
        corner_analysis is not None
        and malformed_region_count == 0
        and model_regions_omitted == 0
        and len(assessments) == raw_region_count
    )
    selection_summary = _selection_summary(reference_selection)
    selection_passed = (
        reference_selection is not None
        and reference_selection.get("status") == "selected"
        and not _strings(reference_selection.get("reasons"))
    )

    return {
        "schema_version": CORNER_LOSS_CANDIDATES_SCHEMA_VERSION,
        "analysis_version": CORNER_LOSS_CANDIDATES_ANALYSIS_VERSION,
        "policy_version": CORNER_LOSS_CANDIDATES_POLICY_VERSION,
        "status": status,
        "measurement_label": (
            "largest supported recorded time differences"
            if regions and (unsupported_count or not all_regions_assessed)
            else "recorded time differences"
        ),
        "coaching_eligible": False,
        "source": {
            "target": _attempt_identity(target),
            "reference": _attempt_identity(reference),
            "model": _model_identity(selected_model, model_approval),
            "reference_selection": selection_summary,
            "capture_evidence": (
                "passed_session_best_policy"
                if selection_passed
                else "not_established"
            ),
        },
        "gate_reasons": gate_reasons[:16],
        "gate_reasons_omitted_count": max(0, len(gate_reasons) - 16),
        "ranked_candidates": ranked,
        "region_assessment": {
            "region_count": raw_region_count,
            "assessed_region_count": len(assessments),
            "connected_interval_count": connected_count,
            "unsupported_region_count": unsupported_count,
            "diagnostic_region_count": diagnostic_count,
            "non_positive_region_count": nonpositive_count,
            "below_display_resolution_count": below_resolution_count,
            "all_model_regions_assessed": all_regions_assessed,
            "excluded_regions": excluded_assessments[:MAX_CORNER_REGION_ASSESSMENTS],
            "excluded_regions_omitted_count": max(
                0, len(excluded_assessments) - MAX_CORNER_REGION_ASSESSMENTS
            ),
            "omitted_region_count": model_regions_omitted,
        },
        "omitted_candidate_count": max(
            0, len(qualified) - MAX_CORNER_LOSS_CANDIDATES
        ),
        "limits": {
            "maximum_ranked_candidates": MAX_CORNER_LOSS_CANDIDATES,
            "maximum_region_assessments": MAX_CORNER_REGION_ASSESSMENTS,
            "maximum_gate_reasons": 16,
            "maximum_reasons_per_region": MAX_REASONS_PER_REGION,
            "display_resolution_seconds": DISPLAY_RESOLUTION_SECONDS,
        },
    }


def _target_gate_reasons(
    target: Mapping[str, object], target_context_supported: bool
) -> list[str]:
    reasons: list[str] = []
    if target.get("disposition") != "completed":
        reasons.append("target_not_completed")
    lap_time_ms = target.get("lap_time_ms")
    if not _positive_integer(lap_time_ms):
        reasons.append("target_positive_official_lap_time_unavailable")
    if target.get("game_valid") is not True:
        reasons.append("target_not_game_valid")
    if target.get("start_observed") is not True:
        reasons.append("target_lap_start_unobserved")
    if target.get("pit_encountered") is not False:
        reasons.append("target_pit_or_in_lap")
    if target.get("lifecycle_assessed") is not True:
        reasons.append("target_lifecycle_unassessed")
    if target.get("superseded") is not False:
        reasons.append("target_superseded_or_unknown")
    if target.get("reference_eligible") is not True:
        reasons.append("target_not_reference_eligible")
    if target_context_supported is not True:
        reasons.append("target_time_trial_conditions_unavailable")
    if not _nonempty(target.get("attempt_key")):
        reasons.append("target_identity_unavailable")
    if not _nonempty(target.get("run_id")):
        reasons.append("target_run_identity_unavailable")
    if not _nonempty(target.get("session_uid")):
        reasons.append("target_session_identity_unavailable")
    if not isinstance(target.get("car_index"), int) or isinstance(
        target.get("car_index"), bool
    ):
        reasons.append("target_player_identity_unavailable")
    if not _nonempty(target.get("trace_sha256")):
        reasons.append("target_trace_checksum_unavailable")
    return reasons


def _reference_gate_reasons(
    target: Mapping[str, object],
    reference: Mapping[str, object],
    selection: Mapping[str, object] | None,
    selection_error: str | None,
) -> list[str]:
    if selection is None:
        return [selection_error or "session_best_assessment_unavailable"]

    reasons: list[str] = []
    if selection.get("reference_kind") != "session_best":
        reasons.append("unsupported_reference_kind")
    if selection.get("policy_version") != EXPECTED_REFERENCE_SELECTION_POLICY_VERSION:
        reasons.append("session_best_policy_version_unsupported")
    if selection.get("status") != "selected":
        reasons.append("session_best_reference_not_selected")
    reasons.extend(_strings(selection.get("reasons"))[:8])
    selection_target = _mapping(selection.get("target"))
    scope = _mapping(selection.get("scope"))
    selected = _mapping(selection.get("selected_reference"))
    if selection_target is None or selection_target.get("attempt_key") != target.get("attempt_key"):
        reasons.append("session_best_target_mismatch")
    if scope is None:
        reasons.append("session_best_scope_unavailable")
    else:
        for field in ("run_id", "session_uid", "car_index"):
            if _identity(scope.get(field)) != _identity(target.get(field)):
                reasons.append(f"session_best_{field}_scope_mismatch")
    if selected is None:
        reasons.append("session_best_selected_reference_unavailable")
    else:
        if selected.get("attempt_key") != reference.get("attempt_key"):
            reasons.append("explicit_reference_differs_from_session_best")
        if selected.get("trace_sha256") != reference.get("trace_sha256"):
            reasons.append("session_best_reference_checksum_mismatch")
        if _identity(scope.get("run_id") if scope else None) != _identity(reference.get("run_id")):
            reasons.append("session_best_reference_run_mismatch")
        if _identity(scope.get("session_uid") if scope else None) != _identity(reference.get("session_uid")):
            reasons.append("session_best_reference_session_mismatch")
        if _identity(scope.get("car_index") if scope else None) != _identity(reference.get("car_index")):
            reasons.append("session_best_reference_player_mismatch")
        candidates = selection.get("candidates")
        matching_candidate = None
        if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)):
            matching_candidate = next(
                (
                    candidate
                    for candidate in candidates
                    if isinstance(candidate, Mapping)
                    and candidate.get("attempt_key") == reference.get("attempt_key")
                    and candidate.get("selected") is True
                    and candidate.get("eligible") is True
                    and candidate.get("trace_sha256") == reference.get("trace_sha256")
                ),
                None,
            )
        if matching_candidate is None:
            reasons.append("session_best_candidate_assessment_mismatch")
    if not _nonempty(reference.get("trace_sha256")):
        reasons.append("reference_trace_checksum_unavailable")
    return reasons


def _model_gate_reasons(
    corner_analysis: Mapping[str, object] | None,
    selected_model: Mapping[str, object] | None,
    approval: Mapping[str, object] | None,
) -> list[str]:
    reasons: list[str] = []
    if selected_model is None:
        return ["track_model_not_selected"]
    if approval is None or approval.get("registered") is not True:
        reasons.append("track_model_revision_not_registered")
    if selected_model.get("validation_status") != "validated":
        reasons.append("track_model_not_validated")
    if approval is None or approval.get("approved_for_candidate_ranking") is not True:
        reasons.append("track_model_not_approved_for_candidate_ranking")
    if approval is not None:
        if (
            approval.get("model_id") != selected_model.get("model_id")
            or approval.get("revision") != selected_model.get("revision")
        ):
            reasons.append("track_model_approval_identity_mismatch")
        if not _nonempty(approval.get("model_content_sha256")):
            reasons.append("track_model_content_identity_unavailable")
        if not _mapping(approval.get("approval_provenance")):
            reasons.append("track_model_approval_provenance_unavailable")
    if not _nonempty(selected_model.get("model_id")):
        reasons.append("track_model_identity_unavailable")
    if corner_analysis is not None:
        model = _mapping(corner_analysis.get("model"))
        if model is None:
            reasons.append("corner_analysis_model_unavailable")
        else:
            for field in ("model_id", "revision", "validation_status"):
                if model.get(field) != selected_model.get(field):
                    reasons.append("corner_analysis_model_identity_mismatch")
                    break
    return reasons


def _corner_source_gate_reasons(
    corner_analysis: Mapping[str, object],
    target: Mapping[str, object],
    reference: Mapping[str, object],
) -> list[str]:
    source = _mapping(corner_analysis.get("source"))
    if source is None:
        return ["corner_analysis_source_unavailable"]
    reasons: list[str] = []
    for name, attempt in (("target", target), ("reference", reference)):
        identity = _mapping(source.get(name))
        if identity is None:
            reasons.append(f"corner_analysis_{name}_source_unavailable")
            continue
        for field in ("attempt_key", "run_id", "trace_sha256"):
            if identity.get(field) != attempt.get(field):
                reasons.append(f"corner_analysis_{name}_source_mismatch")
                break
    return reasons


def _complex_gate_reasons(regions: Sequence[Mapping[str, object]]) -> list[str]:
    if any(_nonempty(region.get("complex_id")) for region in regions):
        return ["complex_regions_not_supported_for_ranking"]
    bounds = [(_bounds(region.get("analysis_window_m")), region) for region in regions]
    valid = [(window, region) for window, region in bounds if window is not None]
    for index, (current, _) in enumerate(valid):
        assert current is not None
        for previous, _ in valid[:index]:
            assert previous is not None
            if current[0] < previous[1] and previous[0] < current[1]:
                return ["overlapping_regions_not_supported_for_ranking"]
    return []


def _assess_region(
    region: Mapping[str, object], *, allow_candidate: bool
) -> tuple[dict[str, object], dict[str, object] | None, bool, bool]:
    identifier = region.get("identifier")
    label = region.get("label")
    window = _bounds(region.get("analysis_window_m"))
    delta = _mapping(region.get("delta_change"))
    delta = delta or {}
    reasons: list[str] = []
    if not _nonempty(identifier) or not _nonempty(label):
        reasons.append("region_identity_unavailable")
    if window is None:
        reasons.append("analysis_window_unavailable")
    support_flags = {
        field: delta.get(field) is True for field in _CONNECTED_SUPPORT_FIELDS
    }
    coverage_values = {
        "target_time_coverage": _finite_coverage(delta.get("target_time_coverage")),
        "reference_time_coverage": _finite_coverage(delta.get("reference_time_coverage")),
        "shared_time_coverage": _finite_coverage(delta.get("shared_time_coverage")),
    }
    interval_connected = all(support_flags.values()) and all(
        value is not None for value in coverage_values.values()
    )
    change = _finite_number(delta.get("delta_change_s"))
    supported_status = delta.get("status") == "supported_region_delta_change"
    structurally_supported = (
        interval_connected
        and change is not None
        and window is not None
        and _nonempty(identifier)
        and _nonempty(label)
    )

    if not interval_connected:
        reasons.extend(_strings(delta.get("unavailable_reasons"))[:MAX_REASONS_PER_REGION])
        if not reasons:
            reasons.append("connected_support_incomplete")
    if change is None:
        reasons.append("delta_change_unavailable")
    if not supported_status:
        reasons.append(
            "corner_delta_diagnostic_only"
            if delta.get("status") == "diagnostic_region_delta_change"
            else "corner_delta_not_supported"
        )
    if not allow_candidate and structurally_supported:
        reasons.append("candidate_policy_gate_failed")
    if supported_status and structurally_supported and allow_candidate and change is not None:
        if change <= 0:
            reasons.append("non_positive_difference")
        elif change < DISPLAY_RESOLUTION_SECONDS / 2:
            reasons.append("below_display_resolution")

    reasons = list(dict.fromkeys(reasons))[:MAX_REASONS_PER_REGION]
    can_rank = not reasons
    candidate = None
    if can_rank:
        assert change is not None and window is not None
        candidate = {
            "region_id": identifier,
            "region_label": label,
            "analysis_window_m": [window[0], window[1]],
            "recorded_time_difference_s": change,
            "measurement_direction": "target_minus_reference_interval_time_difference",
            "connected_support": {
                **support_flags,
                **coverage_values,
                "entry_delta_s": _finite_number(delta.get("entry_delta_s")),
                "exit_delta_s": _finite_number(delta.get("exit_delta_s")),
            },
        }
    assessment = {
        "region_id": identifier if isinstance(identifier, str) else None,
        "region_label": label if isinstance(label, str) else None,
        "analysis_window_m": [window[0], window[1]] if window else None,
        "connected_interval_supported": structurally_supported,
        "recorded_time_difference_s": change,
        "reasons": reasons,
    }
    return assessment, candidate, structurally_supported, not structurally_supported


def _regions(corner_analysis: Mapping[str, object] | None) -> list[Mapping[str, object]]:
    if corner_analysis is None:
        return []
    raw = corner_analysis.get("regions")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [region for region in raw if isinstance(region, Mapping)]


def _raw_region_count(corner_analysis: Mapping[str, object] | None) -> int:
    if corner_analysis is None:
        return 0
    raw = corner_analysis.get("regions")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return 0
    return len(raw)


def _selection_summary(selection: Mapping[str, object] | None) -> dict[str, object] | None:
    if selection is None:
        return None
    selected = _mapping(selection.get("selected_reference"))
    scope = _mapping(selection.get("scope"))
    return {
        "reference_kind": selection.get("reference_kind"),
        "status": selection.get("status"),
        "policy_version": selection.get("policy_version"),
        "scope": (
            {
                field: scope.get(field)
                for field in ("type", "run_id", "session_uid", "car_index", "before_attempt_number")
            }
            if scope is not None
            else None
        ),
        "selected_reference": (
            {
                field: selected.get(field)
                for field in (
                    "attempt_key",
                    "attempt_number",
                    "lap_time_ms",
                    "trace_sha256",
                    "trace_schema_version",
                )
            }
            if selected is not None
            else None
        ),
        "reasons": _strings(selection.get("reasons"))[:8],
    }


def _attempt_identity(attempt: Mapping[str, object]) -> dict[str, object]:
    return {
        field: attempt.get(field)
        for field in (
            "attempt_key",
            "run_id",
            "session_uid",
            "car_index",
            "trace_sha256",
        )
    }


def _model_identity(
    model: Mapping[str, object] | None, approval: Mapping[str, object] | None
) -> dict[str, object] | None:
    if model is None:
        return None
    return {
        "model_id": model.get("model_id"),
        "revision": model.get("revision"),
        "validation_status": model.get("validation_status"),
        "registered": approval.get("registered") if approval is not None else False,
        "approved_for_candidate_ranking": (
            approval.get("approved_for_candidate_ranking")
            if approval is not None
            else False
        ),
        "model_content_sha256": (
            approval.get("model_content_sha256") if approval is not None else None
        ),
        "approval_provenance": (
            dict(_mapping(approval.get("approval_provenance")) or {})
            if approval is not None
            else None
        ),
    }


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, str)]


def _bounds(value: object) -> tuple[float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        return None
    start = _finite_number(value[0])
    end = _finite_number(value[1])
    if start is None or end is None or start >= end:
        return None
    return start, end


def _finite_number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _finite_coverage(value: object) -> float | None:
    number = _finite_number(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _identity(value: object) -> str | None:
    return None if value is None else str(value)
