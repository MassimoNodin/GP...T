from __future__ import annotations

import asyncio
import time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..analysis.continuity import session_time_discontinuity
from ..errors import ProtocolError
from ..pipeline import PipelineResult, TelemetryPipeline, _join_car_status
from ..sessions.context import SessionContext
from ..sessions.manager import SessionTracker
from ..telemetry.canonical import CarSample, make_car_sample
from ..telemetry.frames import FrameAssembler
from ..udp.car_status import CarStatusDecoder, CarStatusPacket
from ..udp.car_telemetry import CarTelemetryDecoder
from ..udp.decoder import PacketDecoder
from ..udp.events import EventDecoder
from ..udp.lap_data import CarLapData, LapDataDecoder
from ..udp.models import DecodedPacket, PacketFormat, PacketFrame, PacketId, RawDatagram
from ..udp.session_context import SessionContextDecoder
from ..udp.source import UDPSource
from .capture import CaptureWriter


LIVE_TELEMETRY_FRESHNESS_LIMIT_MS = 500
LIVE_CAR_STATUS_FRESHNESS_LIMIT_MS = 500
LIVE_LAP_TIMING_FRESHNESS_LIMIT_MS = 500


@dataclass(frozen=True, slots=True)
class RecordingSnapshot:
    state: str
    elapsed_ms: int
    received: int
    queued: int
    recorded: int
    queue_dropped: int
    socket_errors: int
    latest_context: dict[str, object] | None
    live_telemetry: dict[str, object]
    live_car_status: dict[str, object]
    live_lap_timing: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "elapsed_ms": self.elapsed_ms,
            "received": self.received,
            "queued": self.queued,
            "recorded": self.recorded,
            "queue_dropped": self.queue_dropped,
            "socket_errors": self.socket_errors,
            "latest_context": self.latest_context,
            "live_telemetry": dict(self.live_telemetry),
            "live_car_status": dict(self.live_car_status),
            "live_lap_timing": dict(self.live_lap_timing),
        }


@dataclass(frozen=True, slots=True)
class RecordingResult:
    output: Path
    summary: dict[str, int]
    session_contexts: tuple[dict[str, object], ...]
    lap_attempts: tuple[dict[str, object], ...]
    capture_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "capture": str(self.output),
            "summary": dict(self.summary),
            "session_contexts": list(self.session_contexts),
            "lap_attempts": list(self.lap_attempts),
        }


class AcquisitionObserver:
    """Bounded packet and session observer for long-running managed captures."""

    _LIVE_FRESHNESS_LIMIT_MS = LIVE_TELEMETRY_FRESHNESS_LIMIT_MS
    _FRAME_MASK = 0xFFFFFFFF
    _SERIAL_HALF_RANGE = 0x80000000

    def __init__(self, *, reorder_window_frames: int = 3) -> None:
        self.decoder = PacketDecoder()
        self.session_context_decoder = SessionContextDecoder()
        self.event_decoder = EventDecoder()
        self.sessions = SessionTracker(retain_context_history=False)
        self.frames = FrameAssembler(reorder_window_frames=reorder_window_frames)
        self.lap_data_decoder = LapDataDecoder()
        self.car_telemetry_decoder = CarTelemetryDecoder()
        self.car_status_decoder = CarStatusDecoder()
        self.counts: Counter[str] = Counter(completed_frames=0)
        self.latest_context: dict[str, object] | None = None
        self._live_status = "waiting"
        self._live_reason: str | None = None
        self._live_snapshot: dict[str, object] | None = None
        self._live_received_monotonic_ns: int | None = None
        self._live_session_uid: int | None = None
        self._live_packet_format: PacketFormat | None = None
        self._live_player_index: int | None = None
        self._player_header_frame: int | None = None
        self._live_player_barrier_frame: int | None = None
        self._live_last_session_time_s: float | None = None
        self._live_car_status = "waiting"
        self._live_car_status_reason: str | None = None
        self._live_car_status_snapshot: dict[str, object] | None = None
        self._live_car_status_received_monotonic_ns: int | None = None
        self._live_lap_timing = "waiting"
        self._live_lap_timing_reason: str | None = None
        self._live_lap_timing_snapshot: dict[str, object] | None = None
        self._live_lap_timing_received_monotonic_ns: int | None = None
        self._receive_times: OrderedDict[tuple[int, int, bytes], int] = OrderedDict()
        self._max_receive_time_entries = self.frames.max_open_frames * 5 + 4
        self._finished = False

    def process(
        self,
        raw: RawDatagram,
        *,
        delivery_monotonic_ns: int | None = None,
    ) -> None:
        """Observe one unchanged datagram.

        Live UDP callers use the datagram's receive timestamp. Replay callers
        may supply a runtime delivery timestamp so freshness is measured during
        playback without altering the capture's source timestamp.
        """
        observed_monotonic_ns = (
            raw.monotonic_ns
            if delivery_monotonic_ns is None
            else delivery_monotonic_ns
        )
        try:
            packet = self.decoder.decode(raw)
        except ProtocolError:
            self.counts["unrecognized_or_malformed"] += 1
            return

        self.counts[f"format_{packet.packet_format.value}"] += 1
        session_events = self.sessions.observe(packet)
        context_result = self.session_context_decoder.decode(packet)
        if context_result.error is not None:
            self.counts["session_context_decode_errors"] += 1
        if context_result.context is not None and self.sessions.update_context(
            context_result.context, packet.header.overall_frame_identifier
        ):
            self.counts["session_context_updates"] += 1
        if any(
            event.kind in {"session_started", "session_context_invalidated"}
            for event in session_events
        ):
            self.latest_context = None
        if any(
            event.kind in {"session_started", "session_context_invalidated"}
            for event in session_events
        ):
            self._reset_live_monitor()
            if (
                packet.header.session_uid == self.sessions.current_session_uid
                and packet.packet_format is self.sessions.current_packet_format
            ):
                self._live_session_uid = packet.header.session_uid
                self._live_packet_format = packet.packet_format
        if context_result.context is not None:
            current = self.sessions.current_context
            self.latest_context = current.to_dict() if current is not None else None

        for event in session_events:
            if event.kind == "session_ended":
                self._count_completed(
                    self._consume_frames(self.frames.retire_session(event.session_uid))
                )
            elif event.kind == "session_context_invalidated":
                self.counts["session_context_invalidations"] += 1
                self._count_completed(
                    self._consume_frames(self.frames.flush_session(event.session_uid))
                )

        obsolete_format = (
            packet.header.session_uid == self.sessions.current_session_uid
            and packet.packet_format is not self.sessions.current_packet_format
        )
        if self.sessions.is_retired(packet.header.session_uid) or obsolete_format:
            self.frames.late_packets_ignored += 1
            return
        counters_before_add = (
            self.frames.duplicates_ignored,
            self.frames.late_packets_ignored,
            self.frames.overflow_packets_dropped,
        )
        completed = self.frames.add(packet)
        accepted = counters_before_add == (
            self.frames.duplicates_ignored,
            self.frames.late_packets_ignored,
            self.frames.overflow_packets_dropped,
        )
        if accepted:
            self._observe_player_identity(packet)
            if packet.packet_kind in {
                PacketId.LAP_DATA,
                PacketId.CAR_TELEMETRY,
                PacketId.CAR_TELEMETRY_2,
                PacketId.CAR_STATUS,
            }:
                self._remember_receive_time(raw, packet, observed_monotonic_ns)
        self._count_completed(self._consume_frames(completed))

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        frames = self._consume_frames(self.frames.flush())
        self._count_completed(frames)
        self.counts["open_frames_flushed"] = len(frames)
        self.counts["duplicate_envelopes_ignored"] = self.frames.duplicates_ignored
        self.counts["late_packets_ignored"] = self.frames.late_packets_ignored
        self.counts["frame_overflow_packets_dropped"] = (
            self.frames.overflow_packets_dropped
        )

    def live_telemetry_snapshot(
        self, now_monotonic_ns: int | None = None
    ) -> dict[str, object]:
        """Return a copy with freshness measured from capture receive time."""
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        age_ms = (
            None
            if self._live_received_monotonic_ns is None
            else max(0, (now - self._live_received_monotonic_ns) // 1_000_000)
        )
        status = self._live_status
        if (
            age_ms is not None
            and age_ms > self._LIVE_FRESHNESS_LIMIT_MS
            and status in {"fresh", "unavailable"}
        ):
            status = "stale"
        snapshot: dict[str, object] = {
            "status": status,
            "reason": self._live_reason,
            "age_ms": age_ms,
        }
        if self._live_snapshot is not None:
            snapshot.update(self._live_snapshot)
        # Kept only in the in-process status snapshot so the API can recalculate
        # age between UDP packets. The controller removes this before responding.
        if self._live_received_monotonic_ns is not None:
            snapshot["_observed_monotonic_ns"] = self._live_received_monotonic_ns
        return snapshot

    def live_car_status_snapshot(
        self, now_monotonic_ns: int | None = None
    ) -> dict[str, object]:
        """Return independent Car Status freshness using its selected frame evidence."""
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        age_ms = (
            None
            if self._live_car_status_received_monotonic_ns is None
            else max(
                0,
                (now - self._live_car_status_received_monotonic_ns) // 1_000_000,
            )
        )
        status = self._live_car_status
        if (
            age_ms is not None
            and age_ms > LIVE_CAR_STATUS_FRESHNESS_LIMIT_MS
            and status in {"fresh", "unavailable"}
        ):
            status = "stale"
        snapshot: dict[str, object] = {
            "status": status,
            "reason": self._live_car_status_reason,
            "age_ms": age_ms,
        }
        if self._live_car_status_snapshot is not None:
            snapshot.update(self._live_car_status_snapshot)
        if self._live_car_status_received_monotonic_ns is not None:
            snapshot["_observed_monotonic_ns"] = (
                self._live_car_status_received_monotonic_ns
            )
        return snapshot

    def live_lap_timing_snapshot(
        self, now_monotonic_ns: int | None = None
    ) -> dict[str, object]:
        """Return independent reported Lap Data timing and its receive freshness."""
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        age_ms = (
            None
            if self._live_lap_timing_received_monotonic_ns is None
            else max(
                0,
                (now - self._live_lap_timing_received_monotonic_ns) // 1_000_000,
            )
        )
        status = self._live_lap_timing
        if (
            age_ms is not None
            and age_ms > LIVE_LAP_TIMING_FRESHNESS_LIMIT_MS
            and status in {"fresh", "unavailable"}
        ):
            status = "stale"
        snapshot: dict[str, object] = {
            "status": status,
            "reason": self._live_lap_timing_reason,
            "age_ms": age_ms,
        }
        if self._live_lap_timing_snapshot is not None:
            snapshot.update(self._live_lap_timing_snapshot)
        if self._live_lap_timing_received_monotonic_ns is not None:
            snapshot["_observed_monotonic_ns"] = (
                self._live_lap_timing_received_monotonic_ns
            )
        return snapshot

    def _reset_live_monitor(self) -> None:
        self._live_status = "waiting"
        self._live_reason = None
        self._live_snapshot = None
        self._live_received_monotonic_ns = None
        self._live_session_uid = None
        self._live_packet_format = None
        self._live_player_index = None
        self._player_header_frame = None
        self._live_player_barrier_frame = None
        self._live_last_session_time_s = None
        self._reset_live_car_status_monitor()
        self._reset_live_lap_timing_monitor()
        self._receive_times.clear()

    def _observe_player_identity(self, packet: DecodedPacket) -> None:
        header = packet.header
        if (
            header.session_uid == 0
            or header.session_uid != self.sessions.current_session_uid
            or packet.packet_format is not self.sessions.current_packet_format
        ):
            return
        if (
            self._live_session_uid != header.session_uid
            or self._live_packet_format is not packet.packet_format
        ):
            self._reset_live_monitor()
            self._live_session_uid = header.session_uid
            self._live_packet_format = packet.packet_format
            self._live_player_index = header.player_car_index
            self._player_header_frame = header.overall_frame_identifier
            self._live_player_barrier_frame = header.overall_frame_identifier
            return

        frame = header.overall_frame_identifier & self._FRAME_MASK
        watermark = self._player_header_frame
        if watermark is None or self._is_newer_frame(frame, watermark):
            if self._live_player_index != header.player_car_index:
                self._clear_live_sample()
                self._live_player_barrier_frame = frame
            self._live_player_index = header.player_car_index
            self._player_header_frame = frame
        elif frame == watermark and self._live_player_index != header.player_car_index:
            self._clear_live_sample()
            self._live_player_index = None
            self._live_player_barrier_frame = frame
            self._live_reason = "player_identity_conflicted_within_frame"
            self._live_status = "unavailable"

    def _remember_receive_time(
        self,
        raw: RawDatagram,
        packet: DecodedPacket,
        observed_monotonic_ns: int,
    ) -> None:
        key = (
            packet.header.session_uid,
            packet.header.overall_frame_identifier,
            packet.wire_fingerprint,
        )
        self._receive_times[key] = observed_monotonic_ns
        self._receive_times.move_to_end(key)
        while len(self._receive_times) > self._max_receive_time_entries:
            self._receive_times.popitem(last=False)

    def _consume_frames(self, frames: tuple[PacketFrame, ...]) -> tuple[PacketFrame, ...]:
        for frame in frames:
            self._observe_frame(frame)
        return frames

    def _observe_frame(self, frame: PacketFrame) -> None:
        if (
            frame.session_uid == 0
            or frame.session_uid != self.sessions.current_session_uid
            or self.sessions.is_retired(frame.session_uid)
            or self._live_packet_format is not self.sessions.current_packet_format
            or any(
                packet.packet_format is not self.sessions.current_packet_format
                for packet in frame.packets
            )
            or self._live_player_index is None
            or (
                self._live_player_barrier_frame is not None
                and self._is_newer_frame(
                    self._live_player_barrier_frame,
                    frame.overall_frame_identifier,
                )
            )
        ):
            self._discard_frame_receive_times(frame)
            return

        events = [
            self.event_decoder.decode(packet)
            for packet in frame.packets
            if packet.packet_kind is PacketId.EVENT
        ]
        explicit_rewind = any(event.code == "FLBK" for event in events)
        unknown_event = any(event.error is not None for event in events)
        if explicit_rewind or unknown_event:
            self._clear_live_sample()
            self._live_snapshot = self._empty_live_snapshot(
                frame, self._live_player_index
            )
            self._live_status = "unavailable"
            self._live_reason = (
                "flashback_boundary" if explicit_rewind else "event_evidence_unknown"
            )
            self._set_live_lap_timing_unavailable(
                frame,
                self._live_player_index,
                self._live_reason,
                [],
            )
            self._live_last_session_time_s = None
            self._discard_frame_receive_times(frame)
            return

        valid_player_lap_packets: list[DecodedPacket] = []
        for packet in frame.packets:
            if (
                packet.packet_kind is not PacketId.LAP_DATA
                or packet.header.player_car_index != self._live_player_index
            ):
                continue
            decoded_lap = self.lap_data_decoder.decode(packet)
            if (
                decoded_lap.error is None
                and decoded_lap.lap_data is not None
                and 0 <= self._live_player_index < len(decoded_lap.lap_data.cars)
            ):
                valid_player_lap_packets.append(packet)
        if valid_player_lap_packets:
            observed_times = [
                packet.header.session_time for packet in valid_player_lap_packets
            ]
            current_session_time = observed_times[-1]
            baseline_times = (
                ([self._live_last_session_time_s] if self._live_last_session_time_s is not None else [])
                + observed_times
            )
            clock_regression = any(
                session_time_discontinuity(left, right) == "session_time_regression"
                for left, right in zip(baseline_times, baseline_times[1:])
            )
            if clock_regression:
                self._clear_live_sample()
                self._live_snapshot = self._empty_live_snapshot(
                    frame, self._live_player_index
                )
                self._live_status = "unavailable"
                self._live_reason = "session_time_regression"
                self._set_live_lap_timing_unavailable(
                    frame,
                    self._live_player_index,
                    "session_time_regression",
                    valid_player_lap_packets,
                )
                self._live_last_session_time_s = (
                    None if len(valid_player_lap_packets) > 1 else current_session_time
                )
                self._discard_frame_receive_times(frame)
                return
            self._live_last_session_time_s = current_session_time

        lap_packets = [
            packet for packet in frame.packets if packet.packet_kind is PacketId.LAP_DATA
        ]
        self._observe_live_lap_timing(frame, lap_packets)
        car_status_candidates = []
        for packet in frame.packets:
            if packet.packet_kind is not PacketId.CAR_STATUS:
                continue
            result = self.car_status_decoder.decode(packet)
            car_status_candidates.append((packet, result.car_status, result.error))
        if not lap_packets:
            if car_status_candidates and self._live_player_index is not None:
                selected_status_packets = [
                    packet
                    for packet, _decoded, _error in car_status_candidates
                    if packet.header.player_car_index == self._live_player_index
                ]
                if selected_status_packets:
                    unsupported = any(
                        self._is_unsupported_adapter(packet)
                        for packet in selected_status_packets
                    )
                    self._set_live_car_status_unavailable(
                        frame,
                        self._live_player_index,
                        "same_frame_player_lap_missing",
                        selected_status_packets,
                        status="unsupported" if unsupported else "unavailable",
                    )
            unsupported = next(
                (
                    packet
                    for packet in frame.packets
                    if packet.header.player_car_index == self._live_player_index
                    and packet.packet_kind is PacketId.CAR_TELEMETRY
                    and self._is_unsupported_adapter(packet)
                ),
                None,
            )
            if unsupported is not None:
                self._live_snapshot = self._empty_live_snapshot(
                    frame, self._live_player_index
                )
                self._live_status = "unsupported"
                self._live_reason = "car_telemetry_adapter_unsupported"
                self._live_received_monotonic_ns = self._frame_receive_time(
                    frame, (unsupported,)
                )
            self._discard_frame_receive_times(frame)
            return

        decoded_telemetry: dict[int, tuple[object, DecodedPacket]] = {}
        telemetry_errors: list[tuple[DecodedPacket, str]] = []
        for packet in frame.packets:
            if packet.packet_kind is PacketId.CAR_TELEMETRY_2:
                continue
            if packet.packet_kind is not PacketId.CAR_TELEMETRY:
                continue
            result = self.car_telemetry_decoder.decode(packet)
            if result.error is not None:
                telemetry_errors.append((packet, result.error))
            elif result.telemetry is not None:
                player_index = packet.header.player_car_index
                if 0 <= player_index < len(result.telemetry.cars):
                    decoded_telemetry[player_index] = (
                        result.telemetry.cars[player_index],
                        packet,
                    )

        latest_values: dict[str, object] | None = None
        latest_status = "unavailable"
        latest_reason: str | None = "same_frame_car_telemetry_unavailable"
        selected_receive_ns: int | None = None
        matched_player_lap = False
        for packet in lap_packets:
            if packet.header.player_car_index != self._live_player_index:
                continue
            matched_player_lap = True
            selected_packets = [packet]
            selected_receive_ns = self._frame_receive_time(frame, selected_packets)
            result = self.lap_data_decoder.decode(packet)
            if result.error is not None or result.lap_data is None:
                self._set_live_car_status_unavailable(
                    frame,
                    self._live_player_index,
                    "lap_data_adapter_unsupported"
                    if self._is_unsupported_adapter(packet)
                    else "lap_data_decode_failed",
                    [packet]
                    + [
                        candidate
                        for candidate, _decoded, _error in car_status_candidates
                        if candidate.header.player_car_index == self._live_player_index
                    ],
                    status=(
                        "unsupported"
                        if self._is_unsupported_adapter(packet)
                        else "unavailable"
                    ),
                )
                latest_values = None
                latest_status = (
                    "unsupported"
                    if self._is_unsupported_adapter(packet)
                    else "unavailable"
                )
                latest_reason = (
                    "lap_data_adapter_unsupported"
                    if latest_status == "unsupported"
                    else "lap_data_decode_failed"
                )
                continue
            car_index = packet.header.player_car_index
            if not 0 <= car_index < len(result.lap_data.cars):
                self._set_live_car_status_unavailable(
                    frame,
                    car_index,
                    "player_car_index_out_of_range",
                    [packet]
                    + [
                        candidate
                        for candidate, _decoded, _error in car_status_candidates
                        if candidate.header.player_car_index == car_index
                    ],
                )
                latest_values = None
                latest_status = "unavailable"
                latest_reason = "player_car_index_out_of_range"
                continue

            telemetry = None
            if any(
                candidate.packet_kind is PacketId.CAR_TELEMETRY
                and candidate.header.player_car_index == car_index
                for candidate in frame.packets
            ):
                decoded = decoded_telemetry.get(car_index)
                if decoded is not None:
                    telemetry, telemetry_packet = decoded
                    selected_packets.append(telemetry_packet)
            elif decoded_telemetry:
                latest_reason = "player_index_mismatch_in_frame"
            for candidate, _error in telemetry_errors:
                if candidate.header.player_car_index != car_index or telemetry is not None:
                    continue
                if self._is_unsupported_adapter(candidate):
                    latest_status = "unsupported"
                    latest_reason = "car_telemetry_adapter_unsupported"
                    telemetry = None
                    break
                latest_reason = "car_telemetry_decode_failed"

            selected_receive_ns = self._frame_receive_time(frame, selected_packets)

            car = result.lap_data.cars[car_index]
            car_status, car_status_reason = _join_car_status(
                packet, car_index, car_status_candidates
            )
            status_packets = [
                candidate
                for candidate, _decoded, _error in car_status_candidates
                if candidate.header.player_car_index == car_index
            ]
            car_status_receive_ns = self._frame_receive_time(
                frame, [packet, *status_packets]
            )
            sample = make_car_sample(
                session_uid=frame.session_uid,
                frame_identifier=frame.overall_frame_identifier,
                session_time_s=packet.header.session_time,
                car_index=car_index,
                attempt_id="recording_live_snapshot",
                lap=car,
                telemetry=telemetry,
                car_status=car_status,
                car_status_unavailable_reason=car_status_reason,
            )
            self._publish_live_car_status(
                frame,
                packet,
                sample,
                status_reason=car_status_reason,
                received_monotonic_ns=car_status_receive_ns,
                unsupported=any(
                    self._is_unsupported_adapter(candidate)
                    for candidate in status_packets
                ),
            )
            values: dict[str, object] = {
                "session_uid": str(frame.session_uid),
                "frame_identifier": frame.overall_frame_identifier,
                "packet_format": int(packet.packet_format),
                "player_car_index": car_index,
                "lap_number": car.current_lap_number if car.current_lap_number > 0 else None,
                "lap_time_ms": car.current_lap_time_ms,
                "game_invalid": car.current_lap_invalid_id != 0,
                "pit_status_id": car.pit_status_id,
                "driver_status_id": car.driver_status_id,
                "speed_kph": _live_channel(
                    sample.speed_mps * 3.6 if sample.speed_mps is not None else None,
                    sample.validation_flags,
                    "speed_mps",
                ),
                "gear": _live_channel(
                    sample.gear, sample.validation_flags, "gear"
                ),
                "engine_rpm": _live_channel(
                    sample.engine_rpm,
                    sample.validation_flags,
                    "engine_rpm",
                ),
                "throttle": _live_channel(
                    sample.throttle, sample.validation_flags, "throttle"
                ),
                "brake": _live_channel(
                    sample.brake, sample.validation_flags, "brake"
                ),
            }
            latest_values = values
            if latest_status != "unsupported":
                latest_status = "fresh" if telemetry is not None else "unavailable"
                if telemetry is not None:
                    latest_reason = None
            if selected_receive_ns is None and latest_status == "fresh":
                latest_status = "unavailable"
                latest_reason = "receive_provenance_unavailable"

        if latest_values is not None:
            self._live_snapshot = latest_values
            self._live_status = latest_status
            self._live_reason = latest_reason
            self._live_received_monotonic_ns = selected_receive_ns
        elif matched_player_lap:
            self._live_snapshot = self._empty_live_snapshot(
                frame, self._live_player_index
            )
            self._live_status = latest_status
            self._live_reason = latest_reason
            self._live_received_monotonic_ns = selected_receive_ns
        else:
            if car_status_candidates and self._live_player_index is not None:
                selected_status_packets = [
                    candidate
                    for candidate, _decoded, _error in car_status_candidates
                    if candidate.header.player_car_index == self._live_player_index
                ]
                if selected_status_packets:
                    unsupported_status = any(
                        self._is_unsupported_adapter(candidate)
                        for candidate in selected_status_packets
                    )
                    self._set_live_car_status_unavailable(
                        frame,
                        self._live_player_index,
                        "same_frame_player_lap_missing",
                        selected_status_packets,
                        status="unsupported"
                        if unsupported_status
                        else "unavailable",
                    )
            unsupported = next(
                (
                    packet
                    for packet in frame.packets
                    if packet.header.player_car_index == self._live_player_index
                    and packet.packet_kind is PacketId.CAR_TELEMETRY
                    and self._is_unsupported_adapter(packet)
                ),
                None,
            )
            if unsupported is not None:
                self._live_snapshot = self._empty_live_snapshot(
                    frame, self._live_player_index
                )
                self._live_status = "unsupported"
                self._live_reason = "car_telemetry_adapter_unsupported"
                self._live_received_monotonic_ns = self._frame_receive_time(
                    frame, (unsupported,)
                )
        self._discard_frame_receive_times(frame)

    def _frame_receive_time(
        self, frame: PacketFrame, packets: list[DecodedPacket] | tuple[DecodedPacket, ...]
    ) -> int | None:
        times = [
            self._receive_times.get(
                (
                    frame.session_uid,
                    frame.overall_frame_identifier,
                    packet.wire_fingerprint,
                )
            )
            for packet in packets
            if packet.packet_kind
            in {
                PacketId.LAP_DATA,
                PacketId.CAR_TELEMETRY,
                PacketId.CAR_TELEMETRY_2,
                PacketId.CAR_STATUS,
            }
        ]
        if not times or any(value is None for value in times):
            return None
        return min(times)

    def _discard_frame_receive_times(self, frame: PacketFrame) -> None:
        for packet in frame.packets:
            if packet.packet_kind in {
                PacketId.LAP_DATA,
                PacketId.CAR_TELEMETRY,
                PacketId.CAR_TELEMETRY_2,
                PacketId.CAR_STATUS,
            }:
                self._receive_times.pop(
                    (
                        frame.session_uid,
                        frame.overall_frame_identifier,
                        packet.wire_fingerprint,
                    ),
                    None,
                )

    def _publish_live_car_status(
        self,
        frame: PacketFrame,
        lap_packet: DecodedPacket,
        sample: CarSample,
        *,
        status_reason: str | None,
        received_monotonic_ns: int | None,
        unsupported: bool,
    ) -> None:
        player_index = lap_packet.header.player_car_index
        snapshot = self._empty_live_car_status_snapshot(frame, player_index)
        for field in (
            "fuel_in_tank_reported",
            "fuel_remaining_laps",
            "actual_tyre_compound",
            "visual_tyre_compound",
            "tyre_age_laps",
            "front_brake_bias_percent",
            "pit_limiter_active",
        ):
            snapshot[field] = getattr(sample, field)
        snapshot["validation_flags"] = [
            flag
            for flag in sample.validation_flags
            if flag
            in {
                "invalid_car_status_fuel_in_tank_reported",
                "invalid_car_status_fuel_remaining_laps",
                "invalid_car_status_front_brake_bias_percent",
                "invalid_car_status_pit_limiter_active",
            }
        ]
        self._live_car_status_snapshot = snapshot
        self._live_car_status_received_monotonic_ns = received_monotonic_ns
        if getattr(sample, "car_status_available") is True:
            if received_monotonic_ns is None:
                self._live_car_status = "unavailable"
                self._live_car_status_reason = "receive_provenance_unavailable"
            else:
                self._live_car_status = "fresh"
                self._live_car_status_reason = None
        else:
            self._live_car_status = "unsupported" if unsupported else "unavailable"
            self._live_car_status_reason = (
                status_reason or "status_packet_unavailable"
            )

    def _observe_live_lap_timing(
        self, frame: PacketFrame, lap_packets: list[DecodedPacket]
    ) -> None:
        player_index = self._live_player_index
        if player_index is None:
            return
        selected = [
            packet
            for packet in lap_packets
            if packet.header.player_car_index == player_index
        ]
        if not selected:
            return

        decoded: list[tuple[DecodedPacket, CarLapData, tuple[int, ...]]] = []
        failures: list[tuple[DecodedPacket, bool]] = []
        for packet in selected:
            result = self.lap_data_decoder.decode(packet)
            if result.error is not None or result.lap_data is None:
                failures.append((packet, self._is_unsupported_adapter(packet)))
                continue
            if not 0 <= player_index < len(result.lap_data.cars):
                self._set_live_lap_timing_unavailable(
                    frame,
                    player_index,
                    "player_car_index_out_of_range",
                    [packet],
                )
                return
            car = result.lap_data.cars[player_index]
            timing_fields = (
                car.current_lap_number,
                car.current_lap_time_ms,
                car.sector_id,
                car.last_lap_time_ms,
                car.sector1_time_ms,
                car.sector2_time_ms,
            )
            decoded.append((packet, car, timing_fields))

        if failures:
            unsupported = any(is_unsupported for _packet, is_unsupported in failures)
            self._set_live_lap_timing_unavailable(
                frame,
                player_index,
                "lap_data_adapter_unsupported"
                if unsupported
                else "lap_data_decode_failed",
                [packet for packet, _is_unsupported in failures],
                status="unsupported" if unsupported else "unavailable",
            )
            return

        if len({timing_fields for _packet, _car, timing_fields in decoded}) > 1:
            self._set_live_lap_timing_unavailable(
                frame,
                player_index,
                "conflicting_lap_data_packets",
                [packet for packet, _car, _timing_fields in decoded],
            )
            return

        # If the frame contains wire-distinct updates but identical selected-player
        # timing, use the latest one for the snapshot and its exact receive provenance.
        packet, car, _timing_fields = decoded[-1]
        self._publish_live_lap_timing(frame, packet, car)

    def _publish_live_lap_timing(
        self, frame: PacketFrame, packet: DecodedPacket, car: CarLapData
    ) -> None:
        sector_code = car.sector_id
        valid_sector = sector_code in {0, 1, 2}
        received_ns = self._frame_receive_time(frame, [packet])
        self._live_lap_timing_snapshot = {
            "session_uid": str(frame.session_uid),
            "frame_identifier": frame.overall_frame_identifier,
            "packet_format": int(packet.packet_format),
            "player_car_index": packet.header.player_car_index,
            "lap_number": (
                car.current_lap_number if car.current_lap_number > 0 else None
            ),
            "current_lap_time_ms": _reported_positive_ms(car.current_lap_time_ms),
            "current_sector": sector_code + 1 if valid_sector else None,
            "previous_lap_time_ms": _reported_positive_ms(car.last_lap_time_ms),
            "sector1_time_ms": _reported_positive_ms(car.sector1_time_ms),
            "sector2_time_ms": _reported_positive_ms(car.sector2_time_ms),
            "validation_flags": [] if valid_sector else ["invalid_current_sector"],
        }
        self._live_lap_timing_received_monotonic_ns = received_ns
        if received_ns is None:
            self._live_lap_timing = "unavailable"
            self._live_lap_timing_reason = "receive_provenance_unavailable"
        else:
            self._live_lap_timing = "fresh"
            self._live_lap_timing_reason = None

    def _set_live_lap_timing_unavailable(
        self,
        frame: PacketFrame,
        player_index: int,
        reason: str,
        selected_packets: list[DecodedPacket],
        *,
        status: str = "unavailable",
    ) -> None:
        self._live_lap_timing_snapshot = self._empty_live_lap_timing_snapshot(
            frame, player_index
        )
        self._live_lap_timing = status
        self._live_lap_timing_reason = reason
        self._live_lap_timing_received_monotonic_ns = self._frame_receive_time(
            frame, selected_packets
        )

    def _reset_live_lap_timing_monitor(self) -> None:
        self._live_lap_timing = "waiting"
        self._live_lap_timing_reason = None
        self._live_lap_timing_snapshot = None
        self._live_lap_timing_received_monotonic_ns = None

    @staticmethod
    def _empty_live_lap_timing_snapshot(
        frame: PacketFrame, player_index: int
    ) -> dict[str, object]:
        return {
            "session_uid": str(frame.session_uid),
            "frame_identifier": frame.overall_frame_identifier,
            "packet_format": int(frame.packets[-1].packet_format),
            "player_car_index": player_index,
            "lap_number": None,
            "current_lap_time_ms": None,
            "current_sector": None,
            "previous_lap_time_ms": None,
            "sector1_time_ms": None,
            "sector2_time_ms": None,
            "validation_flags": [],
        }

    def _set_live_car_status_unavailable(
        self,
        frame: PacketFrame,
        player_index: int,
        reason: str,
        selected_packets: list[DecodedPacket],
        *,
        status: str = "unavailable",
    ) -> None:
        self._live_car_status_snapshot = self._empty_live_car_status_snapshot(
            frame, player_index
        )
        self._live_car_status = status
        self._live_car_status_reason = reason
        self._live_car_status_received_monotonic_ns = self._frame_receive_time(
            frame, selected_packets
        )

    def _reset_live_car_status_monitor(self) -> None:
        self._live_car_status = "waiting"
        self._live_car_status_reason = None
        self._live_car_status_snapshot = None
        self._live_car_status_received_monotonic_ns = None

    def _clear_live_car_status_sample(self) -> None:
        self._reset_live_car_status_monitor()

    @staticmethod
    def _empty_live_car_status_snapshot(
        frame: PacketFrame, player_index: int
    ) -> dict[str, object]:
        return {
            "session_uid": str(frame.session_uid),
            "frame_identifier": frame.overall_frame_identifier,
            "packet_format": int(frame.packets[-1].packet_format),
            "player_car_index": player_index,
            "fuel_in_tank_reported": None,
            "fuel_remaining_laps": None,
            "actual_tyre_compound": None,
            "visual_tyre_compound": None,
            "tyre_age_laps": None,
            "front_brake_bias_percent": None,
            "pit_limiter_active": None,
            "validation_flags": [],
        }

    @staticmethod
    def _empty_live_snapshot(
        frame: PacketFrame, player_index: int
    ) -> dict[str, object]:
        return {
            "session_uid": str(frame.session_uid),
            "frame_identifier": frame.overall_frame_identifier,
            "packet_format": int(frame.packets[-1].packet_format),
            "player_car_index": player_index,
            "lap_number": None,
            "lap_time_ms": None,
            "game_invalid": None,
            "pit_status_id": None,
            "driver_status_id": None,
            "speed_kph": None,
            "gear": None,
            "engine_rpm": None,
            "throttle": None,
            "brake": None,
        }

    def _clear_live_sample(self) -> None:
        self._live_status = "waiting"
        self._live_reason = None
        self._live_snapshot = None
        self._live_received_monotonic_ns = None
        self._clear_live_car_status_sample()
        self._reset_live_lap_timing_monitor()

    @classmethod
    def _is_newer_frame(cls, candidate: int, current: int) -> bool:
        distance = (candidate - current) & cls._FRAME_MASK
        return 0 < distance < cls._SERIAL_HALF_RANGE

    def _is_unsupported_adapter(self, packet: DecodedPacket) -> bool:
        if packet.packet_kind is PacketId.LAP_DATA:
            return not self.lap_data_decoder.supports(
                packet.packet_format,
                PacketId.LAP_DATA,
                packet.header.packet_version,
            )
        if packet.packet_kind is PacketId.CAR_TELEMETRY:
            return not self.car_telemetry_decoder.supports(
                packet.packet_format,
                PacketId.CAR_TELEMETRY,
                packet.header.packet_version,
            )
        if packet.packet_kind is PacketId.CAR_STATUS:
            return not self.car_status_decoder.supports(
                packet.packet_format,
                PacketId.CAR_STATUS,
                packet.header.packet_version,
            )
        return False


    def _count_completed(self, frames: tuple[object, ...]) -> None:
        self.counts["completed_frames"] += len(frames)


# Preserve the historical internal name used by existing tests and helpers.
_AcquisitionObserver = AcquisitionObserver


async def record_udp_capture(
    output: str | Path,
    *,
    host: str = "0.0.0.0",
    port: int = 20777,
    queue_size: int = 8192,
    duration: float | None = None,
    overwrite: bool = False,
    collect_inventory: bool = True,
    on_snapshot: Callable[[RecordingSnapshot], None] | None = None,
    on_event: Callable[[dict[str, object]], None] | None = None,
) -> RecordingResult:
    """Capture raw UDP first, then process a copy through the shared pipeline."""
    output_path = Path(output)
    source = UDPSource(host=host, port=port, queue_size=queue_size)
    pipeline = TelemetryPipeline() if collect_inventory else None
    observer = None if collect_inventory else AcquisitionObserver()
    counts: Counter[str] = Counter(completed_frames=0)
    session_contexts: dict[int, SessionContext] = {}
    latest_context: dict[str, object] | None = None
    started = time.monotonic()
    writer: CaptureWriter | None = None
    writer_executor: ThreadPoolExecutor | None = None
    operation_error: Exception | None = None
    capture_status: str | None = None
    state = "starting"
    loop = asyncio.get_running_loop()
    deadline = started + duration if duration is not None else None

    def snapshot() -> RecordingSnapshot:
        return RecordingSnapshot(
            state=state,
            elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
            received=source.stats.received,
            queued=source.stats.queued,
            recorded=counts["recorded"],
            queue_dropped=source.stats.dropped,
            socket_errors=source.stats.socket_errors,
            latest_context=latest_context,
            live_telemetry=(
                observer.live_telemetry_snapshot()
                if observer is not None
                else {
                    "status": "unavailable",
                    "reason": "live_monitor_not_enabled",
                    "age_ms": None,
                }
            ),
            live_car_status=(
                observer.live_car_status_snapshot()
                if observer is not None
                else {
                    "status": "unavailable",
                    "reason": "live_monitor_not_enabled",
                    "age_ms": None,
                }
            ),
            live_lap_timing=(
                observer.live_lap_timing_snapshot()
                if observer is not None
                else {
                    "status": "unavailable",
                    "reason": "live_monitor_not_enabled",
                    "age_ms": None,
                }
            ),
        )

    def notify_snapshot() -> None:
        if on_snapshot is not None:
            try:
                on_snapshot(snapshot())
            except Exception:
                # Status reporting must never interrupt raw capture.
                pass

    def notify_event(event: dict[str, object]) -> None:
        if on_event is not None:
            try:
                on_event(event)
            except Exception:
                pass

    def process(raw: RawDatagram) -> None:
        nonlocal latest_context
        if observer is not None:
            observer.process(raw)
            latest_context = observer.latest_context
            return
        assert pipeline is not None
        try:
            result = pipeline.process(raw)
        except ProtocolError:
            counts["unrecognized_or_malformed"] += 1
            return
        counts[f"format_{result.packet.packet_format.value}"] += 1
        counts["completed_frames"] += len(result.completed_frames)
        if any(
            event.kind in {"session_started", "session_context_invalidated"}
            for event in result.session_events
        ):
            latest_context = None
        _report_session_events(result, notify_event)
        _collect_session_context(counts, session_contexts, result)
        if result.session_context is not None:
            latest_context = result.session_context.to_dict()
            notify_event(
                {
                    "event": "session_context_updated",
                    "context": result.session_context.to_dict(),
                }
            )

    async def persist(raw: RawDatagram) -> None:
        assert writer is not None and writer_executor is not None
        pending_write = loop.run_in_executor(writer_executor, writer.write, raw)
        try:
            await asyncio.shield(pending_write)
        except asyncio.CancelledError:
            # Do not lose a datagram already removed from the receive queue.
            await pending_write
            counts["recorded"] += 1
            notify_snapshot()
            raise
        counts["recorded"] += 1
        notify_snapshot()

    try:
        await source.open()
        writer = CaptureWriter(
            output_path,
            metadata={
                "application": "f1-race-engineer",
                "application_version": __version__,
                "bind_host": host,
                "bind_port": port,
            },
            overwrite=overwrite,
        )
        writer_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="f1-capture"
        )
        state = "recording"
        notify_snapshot()
        packet_iterator = source.packets().__aiter__()
        while True:
            try:
                if deadline is None:
                    raw = await packet_iterator.__anext__()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    raw = await asyncio.wait_for(
                        packet_iterator.__anext__(), timeout=remaining
                    )
            except (StopAsyncIteration, asyncio.TimeoutError):
                break

            # Persist before decoding so malformed or unsupported datagrams survive.
            await persist(raw)
            process(raw)
            notify_snapshot()
    except asyncio.CancelledError:
        state = "stopping"
        notify_snapshot()
    except Exception as exc:
        operation_error = exc
        state = "stopping"
        notify_snapshot()
    finally:
        source.close()
        pending = source.drain_pending()
        if writer is not None and writer_executor is not None:
            for raw in pending:
                pending_write = loop.run_in_executor(writer_executor, writer.write, raw)
                try:
                    await asyncio.shield(pending_write)
                except Exception as exc:
                    operation_error = operation_error or exc
                    break
                counts["recorded"] += 1
                process(raw)
                notify_snapshot()
            counts["unpersisted_on_shutdown"] = max(
                0, source.stats.queued - counts["recorded"]
            )
            counts.setdefault("recorded", 0)
            counts["received"] = source.stats.received
            counts["queue_dropped"] = source.stats.dropped
            counts["socket_errors"] = source.stats.socket_errors
            if pipeline is not None:
                counts["open_frames_flushed"] = len(pipeline.finish())
                _add_frame_stats(counts, pipeline)
                _add_lap_stats(counts, pipeline)
            else:
                assert observer is not None
                observer.finish()
                _copy_counts(counts, observer.counts)
            counts["elapsed_ms"] = max(0, int((time.monotonic() - started) * 1000))
            capture_status = (
                "complete"
                if operation_error is None
                and counts["unpersisted_on_shutdown"] == 0
                else "incomplete"
            )
            footer = {"status": capture_status, **dict(counts)}
            try:
                pending_close = loop.run_in_executor(
                    writer_executor, writer.close, footer
                )
                await asyncio.shield(pending_close)
            except Exception as exc:
                operation_error = operation_error or exc
            finally:
                writer_executor.shutdown(wait=True)
        elif writer is not None:
            try:
                writer.close(
                    {"status": "incomplete", "reason": "capture worker unavailable"}
                )
            except Exception as exc:
                operation_error = operation_error or exc

    counts["received"] = source.stats.received
    counts.setdefault("recorded", 0)
    counts.setdefault("queue_dropped", source.stats.dropped)
    counts.setdefault("socket_errors", source.stats.socket_errors)
    counts.setdefault("unpersisted_on_shutdown", 0)
    if pipeline is not None:
        counts.setdefault("open_frames_flushed", len(pipeline.finish()))
        _add_frame_stats(counts, pipeline)
        _add_lap_stats(counts, pipeline)
    elif observer is not None:
        observer.finish()
        _copy_counts(counts, observer.counts)
    counts.setdefault("elapsed_ms", max(0, int((time.monotonic() - started) * 1000)))
    state = (
        "complete"
        if operation_error is None and capture_status == "complete"
        else "failed"
    )
    notify_snapshot()
    if operation_error is not None:
        raise operation_error
    return RecordingResult(
        output=output_path,
        summary=dict(counts),
        session_contexts=tuple(
            session_contexts[uid].to_dict() for uid in sorted(session_contexts)
        ),
        lap_attempts=(
            tuple(attempt.to_dict() for attempt in pipeline.laps.attempts)
            if pipeline is not None
            else ()
        ),
        capture_status=capture_status or "incomplete",
    )


def _report_session_events(
    result: PipelineResult, callback: Callable[[dict[str, object]], None]
) -> None:
    for event in result.session_events:
        callback(
            {
                "event": event.kind,
                "session_uid": event.session_uid,
                "previous_session_uid": event.previous_session_uid,
            }
        )


def _copy_counts(destination: Counter[str], source: Counter[str]) -> None:
    for key, value in source.items():
        destination[key] = value


def _collect_session_context(
    counts: Counter[str],
    contexts: dict[int, SessionContext],
    result: PipelineResult,
) -> None:
    for event in result.session_events:
        if event.kind == "session_context_invalidated":
            counts["session_context_invalidations"] += 1
            contexts.pop(event.session_uid, None)
    if result.session_context_error is not None:
        counts["session_context_decode_errors"] += 1
    if result.session_context is not None:
        counts["session_context_updates"] += 1
        contexts[result.session_context.session_uid] = result.session_context


def _add_frame_stats(counts: Counter[str], pipeline: TelemetryPipeline) -> None:
    counts["duplicate_envelopes_ignored"] = pipeline.frames.duplicates_ignored
    counts["late_packets_ignored"] = pipeline.frames.late_packets_ignored
    counts["frame_overflow_packets_dropped"] = pipeline.frames.overflow_packets_dropped


def _add_lap_stats(counts: Counter[str], pipeline: TelemetryPipeline) -> None:
    attempts = pipeline.laps.attempts
    counts["lap_data_packets_decoded"] = pipeline.lap_data_packets_decoded
    counts["lap_data_decode_errors"] = len(pipeline.lap_data_decode_errors)
    counts["lap_attempts"] = len(attempts)
    counts["completed_lap_attempts"] = sum(
        attempt.disposition.value == "completed" for attempt in attempts
    )
    counts["invalid_lap_attempts"] = sum(
        attempt.game_valid is False for attempt in attempts
    )
    counts["partial_lap_attempts"] = sum(
        attempt.disposition.value == "partial" for attempt in attempts
    )
    counts["abandoned_lap_attempts"] = sum(
        attempt.disposition.value == "abandoned" for attempt in attempts
    )
    counts["reference_eligible_laps"] = sum(
        attempt.reference_eligible for attempt in attempts
    )
    counts["motion_packets_decoded"] = pipeline.motion_packets_decoded
    counts["motion_decode_errors"] = len(pipeline.motion_decode_errors)
    counts["player_motion_samples"] = pipeline.player_motion_samples
    counts["missing_player_motion_samples"] = pipeline.missing_player_motion_samples


def _live_channel(
    value: object, validation_flags: tuple[str, ...], channel: str
) -> object | None:
    return None if f"invalid_{channel}" in validation_flags else value


def _reported_positive_ms(value: int) -> int | None:
    return value if value > 0 else None
