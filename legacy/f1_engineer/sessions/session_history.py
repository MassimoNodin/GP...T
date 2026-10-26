from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .lap_tracker import LapAttempt, LapDisposition
from ..udp.session_history import SessionHistoryLap, SessionHistoryPacket


MAX_HISTORY_ASSOCIATION_WORK = 1_000_000
MAX_CANDIDATES_PER_LAP_IDENTITY = 8
MAX_CANDIDATES_PER_SESSION = 100_000
MAX_CONFLICT_EXAMPLES = 3


@dataclass(frozen=True, slots=True)
class SessionHistoryObservation:
    packet: SessionHistoryPacket
    association_epoch: int
    scope_assessable: bool


@dataclass(slots=True)
class _Candidate:
    first_lap: SessionHistoryLap
    first_packet: SessionHistoryPacket
    last_lap: SessionHistoryLap
    last_packet: SessionHistoryPacket
    scope_assessable: bool
    first_qualifying_by_attempt: dict[
        str, tuple[SessionHistoryLap, SessionHistoryPacket]
    ]

    def qualifying_observation(
        self, attempt_id: str, completion_frame_ordinal: int
    ) -> tuple[SessionHistoryLap, SessionHistoryPacket] | None:
        first_qualifying = self.first_qualifying_by_attempt.get(attempt_id)
        if first_qualifying is not None:
            return first_qualifying
        if self.first_packet.frame_ordinal > completion_frame_ordinal:
            return self.first_lap, self.first_packet
        if self.last_packet.frame_ordinal > completion_frame_ordinal:
            return self.last_lap, self.last_packet
        return None


@dataclass(frozen=True, slots=True)
class AttemptTimingEvidence:
    status: str
    reasons: tuple[str, ...]
    reported_lap_time_ms: int | None = None
    sector1_time_ms: int | None = None
    sector2_time_ms: int | None = None
    sector3_time_ms: int | None = None
    validity_flags: int | None = None
    sector_sum_residual_ms: int | None = None
    source_lap: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    candidates: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, object]:
        flags = self.validity_flags
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "reported_lap_time_ms": self.reported_lap_time_ms,
            "sector1_time_ms": self.sector1_time_ms,
            "sector2_time_ms": self.sector2_time_ms,
            "sector3_time_ms": self.sector3_time_ms,
            "validity_flags": flags,
            "lap_valid": None if flags is None else bool(flags & 0x01),
            "sector1_valid": None if flags is None else bool(flags & 0x02),
            "sector2_valid": None if flags is None else bool(flags & 0x04),
            "sector3_valid": None if flags is None else bool(flags & 0x08),
            "unknown_validity_bits": None if flags is None else flags & ~0x0F,
            "sector_sum_residual_ms": self.sector_sum_residual_ms,
            "source_lap": self.source_lap,
            "source": self.source,
            "candidates": list(self.candidates),
        }


def _candidate_signature(lap: SessionHistoryLap) -> tuple[int, ...]:
    return (
        lap.lap_time_ms,
        lap.sector1_time_ms_part,
        lap.sector1_time_minutes_part,
        lap.sector2_time_ms_part,
        lap.sector2_time_minutes_part,
        lap.sector3_time_ms_part,
        lap.sector3_time_minutes_part,
        lap.validity_flags,
    )


def _source_provenance(
    packet: SessionHistoryPacket,
    lap: SessionHistoryLap,
    *,
    association_epoch: int,
    scope_assessable: bool,
) -> dict[str, object]:
    return {
        "session_uid": packet.session_uid,
        "association_epoch": association_epoch,
        "scope_assessable": scope_assessable,
        "packet_format": packet.packet_format,
        "packet_version": packet.packet_version,
        "header_player_car_index": packet.header_player_car_index,
        "car_index": packet.car_index,
        "source_lap_number": lap.lap_index + 1,
        "num_laps_including_current": packet.num_laps,
        "frame_identifier": packet.frame_identifier,
        "overall_frame_identifier": packet.overall_frame_identifier,
        "frame_ordinal": packet.frame_ordinal,
        "session_time_s": packet.session_time_s,
        "capture_sequence": packet.source_sequence,
        "best_lap_time_lap_number": packet.best_lap_time_lap_number,
        "best_sector1_lap_number": packet.best_sector1_lap_number,
        "best_sector2_lap_number": packet.best_sector2_lap_number,
        "best_sector3_lap_number": packet.best_sector3_lap_number,
        "tyre_stints": [stint.to_dict() for stint in packet.tyre_stints],
    }


def _sector_time_if_available(lap: SessionHistoryLap, sector: int) -> int | None:
    milliseconds = getattr(lap, f"sector{sector}_time_ms_part")
    minutes = getattr(lap, f"sector{sector}_time_minutes_part")
    if milliseconds >= 60_000:
        return None
    value = minutes * 60_000 + milliseconds
    return value if value > 0 else None


class SessionHistoryAccumulator:
    """Retain distinct bounded history candidates and reconcile finalized laps."""

    def __init__(
        self,
        *,
        max_work: int = MAX_HISTORY_ASSOCIATION_WORK,
        max_candidates_per_session: int = MAX_CANDIDATES_PER_SESSION,
    ) -> None:
        if max_work < 0 or max_candidates_per_session < 0:
            raise ValueError("history limits must be non-negative")
        self.max_work = max_work
        self.max_candidates_per_session = max_candidates_per_session
        self.work = 0
        self.reconciliation_work = 0
        self.truncated_session_uids: set[int] = set()
        self._candidate_counts_by_session: dict[int, int] = {}
        self._attempt_cutoffs_by_key: dict[
            tuple[int, int, int, int, int], tuple[str, int]
        ] = {}
        self._repeated_attempt_identity_keys: set[tuple[int, int, int, int, int]] = set()
        self._candidates: dict[
            tuple[int, int, int, int, int], dict[tuple[int, ...], _Candidate]
        ] = {}

    def mark_truncated(self, session_uid: int) -> None:
        if session_uid:
            self.truncated_session_uids.add(session_uid)

    def register_attempt(self, attempt: LapAttempt) -> None:
        """Register a finalized attempt so later snapshots retain first provenance."""
        if (
            not _has_completion_identity(attempt)
            or attempt.start_association_epoch != attempt.association_epoch
            or attempt.start_association_packet_format
            != attempt.association_packet_format
        ):
            return
        assert attempt.association_epoch is not None
        assert attempt.association_packet_format is not None
        assert attempt.completion_frame_ordinal is not None
        key = (
            attempt.session_uid,
            attempt.association_epoch,
            attempt.association_packet_format,
            attempt.car_index,
            attempt.lap_number,
        )
        if key in self._repeated_attempt_identity_keys:
            return
        if key in self._attempt_cutoffs_by_key:
            self._attempt_cutoffs_by_key.pop(key, None)
            self._repeated_attempt_identity_keys.add(key)
            return
        self._attempt_cutoffs_by_key[key] = (
            attempt.attempt_id,
            attempt.completion_frame_ordinal,
        )

    def observe(self, observation: SessionHistoryObservation) -> None:
        packet = observation.packet
        uid = packet.session_uid
        if uid == 0 or uid in self.truncated_session_uids:
            return
        # Any populated row can be a completed lap. Completion, exact reported
        # time, and strict post-finalization chronology decide whether it matches.
        for lap in packet.lap_history:
            if self.work >= self.max_work:
                self.mark_truncated(uid)
                return
            self.work += 1
            key = (
                uid,
                observation.association_epoch,
                packet.packet_format,
                packet.car_index,
                lap.lap_index + 1,
            )
            bucket = self._candidates.setdefault(key, {})
            signature = _candidate_signature(lap)
            candidate = bucket.get(signature)
            if candidate is not None:
                candidate.last_lap = lap
                candidate.last_packet = packet
                candidate.scope_assessable = (
                    candidate.scope_assessable and observation.scope_assessable
                )
                self._capture_first_qualifying(candidate, key, lap, packet, observation)
                continue
            session_candidate_count = self._candidate_counts_by_session.get(uid, 0)
            if (
                len(bucket) >= MAX_CANDIDATES_PER_LAP_IDENTITY
                or session_candidate_count >= self.max_candidates_per_session
            ):
                self.mark_truncated(uid)
                return
            bucket[signature] = _Candidate(
                first_lap=lap,
                first_packet=packet,
                last_lap=lap,
                last_packet=packet,
                scope_assessable=observation.scope_assessable,
                first_qualifying_by_attempt={},
            )
            self._capture_first_qualifying(
                bucket[signature], key, lap, packet, observation
            )
            self._candidate_counts_by_session[uid] = session_candidate_count + 1

    def _capture_first_qualifying(
        self,
        candidate: _Candidate,
        key: tuple[int, int, int, int, int],
        lap: SessionHistoryLap,
        packet: SessionHistoryPacket,
        observation: SessionHistoryObservation,
    ) -> None:
        registered = self._attempt_cutoffs_by_key.get(key)
        if registered is None or not observation.scope_assessable:
            return
        attempt_id, completion_ordinal = registered
        if packet.frame_ordinal > completion_ordinal:
            candidate.first_qualifying_by_attempt.setdefault(
                attempt_id, (lap, packet)
            )

    @property
    def candidate_count(self) -> int:
        return sum(self._candidate_counts_by_session.values())

    def reconcile(
        self,
        attempts: tuple[LapAttempt, ...],
        *,
        truncated_session_uids: frozenset[int] = frozenset(),
    ) -> dict[str, AttemptTimingEvidence]:
        self.truncated_session_uids.update(truncated_session_uids)
        repeated: set[str] = set()
        identity_counts: dict[tuple[int, int, int, int, int], int] = {}
        identity_by_attempt: dict[str, tuple[int, int, int, int, int]] = {}
        over_budget_sessions: set[int] = set()
        for attempt in attempts:
            if self._consume_work(attempt.session_uid):
                over_budget_sessions.add(attempt.session_uid)
            if not _has_completion_identity(attempt):
                continue
            assert attempt.association_epoch is not None
            assert attempt.association_packet_format is not None
            assert attempt.lap_time_ms is not None
            identity = (
                attempt.session_uid,
                attempt.association_epoch,
                attempt.association_packet_format,
                attempt.car_index,
                attempt.lap_number,
            )
            identity_counts[identity] = identity_counts.get(identity, 0) + 1
            identity_by_attempt[attempt.attempt_id] = identity
        repeated = {
            attempt_id
            for attempt_id, identity in identity_by_attempt.items()
            if identity_counts[identity] > 1
        }

        output: dict[str, AttemptTimingEvidence] = {}
        for attempt in attempts:
            uid = attempt.session_uid
            if uid in over_budget_sessions:
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "truncated", ("session_history_association_work_limit_exceeded",)
                )
                continue
            if uid in self.truncated_session_uids:
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "truncated", ("session_history_evidence_truncated",)
                )
                continue
            if attempt.disposition is not LapDisposition.COMPLETED:
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "unavailable", ("attempt_not_completed",)
                )
                continue
            if not _has_completion_identity(attempt):
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "unavailable", ("attempt_completion_provenance_unavailable",)
                )
                continue
            if (
                attempt.start_association_epoch != attempt.association_epoch
                or attempt.start_association_packet_format
                != attempt.association_packet_format
            ):
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "ambiguous", ("attempt_crosses_association_boundary",)
                )
                continue
            if not attempt.association_scope_assessable:
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "unavailable", ("association_scope_uncertain",)
                )
                continue
            if attempt.attempt_id in repeated:
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "ambiguous", ("repeated_lap_identity_in_association_epoch",)
                )
                continue

            assert attempt.association_epoch is not None
            assert attempt.association_packet_format is not None
            assert attempt.end_frame_ordinal is not None
            assert attempt.completion_frame_ordinal is not None
            assert attempt.lap_time_ms is not None
            key = (
                uid,
                attempt.association_epoch,
                attempt.association_packet_format,
                attempt.car_index,
                attempt.lap_number,
            )
            bucket = self._candidates.get(key, {})
            eligible: list[tuple[SessionHistoryLap, SessionHistoryPacket, bool]] = []
            for candidate in bucket.values():
                if self._consume_work(uid):
                    over_budget_sessions.add(uid)
                    break
                selected = candidate.qualifying_observation(
                    attempt.attempt_id, attempt.completion_frame_ordinal
                )
                if selected is not None:
                    lap, packet = selected
                    if lap.lap_time_ms > 0:
                        eligible.append((lap, packet, candidate.scope_assessable))
            if uid in over_budget_sessions:
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "truncated", ("session_history_association_work_limit_exceeded",)
                )
                continue
            if not eligible:
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "unavailable", ("no_post_finalization_player_history",)
                )
                continue
            if any(not assessable for _, _, assessable in eligible):
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "unavailable", ("association_scope_uncertain",)
                )
                continue
            if len(eligible) > 1:
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "conflicting",
                    ("player_history_snapshots_disagree",),
                    candidates=tuple(
                        _candidate_example(lap, packet, attempt.association_epoch)
                        for lap, packet, _ in eligible[:MAX_CONFLICT_EXAMPLES]
                    ),
                )
                continue

            lap, packet, _ = eligible[0]
            if lap.lap_time_ms != attempt.lap_time_ms:
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "unavailable", ("reported_lap_time_mismatch",)
                )
                continue
            output[attempt.attempt_id] = AttemptTimingEvidence(
                "matched",
                (),
                reported_lap_time_ms=lap.lap_time_ms,
                sector1_time_ms=_sector_time_if_available(lap, 1),
                sector2_time_ms=_sector_time_if_available(lap, 2),
                sector3_time_ms=_sector_time_if_available(lap, 3),
                validity_flags=lap.validity_flags,
                sector_sum_residual_ms=lap.sector_sum_residual_ms,
                source_lap=lap.to_dict(),
                source=_source_provenance(
                    packet,
                    lap,
                    association_epoch=attempt.association_epoch,
                    scope_assessable=True,
                ),
            )
        # A budget crossed while processing a later attempt invalidates all results
        # for that session because uniqueness can no longer be established globally.
        for attempt in attempts:
            if attempt.session_uid in over_budget_sessions:
                output[attempt.attempt_id] = AttemptTimingEvidence(
                    "truncated", ("session_history_association_work_limit_exceeded",)
                )
        self.truncated_session_uids.update(over_budget_sessions)
        return output

    def _consume_work(self, session_uid: int) -> bool:
        if self.work >= self.max_work:
            return True
        self.work += 1
        self.reconciliation_work += 1
        return False


def _has_completion_identity(attempt: LapAttempt) -> bool:
    return (
        attempt.disposition is LapDisposition.COMPLETED
        and attempt.end_frame_ordinal is not None
        and attempt.completion_frame_ordinal is not None
        and attempt.association_epoch is not None
        and attempt.start_association_epoch is not None
        and attempt.association_packet_format is not None
        and attempt.start_association_packet_format is not None
        and attempt.lap_time_ms is not None
        and attempt.lap_time_ms > 0
    )


def _candidate_example(
    lap: SessionHistoryLap, packet: SessionHistoryPacket, association_epoch: int
) -> dict[str, object]:
    return {
        "lap": lap.to_dict(),
        "source": _source_provenance(
            packet,
            lap,
            association_epoch=association_epoch,
            scope_assessable=True,
        ),
    }
