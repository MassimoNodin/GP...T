from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from ..storage.query import (
    StoredAttemptInventoryEntry,
    StoredAttemptTrace,
    load_attempt_trace,
    load_reference_inventory,
)
from .service import TimeTrialContextError, stable_time_trial_context


REFERENCE_SELECTION_POLICY_VERSION = "tt-session-best-v2-lifecycle"
_TRACE_IDENTITY_COLUMNS = ["frame_identifier"]
_CAPTURE_LOSS_FIELDS = {
    "queue_dropped": "capture_queue_drops",
    "frame_overflow_packets_dropped": "frame_overflow_drops",
    "late_packets_ignored": "late_packet_drops",
    "socket_errors": "capture_socket_errors",
    "unpersisted_on_shutdown": "unpersisted_recording_data",
}
_IMPORT_FRAME_LOSS_FIELDS = {
    "import_late_packets_ignored": "import_late_packet_drops",
    "import_frame_overflow_packets_dropped": "import_frame_overflow_drops",
}


class ReferenceKind(str, Enum):
    SESSION_BEST = "session_best"


class ReferenceSelectionStatus(str, Enum):
    SELECTED = "selected"
    NO_ELIGIBLE_REFERENCE = "no_eligible_reference"
    UNSUPPORTED_POLICY = "unsupported_policy"
    TARGET_NOT_COMPLETED = "target_not_completed"
    TARGET_UNAVAILABLE = "target_unavailable"


@dataclass(frozen=True, slots=True)
class ReferenceRequest:
    target_attempt_key: str
    reference_kind: ReferenceKind = ReferenceKind.SESSION_BEST


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    attempt_key: str
    attempt_number: int
    lap_time_ms: int | None
    eligible: bool
    selected: bool
    trace_sha256: str | None
    exclusion_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_key": self.attempt_key,
            "attempt_number": self.attempt_number,
            "lap_time_ms": self.lap_time_ms,
            "eligible": self.eligible,
            "selected": self.selected,
            "trace_sha256": self.trace_sha256,
            "exclusion_reasons": list(self.exclusion_reasons),
        }


@dataclass(frozen=True, slots=True)
class ReferenceSelection:
    target_attempt_key: str
    reference_kind: ReferenceKind
    status: ReferenceSelectionStatus
    policy_version: str
    scope: Mapping[str, object] | None
    target_reference_eligible: bool | None
    diagnostic_only: bool
    selected_reference: Mapping[str, object] | None
    candidates: tuple[CandidateAssessment, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_kind": self.reference_kind.value,
            "status": self.status.value,
            "policy_version": self.policy_version,
            "target": {
                "attempt_key": self.target_attempt_key,
                "reference_eligible": self.target_reference_eligible,
                "diagnostic_only": self.diagnostic_only,
            },
            "scope": dict(self.scope) if self.scope is not None else None,
            "selected_reference": (
                dict(self.selected_reference)
                if self.selected_reference is not None
                else None
            ),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "reasons": list(self.reasons),
        }


def select_reference(
    database_path: str | Path,
    request: ReferenceRequest,
) -> ReferenceSelection:
    """Select the fastest eligible prior Time Trial lap in the target's run scope."""
    if request.reference_kind is not ReferenceKind.SESSION_BEST:
        raise ValueError(f"unsupported reference kind {request.reference_kind!r}")

    try:
        target = load_attempt_trace(
            database_path,
            request.target_attempt_key,
            columns=_TRACE_IDENTITY_COLUMNS,
        )
    except (OSError, ValueError):
        return _empty_result(
            request,
            ReferenceSelectionStatus.TARGET_UNAVAILABLE,
            reason="target_trace_missing_or_corrupt",
        )
    if target is None:
        return _empty_result(
            request,
            ReferenceSelectionStatus.TARGET_UNAVAILABLE,
            reason="target_attempt_not_found_or_unavailable",
        )
    inventory = load_reference_inventory(database_path, request.target_attempt_key)
    if inventory is None:
        return _empty_result(
            request,
            ReferenceSelectionStatus.TARGET_UNAVAILABLE,
            reason="target_inventory_unavailable",
            target_reference_eligible=target.reference_eligible,
            diagnostic_only=not target.reference_eligible,
        )

    scope = {
        "type": "same_run_session_player_prior_attempts",
        "run_id": target.run_id,
        "session_uid": target.session_uid,
        "car_index": target.car_index,
        "before_attempt_number": target.attempt_number,
    }
    if not target.lifecycle_assessed or target.superseded is None:
        return _empty_result(
            request,
            ReferenceSelectionStatus.NO_ELIGIBLE_REFERENCE,
            reason="target_lifecycle_evidence_unassessed",
            scope=scope,
            target_reference_eligible=target.reference_eligible,
            diagnostic_only=True,
        )
    if target.superseded:
        return _empty_result(
            request,
            ReferenceSelectionStatus.NO_ELIGIBLE_REFERENCE,
            reason="target_superseded_by_flashback",
            scope=scope,
            target_reference_eligible=target.reference_eligible,
            diagnostic_only=True,
        )
    if target.disposition != "completed":
        return _empty_result(
            request,
            ReferenceSelectionStatus.TARGET_NOT_COMPLETED,
            reason="target_not_completed",
            scope=scope,
            target_reference_eligible=target.reference_eligible,
            diagnostic_only=not target.reference_eligible,
        )

    try:
        _, target_signature = stable_time_trial_context(
            target.context_segments,
            target.attempt_key,
            require_conditions=True,
        )
    except TimeTrialContextError as exc:
        status = (
            ReferenceSelectionStatus.UNSUPPORTED_POLICY
            if exc.reason_code in {"unsupported_mode", "unknown_mode"}
            else ReferenceSelectionStatus.NO_ELIGIBLE_REFERENCE
        )
        return _empty_result(
            request,
            status,
            reason=f"target_{exc.reason_code}",
            scope=scope,
            target_reference_eligible=target.reference_eligible,
            diagnostic_only=not target.reference_eligible,
        )

    capture_reasons = _capture_loss_reasons(
        inventory.capture_complete,
        inventory.capture_completion,
        inventory.processing_quality,
    )
    assessments: list[CandidateAssessment] = []
    verified_candidates: list[StoredAttemptInventoryEntry] = []
    for entry in inventory.attempts:
        reasons = _candidate_exclusions(
            entry,
            target=target,
            target_signature=target_signature,
            capture_reasons=capture_reasons,
        )
        candidate_trace: StoredAttemptTrace | None = None
        if not reasons:
            try:
                candidate_trace = load_attempt_trace(
                    database_path,
                    entry.attempt_key,
                    columns=_TRACE_IDENTITY_COLUMNS,
                )
            except (OSError, ValueError):
                reasons.append("trace_missing_or_corrupt")
            if candidate_trace is None and not reasons:
                reasons.append("trace_unavailable")
        eligible = not reasons
        if eligible and candidate_trace is not None:
            verified_candidates.append(entry)
            del candidate_trace
        assessments.append(
            CandidateAssessment(
                attempt_key=entry.attempt_key,
                attempt_number=entry.attempt_number,
                lap_time_ms=entry.lap_time_ms,
                eligible=eligible,
                selected=False,
                trace_sha256=entry.trace_sha256,
                exclusion_reasons=tuple(reasons),
            )
        )

    chosen_entry: StoredAttemptInventoryEntry | None = None
    if verified_candidates:
        chosen_entry = min(
            verified_candidates,
            key=lambda item: (int(item.lap_time_ms), item.attempt_number),
        )
        assessments = [
            CandidateAssessment(
                attempt_key=item.attempt_key,
                attempt_number=item.attempt_number,
                lap_time_ms=item.lap_time_ms,
                eligible=item.eligible,
                selected=item.attempt_key == chosen_entry.attempt_key,
                trace_sha256=item.trace_sha256,
                exclusion_reasons=item.exclusion_reasons,
            )
            for item in assessments
        ]

    selected_reference = (
        {
            "attempt_key": chosen_entry.attempt_key,
            "attempt_number": chosen_entry.attempt_number,
            "lap_time_ms": chosen_entry.lap_time_ms,
            "trace_sha256": chosen_entry.trace_sha256,
            "trace_schema_version": chosen_entry.trace_schema_version,
        }
        if chosen_entry is not None
        else None
    )
    selection_reasons = capture_reasons or (
        ("no_prior_candidate_passed_policy",) if selected_reference is None else ()
    )
    return ReferenceSelection(
        target_attempt_key=target.attempt_key,
        reference_kind=request.reference_kind,
        status=(
            ReferenceSelectionStatus.SELECTED
            if selected_reference is not None
            else ReferenceSelectionStatus.NO_ELIGIBLE_REFERENCE
        ),
        policy_version=REFERENCE_SELECTION_POLICY_VERSION,
        scope=scope,
        target_reference_eligible=target.reference_eligible,
        diagnostic_only=not target.reference_eligible,
        selected_reference=selected_reference,
        candidates=tuple(assessments),
        reasons=selection_reasons,
    )


def _candidate_exclusions(
    entry: StoredAttemptInventoryEntry,
    *,
    target: StoredAttemptTrace,
    target_signature: tuple[object, ...],
    capture_reasons: tuple[str, ...],
) -> list[str]:
    if entry.attempt_key == target.attempt_key:
        return ["target_attempt"]
    if entry.attempt_number >= target.attempt_number:
        return ["recorded_after_target"]

    reasons: list[str] = []
    if not entry.lifecycle_assessed or entry.superseded is None:
        reasons.append("lifecycle_evidence_unassessed")
    elif entry.superseded:
        reasons.append("superseded_by_flashback")
    if entry.disposition != "completed":
        reasons.append("not_completed")
    if entry.game_valid is not True:
        reasons.append("game_invalid" if entry.game_valid is False else "game_validity_unknown")
    if not entry.start_observed:
        reasons.append("lap_start_not_observed")
    if entry.pit_encountered:
        reasons.append("pit_or_in_lap")
    if entry.lap_time_ms is None or entry.lap_time_ms <= 0:
        reasons.append("positive_official_lap_time_unavailable")
    if not entry.reference_eligible:
        reasons.extend(entry.exclusion_reasons)
        reasons.append("not_reference_eligible")
    if not entry.trace_ready:
        reasons.append("trace_not_ready")
    if entry.sample_count <= 0:
        reasons.append("no_telemetry_samples")
    if entry.trace_row_count != entry.sample_count:
        reasons.append("trace_row_count_mismatch")
    if entry.trace_sha256 is None:
        reasons.append("trace_checksum_unavailable")
    try:
        _, signature = stable_time_trial_context(
            entry.context_segments,
            entry.attempt_key,
            require_conditions=True,
        )
    except TimeTrialContextError as exc:
        reasons.append(exc.reason_code)
    else:
        if signature != target_signature:
            reasons.append("incompatible_time_trial_context")
    reasons.extend(capture_reasons)
    return list(dict.fromkeys(reasons))


def _capture_loss_reasons(
    capture_complete: bool,
    completion: Mapping[str, object] | None,
    processing_quality: Mapping[str, object],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not capture_complete:
        reasons.append("capture_not_finalized")
    if completion is None or completion.get("status") != "complete":
        reasons.append("capture_completion_unavailable")
        return tuple(reasons)
    if "recorded" not in completion or "received" not in completion:
        reasons.append("capture_packet_counts_unavailable")
    else:
        try:
            if int(completion["recorded"]) != int(completion["received"]):
                reasons.append("recording_count_mismatch")
        except (TypeError, ValueError):
            reasons.append("capture_packet_counts_invalid")
    for field, reason in _CAPTURE_LOSS_FIELDS.items():
        if field not in completion:
            reasons.append("capture_loss_metrics_unavailable")
            continue
        value = completion[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            reasons.append("capture_loss_metrics_invalid")
        elif value != 0:
            reasons.append(reason)
    for field, reason in _IMPORT_FRAME_LOSS_FIELDS.items():
        if field not in processing_quality:
            reasons.append("import_frame_loss_metrics_unavailable")
            continue
        value = processing_quality[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            reasons.append("import_frame_loss_metrics_invalid")
        elif value != 0:
            reasons.append(reason)
    return tuple(dict.fromkeys(reasons))


def _empty_result(
    request: ReferenceRequest,
    status: ReferenceSelectionStatus,
    *,
    reason: str,
    scope: Mapping[str, object] | None = None,
    target_reference_eligible: bool | None = None,
    diagnostic_only: bool = False,
) -> ReferenceSelection:
    return ReferenceSelection(
        target_attempt_key=request.target_attempt_key,
        reference_kind=request.reference_kind,
        status=status,
        policy_version=REFERENCE_SELECTION_POLICY_VERSION,
        scope=scope,
        target_reference_eligible=target_reference_eligible,
        diagnostic_only=diagnostic_only,
        selected_reference=None,
        candidates=(),
        reasons=(reason,),
    )
