from __future__ import annotations

from dataclasses import dataclass

from .sessions.lap_tracker import (
    LapAttempt,
    LapObservation,
    LapTracker,
    SessionContextSegment,
)
from .sessions.context import SessionContext
from .sessions.manager import SessionTracker
from .telemetry.frames import FrameAssembler
from .udp.decoder import PacketDecoder
from .udp.models import DecodedPacket, PacketFrame, PacketId, RawDatagram, SessionEvent
from .udp.lap_data import LapDataDecoder
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
        self.laps = LapTracker()
        self.lap_data_packets_decoded = 0
        self.lap_data_decode_errors: list[str] = []

    def _process_frames(
        self, frames: tuple[PacketFrame, ...]
    ) -> tuple[tuple[LapAttempt, ...], tuple[str, ...]]:
        attempts: list[LapAttempt] = []
        errors: list[str] = []
        for frame in frames:
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
        return tuple(attempts), tuple(errors)

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
        for event in session_events:
            if event.kind == "session_ended":
                retired_frames = self.frames.retire_session(event.session_uid)
                completed_frames.extend(retired_frames)
                attempts, errors = self._process_frames(retired_frames)
                lap_attempts.extend(attempts)
                lap_data_errors.extend(errors)
                lap_attempts.extend(self.laps.end_session(event.session_uid))
            elif event.kind == "session_started":
                self.laps.start_session(event.session_uid)
            elif event.kind == "session_context_invalidated":
                old_format_frames = self.frames.flush_session(event.session_uid)
                completed_frames.extend(old_format_frames)
                attempts, errors = self._process_frames(old_format_frames)
                lap_attempts.extend(attempts)
                lap_data_errors.extend(errors)
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
            attempts, errors = self._process_frames(ready_frames)
            lap_attempts.extend(attempts)
            lap_data_errors.extend(errors)
        return PipelineResult(
            packet=packet,
            session_events=session_events,
            completed_frames=tuple(completed_frames),
            session_context=updated_context,
            session_context_error=context_result.error,
            lap_attempts=tuple(lap_attempts),
            lap_data_errors=tuple(lap_data_errors),
        )

    def finish(self) -> tuple[PacketFrame, ...]:
        frames = self.frames.flush()
        self._process_frames(frames)
        self.laps.finish()
        return frames
