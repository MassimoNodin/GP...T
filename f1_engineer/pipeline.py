from __future__ import annotations

from dataclasses import dataclass

from .sessions.lap_tracker import (
    LapAttempt,
    LapObservation,
    LapTracker,
    SessionContextSegment,
)
from .sessions.context import SessionContext
from .sessions.manager import ContextHistoryChange, SessionTracker
from .telemetry.canonical import CarSample, make_car_sample
from .telemetry.frames import FrameAssembler
from .udp.car_telemetry import CarTelemetryDecoder
from .udp.car_status import CarStatusDecoder, CarStatusData, CarStatusPacket
from .udp.decoder import PacketDecoder
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


@dataclass(frozen=True, slots=True)
class PipelineFlushResult:
    completed_frames: tuple[PacketFrame, ...]
    lap_attempts: tuple[LapAttempt, ...]
    car_samples: tuple[CarSample, ...]
    lap_data_errors: tuple[str, ...]
    car_telemetry_errors: tuple[str, ...]


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
        self.laps = LapTracker()
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
