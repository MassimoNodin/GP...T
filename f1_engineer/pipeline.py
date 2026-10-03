from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .sessions.lap_tracker import (
    LapAttempt,
    LapObservation,
    LapTracker,
    SessionContextSegment,
)
from .sessions.lifecycle import LifecycleEvent
from .sessions.context import SessionContext
from .sessions.manager import ContextHistoryChange, SessionTracker
from .analysis.continuity import float32_ulp, session_time_discontinuity
from .telemetry.canonical import CarSample, make_car_sample
from .telemetry.frames import FrameAssembler
from .udp.car_telemetry import CarTelemetryDecoder
from .udp.car_status import CarStatusDecoder, CarStatusData, CarStatusPacket
from .udp.decoder import PacketDecoder
from .udp.events import EventData, EventDecoder
from .udp.models import DecodedPacket, PacketFrame, PacketId, RawDatagram, SessionEvent
from .udp.lap_data import LapDataDecoder
from .udp.motion import CarMotionData, MotionDecoder
from .udp.participants import ParticipantsDecoder, ParticipantsPacket
from .udp.session_context import SessionContextDecoder


@dataclass(frozen=True, slots=True)
class PipelineResult:
    packet: DecodedPacket
    session_events: tuple[SessionEvent, ...]
    completed_frames: tuple[PacketFrame, ...]
    session_context: SessionContext | None = None
    session_context_error: str | None = None
    lap_attempts: tuple[LapAttempt, ...] = ()
    lap_data_errors: tuple[str, ...] = ()
    car_samples: tuple[CarSample, ...] = ()
    car_telemetry_errors: tuple[str, ...] = ()
    participants: ParticipantsPacket | None = None
    participants_error: str | None = None
    context_history_changes: tuple[ContextHistoryChange, ...] = ()
    lifecycle_events: tuple[LifecycleEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class PipelineFlushResult:
    completed_frames: tuple[PacketFrame, ...]
    lap_attempts: tuple[LapAttempt, ...]
    car_samples: tuple[CarSample, ...]
    lap_data_errors: tuple[str, ...]
    car_telemetry_errors: tuple[str, ...]
    lifecycle_events: tuple[LifecycleEvent, ...] = ()


class TelemetryPipeline:
    def __init__(
        self,
        max_open_frames: int = 256,
        reorder_window_frames: int = 3,
    ) -> None:
        self.decoder = PacketDecoder()
        self.session_context_decoder = SessionContextDecoder()
        self.sessions = SessionTracker(reorder_window_frames=reorder_window_frames)
        self.frames = FrameAssembler(
            max_open_frames=max_open_frames,
            reorder_window_frames=reorder_window_frames,
        )
        self.lap_data_decoder = LapDataDecoder()
        self.car_telemetry_decoder = CarTelemetryDecoder()
        self.motion_decoder = MotionDecoder()
        self.car_status_decoder = CarStatusDecoder()
        self.participants_decoder = ParticipantsDecoder()
        self.event_decoder = EventDecoder()
        self.laps = LapTracker()
        self._frame_ordinal_by_uid: dict[int, int] = {}
        self._event_ordinal_by_uid: dict[int, int] = {}
        self._last_session_time_by_uid: dict[int, float] = {}
        self._lifecycle_events: list[LifecycleEvent] = []
        self.max_buffered_lifecycle_events = 8192
        self.lifecycle_events_dropped = 0
        self.lifecycle_events_truncated_session_uids: set[int] = set()
        self.event_packets_decoded = 0
        self.event_decode_error_count = 0
        self.event_code_counts: Counter[str] = Counter()
        self._max_event_code_counts = 64
        self.lap_data_packets_decoded = 0
        self.lap_data_decode_errors: list[str] = []
        self.car_telemetry_packets_decoded = 0
        self.car_telemetry_decode_errors: list[str] = []
        self.motion_packets_decoded = 0
        self.motion_decode_errors: list[str] = []
        self.player_motion_samples = 0
        self.missing_player_motion_samples = 0
        self.car_status_packets_decoded = 0
        self.car_status_decode_errors: list[str] = []
        self.player_car_status_samples = 0
        self.missing_player_car_status_samples = 0
        self.participants_packets_decoded = 0
        self.participants_decode_errors: list[str] = []
        self.missing_car_telemetry_frame_count = 0
        self.missing_car_telemetry_frame_examples: list[tuple[int, int]] = []

    def _process_frames(
        self, frames: tuple[PacketFrame, ...]
    ) -> tuple[tuple[LapAttempt, ...], tuple[str, ...], tuple[CarSample, ...], tuple[str, ...]]:
        attempts: list[LapAttempt] = []
        errors: list[str] = []
        samples: list[CarSample] = []
        telemetry_errors: list[str] = []
        for frame in frames:
            frame_ordinal = self._frame_ordinal_by_uid.get(frame.session_uid, 0) + 1
            self._frame_ordinal_by_uid[frame.session_uid] = frame_ordinal
            lifecycle_attempts, quarantine_lap_data = self._process_lifecycle(
                frame, frame_ordinal
            )
            attempts.extend(lifecycle_attempts)
            telemetry_by_car: dict[int, object] = {}
            motion_by_car: tuple[CarMotionData, ...] | None = None
            conflicting_motion = False
            car_status_candidates: list[
                tuple[DecodedPacket, CarStatusPacket | None, str | None]
            ] = []
            missing_telemetry_for_frame = False
            for packet in frame.packets:
                if packet.packet_kind is PacketId.CAR_TELEMETRY:
                    decoded_telemetry = self.car_telemetry_decoder.decode(packet)
                    if decoded_telemetry.error is not None:
                        telemetry_errors.append(decoded_telemetry.error)
                        self.car_telemetry_decode_errors.append(decoded_telemetry.error)
                    elif decoded_telemetry.telemetry is not None:
                        self.car_telemetry_packets_decoded += 1
                        for car_index, car in enumerate(decoded_telemetry.telemetry.cars):
                            telemetry_by_car[car_index] = car
                elif packet.packet_kind is PacketId.MOTION:
                    decoded_motion = self.motion_decoder.decode(packet)
                    if decoded_motion.error is not None:
                        self.motion_decode_errors.append(decoded_motion.error)
                    elif decoded_motion.motion is not None:
                        self.motion_packets_decoded += 1
                        if motion_by_car is None:
                            motion_by_car = decoded_motion.motion.cars
                        elif motion_by_car != decoded_motion.motion.cars:
                            conflicting_motion = True
                elif packet.packet_kind is PacketId.CAR_STATUS:
                    decoded_status = self.car_status_decoder.decode(packet)
                    if decoded_status.error is not None:
                        self.car_status_decode_errors.append(decoded_status.error)
                    elif decoded_status.car_status is not None:
                        self.car_status_packets_decoded += 1
                    car_status_candidates.append(
                        (packet, decoded_status.car_status, decoded_status.error)
                    )
            if conflicting_motion:
                motion_by_car = None
                self.motion_decode_errors.append(
                    "conflicting Motion packets in assembled frame "
                    f"{frame.session_uid}:{frame.overall_frame_identifier}"
                )
            if frame.session_uid == 0:
                continue
            if quarantine_lap_data:
                continue
            for packet in frame.packets:
                if packet.packet_kind is not PacketId.LAP_DATA:
                    continue
                result = self.lap_data_decoder.decode(packet)
                if result.error is not None:
                    errors.append(result.error)
                    self.lap_data_decode_errors.append(result.error)
                    continue
                if result.lap_data is None:
                    continue
                self.lap_data_packets_decoded += 1
                car_index = packet.header.player_car_index
                if not 0 <= car_index < len(result.lap_data.cars):
                    error = f"invalid player car index {car_index} in Lap Data packet"
                    errors.append(error)
                    self.lap_data_decode_errors.append(error)
                    continue
                frame_identifier = frame.overall_frame_identifier
                if car_index not in telemetry_by_car:
                    missing_telemetry_for_frame = True
                context, _ = self.sessions.context_at(
                    frame_identifier, session_uid=frame.session_uid
                )
                active_start = self.laps.active_start_frame(car_index)
                context_timeline = self.sessions.context_timeline(
                    frame_identifier if active_start is None else active_start,
                    frame_identifier,
                    session_uid=frame.session_uid,
                )
                attempts.extend(
                    self.laps.observe(
                        LapObservation(
                            session_uid=frame.session_uid,
                            frame_identifier=frame_identifier,
                            session_time_s=packet.header.session_time,
                            car_index=car_index,
                            data=result.lap_data.cars[car_index],
                            session_context=context,
                            context_timeline=tuple(
                                SessionContextSegment(event_frame, event_context)
                                for event_frame, event_context in context_timeline
                            ),
                            frame_ordinal=frame_ordinal,
                            game_frame_identifier=packet.header.frame_identifier,
                        )
                    )
                )
                attempt_id = self.laps.active_attempt_id(car_index)
                if attempt_id is not None:
                    player_car_index = packet.header.player_car_index
                    motion = (
                        motion_by_car[car_index]
                        if motion_by_car is not None
                        and car_index == player_car_index
                        and 0 <= car_index < len(motion_by_car)
                        else None
                    )
                    if car_index == player_car_index:
                        self.player_motion_samples += 1
                        if motion is None:
                            self.missing_player_motion_samples += 1
                    car_status, car_status_reason = _join_car_status(
                        packet,
                        car_index,
                        car_status_candidates,
                    )
                    if car_index == player_car_index:
                        if car_status is None:
                            self.missing_player_car_status_samples += 1
                        else:
                            self.player_car_status_samples += 1
                    samples.append(
                        make_car_sample(
                            session_uid=frame.session_uid,
                            frame_identifier=frame_identifier,
                            session_time_s=packet.header.session_time,
                            car_index=car_index,
                            attempt_id=attempt_id,
                            lap=result.lap_data.cars[car_index],
                            telemetry=telemetry_by_car.get(car_index),
                            motion=motion,
                            car_status=car_status,
                            car_status_unavailable_reason=car_status_reason,
                        )
                    )
            if missing_telemetry_for_frame:
                self.missing_car_telemetry_frame_count += 1
                if len(self.missing_car_telemetry_frame_examples) < 128:
                    self.missing_car_telemetry_frame_examples.append(
                        (frame.session_uid, frame.overall_frame_identifier)
                    )
        return tuple(attempts), tuple(errors), tuple(samples), tuple(telemetry_errors)

    def _process_lifecycle(
        self, frame: PacketFrame, frame_ordinal: int
    ) -> tuple[tuple[LapAttempt, ...], bool]:
        decoded: list[tuple[DecodedPacket, EventData]] = []
        for packet in frame.packets:
            if packet.packet_kind is not PacketId.EVENT:
                continue
            event = self.event_decoder.decode(packet)
            self.event_packets_decoded += 1
            if event.error is not None:
                self.event_decode_error_count += 1
            if event.code is not None:
                if (
                    event.code in self.event_code_counts
                    or len(self.event_code_counts) < self._max_event_code_counts - 1
                ):
                    self.event_code_counts[event.code] += 1
                else:
                    self.event_code_counts["__other__"] += 1
            decoded.append((packet, event))

        uid = frame.session_uid
        previous_time = self._last_session_time_by_uid.get(uid)
        selected_lap_packets: list[DecodedPacket] = []
        for packet in frame.packets:
            if packet.packet_kind is not PacketId.LAP_DATA:
                continue
            decoded_lap = self.lap_data_decoder.decode(packet)
            if (
                decoded_lap.error is None
                and decoded_lap.lap_data is not None
                and 0 <= packet.header.player_car_index < len(decoded_lap.lap_data.cars)
            ):
                selected_lap_packets.append(packet)
        selected_lap_packet = selected_lap_packets[-1] if selected_lap_packets else None
        same_frame_clock_regression = False
        same_frame_prior_time: float | None = None
        same_frame_regression_packet: DecodedPacket | None = None
        selected_times = [packet.header.session_time for packet in selected_lap_packets]
        for left_packet, right_packet in zip(
            selected_lap_packets, selected_lap_packets[1:]
        ):
            left = left_packet.header.session_time
            right = right_packet.header.session_time
            if session_time_discontinuity(left, right) == "session_time_regression":
                same_frame_clock_regression = True
                same_frame_prior_time = left
                same_frame_regression_packet = right_packet
                break
        if (
            len(selected_times) > 1
            and previous_time is not None
            and session_time_discontinuity(previous_time, selected_times[0])
            == "session_time_regression"
        ):
            same_frame_clock_regression = True
            same_frame_prior_time = previous_time
            same_frame_regression_packet = selected_lap_packets[0]
        clock_packet = selected_lap_packet or next(
            (
                packet
                for packet in frame.packets
                if packet.packet_kind is not PacketId.SESSION
            ),
            frame.packets[0] if frame.packets else None,
        )
        flashbacks = [(packet, event) for packet, event in decoded if event.code == "FLBK"]
        untrusted = [
            (packet, event)
            for packet, event in decoded
            if event.error is not None
        ]
        lifecycle_attempts: list[LapAttempt] = []
        explicit_boundary = bool(flashbacks)
        unknown_boundary = bool(untrusted)
        regression_boundary = (
            not explicit_boundary
            and not unknown_boundary
            and uid != 0
            and selected_lap_packet is not None
            and (
                same_frame_clock_regression
                or (
                    len(selected_times) == 1
                    and previous_time is not None
                    and session_time_discontinuity(previous_time, selected_times[0])
                    == "session_time_regression"
                )
            )
        )

        boundary_cause: str | None = None
        boundary_reason: str | None = None
        quarantine = False
        if explicit_boundary:
            boundary_cause = "flashback"
            boundary_reason = "flashback"
            quarantine = True
        elif unknown_boundary:
            boundary_cause = "event_evidence_unknown"
            boundary_reason = "event_lifecycle_evidence_unknown"
            quarantine = True
        elif regression_boundary:
            boundary_cause = "session_time_regression"
            boundary_reason = "session_time_regression"
            quarantine = same_frame_clock_regression

        if boundary_cause is not None and uid != 0:
            lifecycle_attempts.extend(
                self.laps.close_lifecycle_boundary(uid, reason=boundary_reason or boundary_cause)
            )

        flashback_targets = {
            (event.target_frame_identifier, event.target_session_time_s)
            for _, event in flashbacks
            if event.error is None
        }
        candidate_target = (
            next(iter(flashback_targets)) if len(flashback_targets) == 1 else None
        )
        flashback_valid = (
            bool(flashbacks)
            and not unknown_boundary
            and len(flashback_targets) == 1
            and all(
                event.target_frame_identifier is not None
                and event.target_session_time_s is not None
                and event.error is None
                for _, event in flashbacks
            )
            and previous_time is not None
            and candidate_target is not None
            and candidate_target[1] is not None
            and candidate_target[1]
            <= previous_time
            + max(float32_ulp(candidate_target[1]), float32_ulp(previous_time))
        )
        target = (
            candidate_target
            if flashback_valid and candidate_target is not None
            else (None, None)
        )

        if uid != 0:
            if explicit_boundary or unknown_boundary or same_frame_clock_regression:
                # A lifecycle boundary invalidates the old timer baseline. The
                # next valid player Lap Data observation establishes the branch.
                self._last_session_time_by_uid.pop(uid, None)
            elif selected_lap_packet is not None:
                self._last_session_time_by_uid[uid] = (
                    selected_lap_packet.header.session_time
                )

        if uid != 0:
            for packet, event in decoded:
                if event.error is not None:
                    self._record_lifecycle_event(
                        packet,
                        frame_ordinal,
                        event_code=event.code,
                        event_kind="malformed_or_unsupported",
                        cause="event_evidence_unknown",
                        evidence_status=event.error,
                        details=event.details,
                        details_length_bytes=event.details_length_bytes,
                        details_truncated=event.details_truncated,
                        prior_session_time_s=previous_time,
                    )
                elif event.code in ("SSTA", "SEND"):
                    self._record_lifecycle_event(
                        packet,
                        frame_ordinal,
                        event_code=event.code,
                        event_kind="session_start_annotation" if event.code == "SSTA" else "session_end_annotation",
                        cause="event_annotation",
                        evidence_status="verified",
                        details=event.details,
                        details_length_bytes=event.details_length_bytes,
                        details_truncated=event.details_truncated,
                    )
                elif event.code == "FLBK":
                    self._record_lifecycle_event(
                        packet,
                        frame_ordinal,
                        event_code="FLBK",
                        event_kind="flashback",
                        cause="flashback",
                        evidence_status="verified" if flashback_valid else "ambiguous",
                        details=event.details,
                        details_length_bytes=event.details_length_bytes,
                        details_truncated=event.details_truncated,
                        target_frame_identifier=target[0],
                        target_session_time_s=target[1],
                        prior_session_time_s=previous_time,
                        duplicate_count=len(flashbacks),
                    )

        if regression_boundary and uid != 0:
            boundary_clock_packet = same_frame_regression_packet or clock_packet
            assert boundary_clock_packet is not None
            self._record_synthetic_boundary(
                frame,
                frame_ordinal,
                event_kind="session_time_regression",
                cause="session_time_regression",
                evidence_status=(
                    "same_frame_clock_regression"
                    if same_frame_clock_regression
                    else "target_unavailable"
                ),
                prior_session_time_s=(
                    same_frame_prior_time
                    if same_frame_clock_regression
                    else previous_time
                ),
                current_frame_identifier=boundary_clock_packet.header.frame_identifier,
                session_time_s=boundary_clock_packet.header.session_time,
                packet_format=int(boundary_clock_packet.packet_format),
            )
        return tuple(lifecycle_attempts), quarantine

    def _record_lifecycle_event(
        self,
        packet: DecodedPacket,
        frame_ordinal: int,
        *,
        event_code: str | None,
        event_kind: str,
        cause: str,
        evidence_status: str,
        details: bytes,
        details_length_bytes: int = 0,
        details_truncated: bool = False,
        target_frame_identifier: int | None = None,
        target_session_time_s: float | None = None,
        prior_session_time_s: float | None = None,
        duplicate_count: int = 1,
    ) -> None:
        self._append_lifecycle_event(
            LifecycleEvent(
                session_uid=packet.header.session_uid,
                event_ordinal=self._next_event_ordinal(packet.header.session_uid),
                frame_ordinal=frame_ordinal,
                current_frame_identifier=packet.header.frame_identifier,
                current_overall_frame_identifier=packet.header.overall_frame_identifier,
                packet_format=int(packet.packet_format),
                packet_version=packet.header.packet_version,
                event_code=event_code,
                event_kind=event_kind,
                session_time_s=packet.header.session_time,
                target_game_frame_identifier=target_frame_identifier,
                target_session_time_s=target_session_time_s,
                prior_session_time_s=prior_session_time_s,
                cause=cause,
                evidence_status=evidence_status,
                details_hex=details[:12].hex(),
                details_length_bytes=details_length_bytes,
                details_truncated=details_truncated,
                duplicate_count=duplicate_count,
            )
        )

    def _record_synthetic_boundary(
        self,
        frame: PacketFrame,
        frame_ordinal: int,
        *,
        event_kind: str,
        cause: str,
        evidence_status: str,
        prior_session_time_s: float | None,
        current_frame_identifier: int,
        session_time_s: float,
        packet_format: int,
    ) -> None:
        if not frame.packets:
            return
        packet = frame.packets[0]
        self._append_lifecycle_event(
            LifecycleEvent(
                session_uid=frame.session_uid,
                event_ordinal=self._next_event_ordinal(frame.session_uid),
                frame_ordinal=frame_ordinal,
                current_frame_identifier=current_frame_identifier,
                current_overall_frame_identifier=frame.overall_frame_identifier,
                packet_format=packet_format,
                packet_version=None,
                event_code=None,
                event_kind=event_kind,
                session_time_s=session_time_s,
                target_game_frame_identifier=None,
                target_session_time_s=None,
                prior_session_time_s=prior_session_time_s,
                cause=cause,
                evidence_status=evidence_status,
                details_hex="",
                details_length_bytes=0,
                details_truncated=False,
            )
        )

    def _next_event_ordinal(self, session_uid: int) -> int:
        ordinal = self._event_ordinal_by_uid.get(session_uid, 0) + 1
        self._event_ordinal_by_uid[session_uid] = ordinal
        return ordinal

    def _append_lifecycle_event(self, event: LifecycleEvent) -> None:
        if len(self._lifecycle_events) < self.max_buffered_lifecycle_events:
            self._lifecycle_events.append(event)
        else:
            self.lifecycle_events_dropped += 1
            self.lifecycle_events_truncated_session_uids.add(event.session_uid)

    def drain_lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        events = tuple(self._lifecycle_events)
        self._lifecycle_events.clear()
        return events

    def process(self, raw: RawDatagram) -> PipelineResult:
        packet = self.decoder.decode(raw)
        session_events = self.sessions.observe(packet)
        context_result = self.session_context_decoder.decode(packet)
        updated_context: SessionContext | None = None
        if context_result.context is not None and self.sessions.update_context(
            context_result.context, packet.header.overall_frame_identifier
        ):
            updated_context = context_result.context
        completed_frames: list[PacketFrame] = []
        lap_attempts: list[LapAttempt] = []
        lap_data_errors: list[str] = []
        car_samples: list[CarSample] = []
        car_telemetry_errors: list[str] = []
        participant_result = self.participants_decoder.decode(packet)
        participants = participant_result.participants
        if participant_result.error is not None:
            self.participants_decode_errors.append(participant_result.error)
        elif participants is not None:
            self.participants_packets_decoded += 1
        for event in session_events:
            if event.kind == "session_ended":
                retired_frames = self.frames.retire_session(event.session_uid)
                completed_frames.extend(retired_frames)
                attempts, errors, samples, telemetry_errors = self._process_frames(retired_frames)
                lap_attempts.extend(attempts)
                lap_data_errors.extend(errors)
                car_samples.extend(samples)
                car_telemetry_errors.extend(telemetry_errors)
                lap_attempts.extend(self.laps.end_session(event.session_uid))
            elif event.kind == "session_started":
                self.laps.start_session(event.session_uid)
            elif event.kind == "session_context_invalidated":
                old_format_frames = self.frames.flush_session(event.session_uid)
                completed_frames.extend(old_format_frames)
                attempts, errors, samples, telemetry_errors = self._process_frames(old_format_frames)
                lap_attempts.extend(attempts)
                lap_data_errors.extend(errors)
                car_samples.extend(samples)
                car_telemetry_errors.extend(telemetry_errors)
                lap_attempts.extend(
                    self.laps.close_segment(
                        event.session_uid, reason="packet_format_changed"
                    )
                )
        obsolete_format = (
            packet.header.session_uid == self.sessions.current_session_uid
            and packet.packet_format is not self.sessions.current_packet_format
        )
        if self.sessions.is_retired(packet.header.session_uid) or obsolete_format:
            self.frames.late_packets_ignored += 1
        else:
            ready_frames = self.frames.add(packet)
            completed_frames.extend(ready_frames)
            attempts, errors, samples, telemetry_errors = self._process_frames(ready_frames)
            lap_attempts.extend(attempts)
            lap_data_errors.extend(errors)
            car_samples.extend(samples)
            car_telemetry_errors.extend(telemetry_errors)
        return PipelineResult(
            packet=packet,
            session_events=session_events,
            completed_frames=tuple(completed_frames),
            session_context=updated_context,
            session_context_error=context_result.error,
            lap_attempts=tuple(lap_attempts),
            lap_data_errors=tuple(lap_data_errors),
            car_samples=tuple(car_samples),
            car_telemetry_errors=tuple(car_telemetry_errors),
            participants=participants,
            participants_error=participant_result.error,
            context_history_changes=self.sessions.drain_context_history_changes(),
            lifecycle_events=self.drain_lifecycle_events(),
        )

    def finish(self) -> tuple[PacketFrame, ...]:
        return self.finish_with_outputs().completed_frames

    def finish_with_outputs(self) -> PipelineFlushResult:
        frames = self.frames.flush()
        attempts, errors, samples, telemetry_errors = self._process_frames(frames)
        start_attempt_count = len(self.laps.attempts)
        self.laps.finish()
        attempts = (*attempts, *self.laps.attempts[start_attempt_count:])
        return PipelineFlushResult(
            completed_frames=frames,
            lap_attempts=tuple(attempts),
            car_samples=samples,
            lap_data_errors=errors,
            car_telemetry_errors=telemetry_errors,
            lifecycle_events=self.drain_lifecycle_events(),
        )


def _join_car_status(
    lap_packet: DecodedPacket,
    player_car_index: int,
    candidates: list[tuple[DecodedPacket, CarStatusPacket | None, str | None]],
) -> tuple[CarStatusData | None, str | None]:
    if not candidates:
        return None, "status_packet_missing"

    if any(
        status_packet.packet_format is not lap_packet.packet_format
        for status_packet, _, _ in candidates
    ):
        return None, "wire_format_mismatch"

    if any(error is not None for _, _, error in candidates):
        return None, "status_packet_malformed_or_unsupported"

    matching = [
        (packet, decoded)
        for packet, decoded, _ in candidates
        if decoded is not None
        and packet.header.player_car_index == lap_packet.header.player_car_index
    ]
    if not matching:
        return None, "player_index_mismatch"
    if len(matching) > 1:
        first_packet, first_data = matching[0]
        if any(
            packet.header.player_car_index != first_packet.header.player_car_index
            or data != first_data
            for packet, data in matching[1:]
        ):
            return None, "conflicting_status_packets"
    _, decoded = matching[0]
    if decoded is None or not 0 <= player_car_index < len(decoded.cars):
        return None, "player_index_mismatch"
    return decoded.cars[player_car_index], None
