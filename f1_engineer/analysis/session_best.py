from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from ..storage.query import (
    AttemptInventoryReadLimitError,
    AttemptTraceReadLimitError,
    StoredAttemptInventoryEntry,
    load_attempt_trace,
    load_reference_inventory,
)
from .reference_selection import (
    MAX_BOUNDED_PRIOR_CANDIDATES,
    MAX_BOUNDED_PRIOR_CONTEXT_BYTES,
    MAX_BOUNDED_PRIOR_CONTEXT_SEGMENTS,
    MAX_BOUNDED_PRIOR_TRACE_BYTES,
    MAX_BOUNDED_PRIOR_TRACE_ROWS,
    _IMPORT_FRAME_LOSS_FIELDS,
    _candidate_policy_exclusions,
    _capture_loss_reasons,
)
from .service import TimeTrialContextError, stable_time_trial_context


SESSION_BEST_POLICY_VERSION = "run-session-player-best-v1"
MAX_SESSION_BEST_CANDIDATE_ROWS = 32
MAX_SESSION_BEST_SCOPE_ATTEMPTS = MAX_BOUNDED_PRIOR_CANDIDATES
MAX_SESSION_BEST_TRACE_BYTES = MAX_BOUNDED_PRIOR_TRACE_BYTES
MAX_SESSION_BEST_TRACE_ROWS = MAX_BOUNDED_PRIOR_TRACE_ROWS
MAX_SESSION_BEST_CONTEXT_BYTES = MAX_BOUNDED_PRIOR_CONTEXT_BYTES
MAX_SESSION_BEST_CONTEXT_SEGMENTS = MAX_BOUNDED_PRIOR_CONTEXT_SEGMENTS
_TRACE_IDENTITY_COLUMNS = ["frame_identifier"]


class SessionBestStatus(str, Enum):
    ASSESSED = "assessed"
    ABSTAINED = "abstained"
    ANCHOR_UNAVAILABLE = "anchor_unavailable"


@dataclass(frozen=True, slots=True)
class SessionBestCandidate:
    attempt_key: str
    attempt_number: int
    disposition: str
    lap_time_ms: int | None
    recorded_time_rank: int | None
    eligibility_status: str
    trace_sha256: str | None
    exclusion_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_key": self.attempt_key,
            "attempt_number": self.attempt_number,
            "disposition": self.disposition,
            "lap_time_ms": self.lap_time_ms,
            "recorded_time_rank": self.recorded_time_rank,
            "time_trial_eligibility": self.eligibility_status,
            "trace_sha256": self.trace_sha256,
            "exclusion_reasons": list(self.exclusion_reasons),
        }


@dataclass(frozen=True, slots=True)
class SessionBestAssessment:
    anchor_attempt_key: str
    status: SessionBestStatus
    scope: Mapping[str, object] | None
    candidate_count: int | None
    timed_candidate_count: int | None
    candidates: tuple[SessionBestCandidate, ...]
    candidates_omitted_count: int | None
    time_trial_best_status: str
    time_trial_best: Mapping[str, object] | None
    reasons: tuple[str, ...]
    limits: Mapping[str, int]

    def to_dict(self) -> dict[str, object]:
        recorded_status = (
            "unavailable"
            if self.candidate_count is None
            else "available"
            if self.timed_candidate_count
            else "no_recorded_times"
        )
        return {
            "policy_version": SESSION_BEST_POLICY_VERSION,
            "status": self.status.value,
            "anchor_attempt_key": self.anchor_attempt_key,
            "scope": dict(self.scope) if self.scope is not None else None,
            "recorded_time_ordering": {
                "status": recorded_status,
                "diagnostic_only": True,
                "sort": "reported_lap_time_ms_ascending_then_attempt_number",
                "candidate_count": self.candidate_count,
                "timed_candidate_count": self.timed_candidate_count,
                "candidates_omitted_count": self.candidates_omitted_count,
            },
            "eligible_time_trial_best": {
                "status": self.time_trial_best_status,
                "attempt": (
                    dict(self.time_trial_best)
                    if self.time_trial_best is not None
                    else None
                ),
            },
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "reasons": list(self.reasons),
            "limits": dict(self.limits),
        }


def assess_session_best(
    database_path: str | Path,
    anchor_attempt_key: str,
    *,
    max_scope_attempts: int = MAX_SESSION_BEST_SCOPE_ATTEMPTS,
    max_candidate_rows: int = MAX_SESSION_BEST_CANDIDATE_ROWS,
    max_candidate_trace_bytes: int = MAX_SESSION_BEST_TRACE_BYTES,
    max_candidate_trace_rows: int = MAX_SESSION_BEST_TRACE_ROWS,
    max_candidate_context_bytes: int = MAX_SESSION_BEST_CONTEXT_BYTES,
    max_candidate_context_segments: int = MAX_SESSION_BEST_CONTEXT_SEGMENTS,
) -> SessionBestAssessment:
    """Assess every lap in an explicitly anchored run/session/player scope."""
    if not isinstance(anchor_attempt_key, str) or not anchor_attempt_key:
        raise ValueError("anchor_attempt_key must be a non-empty string")
    for name, value in (
        ("max_scope_attempts", max_scope_attempts),
        ("max_candidate_rows", max_candidate_rows),
        ("max_candidate_trace_bytes", max_candidate_trace_bytes),
        ("max_candidate_trace_rows", max_candidate_trace_rows),
        ("max_candidate_context_bytes", max_candidate_context_bytes),
        ("max_candidate_context_segments", max_candidate_context_segments),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if max_candidate_rows > MAX_SESSION_BEST_CANDIDATE_ROWS:
        raise ValueError(
            f"max_candidate_rows cannot exceed {MAX_SESSION_BEST_CANDIDATE_ROWS}"
        )

    limits = {
        "scope_attempts": max_scope_attempts,
        "candidate_rows": max_candidate_rows,
        "candidate_trace_bytes": max_candidate_trace_bytes,
        "candidate_trace_rows": max_candidate_trace_rows,
        "candidate_context_bytes": max_candidate_context_bytes,
        "candidate_context_segments": max_candidate_context_segments,
    }
    try:
        inventory = load_reference_inventory(
            database_path,
            anchor_attempt_key,
            include_after_target=True,
            max_scope_attempts=max_scope_attempts,
            max_context_bytes=max_candidate_context_bytes,
            max_context_segments=max_candidate_context_segments,
        )
    except AttemptInventoryReadLimitError as exc:
        return _unavailable_result(
            anchor_attempt_key,
            SessionBestStatus.ABSTAINED,
            reason=f"candidate_{exc.limit_kind}_limit_exceeded",
            limits=limits,
        )
    except (OSError, ValueError):
        return _unavailable_result(
            anchor_attempt_key,
            SessionBestStatus.ANCHOR_UNAVAILABLE,
            reason="session_best_source_unavailable",
            limits=limits,
        )
    if inventory is None:
        return _unavailable_result(
            anchor_attempt_key,
            SessionBestStatus.ANCHOR_UNAVAILABLE,
            reason="anchor_attempt_not_found_or_unavailable",
            limits=limits,
        )

    scope = {
        "type": "same_run_session_player",
        "run_id": inventory.run_id,
        "session_uid": inventory.session_uid,
        "car_index": inventory.car_index,
        "context_anchor_attempt_key": anchor_attempt_key,
    }
    anchor = next(
        (entry for entry in inventory.attempts if entry.attempt_key == anchor_attempt_key),
        None,
    )
    if anchor is None:
        return _unavailable_result(
            anchor_attempt_key,
            SessionBestStatus.ANCHOR_UNAVAILABLE,
            reason="anchor_attempt_missing_from_scope_inventory",
            limits=limits,
            scope=scope,
        )

    recorded_order = _recorded_order(inventory.attempts)
    candidate_count = len(inventory.attempts)
    timed_candidate_count = sum(
        _has_positive_time(entry.lap_time_ms) for entry in inventory.attempts
    )
    capture_reasons = _session_best_capture_reasons(
        inventory.capture_complete,
        inventory.capture_completion,
        inventory.processing_quality,
    )
    try:
        _, context_signature = stable_time_trial_context(
            anchor.context_segments,
            anchor.attempt_key,
            require_conditions=True,
        )
    except TimeTrialContextError as exc:
        candidates = tuple(
            _to_candidate(entry, rank, "not_assessed", (f"anchor_{exc.reason_code}",))
            for entry, rank in recorded_order[:max_candidate_rows]
        )
        best_status = (
            "unsupported_mode"
            if exc.reason_code == "unsupported_mode"
            else "unknown_mode"
            if exc.reason_code == "unknown_mode"
            else "context_unavailable"
        )
        return SessionBestAssessment(
            anchor_attempt_key=anchor_attempt_key,
            status=SessionBestStatus.ASSESSED,
            scope=scope,
            candidate_count=candidate_count,
            timed_candidate_count=timed_candidate_count,
            candidates=candidates,
            candidates_omitted_count=max(0, candidate_count - len(candidates)),
            time_trial_best_status=best_status,
            time_trial_best=None,
            reasons=tuple(dict.fromkeys([*capture_reasons, f"anchor_{exc.reason_code}"])),
            limits=limits,
        )

    pre_assessments = [
        (
            entry,
            _candidate_policy_exclusions(
                entry,
                context_signature=context_signature,
                capture_reasons=capture_reasons,
            ),
        )
        for entry in inventory.attempts
    ]
    aggregate_bytes = 0
    aggregate_rows = 0
    aggregate_context_segments = 0
    aggregate_context_bytes = 0
    aggregate_limit_reason: str | None = None
    for entry, reasons in pre_assessments:
        if reasons:
            continue
        if entry.trace_size_bytes is None or entry.trace_size_bytes < 0:
            aggregate_limit_reason = "candidate_trace_size_unavailable"
            break
        if entry.trace_row_count is None or entry.trace_row_count < 0:
            aggregate_limit_reason = "candidate_trace_rows_unavailable"
            break
        context_resources = _inventory_context_resources(entry)
        if context_resources is None:
            aggregate_limit_reason = "candidate_context_resources_unavailable"
            break
        aggregate_bytes += entry.trace_size_bytes
        aggregate_rows += entry.trace_row_count
        aggregate_context_segments += context_resources[0]
        aggregate_context_bytes += context_resources[1]
    if aggregate_bytes > max_candidate_trace_bytes:
        aggregate_limit_reason = "candidate_trace_byte_limit_exceeded"
    elif aggregate_rows > max_candidate_trace_rows:
        aggregate_limit_reason = "candidate_trace_row_limit_exceeded"
    elif aggregate_context_segments > max_candidate_context_segments:
        aggregate_limit_reason = "candidate_context_segment_limit_exceeded"
    elif aggregate_context_bytes > max_candidate_context_bytes:
        aggregate_limit_reason = "candidate_context_byte_limit_exceeded"
    if aggregate_limit_reason is not None:
        assessment_by_key = {
            entry.attempt_key: (
                "excluded" if reasons else "not_assessed",
                tuple(reasons) if reasons else (aggregate_limit_reason,),
            )
            for entry, reasons in pre_assessments
        }
        candidates = _candidate_rows(
            recorded_order, assessment_by_key, max_candidate_rows
        )
        return SessionBestAssessment(
            anchor_attempt_key=anchor_attempt_key,
            status=SessionBestStatus.ABSTAINED,
            scope=scope,
            candidate_count=candidate_count,
            timed_candidate_count=timed_candidate_count,
            candidates=candidates,
            candidates_omitted_count=max(0, candidate_count - len(candidates)),
            time_trial_best_status="assessment_limit_exceeded",
            time_trial_best=None,
            reasons=(aggregate_limit_reason,),
            limits=limits,
        )

    assessment_by_key: dict[str, tuple[str, tuple[str, ...]]] = {}
    verified_entries: list[StoredAttemptInventoryEntry] = []
    used_trace_bytes = 0
    used_trace_rows = 0
    used_context_segments = 0
    used_context_bytes = 0
    for entry, reasons in pre_assessments:
        if reasons:
            assessment_by_key[entry.attempt_key] = ("excluded", tuple(reasons))
            continue
        context_resources = _inventory_context_resources(entry)
        if context_resources is None:
            assessment_by_key[entry.attempt_key] = (
                "not_assessed",
                ("candidate_context_resources_unavailable",),
            )
            return _abstained_assessment(
                anchor_attempt_key,
                scope,
                recorded_order,
                assessment_by_key,
                candidate_count,
                timed_candidate_count,
                max_candidate_rows,
                limits,
                "candidate_context_resources_unavailable",
            )
        remaining_trace_bytes = max_candidate_trace_bytes - used_trace_bytes
        remaining_trace_rows = max_candidate_trace_rows - used_trace_rows
        remaining_context_segments = (
            max_candidate_context_segments - used_context_segments
        )
        remaining_context_bytes = max_candidate_context_bytes - used_context_bytes
        if entry.trace_size_bytes is None or entry.trace_size_bytes > remaining_trace_bytes:
            reason = "candidate_trace_byte_limit_exceeded"
        elif entry.trace_row_count is None or entry.trace_row_count > remaining_trace_rows:
            reason = "candidate_trace_row_limit_exceeded"
        elif context_resources[0] > remaining_context_segments:
            reason = "candidate_context_segment_limit_exceeded"
        elif context_resources[1] > remaining_context_bytes:
            reason = "candidate_context_byte_limit_exceeded"
        else:
            reason = None
        if reason is not None:
            assessment_by_key[entry.attempt_key] = ("not_assessed", (reason,))
            return _abstained_assessment(
                anchor_attempt_key,
                scope,
                recorded_order,
                assessment_by_key,
                candidate_count,
                timed_candidate_count,
                max_candidate_rows,
                limits,
                reason,
            )
        trace_byte_reservation = min(remaining_trace_bytes, entry.trace_size_bytes)
        trace_row_reservation = min(remaining_trace_rows, entry.trace_row_count)
        context_segment_reservation = min(
            remaining_context_segments, context_resources[0]
        )
        context_byte_reservation = min(remaining_context_bytes, context_resources[1])
        # Reserve the snapshot estimate before reading. This keeps every decode
        # within the remaining aggregate budget even if a read later fails.
        used_trace_bytes += trace_byte_reservation
        used_trace_rows += trace_row_reservation
        used_context_segments += context_segment_reservation
        used_context_bytes += context_byte_reservation
        try:
            trace = load_attempt_trace(
                database_path,
                entry.attempt_key,
                columns=_TRACE_IDENTITY_COLUMNS,
                max_trace_bytes=trace_byte_reservation,
                max_trace_rows=trace_row_reservation,
                max_context_bytes=context_byte_reservation,
                max_context_segments=context_segment_reservation,
            )
        except AttemptTraceReadLimitError as exc:
            assessment_by_key[entry.attempt_key] = (
                "not_assessed",
                (f"trace_{exc.limit_kind}_limit_exceeded",),
            )
            return _abstained_assessment(
                anchor_attempt_key,
                scope,
                recorded_order,
                assessment_by_key,
                candidate_count,
                timed_candidate_count,
                max_candidate_rows,
                limits,
                f"trace_{exc.limit_kind}_limit_exceeded",
            )
        except (OSError, ValueError):
            assessment_by_key[entry.attempt_key] = (
                "excluded",
                ("trace_missing_or_corrupt",),
            )
            continue
        if trace is None:
            reason = "candidate_inventory_changed_during_assessment"
            assessment_by_key[entry.attempt_key] = ("not_assessed", (reason,))
            return _abstained_assessment(
                anchor_attempt_key,
                scope,
                recorded_order,
                assessment_by_key,
                candidate_count,
                timed_candidate_count,
                max_candidate_rows,
                limits,
                reason,
            )
        if not _attempt_matches_inventory_snapshot(entry, trace):
            reason = "candidate_inventory_changed_during_assessment"
            assessment_by_key[entry.attempt_key] = ("not_assessed", (reason,))
            return _abstained_assessment(
                anchor_attempt_key,
                scope,
                recorded_order,
                assessment_by_key,
                candidate_count,
                timed_candidate_count,
                max_candidate_rows,
                limits,
                reason,
            )
        del trace
        verified_entries.append(entry)
        assessment_by_key[entry.attempt_key] = ("eligible", ())

    best_entry = (
        min(verified_entries, key=lambda entry: (int(entry.lap_time_ms), entry.attempt_number))
        if verified_entries
        else None
    )
    candidates = _candidate_rows(recorded_order, assessment_by_key, max_candidate_rows)
    best = (
        {
            "attempt_key": best_entry.attempt_key,
            "attempt_number": best_entry.attempt_number,
            "lap_time_ms": best_entry.lap_time_ms,
            "trace_sha256": best_entry.trace_sha256,
            "trace_schema_version": best_entry.trace_schema_version,
        }
        if best_entry is not None
        else None
    )
    reasons = capture_reasons or (
        ("no_candidate_passed_time_trial_policy",) if best is None else ()
    )
    return SessionBestAssessment(
        anchor_attempt_key=anchor_attempt_key,
        status=SessionBestStatus.ASSESSED,
        scope=scope,
        candidate_count=candidate_count,
        timed_candidate_count=timed_candidate_count,
        candidates=candidates,
        candidates_omitted_count=max(0, candidate_count - len(candidates)),
        time_trial_best_status="selected" if best is not None else "no_eligible_lap",
        time_trial_best=best,
        reasons=tuple(reasons),
        limits=limits,
    )


def _inventory_context_resources(
    entry: StoredAttemptInventoryEntry,
) -> tuple[int, int] | None:
    if entry.context_segment_count is None or entry.context_bytes is None:
        return None
    if entry.context_segment_count < 0 or entry.context_bytes < 0:
        return None
    return entry.context_segment_count, entry.context_bytes


def _attempt_matches_inventory_snapshot(
    entry: StoredAttemptInventoryEntry,
    trace: object,
) -> bool:
    """Require loaded provenance and eligibility to match the assessed inventory."""
    context_resources = _inventory_context_resources(entry)
    if context_resources is None:
        return False
    samples = getattr(trace, "samples", None)
    return (
        getattr(trace, "attempt_key", None) == entry.attempt_key
        and getattr(trace, "run_id", None) == entry.run_id
        and getattr(trace, "session_uid", None) == entry.session_uid
        and getattr(trace, "car_index", None) == entry.car_index
        and getattr(trace, "attempt_number", None) == entry.attempt_number
        and getattr(trace, "disposition", None) == entry.disposition
        and getattr(trace, "lap_time_ms", None) == entry.lap_time_ms
        and getattr(trace, "game_valid", None) == entry.game_valid
        and getattr(trace, "reference_eligible", None) == entry.reference_eligible
        and getattr(trace, "start_observed", None) == entry.start_observed
        and getattr(trace, "pit_encountered", None) == entry.pit_encountered
        and getattr(trace, "superseded", None) == entry.superseded
        and getattr(trace, "lifecycle_assessed", None) == entry.lifecycle_assessed
        and getattr(trace, "exclusion_reasons", None) == entry.exclusion_reasons
        and getattr(trace, "trace_sha256", None) == entry.trace_sha256
        and getattr(trace, "trace_schema_version", None) == entry.trace_schema_version
        and getattr(trace, "quality", None) == entry.quality
        and getattr(trace, "context_segments", None) == entry.context_segments
        and getattr(trace, "source_sample_count", None) == entry.trace_row_count
        and isinstance(samples, tuple)
        and len(samples) == entry.trace_row_count
        and entry.trace_ready is True
    )


def _abstained_assessment(
    anchor_attempt_key: str,
    scope: Mapping[str, object],
    recorded_order: list[tuple[StoredAttemptInventoryEntry, int | None]],
    assessment_by_key: Mapping[str, tuple[str, tuple[str, ...]]],
    candidate_count: int,
    timed_candidate_count: int,
    max_candidate_rows: int,
    limits: Mapping[str, int],
    reason: str,
) -> SessionBestAssessment:
    candidates = _candidate_rows(recorded_order, assessment_by_key, max_candidate_rows)
    return SessionBestAssessment(
        anchor_attempt_key=anchor_attempt_key,
        status=SessionBestStatus.ABSTAINED,
        scope=scope,
        candidate_count=candidate_count,
        timed_candidate_count=timed_candidate_count,
        candidates=candidates,
        candidates_omitted_count=max(0, candidate_count - len(candidates)),
        time_trial_best_status="assessment_limit_exceeded",
        time_trial_best=None,
        reasons=(reason,),
        limits=limits,
    )


def _has_positive_time(value: int | None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _session_best_capture_reasons(
    capture_complete: bool,
    completion: Mapping[str, object] | None,
    processing_quality: Mapping[str, object],
) -> tuple[str, ...]:
    """Keep bounded replay-import evidence visible even without a footer."""
    reasons = list(_capture_loss_reasons(capture_complete, completion, processing_quality))
    for field, reason in _IMPORT_FRAME_LOSS_FIELDS.items():
        if field not in processing_quality:
            reasons.append("import_frame_loss_metrics_unavailable")
            continue
        value = processing_quality[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            reasons.append("import_frame_loss_metrics_invalid")
        elif value > 0:
            reasons.append(reason)
    return tuple(dict.fromkeys(reasons))


def _recorded_order(
    attempts: tuple[StoredAttemptInventoryEntry, ...],
) -> list[tuple[StoredAttemptInventoryEntry, int | None]]:
    ordered = sorted(
        attempts,
        key=lambda entry: (
            0 if _has_positive_time(entry.lap_time_ms) else 1,
            int(entry.lap_time_ms) if _has_positive_time(entry.lap_time_ms) else 0,
            entry.attempt_number,
        ),
    )
    rank = 0
    result: list[tuple[StoredAttemptInventoryEntry, int | None]] = []
    for entry in ordered:
        if _has_positive_time(entry.lap_time_ms):
            rank += 1
            result.append((entry, rank))
        else:
            result.append((entry, None))
    return result


def _to_candidate(
    entry: StoredAttemptInventoryEntry,
    recorded_time_rank: int | None,
    eligibility_status: str,
    exclusion_reasons: tuple[str, ...],
) -> SessionBestCandidate:
    return SessionBestCandidate(
        attempt_key=entry.attempt_key,
        attempt_number=entry.attempt_number,
        disposition=entry.disposition,
        lap_time_ms=entry.lap_time_ms,
        recorded_time_rank=recorded_time_rank,
        eligibility_status=eligibility_status,
        trace_sha256=entry.trace_sha256,
        exclusion_reasons=exclusion_reasons,
    )


def _candidate_rows(
    recorded_order: list[tuple[StoredAttemptInventoryEntry, int | None]],
    assessment_by_key: Mapping[str, tuple[str, tuple[str, ...]]],
    limit: int,
) -> tuple[SessionBestCandidate, ...]:
    return tuple(
        _to_candidate(
            entry,
            rank,
            assessment_by_key.get(entry.attempt_key, ("not_assessed", ()))[0],
            assessment_by_key.get(entry.attempt_key, ("not_assessed", ()))[1],
        )
        for entry, rank in recorded_order[:limit]
    )


def _unavailable_result(
    anchor_attempt_key: str,
    status: SessionBestStatus,
    *,
    reason: str,
    limits: Mapping[str, int],
    scope: Mapping[str, object] | None = None,
) -> SessionBestAssessment:
    return SessionBestAssessment(
        anchor_attempt_key=anchor_attempt_key,
        status=status,
        scope=scope,
        candidate_count=None,
        timed_candidate_count=None,
        candidates=(),
        candidates_omitted_count=None,
        time_trial_best_status=(
            "assessment_limit_exceeded"
            if status is SessionBestStatus.ABSTAINED
            else "anchor_unavailable"
        ),
        time_trial_best=None,
        reasons=(reason,),
        limits=limits,
    )
