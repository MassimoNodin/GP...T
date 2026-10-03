from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, replace

from ..analysis.continuity import float32_ulp
from .lap_tracker import LapAttempt, LapDisposition


MAX_RECONCILIATION_WORK = 1_000_000


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    session_uid: int
    event_ordinal: int
    frame_ordinal: int
    current_frame_identifier: int
    current_overall_frame_identifier: int
    packet_format: int
    packet_version: int | None
    event_code: str | None
    event_kind: str
    session_time_s: float
    target_game_frame_identifier: int | None
    target_session_time_s: float | None
    prior_session_time_s: float | None
    cause: str
    evidence_status: str
    details_hex: str
    details_length_bytes: int = 0
    details_truncated: bool = False
    duplicate_count: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "session_uid": self.session_uid,
            "event_ordinal": self.event_ordinal,
            "frame_ordinal": self.frame_ordinal,
            "current_frame_identifier": self.current_frame_identifier,
            "current_overall_frame_identifier": self.current_overall_frame_identifier,
            "packet_format": self.packet_format,
            "packet_version": self.packet_version,
            "event_code": self.event_code,
            "event_kind": self.event_kind,
            "session_time_s": self.session_time_s,
            "target_game_frame_identifier": self.target_game_frame_identifier,
            "target_session_time_s": self.target_session_time_s,
            "prior_session_time_s": self.prior_session_time_s,
            "cause": self.cause,
            "evidence_status": self.evidence_status,
            "details_hex": self.details_hex,
            "details_length_bytes": self.details_length_bytes,
            "details_truncated": self.details_truncated,
            "duplicate_count": self.duplicate_count,
        }


def reconcile_attempt_lifecycle(
    attempts: tuple[LapAttempt, ...],
    events: tuple[LifecycleEvent, ...],
    *,
    truncated_session_uids: frozenset[int] = frozenset(),
    max_work: int = MAX_RECONCILIATION_WORK,
) -> tuple[
    tuple[LapAttempt, ...],
    tuple[tuple[str, int, str], ...],
    frozenset[int],
    int,
]:
    """Assess rewind evidence with a bounded per-session sweep.

    Attempts are activated as their ordered end ordinal falls before a boundary.
    A max-heap lets verified flashbacks supersede only completed attempts whose
    completion time reaches the target, while uncertain boundaries change each
    still-assessable attempt once. If a work budget or event buffer is exhausted,
    the affected session fails closed as unassessed.
    """
    if max_work < 0:
        raise ValueError("max_work must be non-negative")

    assessed = {
        attempt.attempt_id: replace(
            attempt,
            superseded=False,
            lifecycle_assessed=attempt.session_uid not in truncated_session_uids,
        )
        for attempt in attempts
    }
    by_session: dict[int, list[LapAttempt]] = {}
    for attempt in attempts:
        by_session.setdefault(attempt.session_uid, []).append(attempt)
    events_by_session: dict[int, list[LifecycleEvent]] = {}
    for event in events:
        if event.cause in {
            "flashback",
            "session_time_regression",
            "event_evidence_unknown",
        }:
            events_by_session.setdefault(event.session_uid, []).append(event)

    links: list[tuple[str, int, str]] = []
    truncated = set(truncated_session_uids)
    work = 0
    budget_exhausted = False

    def mark_unassessed(session_attempts: list[LapAttempt]) -> None:
        for attempt in session_attempts:
            current = assessed[attempt.attempt_id]
            if current.superseded is True:
                continue
            assessed[attempt.attempt_id] = replace(
                current, superseded=None, lifecycle_assessed=False
            )

    for uid in sorted(set(by_session) | set(events_by_session)):
        session_attempts = by_session.get(uid, [])
        session_events = sorted(
            events_by_session.get(uid, []),
            key=lambda item: (item.frame_ordinal, item.event_ordinal),
        )
        if uid in truncated:
            mark_unassessed(session_attempts)
            continue
        if budget_exhausted:
            truncated.add(uid)
            mark_unassessed(session_attempts)
            continue
        if not session_events:
            continue

        # A missing ordinal cannot be safely placed on either side of a boundary.
        if any(attempt.end_frame_ordinal is None for attempt in session_attempts):
            truncated.add(uid)
            mark_unassessed(session_attempts)
            continue

        ordered_attempts = sorted(
            session_attempts,
            key=lambda item: (int(item.end_frame_ordinal), item.attempt_number),
        )
        pending_index = 0
        active: dict[str, LapAttempt] = {}
        # Negative completion times implement a max-heap. The attempt ID is a
        # deterministic tie-breaker and each attempt is inserted at most once.
        completed_heap: list[tuple[float, str, LapAttempt]] = []
        session_truncated = False

        for event in session_events:
            while (
                pending_index < len(ordered_attempts)
                and int(ordered_attempts[pending_index].end_frame_ordinal)
                < event.frame_ordinal
            ):
                if work >= max_work:
                    session_truncated = True
                    budget_exhausted = True
                    break
                attempt = ordered_attempts[pending_index]
                pending_index += 1
                work += 1
                current = assessed[attempt.attempt_id]
                if current.superseded is True or not current.lifecycle_assessed:
                    continue
                active[attempt.attempt_id] = attempt
                if (
                    attempt.disposition is LapDisposition.COMPLETED
                    and attempt.end_session_time_s is not None
                    and math.isfinite(attempt.end_session_time_s)
                ):
                    heapq.heappush(
                        completed_heap,
                        (-attempt.end_session_time_s, attempt.attempt_id, attempt),
                    )
            if session_truncated:
                break

            target_time = event.target_session_time_s
            uncertain = (
                event.cause != "flashback"
                or event.evidence_status != "verified"
                or target_time is None
                or not math.isfinite(target_time)
                or target_time < 0
            )
            if uncertain:
                for attempt_id in tuple(active):
                    if work >= max_work:
                        session_truncated = True
                        budget_exhausted = True
                        break
                    current = assessed[attempt_id]
                    work += 1
                    if current.lifecycle_assessed and current.superseded is not True:
                        assessed[attempt_id] = replace(
                            current, superseded=None, lifecycle_assessed=False
                        )
                        links.append(
                            (attempt_id, event.event_ordinal, "lifecycle_evidence_uncertain")
                        )
                active.clear()
                completed_heap.clear()
                if session_truncated:
                    break
                continue

            assert target_time is not None
            while completed_heap:
                completion_time = -completed_heap[0][0]
                tolerance = max(float32_ulp(completion_time), float32_ulp(target_time))
                if completion_time < target_time - tolerance:
                    break
                if work >= max_work:
                    session_truncated = True
                    budget_exhausted = True
                    break
                _, attempt_id, attempt = heapq.heappop(completed_heap)
                work += 1
                current = assessed[attempt_id]
                if attempt_id not in active or current.superseded is True:
                    continue
                assessed[attempt_id] = replace(current, superseded=True)
                active.pop(attempt_id, None)
                links.append((attempt_id, event.event_ordinal, "superseded_by_flashback"))
            if session_truncated:
                break

        if session_truncated:
            truncated.add(uid)
            mark_unassessed(session_attempts)

    output: list[LapAttempt] = []
    for attempt in attempts:
        current = assessed[attempt.attempt_id]
        reasons = list(current.exclusion_reasons)
        if current.superseded is True:
            reasons.append("superseded_by_flashback")
        elif not current.lifecycle_assessed or current.superseded is None:
            reasons.append("lifecycle_evidence_unassessed")
        reasons_tuple = tuple(dict.fromkeys(reasons))
        output.append(
            replace(
                current,
                reference_eligible=(
                    current.reference_eligible
                    and current.superseded is not True
                    and current.lifecycle_assessed
                    and current.superseded is not None
                ),
                exclusion_reasons=reasons_tuple,
            )
        )
    return tuple(output), tuple(links), frozenset(truncated), work
