from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass

from .sessions.lap_tracker import (
    LapAttempt,
    LapObservation,
    LapTracker,
    SessionContextSegment,
)
from .sessions.lifecycle import LifecycleEvent
from .sessions.car_lap_inventory import (
    CarLapInventoryTracker,
    CarSlotTenure,
    ObservedCarLapAttempt,
)
from .sessions.session_history import SessionHistoryObservation
from .sessions.participant_context import PlayerParticipantObservation
from .sessions.setup_context import PlayerCarSetupObservation
from .sessions.context import SessionContext
from .sessions.manager import ContextHistoryChange, SessionTracker
from .analysis.continuity import float32_ulp, session_time_discontinuity
from .telemetry.canonical import (
    CarObservation,
    CarSample,
    make_car_observation,
    make_car_sample,
)
from .telemetry.frames import FrameAssembler
from .udp.car_telemetry import CarTelemetryDecoder
from .udp.car_setups import CarSetupData, CarSetupsDecoder, CarSetupsPacket
from .udp.car_status import CarStatusDecoder, CarStatusData, CarStatusPacket
from .udp.car_damage import CarDamageData, CarDamageDecoder, CarDamagePacket
from .udp.decoder import PacketDecoder
from .udp.events import EventData, EventDecoder
from .udp.models import (
    DecodedPacket,
    PacketFormat,
    PacketFrame,
    PacketId,
    RawDatagram,
    SessionEvent,
)
from .udp.lap_data import LapDataDecoder
from .udp.motion import CarMotionData, MotionDecoder
from .udp.participants import ParticipantData, ParticipantsDecoder, ParticipantsPacket
from .udp.session_history import (
    SESSION_HISTORY_BODY_SIZE,
    SessionHistoryDecoder,
)


MAX_PLAYER_PARTICIPANT_TRUNCATION_FENCE_SESSIONS = 256
MAX_PLAYER_CAR_SETUP_TRUNCATION_FENCE_SESSIONS = 256
from .udp.session_context import SessionContextDecoder


@dataclass(frozen=True, slots=True)
class PipelineResult:
    packet: DecodedPacket
    session_events: tuple[SessionEvent, ...]
    completed_frames: tuple[PacketFrame, ...]
    session_context: SessionContext | None = None
    session_context_error: str | None = None
    session_progress: dict[str, int] | None = None
    lap_attempts: tuple[LapAttempt, ...] = ()
    lap_data_errors: tuple[str, ...] = ()
    car_samples: tuple[CarSample, ...] = ()
    car_observations: tuple[CarObservation, ...] = ()
    car_telemetry_errors: tuple[str, ...] = ()
    participants: ParticipantsPacket | None = None
    participants_error: str | None = None
    context_history_changes: tuple[ContextHistoryChange, ...] = ()
    lifecycle_events: tuple[LifecycleEvent, ...] = ()
    session_history: tuple[SessionHistoryObservation, ...] = ()
    session_history_decode_errors: tuple[str, ...] = ()
    player_participant_observations: tuple[PlayerParticipantObservation, ...] = ()
    player_car_setup_observations: tuple[PlayerCarSetupObservation, ...] = ()
    car_setup_decode_errors: tuple[str, ...] = ()
    car_slot_tenures: tuple[CarSlotTenure, ...] = ()
    observed_car_lap_attempts: tuple[ObservedCarLapAttempt, ...] = ()


@dataclass(frozen=True, slots=True)
class PipelineFlushResult:
    completed_frames: tuple[PacketFrame, ...]
    lap_attempts: tuple[LapAttempt, ...]
    car_samples: tuple[CarSample, ...]
    car_observations: tuple[CarObservation, ...]
    lap_data_errors: tuple[str, ...]
    car_telemetry_errors: tuple[str, ...]
    lifecycle_events: tuple[LifecycleEvent, ...] = ()
    session_history: tuple[SessionHistoryObservation, ...] = ()
    session_history_decode_errors: tuple[str, ...] = ()
    player_participant_observations: tuple[PlayerParticipantObservation, ...] = ()
    player_car_setup_observations: tuple[PlayerCarSetupObservation, ...] = ()
    car_setup_decode_errors: tuple[str, ...] = ()
    car_slot_tenures: tuple[CarSlotTenure, ...] = ()
    observed_car_lap_attempts: tuple[ObservedCarLapAttempt, ...] = ()


class TelemetryPipeline:
    def frame_ordinal(self, session_uid: int) -> int:
        return self._frame_ordinal_by_uid.get(session_uid, 0)

    def release_consumed_history(self) -> None:
        """Opt-in streaming retention; archived import keeps its historical defaults."""
        self.laps.drain_attempts()
        self.sessions.bound_context_history(512)
        self.laps.bound_context_history(64)
        self.car_lap_inventory.lap_tracker.bound_context_history(64)
        for name in ("lap_data_decode_errors", "car_telemetry_decode_errors", "motion_decode_errors",
                     "car_status_decode_errors", "car_damage_decode_errors", "participants_decode_errors",
                     "session_history_decode_errors", "car_setups_decode_errors"):
            values = getattr(self, name)
            del values[:-64]
        for name in ("_frame_ordinal_by_uid", "_event_ordinal_by_uid", "_last_session_time_by_uid",
                     "_association_epoch_by_uid", "_association_scope_assessable_by_uid",
                     "_last_association_player_by_uid", "_last_association_format_by_uid"):
            values = getattr(self, name)
            while len(values) > 128:
                values.pop(next(iter(values)))

    def __init__(
        self,
        max_open_frames: int = 256,
        reorder_window_frames: int = 3,
        max_pending_packets: int = 8192,
    ) -> None:
        self.decoder = PacketDecoder()
        self.session_context_decoder = SessionContextDecoder()
        self.sessions = SessionTracker(reorder_window_frames=reorder_window_frames)
        self.frames = FrameAssembler(
            max_open_frames=max_open_frames,
            max_pending_packets=max_pending_packets,
            reorder_window_frames=reorder_window_frames,
        )
        self.lap_data_decoder = LapDataDecoder()
        self.car_telemetry_decoder = CarTelemetryDecoder()
        self.motion_decoder = MotionDecoder()
        self.car_status_decoder = CarStatusDecoder()
        self.car_damage_decoder = CarDamageDecoder()
        self.participants_decoder = ParticipantsDecoder()
        self.car_setups_decoder = CarSetupsDecoder()
        self.event_decoder = EventDecoder()
        self.session_history_decoder = SessionHistoryDecoder()
        self.laps = LapTracker()
        self.car_lap_inventory = CarLapInventoryTracker()
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
        self._association_epoch_by_uid: dict[int, int] = {}
        self._association_scope_assessable_by_uid: dict[int, bool] = {}
        self._last_association_player_by_uid: dict[int, int | None] = {}
        self._last_association_format_by_uid: dict[int, int | None] = {}
        self._session_history: list[SessionHistoryObservation] = []
        self.max_buffered_session_history = 4096
        self.session_history_packets_admitted = 0
        self.session_history_packets_decoded = 0
        self.session_history_non_player_packets = 0
        self.session_history_player_index_mismatches = 0
        self.session_history_decode_errors: list[str] = []
        self.session_history_packets_dropped = 0
        self._player_participant_observations: list[
            PlayerParticipantObservation
        ] = []
        self.max_buffered_player_participant_observations = 4096
        self.player_participant_observations_dropped = 0
        self.player_participant_observation_truncated_session_uids: set[int] = set()
        self.player_participant_observation_truncation_marker_overflowed = False
        self._player_car_setup_observations: list[PlayerCarSetupObservation] = []
        self.max_buffered_player_car_setup_observations = 4096
        self.player_car_setup_observations_dropped = 0
        self.player_car_setup_observation_truncated_session_uids: set[int] = set()
        self.player_car_setup_observation_truncation_marker_overflowed = False
        self.car_setups_packets_decoded = 0
        self.car_setups_decode_errors: list[str] = []
        self.session_history_truncated_session_uids: set[int] = set()
        self.lap_data_packets_decoded = 0
        self.lap_data_decode_errors: list[str] = []
        self.car_telemetry_packets_decoded = 0
        self.car_telemetry_decode_errors: list[str] = []
        self.car_observation_conflict_frames = 0
        self.car_observation_conflict_examples: list[tuple[int, int]] = []
        self.motion_packets_decoded = 0
        self.motion_decode_errors: list[str] = []
        self.player_motion_samples = 0
        self.missing_player_motion_samples = 0
        self.car_status_packets_decoded = 0
        self.car_status_decode_errors: list[str] = []
        self.player_car_status_samples = 0
        self.missing_player_car_status_samples = 0
        self.car_damage_packets_admitted = 0
        self.car_damage_packets_decoded = 0
        self.car_damage_decode_errors: list[str] = []
        self.player_car_damage_samples = 0
        self.missing_player_car_damage_samples = 0
        self.participants_packets_decoded = 0
        self.participants_decode_errors: list[str] = []
        self.missing_car_telemetry_frame_count = 0
        self.missing_car_telemetry_frame_examples: list[tuple[int, int]] = []

    def _process_frames(
        self, frames: tuple[PacketFrame, ...]
    ) -> tuple[
        tuple[LapAttempt, ...],
        tuple[str, ...],
        tuple[CarSample, ...],
        tuple[CarObservation, ...],
        tuple[str, ...],
    ]:
        attempts: list[LapAttempt] = []
        errors: list[str] = []
        samples: list[CarSample] = []
        observations: list[CarObservation] = []
        telemetry_errors: list[str] = []
        for frame in frames:
            frame_ordinal = self._frame_ordinal_by_uid.get(frame.session_uid, 0) + 1
            self._frame_ordinal_by_uid[frame.session_uid] = frame_ordinal
            (
                lifecycle_attempts,
                quarantine_lap_data,
                association_epoch,
                association_scope_assessable,
                lifecycle_boundary,
            ) = self._process_lifecycle(
                frame, frame_ordinal
            )
            attempts.extend(lifecycle_attempts)
            self._process_player_participants(
                frame,
                frame_ordinal,
                association_epoch=association_epoch,
                association_scope_assessable=association_scope_assessable,
                lifecycle_boundary=lifecycle_boundary,
            )
            self._process_player_car_setups(
                frame,
                frame_ordinal,
                association_epoch=association_epoch,
                association_scope_assessable=association_scope_assessable,
                lifecycle_boundary=lifecycle_boundary,
            )
            if not lifecycle_boundary:
                self._process_session_history(
                    frame,
                    frame_ordinal,
                    association_epoch=association_epoch,
                    association_scope_assessable=association_scope_assessable,
                )
            telemetry_by_car: dict[int, object] = {}
            telemetry_candidates: list[tuple[DecodedPacket, object]] = []
            raw_telemetry_candidates: list[DecodedPacket] = []
            motion_by_car: tuple[CarMotionData, ...] | None = None
            motion_candidates: list[tuple[DecodedPacket, tuple[CarMotionData, ...]]] = []
            raw_motion_candidates: list[DecodedPacket] = []
            conflicting_motion = False
            car_status_candidates: list[
                tuple[DecodedPacket, CarStatusPacket | None, str | None]
            ] = []
            car_damage_candidates: list[
                tuple[DecodedPacket, CarDamagePacket | None, str | None]
            ] = []
            missing_telemetry_for_frame = False
            for packet in frame.packets:
                if packet.packet_kind is PacketId.CAR_TELEMETRY:
                    raw_telemetry_candidates.append(packet)
                    decoded_telemetry = self.car_telemetry_decoder.decode(packet)
                    if decoded_telemetry.error is not None:
                        telemetry_errors.append(decoded_telemetry.error)
                        self.car_telemetry_decode_errors.append(decoded_telemetry.error)
                    elif decoded_telemetry.telemetry is not None:
                        self.car_telemetry_packets_decoded += 1
                        telemetry_candidates.append((packet, decoded_telemetry.telemetry))
                        for car_index, car in enumerate(decoded_telemetry.telemetry.cars):
                            telemetry_by_car[car_index] = car
                elif packet.packet_kind is PacketId.MOTION:
                    raw_motion_candidates.append(packet)
                    decoded_motion = self.motion_decoder.decode(packet)
                    if decoded_motion.error is not None:
                        self.motion_decode_errors.append(decoded_motion.error)
                    elif decoded_motion.motion is not None:
                        self.motion_packets_decoded += 1
                        motion_candidates.append((packet, decoded_motion.motion.cars))
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
                elif packet.packet_kind is PacketId.CAR_DAMAGE:
                    self.car_damage_packets_admitted += 1
                    decoded_damage = self.car_damage_decoder.decode(packet)
                    if decoded_damage.error is not None:
                        self.car_damage_decode_errors.append(decoded_damage.error)
                    elif decoded_damage.car_damage is not None:
                        self.car_damage_packets_decoded += 1
                    car_damage_candidates.append(
                        (packet, decoded_damage.car_damage, decoded_damage.error)
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
                player_slots = {packet.header.player_car_index for packet in frame.packets}
                packet_formats = {int(packet.packet_format) for packet in frame.packets}
                self.car_lap_inventory.observe_frame(
                    session_uid=frame.session_uid,
                    frame_ordinal=frame_ordinal,
                    packet_format=next(iter(packet_formats), 0),
                    lifecycle_epoch=association_epoch,
                    player_car_index=(next(iter(player_slots)) if len(player_slots) == 1 else None),
                    session_time_s=frame.packets[0].header.session_time,
                    frame_identifier=frame.overall_frame_identifier,
                    packets=frame.packets,
                    lap_data=None,
                    lap_data_conflicted=False,
                    association_scope_assessable=association_scope_assessable,
                    context=None,
                    timeline_provider=lambda _start, _end: (),
                    boundary_reason="post_lifecycle_lap_quarantine",
                )
                continue
            observation_lap_data: list[tuple[DecodedPacket, object]] = []
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
                observation_lap_data.append((packet, result.lap_data))
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
                            association_epoch=association_epoch,
                            association_scope_assessable=association_scope_assessable,
                            association_packet_format=int(packet.packet_format),
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
                    car_damage, car_damage_reason = _join_car_damage(
                        packet,
                        car_index,
                        car_damage_candidates,
                    )
                    if car_index == player_car_index:
                        if car_damage is None:
                            self.missing_player_car_damage_samples += 1
                        else:
                            self.player_car_damage_samples += 1
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
                            car_damage=car_damage,
                            car_damage_unavailable_reason=car_damage_reason,
                        )
                    )
            frame_observation_conflicted = False
            if observation_lap_data:
                observation_signatures = {
                    (
                        int(packet.packet_format),
                        packet.header.player_car_index,
                        packet.header.session_time,
                        lap_data,
                    )
                    for packet, lap_data in observation_lap_data
                }
                if len(observation_signatures) != 1:
                    frame_observation_conflicted = True
                else:
                    lap_packet, lap_data_packet = observation_lap_data[-1]
                    player_index = lap_packet.header.player_car_index
                    matching_telemetry = [
                        decoded
                        for candidate, decoded in telemetry_candidates
                        if candidate.packet_format is lap_packet.packet_format
                        and candidate.header.player_car_index == player_index
                    ]
                    telemetry_conflict = (
                        len(matching_telemetry) > 1
                        and any(
                            candidate.cars != matching_telemetry[0].cars
                            for candidate in matching_telemetry[1:]
                        )
                    )
                    if telemetry_conflict:
                        frame_observation_conflicted = True
                    observation_telemetry = (
                        matching_telemetry[-1]
                        if matching_telemetry and not telemetry_conflict
                        else None
                    )
                    telemetry_reason = None
                    if observation_telemetry is None:
                        if telemetry_conflict:
                            telemetry_reason = "conflicting_car_telemetry_packets"
                        elif any(
                            candidate.packet_format is lap_packet.packet_format
                            and candidate.header.player_car_index == player_index
                            for candidate in raw_telemetry_candidates
                        ):
                            telemetry_reason = "car_telemetry_malformed_or_unsupported"
                        elif raw_telemetry_candidates:
                            telemetry_reason = "car_telemetry_association_mismatch"
                        else:
                            telemetry_reason = "car_telemetry_packet_missing"
                    matching_motion = [
                        decoded
                        for candidate, decoded in motion_candidates
                        if candidate.packet_format is lap_packet.packet_format
                        and candidate.header.player_car_index == player_index
                    ]
                    motion_conflict = (
                        conflicting_motion
                        or (
                            len(matching_motion) > 1
                            and any(
                                candidate != matching_motion[0]
                                for candidate in matching_motion[1:]
                            )
                        )
                    )
                    if motion_conflict:
                        frame_observation_conflicted = True
                    observation_motion = (
                        matching_motion[-1]
                        if matching_motion and not motion_conflict
                        else None
                    )
                    motion_reason = None
                    if observation_motion is None:
                        if motion_conflict:
                            motion_reason = "conflicting_motion_packets"
                        elif any(
                            candidate.packet_format is lap_packet.packet_format
                            and candidate.header.player_car_index == player_index
                            for candidate in raw_motion_candidates
                        ):
                            motion_reason = "motion_malformed_or_unsupported"
                        elif raw_motion_candidates:
                            motion_reason = "motion_association_mismatch"
                        else:
                            motion_reason = "motion_packet_missing"
                    context, _ = self.sessions.context_at(
                        frame.overall_frame_identifier, session_uid=frame.session_uid
                    )
                    context_json = (
                        json.dumps(context.to_dict(), separators=(",", ":"), sort_keys=True)
                        if context is not None
                        else None
                    )
                    for car_index, lap_data in enumerate(lap_data_packet.cars):
                        telemetry = (
                            observation_telemetry.cars[car_index]
                            if observation_telemetry is not None
                            and car_index < len(observation_telemetry.cars)
                            else None
                        )
                        motion = (
                            observation_motion[car_index]
                            if observation_motion is not None
                            and car_index < len(observation_motion)
                            else None
                        )
                        observations.append(
                            make_car_observation(
                                session_uid=frame.session_uid,
                                frame_identifier=frame.overall_frame_identifier,
                                session_time_s=lap_packet.header.session_time,
                                car_index=car_index,
                                frame_ordinal=frame_ordinal,
                                packet_format=int(lap_packet.packet_format),
                                lifecycle_epoch=association_epoch,
                                header_player_car_index=player_index,
                                context_json=context_json,
                                lap=lap_data,
                                telemetry=telemetry,
                                motion=motion,
                                car_telemetry_unavailable_reason=telemetry_reason,
                                motion_unavailable_reason=motion_reason,
                            )
                        )
            lap_data_signatures = {
                (
                    int(candidate.packet_format),
                    candidate.header.player_car_index,
                    candidate.header.session_time,
                    candidate_lap_data,
                )
                for candidate, candidate_lap_data in observation_lap_data
            }
            lap_data_conflicted = len(lap_data_signatures) > 1
            if frame_observation_conflicted:
                self.car_observation_conflict_frames += 1
                if len(self.car_observation_conflict_examples) < 128:
                    self.car_observation_conflict_examples.append(
                        (frame.session_uid, frame.overall_frame_identifier)
                    )
            packet_formats = {int(packet.packet_format) for packet in frame.packets}
            player_slots = {packet.header.player_car_index for packet in frame.packets}
            selected_lap = observation_lap_data[-1] if observation_lap_data and not lap_data_conflicted else None
            inventory_packet_format = (
                int(selected_lap[0].packet_format)
                if selected_lap is not None
                else next(iter(packet_formats), 0)
            )
            inventory_player_index = (
                selected_lap[0].header.player_car_index
                if selected_lap is not None
                else (next(iter(player_slots)) if len(player_slots) == 1 else None)
            )
            inventory_session_time = (
                selected_lap[0].header.session_time
                if selected_lap is not None
                else frame.packets[0].header.session_time
            )
            inventory_context, _ = self.sessions.context_at(
                frame.overall_frame_identifier, session_uid=frame.session_uid
            )
            raw_lap_data_packets = any(
                packet.packet_kind is PacketId.LAP_DATA for packet in frame.packets
            )
            lap_data_unavailable = raw_lap_data_packets and not observation_lap_data
            self.car_lap_inventory.observe_frame(
                session_uid=frame.session_uid,
                frame_ordinal=frame_ordinal,
                packet_format=inventory_packet_format,
                lifecycle_epoch=association_epoch,
                player_car_index=inventory_player_index,
                session_time_s=inventory_session_time,
                frame_identifier=frame.overall_frame_identifier,
                packets=frame.packets,
                lap_data=(selected_lap[1].cars if selected_lap is not None else None),
                lap_data_conflicted=lap_data_conflicted or lap_data_unavailable,
                association_scope_assessable=association_scope_assessable,
                context=inventory_context,
                timeline_provider=lambda start, end, uid=frame.session_uid: self.sessions.context_timeline(
                    start, end, session_uid=uid
                ),
                boundary_reason=("lifecycle_boundary" if lifecycle_boundary else None),
            )
            if missing_telemetry_for_frame:
                self.missing_car_telemetry_frame_count += 1
                if len(self.missing_car_telemetry_frame_examples) < 128:
                    self.missing_car_telemetry_frame_examples.append(
                        (frame.session_uid, frame.overall_frame_identifier)
                    )
        return (
            tuple(attempts),
            tuple(errors),
            tuple(samples),
            tuple(observations),
            tuple(telemetry_errors),
        )

    def _process_lifecycle(
        self, frame: PacketFrame, frame_ordinal: int
    ) -> tuple[tuple[LapAttempt, ...], bool, int, bool, bool]:
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
        association_epoch = self._association_epoch_by_uid.get(uid, 0)
        association_scope_assessable = self._association_scope_assessable_by_uid.get(
            uid, True
        )
        packet_formats = {int(packet.packet_format) for packet in frame.packets}
        primary_players = {packet.header.player_car_index for packet in frame.packets}
        frame_scope_uncertain = len(packet_formats) != 1 or len(primary_players) != 1
        current_format = next(iter(packet_formats)) if len(packet_formats) == 1 else None
        current_player = next(iter(primary_players)) if len(primary_players) == 1 else None
        previous_format = self._last_association_format_by_uid.get(uid)
        previous_player = self._last_association_player_by_uid.get(uid)
        frame_scope_changed = (
            frame_scope_uncertain
            or (previous_format is not None and previous_format != current_format)
            or (previous_player is not None and previous_player != current_player)
        )
        self._last_association_format_by_uid[uid] = current_format
        self._last_association_player_by_uid[uid] = current_player
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
        lifecycle_boundary = boundary_cause is not None
        if uid != 0 and (lifecycle_boundary or frame_scope_changed):
            association_epoch += 1
            if (
                unknown_boundary
                or regression_boundary
                or (explicit_boundary and not flashback_valid)
                or frame_scope_uncertain
            ):
                association_scope_assessable = False
            self._association_epoch_by_uid[uid] = association_epoch
            self._association_scope_assessable_by_uid[uid] = (
                association_scope_assessable
            )
        return (
            tuple(lifecycle_attempts),
            quarantine,
            association_epoch,
            association_scope_assessable,
            lifecycle_boundary,
        )

    def _process_session_history(
        self,
        frame: PacketFrame,
        frame_ordinal: int,
        *,
        association_epoch: int,
        association_scope_assessable: bool,
    ) -> None:
        if frame.session_uid == 0:
            return
        for packet in frame.packets:
            if packet.packet_kind is not PacketId.SESSION_HISTORY:
                continue
            self.session_history_packets_admitted += 1
            if (
                len(packet.body) != SESSION_HISTORY_BODY_SIZE
                or packet.header.packet_version != 1
                or packet.packet_format
                not in (PacketFormat.F1_25, PacketFormat.SEASON_PACK_2026)
            ):
                decoded = self.session_history_decoder.decode(
                    packet, frame_ordinal=frame_ordinal
                )
                if decoded.error is not None:
                    self.session_history_decode_errors.append(decoded.error)
                continue
            if packet.body[0] != packet.header.player_car_index:
                self.session_history_non_player_packets += 1
                continue
            decoded = self.session_history_decoder.decode(
                packet, frame_ordinal=frame_ordinal
            )
            if decoded.error is not None or decoded.history is None:
                self.session_history_decode_errors.append(
                    decoded.error or "session_history_unavailable"
                )
                continue
            if decoded.history.car_index != packet.header.player_car_index:
                self.session_history_player_index_mismatches += 1
                continue
            self.session_history_packets_decoded += 1
            if len(self._session_history) < self.max_buffered_session_history:
                self._session_history.append(
                    SessionHistoryObservation(
                        packet=decoded.history,
                        association_epoch=association_epoch,
                        scope_assessable=association_scope_assessable,
                    )
                )
            else:
                self.session_history_packets_dropped += 1
                self.session_history_truncated_session_uids.add(frame.session_uid)

    def drain_session_history(self) -> tuple[SessionHistoryObservation, ...]:
        observations = tuple(self._session_history)
        self._session_history.clear()
        return observations

    def drain_session_history_decode_errors(self) -> tuple[str, ...]:
        errors = tuple(self.session_history_decode_errors)
        self.session_history_decode_errors.clear()
        return errors

    def drain_player_participant_observations(
        self,
    ) -> tuple[PlayerParticipantObservation, ...]:
        observations = tuple(self._player_participant_observations)
        self._player_participant_observations.clear()
        return observations

    def drain_player_car_setup_observations(
        self,
    ) -> tuple[PlayerCarSetupObservation, ...]:
        observations = tuple(self._player_car_setup_observations)
        self._player_car_setup_observations.clear()
        return observations

    def drain_car_setups_decode_errors(self) -> tuple[str, ...]:
        errors = tuple(self.car_setups_decode_errors)
        self.car_setups_decode_errors.clear()
        return errors

    def _process_player_car_setups(
        self,
        frame: PacketFrame,
        frame_ordinal: int,
        *,
        association_epoch: int,
        association_scope_assessable: bool,
        lifecycle_boundary: bool,
    ) -> None:
        if frame.session_uid == 0:
            return
        packets = [
            packet
            for packet in frame.packets
            if packet.packet_kind is PacketId.CAR_SETUPS
        ]
        if not packets:
            return

        formats = {int(packet.packet_format) for packet in frame.packets}
        player_slots = {packet.header.player_car_index for packet in frame.packets}
        packet_format = next(iter(formats)) if len(formats) == 1 else None
        player_car_index = next(iter(player_slots)) if len(player_slots) == 1 else None
        source = packets[0]

        def observation_base(provenance_packet: DecodedPacket | None = None):
            provenance = provenance_packet or source
            return {
                "session_uid": frame.session_uid,
                "frame_ordinal": frame_ordinal,
                "frame_identifier": provenance.header.frame_identifier,
                "overall_frame_identifier": frame.overall_frame_identifier,
                "packet_format": packet_format,
                "association_epoch": association_epoch,
                "association_scope_assessable": association_scope_assessable,
                "player_car_index": player_car_index,
                "session_time_s": provenance.header.session_time,
            }

        def record(
            *,
            status: str,
            reason: str | None,
            provenance_packet: DecodedPacket | None = None,
            setup: CarSetupData | None = None,
            next_front_wing_value: float | None = None,
            source_packet_count: int = 0,
        ) -> None:
            observation = PlayerCarSetupObservation(
                **observation_base(provenance_packet),
                status=status,  # type: ignore[arg-type]
                reason=reason,
                setup=setup,
                next_front_wing_value=next_front_wing_value,
                source_packet_count=source_packet_count,
            )
            if len(self._player_car_setup_observations) < (
                self.max_buffered_player_car_setup_observations
            ):
                self._player_car_setup_observations.append(observation)
                return
            self.player_car_setup_observations_dropped += 1
            if frame.session_uid in self.player_car_setup_observation_truncated_session_uids:
                return
            if len(self.player_car_setup_observation_truncated_session_uids) >= (
                MAX_PLAYER_CAR_SETUP_TRUNCATION_FENCE_SESSIONS
            ):
                self.player_car_setup_observation_truncation_marker_overflowed = True
                return
            self.player_car_setup_observation_truncated_session_uids.add(
                frame.session_uid
            )
            self._player_car_setup_observations.append(
                PlayerCarSetupObservation(
                    **observation_base(provenance_packet),
                    status="truncated",
                    reason="pipeline_observation_buffer_limit",
                    setup=None,
                    next_front_wing_value=None,
                    source_packet_count=0,
                )
            )

        if packet_format is None or player_car_index is None:
            record(status="unavailable", reason="frame_player_scope_conflict")
            return
        if lifecycle_boundary:
            record(status="unavailable", reason="lifecycle_boundary")
            return

        decoded: list[tuple[DecodedPacket, CarSetupsPacket]] = []
        errors: list[str] = []
        for packet in packets:
            result = self.car_setups_decoder.decode(packet)
            if result.error is not None:
                errors.append(result.error)
                self.car_setups_decode_errors.append(result.error)
            elif result.setups is not None:
                self.car_setups_packets_decoded += 1
                decoded.append((packet, result.setups))
        if not decoded:
            record(
                status="unavailable",
                reason="car_setup_packet_unavailable"
                if errors
                else "car_setup_packet_not_decoded",
            )
            return

        selected: list[tuple[DecodedPacket, CarSetupData, float | None]] = []
        out_of_range = False
        for packet, setups in decoded:
            if int(packet.packet_format) != packet_format:
                continue
            if packet.header.player_car_index != player_car_index:
                continue
            if player_car_index >= len(setups.cars):
                out_of_range = True
                continue
            selected.append(
                (
                    packet,
                    setups.cars[player_car_index],
                    setups.next_front_wing_value,
                )
            )
        if not selected:
            record(
                status="unavailable",
                reason="player_slot_out_of_range"
                if out_of_range
                else "car_setup_player_slot_mismatch",
            )
            return
        if out_of_range:
            record(
                status="unavailable",
                reason="conflicting_player_car_setup_evidence",
            )
            return

        def signature(setup: CarSetupData, next_value: float | None) -> tuple[object, ...]:
            return (*asdict(setup).values(), next_value)

        signatures = {signature(setup, next_value) for _, setup, next_value in selected}
        ordered_selected = sorted(selected, key=lambda item: item[0].wire_fingerprint)
        if len(signatures) != 1:
            record(
                status="unavailable",
                reason="conflicting_player_car_setup_evidence",
                provenance_packet=ordered_selected[0][0],
                source_packet_count=len(ordered_selected),
            )
            return
        provenance_keys = {
            (packet.header.frame_identifier, packet.header.session_time)
            for packet, _, _ in ordered_selected
        }
        if len(provenance_keys) != 1:
            record(
                status="unavailable",
                reason="conflicting_player_car_setup_provenance",
                provenance_packet=ordered_selected[0][0],
                source_packet_count=len(ordered_selected),
            )
            return

        record(
            status="observed",
            reason=None,
            provenance_packet=ordered_selected[0][0],
            setup=ordered_selected[0][1],
            next_front_wing_value=ordered_selected[0][2],
            source_packet_count=len(ordered_selected),
        )

    def _process_player_participants(
        self,
        frame: PacketFrame,
        frame_ordinal: int,
        *,
        association_epoch: int,
        association_scope_assessable: bool,
        lifecycle_boundary: bool,
    ) -> None:
        if frame.session_uid == 0:
            return
        packets = [
            packet
            for packet in frame.packets
            if packet.packet_kind is PacketId.PARTICIPANTS
        ]
        if not packets:
            return

        formats = {int(packet.packet_format) for packet in frame.packets}
        player_slots = {packet.header.player_car_index for packet in frame.packets}
        packet_format = next(iter(formats)) if len(formats) == 1 else None
        player_car_index = next(iter(player_slots)) if len(player_slots) == 1 else None
        source = packets[0]

        def observation_base(provenance_packet: DecodedPacket | None = None):
            provenance = provenance_packet or source
            return {
                "session_uid": frame.session_uid,
                "frame_ordinal": frame_ordinal,
                "frame_identifier": provenance.header.frame_identifier,
                "overall_frame_identifier": frame.overall_frame_identifier,
                "packet_format": packet_format,
                "association_epoch": association_epoch,
                "association_scope_assessable": association_scope_assessable,
                "player_car_index": player_car_index,
                "session_time_s": provenance.header.session_time,
            }

        def record(
            *,
            status: str,
            reason: str | None,
            provenance_packet: DecodedPacket | None = None,
            active_car_count: int | None = None,
            participant: ParticipantData | None = None,
            source_packet_count: int = 0,
        ) -> None:
            observation = PlayerParticipantObservation(
                **observation_base(provenance_packet),
                status=status,  # type: ignore[arg-type]
                reason=reason,
                active_car_count=active_car_count,
                participant=participant,
                source_packet_count=source_packet_count,
            )
            if len(self._player_participant_observations) < (
                self.max_buffered_player_participant_observations
            ):
                self._player_participant_observations.append(observation)
                return
            self.player_participant_observations_dropped += 1
            if frame.session_uid in self.player_participant_observation_truncated_session_uids:
                return
            if len(self.player_participant_observation_truncated_session_uids) >= (
                MAX_PLAYER_PARTICIPANT_TRUNCATION_FENCE_SESSIONS
            ):
                self.player_participant_observation_truncation_marker_overflowed = True
                return
            self.player_participant_observation_truncated_session_uids.add(
                frame.session_uid
            )
            self._player_participant_observations.append(
                PlayerParticipantObservation(
                    **observation_base(provenance_packet),
                    status="truncated",
                    reason="pipeline_observation_buffer_limit",
                    active_car_count=None,
                    participant=None,
                    source_packet_count=0,
                )
            )

        if packet_format is None or player_car_index is None:
            record(status="unavailable", reason="frame_player_scope_conflict")
            return

        if lifecycle_boundary:
            record(status="unavailable", reason="lifecycle_boundary")
            return

        decoded = []
        errors = []
        for packet in packets:
            result = self.participants_decoder.decode(packet)
            if result.error is not None:
                errors.append(result.error)
            elif result.participants is not None:
                decoded.append((packet, result.participants))
        if not decoded:
            record(
                status="unavailable",
                reason="participant_packet_unavailable"
                if errors
                else "participant_packet_not_decoded",
            )
            return

        selected: list[tuple[DecodedPacket, ParticipantData, int]] = []
        inactive = False
        out_of_range = False
        for packet, participants in decoded:
            if int(packet.packet_format) != packet_format:
                continue
            if packet.header.player_car_index != player_car_index:
                continue
            if player_car_index >= len(participants.cars):
                out_of_range = True
                continue
            if player_car_index >= participants.active_car_count:
                inactive = True
                continue
            selected.append(
                (
                    packet,
                    participants.cars[player_car_index],
                    participants.active_car_count,
                )
            )

        if not selected:
            reason = (
                "player_slot_inactive"
                if inactive
                else "player_slot_out_of_range"
                if out_of_range
                else "participant_player_slot_mismatch"
            )
            record(status="unavailable", reason=reason)
            return

        if inactive or out_of_range:
            record(
                status="unavailable",
                reason="conflicting_player_participant_evidence",
            )
            return

        def signature(participant: ParticipantData) -> tuple[object, ...]:
            return tuple(
                getattr(participant, field)
                for field in (
                    "ai_controlled",
                    "driver_id",
                    "network_id",
                    "team_id",
                    "my_team",
                    "race_number",
                    "nationality_id",
                    "name",
                    "your_telemetry",
                    "tech_level",
                    "platform_id",
                )
            )

        signatures = {signature(participant) for _, participant, _ in selected}
        if len(signatures) != 1:
            record(
                status="unavailable",
                reason="conflicting_player_participant_evidence",
                provenance_packet=min(
                    selected, key=lambda item: item[0].wire_fingerprint
                )[0],
                source_packet_count=len(selected),
            )
            return

        ordered_selected = sorted(selected, key=lambda item: item[0].wire_fingerprint)
        provenance_keys = {
            (packet.header.frame_identifier, packet.header.session_time)
            for packet, _, _ in ordered_selected
        }
        if len(provenance_keys) != 1:
            record(
                status="unavailable",
                reason="conflicting_player_participant_provenance",
                provenance_packet=ordered_selected[0][0],
                source_packet_count=len(ordered_selected),
            )
            return

        record(
            status="observed",
            reason=None,
            provenance_packet=ordered_selected[0][0],
            active_car_count=ordered_selected[0][2],
            participant=ordered_selected[0][1],
            source_packet_count=len(ordered_selected),
        )

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
        car_observations: list[CarObservation] = []
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
                attempts, errors, samples, observations, telemetry_errors = self._process_frames(retired_frames)
                lap_attempts.extend(attempts)
                lap_data_errors.extend(errors)
                car_samples.extend(samples)
                car_observations.extend(observations)
                car_telemetry_errors.extend(telemetry_errors)
                lap_attempts.extend(self.laps.end_session(event.session_uid))
                self.car_lap_inventory.end_session(
                    event.session_uid, reason="session_ended"
                )
            elif event.kind == "session_started":
                self.laps.start_session(event.session_uid)
                self.car_lap_inventory.start_session(event.session_uid)
                self._association_epoch_by_uid[event.session_uid] = 0
                self._association_scope_assessable_by_uid[event.session_uid] = True
                self._last_association_format_by_uid[event.session_uid] = int(
                    packet.packet_format
                )
                self._last_association_player_by_uid[event.session_uid] = (
                    packet.header.player_car_index
                )
            elif event.kind == "session_context_invalidated":
                old_format_frames = self.frames.flush_session(event.session_uid)
                completed_frames.extend(old_format_frames)
                attempts, errors, samples, observations, telemetry_errors = self._process_frames(old_format_frames)
                lap_attempts.extend(attempts)
                lap_data_errors.extend(errors)
                car_samples.extend(samples)
                car_observations.extend(observations)
                car_telemetry_errors.extend(telemetry_errors)
                lap_attempts.extend(
                    self.laps.close_segment(
                        event.session_uid, reason="packet_format_changed"
                    )
                )
                self.car_lap_inventory.end_session(
                    event.session_uid, reason="packet_format_changed"
                )
                self.car_lap_inventory.start_session(event.session_uid)
                self._association_epoch_by_uid[event.session_uid] = (
                    self._association_epoch_by_uid.get(event.session_uid, 0) + 1
                )
                self._last_association_format_by_uid[event.session_uid] = int(
                    packet.packet_format
                )
                self._last_association_player_by_uid[event.session_uid] = (
                    packet.header.player_car_index
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
            attempts, errors, samples, observations, telemetry_errors = self._process_frames(ready_frames)
            lap_attempts.extend(attempts)
            lap_data_errors.extend(errors)
            car_samples.extend(samples)
            car_observations.extend(observations)
            car_telemetry_errors.extend(telemetry_errors)
        car_slot_tenures, observed_car_lap_attempts = self.car_lap_inventory.drain()
        return PipelineResult(
            packet=packet,
            session_events=session_events,
            completed_frames=tuple(completed_frames),
            session_context=updated_context,
            session_context_error=context_result.error,
            session_progress=(context_result.progress if packet.header.session_uid == self.sessions.current_session_uid
                              and packet.header.overall_frame_identifier == self.sessions.latest_context_frame else None),
            lap_attempts=tuple(lap_attempts),
            lap_data_errors=tuple(lap_data_errors),
            car_samples=tuple(car_samples),
            car_observations=tuple(car_observations),
            car_telemetry_errors=tuple(car_telemetry_errors),
            participants=participants,
            participants_error=participant_result.error,
            context_history_changes=self.sessions.drain_context_history_changes(),
            lifecycle_events=self.drain_lifecycle_events(),
            session_history=self.drain_session_history(),
            session_history_decode_errors=self.drain_session_history_decode_errors(),
            player_participant_observations=self.drain_player_participant_observations(),
            player_car_setup_observations=self.drain_player_car_setup_observations(),
            car_setup_decode_errors=self.drain_car_setups_decode_errors(),
            car_slot_tenures=car_slot_tenures,
            observed_car_lap_attempts=observed_car_lap_attempts,
        )

    def finish(self) -> tuple[PacketFrame, ...]:
        return self.finish_with_outputs().completed_frames

    def finish_with_outputs(self, *, interruption_reason: str | None = None) -> PipelineFlushResult:
        frames = self.frames.flush()
        attempts, errors, samples, observations, telemetry_errors = self._process_frames(frames)
        start_attempt_count = len(self.laps.attempts)
        if interruption_reason is None:
            self.laps.finish()
        elif self.sessions.current_session_uid is not None:
            self.laps.close_lifecycle_boundary(self.sessions.current_session_uid, reason=interruption_reason)
        attempts = (*attempts, *self.laps.attempts[start_attempt_count:])
        if interruption_reason is None:
            self.car_lap_inventory.finish()
        elif self.sessions.current_session_uid is not None:
            uid = self.sessions.current_session_uid
            self.car_lap_inventory.interrupt(self._frame_ordinal_by_uid.get(uid, 0) + 1, interruption_reason)
            self._association_epoch_by_uid[uid] = self._association_epoch_by_uid.get(uid, 0) + 1
        car_slot_tenures, observed_car_lap_attempts = self.car_lap_inventory.drain()
        return PipelineFlushResult(
            completed_frames=frames,
            lap_attempts=tuple(attempts),
            car_samples=samples,
            car_observations=observations,
            lap_data_errors=errors,
            car_telemetry_errors=telemetry_errors,
            lifecycle_events=self.drain_lifecycle_events(),
            session_history=self.drain_session_history(),
            session_history_decode_errors=self.drain_session_history_decode_errors(),
            player_participant_observations=self.drain_player_participant_observations(),
            player_car_setup_observations=self.drain_player_car_setup_observations(),
            car_setup_decode_errors=self.drain_car_setups_decode_errors(),
            car_slot_tenures=car_slot_tenures,
            observed_car_lap_attempts=observed_car_lap_attempts,
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


def _join_car_damage(
    lap_packet: DecodedPacket,
    player_car_index: int,
    candidates: list[tuple[DecodedPacket, CarDamagePacket | None, str | None]],
) -> tuple[CarDamageData | None, str | None]:
    if not candidates:
        return None, "damage_packet_missing"
    if any(
        damage_packet.packet_format is not lap_packet.packet_format
        for damage_packet, _, _ in candidates
    ):
        return None, "wire_format_mismatch"
    if any(error is not None for _, _, error in candidates):
        return None, "damage_packet_malformed_or_unsupported"
    candidate_player_indices = {
        packet.header.player_car_index for packet, _, _ in candidates
    }
    if len(candidate_player_indices) != 1 or (
        next(iter(candidate_player_indices)) != lap_packet.header.player_car_index
    ):
        return None, "player_index_mismatch"
    decoded_packets = [decoded for _, decoded, _ in candidates if decoded is not None]
    if len(decoded_packets) != len(candidates):
        return None, "player_index_mismatch"
    if not 0 <= player_car_index < min(len(decoded.cars) for decoded in decoded_packets):
        return None, "player_index_mismatch"
    selected_records = [
        decoded.cars[player_car_index]
        for decoded in decoded_packets
    ]
    if not selected_records:
        return None, "player_index_mismatch"
    first = selected_records[0]
    if any(record.raw_record != first.raw_record for record in selected_records[1:]):
        return None, "conflicting_damage_packets"
    return first, None
