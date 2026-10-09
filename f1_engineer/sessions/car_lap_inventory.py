from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Callable

from ..udp.models import DecodedPacket, PacketId
from ..udp.lap_data import CarLapData
from ..udp.participants import ParticipantData, ParticipantsDecoder, ParticipantsPacket
from .context import SessionContext
from .lap_tracker import (
    LapAttempt,
    LapDisposition,
    LapObservation,
    LapTracker,
    SessionContextSegment,
)

MAX_CAR_SLOTS = 24
MAX_ROSTER_PACKETS_PER_FRAME = 16
MAX_RETAINED_CAR_LAP_EVENTS = 8192
MAX_CAR_LAP_COVERAGE_SESSIONS = 64
MAX_CAR_LAP_CONTEXT_SEGMENTS = 64


@dataclass(frozen=True, slots=True)
class CarSlotTenure:
    session_uid: int
    packet_format: int
    lifecycle_epoch: int
    car_index: int
    tenure_ordinal: int
    start_frame_ordinal: int
    end_frame_ordinal_exclusive: int
    participant_frame_identifier: int
    participant_wire_fingerprint: str
    participant_identity_fingerprint: str
    close_reason: str


@dataclass(frozen=True, slots=True)
class ObservedCarLapAttempt:
    session_uid: int
    packet_format: int
    lifecycle_epoch: int
    car_index: int
    tenure_ordinal: int
    attempt: LapAttempt
    participant_frame_identifier: int
    participant_wire_fingerprint: str
    participant_identity_fingerprint: str


@dataclass(slots=True)
class _OpenTenure:
    session_uid: int
    packet_format: int
    lifecycle_epoch: int
    car_index: int
    tenure_ordinal: int
    start_frame_ordinal: int
    participant_frame_identifier: int
    participant_wire_fingerprint: str
    participant_identity_fingerprint: str
    last_frame_ordinal: int


ContextTimelineProvider = Callable[
    [int, int], tuple[tuple[int, SessionContext | None], ...]
]


def _identity_fingerprint(participant: ParticipantData) -> str | None:
    identity = (
        participant.ai_controlled,
        participant.driver_id,
        participant.network_id,
        participant.team_id,
        participant.race_number,
        participant.nationality_id,
        participant.name.strip(),
        participant.platform_id,
    )
    if not any(value not in (0, False, "") for value in identity):
        return None
    payload = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _wire_fingerprint(packet: DecodedPacket) -> str:
    return hashlib.sha256(packet.wire_fingerprint).hexdigest()


class CarLapInventoryTracker:
    """Track diagnostic lap attempts only inside an admitted participant tenure."""

    def active_tenures(self, frame_ordinals: dict[int, int] | None = None) -> tuple[CarSlotTenure, ...]:
        return tuple(CarSlotTenure(
            session_uid=tenure.session_uid, packet_format=tenure.packet_format,
            lifecycle_epoch=tenure.lifecycle_epoch, car_index=tenure.car_index,
            tenure_ordinal=tenure.tenure_ordinal, start_frame_ordinal=tenure.start_frame_ordinal,
            end_frame_ordinal_exclusive=max(tenure.last_frame_ordinal, (frame_ordinals or {}).get(tenure.session_uid, 0)) + 1,
            participant_frame_identifier=tenure.participant_frame_identifier,
            participant_wire_fingerprint=tenure.participant_wire_fingerprint,
            participant_identity_fingerprint=tenure.participant_identity_fingerprint,
            close_reason="active",
        ) for tenure in self._open_tenures.values())

    def interrupt(self, frame_ordinal: int, reason: str) -> None:
        self._close_scope(frame_ordinal=frame_ordinal, reason=reason)
        self._slot_state = {slot: "unknown" for slot in range(MAX_CAR_SLOTS)}

    def __init__(self) -> None:
        self.lap_tracker = LapTracker()
        self.participants_decoder = ParticipantsDecoder()
        self.session_uid: int | None = None
        self.packet_format: int | None = None
        self.lifecycle_epoch: int | None = None
        self._open_tenures: dict[int, _OpenTenure] = {}
        self._slot_state: dict[int, str] = {}
        self._next_tenure_ordinal: dict[int, int] = {}
        self._completed_tenures: list[CarSlotTenure] = []
        self._completed_attempts: list[ObservedCarLapAttempt] = []
        self._overflowed = False
        self._coverage_counts: dict[tuple[int, int, str], int] = {}
        self._coverage_overflowed = False

    @property
    def overflowed(self) -> bool:
        return self._overflowed or self._coverage_overflowed

    @property
    def coverage_counts(self) -> dict[tuple[int, int, str], int]:
        return dict(self._coverage_counts)

    def _count_unassociated(self, car_index: int, reason: str) -> None:
        if self.session_uid is None:
            return
        key = (self.session_uid, car_index, reason)
        if key not in self._coverage_counts and len(self._coverage_counts) >= (
            MAX_CAR_LAP_COVERAGE_SESSIONS * MAX_CAR_SLOTS * 8
        ):
            self._coverage_overflowed = True
            return
        self._coverage_counts[key] = self._coverage_counts.get(key, 0) + 1

    def _append_attempts(
        self,
        attempts: tuple[LapAttempt, ...],
        car_index: int,
        tenure: _OpenTenure | None = None,
    ) -> None:
        tenure = tenure or self._open_tenures.get(car_index)
        for attempt in attempts:
            if tenure is None:
                self._count_unassociated(car_index, "attempt_without_admitted_tenure")
                continue
            if len(self._completed_attempts) >= MAX_RETAINED_CAR_LAP_EVENTS:
                self._overflowed = True
                continue
            self._completed_attempts.append(
                ObservedCarLapAttempt(
                    session_uid=tenure.session_uid,
                    packet_format=tenure.packet_format,
                    lifecycle_epoch=tenure.lifecycle_epoch,
                    car_index=car_index,
                    tenure_ordinal=tenure.tenure_ordinal,
                    attempt=replace(attempt, reference_eligible=False),
                    participant_frame_identifier=tenure.participant_frame_identifier,
                    participant_wire_fingerprint=tenure.participant_wire_fingerprint,
                    participant_identity_fingerprint=tenure.participant_identity_fingerprint,
                )
            )

    def _close_car(self, car_index: int, *, frame_ordinal: int, reason: str) -> None:
        tenure = self._open_tenures.get(car_index)
        attempts = self.lap_tracker.close_car(
            car_index,
            reason=reason,
            disposition=LapDisposition.ABANDONED,
        )
        self._append_attempts(attempts, car_index, tenure)
        self.lap_tracker.drain_attempts()
        self._open_tenures.pop(car_index, None)
        if tenure is None:
            return
        if len(self._completed_tenures) >= MAX_RETAINED_CAR_LAP_EVENTS:
            self._overflowed = True
        else:
            self._completed_tenures.append(
                CarSlotTenure(
                    session_uid=tenure.session_uid,
                    packet_format=tenure.packet_format,
                    lifecycle_epoch=tenure.lifecycle_epoch,
                    car_index=car_index,
                    tenure_ordinal=tenure.tenure_ordinal,
                    start_frame_ordinal=tenure.start_frame_ordinal,
                    end_frame_ordinal_exclusive=max(
                        tenure.start_frame_ordinal + 1, frame_ordinal
                    ),
                    participant_frame_identifier=tenure.participant_frame_identifier,
                    participant_wire_fingerprint=tenure.participant_wire_fingerprint,
                    participant_identity_fingerprint=tenure.participant_identity_fingerprint,
                    close_reason=reason,
                )
            )
        self._slot_state[car_index] = reason

    def _close_scope(self, *, frame_ordinal: int, reason: str) -> None:
        for car_index in range(MAX_CAR_SLOTS):
            self._close_car(car_index, frame_ordinal=frame_ordinal, reason=reason)
            self._slot_state[car_index] = "unknown"

    def start_session(self, session_uid: int) -> None:
        if self.session_uid == session_uid:
            return
        if self.session_uid is not None:
            self.end_session(self.session_uid, reason="session_replaced")
        self.session_uid = session_uid
        self.packet_format = None
        self.lifecycle_epoch = None
        self.lap_tracker.start_session(session_uid)
        self._slot_state = {slot: "unknown" for slot in range(MAX_CAR_SLOTS)}
        self._next_tenure_ordinal = {slot: 0 for slot in range(MAX_CAR_SLOTS)}

    def end_session(self, session_uid: int, *, reason: str = "session_ended") -> None:
        if self.session_uid != session_uid:
            return
        frame_ordinal = max(
            (tenure.last_frame_ordinal for tenure in self._open_tenures.values()),
            default=0,
        ) + 1
        for car_index in range(MAX_CAR_SLOTS):
            tenure = self._open_tenures.get(car_index)
            attempts = self.lap_tracker.close_car(
                car_index,
                reason=reason,
                disposition=LapDisposition.PARTIAL,
            )
            self._append_attempts(attempts, car_index, tenure)
            self.lap_tracker.drain_attempts()
            if tenure is not None:
                self._close_tenure_row(
                    tenure,
                    frame_ordinal=frame_ordinal,
                    reason=reason,
                )
            self._open_tenures.pop(car_index, None)
            self._slot_state[car_index] = "unknown"
        attempts = self.lap_tracker.end_session(session_uid, reason=reason)
        # `_close_scope` has already closed active cars; this handles any tracker-only residue.
        for attempt in attempts:
            self._append_attempts((attempt,), attempt.car_index)
        self.session_uid = None
        self.packet_format = None
        self.lifecycle_epoch = None

    def finish(self) -> None:
        if self.session_uid is None:
            return
        frame_ordinal = max(
            (tenure.last_frame_ordinal for tenure in self._open_tenures.values()),
            default=0,
        ) + 1
        for car_index in tuple(self._open_tenures):
            tenure = self._open_tenures[car_index]
            attempts = self.lap_tracker.close_car(
                car_index,
                reason="capture_ended_before_lap_completion",
                disposition=LapDisposition.PARTIAL,
            )
            self._append_attempts(attempts, car_index)
            self._close_tenure_row(
                tenure,
                frame_ordinal=frame_ordinal,
                reason="capture_ended",
            )
            self._open_tenures.pop(car_index, None)
        attempts = self.lap_tracker.finish()
        for attempt in attempts:
            self._append_attempts((attempt,), attempt.car_index)

    def _close_tenure_row(
        self, tenure: _OpenTenure, *, frame_ordinal: int, reason: str
    ) -> None:
        if len(self._completed_tenures) >= MAX_RETAINED_CAR_LAP_EVENTS:
            self._overflowed = True
            return
        self._completed_tenures.append(
            CarSlotTenure(
                session_uid=tenure.session_uid,
                packet_format=tenure.packet_format,
                lifecycle_epoch=tenure.lifecycle_epoch,
                car_index=tenure.car_index,
                tenure_ordinal=tenure.tenure_ordinal,
                start_frame_ordinal=tenure.start_frame_ordinal,
                end_frame_ordinal_exclusive=max(
                    tenure.start_frame_ordinal + 1, frame_ordinal
                ),
                participant_frame_identifier=tenure.participant_frame_identifier,
                participant_wire_fingerprint=tenure.participant_wire_fingerprint,
                participant_identity_fingerprint=tenure.participant_identity_fingerprint,
                close_reason=reason,
            )
        )

    def _invalidate_slot(
        self, car_index: int, *, frame_ordinal: int, reason: str
    ) -> None:
        self._close_car(car_index, frame_ordinal=frame_ordinal, reason=reason)
        self._slot_state[car_index] = "unknown"

    def _apply_roster(
        self,
        *,
        frame_ordinal: int,
        packet_format: int,
        decoded: tuple[tuple[DecodedPacket, ParticipantsPacket], ...],
        errors: tuple[str, ...],
    ) -> None:
        if not decoded and not errors:
            return
        if errors or not decoded or len(decoded) > MAX_ROSTER_PACKETS_PER_FRAME:
            reason = (
                "participants_frame_limit_exceeded"
                if len(decoded) > MAX_ROSTER_PACKETS_PER_FRAME
                else "participants_roster_unavailable"
            )
            for car_index in range(MAX_CAR_SLOTS):
                self._invalidate_slot(car_index, frame_ordinal=frame_ordinal, reason=reason)
            return
        if any(int(packet.packet_format) != packet_format for packet, _ in decoded):
            for car_index in range(MAX_CAR_SLOTS):
                self._invalidate_slot(
                    car_index,
                    frame_ordinal=frame_ordinal,
                    reason="participants_format_conflict",
                )
            return

        slot_evidence: dict[int, set[tuple[bool, str | None]]] = {
            slot: set() for slot in range(MAX_CAR_SLOTS)
        }
        for _, roster in decoded:
            if len(roster.cars) > MAX_CAR_SLOTS:
                for car_index in range(MAX_CAR_SLOTS):
                    self._invalidate_slot(
                        car_index,
                        frame_ordinal=frame_ordinal,
                        reason="participants_roster_shape_invalid",
                    )
                return
            for car_index in range(MAX_CAR_SLOTS):
                if car_index >= len(roster.cars):
                    slot_evidence[car_index].add((False, None))
                    continue
                participant = roster.cars[car_index]
                slot_evidence[car_index].add(
                    (
                        car_index < roster.active_car_count,
                        _identity_fingerprint(participant),
                    )
                )

        provenance_packet = min(decoded, key=lambda item: item[0].wire_fingerprint)[0]
        provenance_keys = {
            (packet.header.frame_identifier, packet.header.session_time)
            for packet, _ in decoded
        }
        if len(provenance_keys) != 1:
            for car_index in range(MAX_CAR_SLOTS):
                self._invalidate_slot(
                    car_index,
                    frame_ordinal=frame_ordinal,
                    reason="participants_provenance_conflict",
                )
            return
        wire_fingerprint = _wire_fingerprint(provenance_packet)
        for car_index, evidence in slot_evidence.items():
            if len(evidence) != 1:
                self._invalidate_slot(
                    car_index,
                    frame_ordinal=frame_ordinal,
                    reason="participants_slot_conflict",
                )
                continue
            active, identity_fingerprint = next(iter(evidence))
            if not active:
                if car_index in self._open_tenures:
                    self._close_car(
                        car_index,
                        frame_ordinal=frame_ordinal,
                        reason="participants_slot_inactive",
                    )
                self._slot_state[car_index] = "inactive"
                continue
            if identity_fingerprint is None:
                self._invalidate_slot(
                    car_index,
                    frame_ordinal=frame_ordinal,
                    reason="participants_identity_unavailable",
                )
                continue
            current = self._open_tenures.get(car_index)
            if (
                current is not None
                and current.participant_identity_fingerprint == identity_fingerprint
            ):
                current.last_frame_ordinal = frame_ordinal
                self._slot_state[car_index] = "active"
                continue
            if current is not None:
                self._close_car(
                    car_index,
                    frame_ordinal=frame_ordinal,
                    reason="participants_identity_changed",
                )
            tenure_ordinal = self._next_tenure_ordinal.get(car_index, 0) + 1
            self._next_tenure_ordinal[car_index] = tenure_ordinal
            self._open_tenures[car_index] = _OpenTenure(
                session_uid=self.session_uid or 0,
                packet_format=packet_format,
                lifecycle_epoch=self.lifecycle_epoch or 0,
                car_index=car_index,
                tenure_ordinal=tenure_ordinal,
                start_frame_ordinal=frame_ordinal,
                participant_frame_identifier=provenance_packet.header.frame_identifier,
                participant_wire_fingerprint=wire_fingerprint,
                participant_identity_fingerprint=identity_fingerprint,
                last_frame_ordinal=frame_ordinal,
            )
            self._slot_state[car_index] = "active"

    def observe_frame(
        self,
        *,
        session_uid: int,
        frame_ordinal: int,
        packet_format: int,
        lifecycle_epoch: int,
        player_car_index: int | None,
        session_time_s: float,
        frame_identifier: int,
        packets: tuple[DecodedPacket, ...],
        lap_data: tuple[CarLapData, ...] | None,
        lap_data_conflicted: bool,
        association_scope_assessable: bool,
        context: SessionContext | None,
        timeline_provider: ContextTimelineProvider,
        boundary_reason: str | None = None,
    ) -> None:
        if self.session_uid != session_uid:
            self.start_session(session_uid)
        if (
            self.packet_format is not None
            and (self.packet_format != packet_format or self.lifecycle_epoch != lifecycle_epoch)
        ):
            self._close_scope(
                frame_ordinal=frame_ordinal,
                reason="association_scope_changed",
            )
            self._slot_state = {slot: "unknown" for slot in range(MAX_CAR_SLOTS)}
        self.packet_format = packet_format
        self.lifecycle_epoch = lifecycle_epoch
        if boundary_reason is not None:
            self._close_scope(frame_ordinal=frame_ordinal, reason=boundary_reason)
            self._slot_state = {slot: "unknown" for slot in range(MAX_CAR_SLOTS)}
            return
        if not association_scope_assessable:
            self._close_scope(
                frame_ordinal=frame_ordinal,
                reason="association_scope_unassessable",
            )
            self._slot_state = {slot: "unknown" for slot in range(MAX_CAR_SLOTS)}
            return

        participant_packets = tuple(
            packet for packet in packets if packet.packet_kind is PacketId.PARTICIPANTS
        )
        decoded_rosters: list[tuple[DecodedPacket, ParticipantsPacket]] = []
        roster_errors: list[str] = []
        for packet in participant_packets:
            result = self.participants_decoder.decode(packet)
            if result.error is not None:
                roster_errors.append(result.error)
            elif result.participants is not None:
                decoded_rosters.append((packet, result.participants))
        self._apply_roster(
            frame_ordinal=frame_ordinal,
            packet_format=packet_format,
            decoded=tuple(decoded_rosters),
            errors=tuple(roster_errors),
        )

        if lap_data_conflicted:
            for car_index in tuple(self._open_tenures):
                if car_index != player_car_index:
                    attempts = self.lap_tracker.close_car(
                        car_index,
                        reason="conflicting_lap_data_frame",
                        disposition=LapDisposition.ABANDONED,
                    )
                    self._append_attempts(attempts, car_index)
                    self._count_unassociated(car_index, "conflicting_lap_data_frame")
            self.lap_tracker.drain_attempts()
            return
        if lap_data is None:
            if participant_packets:
                return
            return
        if player_car_index is None:
            for car_index in tuple(self._open_tenures):
                self._invalidate_slot(
                    car_index,
                    frame_ordinal=frame_ordinal,
                    reason="player_slot_scope_conflict",
                )
            return
        if len(lap_data) > MAX_CAR_SLOTS:
            for car_index in tuple(self._open_tenures):
                self._invalidate_slot(
                    car_index,
                    frame_ordinal=frame_ordinal,
                    reason="lap_data_roster_shape_mismatch",
                )
            return

        for car_index, data in enumerate(lap_data):
            if car_index == player_car_index:
                continue
            tenure = self._open_tenures.get(car_index)
            if tenure is None:
                state = self._slot_state.get(car_index, "unknown")
                self._count_unassociated(
                    car_index,
                    "participants_slot_inactive"
                    if state == "inactive"
                    else "participants_tenure_unavailable",
                )
                continue
            tenure.last_frame_ordinal = frame_ordinal
            start_frame = self.lap_tracker.active_start_frame(car_index)
            timeline = timeline_provider(
                frame_identifier if start_frame is None else start_frame,
                frame_identifier,
            )
            if not timeline or timeline[-1][1] != context:
                timeline = (*timeline, (frame_identifier, context))
            if len(timeline) > MAX_CAR_LAP_CONTEXT_SEGMENTS:
                timeline = (
                    *timeline[: MAX_CAR_LAP_CONTEXT_SEGMENTS - 2],
                    (timeline[MAX_CAR_LAP_CONTEXT_SEGMENTS - 2][0], None),
                    (frame_identifier, context),
                )
            observations = tuple(
                SessionContextSegment(event_frame, event_context)
                for event_frame, event_context in timeline
            )
            attempts = self.lap_tracker.observe(
                LapObservation(
                    session_uid=session_uid,
                    frame_identifier=frame_identifier,
                    session_time_s=session_time_s,
                    car_index=car_index,
                    data=data,
                    session_context=context,
                    context_timeline=observations,
                    frame_ordinal=frame_ordinal,
                    association_epoch=lifecycle_epoch,
                    association_scope_assessable=True,
                    association_packet_format=packet_format,
                )
            )
            self._append_attempts(attempts, car_index)
            self.lap_tracker.drain_attempts()

    def drain(self) -> tuple[tuple[CarSlotTenure, ...], tuple[ObservedCarLapAttempt, ...]]:
        tenures = tuple(self._completed_tenures)
        attempts = tuple(self._completed_attempts)
        self._completed_tenures.clear()
        self._completed_attempts.clear()
        return tenures, attempts
