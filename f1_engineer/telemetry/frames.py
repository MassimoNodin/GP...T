from __future__ import annotations

from collections import OrderedDict

from ..udp.models import DecodedPacket, PacketFrame

_FRAME_MASK = 0xFFFFFFFF
_SERIAL_HALF_RANGE = 0x80000000


class FrameAssembler:
    """Group envelopes and emit frames after a small out-of-order allowance."""

    def __init__(
        self,
        max_open_frames: int = 256,
        max_pending_packets: int = 8192,
        reorder_window_frames: int = 3,
        max_tracked_sessions: int = 128,
    ) -> None:
        if max_open_frames < 1 or max_pending_packets < 1:
            raise ValueError("frame and packet bounds must be at least 1")
        if reorder_window_frames < 1 or reorder_window_frames >= _SERIAL_HALF_RANGE:
            raise ValueError("reorder_window_frames must be a positive 32-bit frame distance")
        if max_tracked_sessions < 1:
            raise ValueError("max_tracked_sessions must be at least 1")
        self.max_open_frames = max_open_frames
        self.max_pending_packets = max_pending_packets
        self.reorder_window_frames = reorder_window_frames
        self.max_tracked_sessions = max_tracked_sessions
        self._open: OrderedDict[
            tuple[int, int], dict[bytes, DecodedPacket]
        ] = OrderedDict()
        self._closed: OrderedDict[tuple[int, int], None] = OrderedDict()
        self._max_closed_keys = max(64, max_open_frames * 4)
        self._pending_packets = 0
        self.duplicates_ignored = 0
        self.late_packets_ignored = 0
        self.overflow_packets_dropped = 0
        self._latest_frame_by_session: OrderedDict[int, int] = OrderedDict()
        self._last_emitted_frame_by_session: dict[int, int] = {}

    @staticmethod
    def _frame(key: tuple[int, int], packets: dict[bytes, DecodedPacket]) -> PacketFrame:
        return PacketFrame(key[0], key[1], tuple(packets.values()))

    def _eviction_key(self) -> tuple[int, int]:
        """Choose the oldest pending frame in the earliest session group."""
        first_uid = next(iter(self._open))[0]
        keys = [key for key in self._open if key[0] == first_uid]
        latest = self._latest_frame_by_session.get(first_uid)
        if latest is not None:
            keys.sort(
                key=lambda key: self._distance_behind(latest, key[1]) or 0,
                reverse=True,
            )
        return keys[0]

    def _mark_emitted(self, key: tuple[int, int]) -> None:
        self._remember_closed(key)
        self._last_emitted_frame_by_session[key[0]] = key[1]

    def _evict_oldest(self) -> tuple[tuple[int, int], PacketFrame]:
        key = self._eviction_key()
        packets = self._open.pop(key)
        self._pending_packets -= len(packets)
        self._mark_emitted(key)
        return key, self._frame(key, packets)

    def _remember_closed(self, key: tuple[int, int]) -> None:
        self._closed[key] = None
        self._closed.move_to_end(key)
        while len(self._closed) > self._max_closed_keys:
            self._closed.popitem(last=False)

    @staticmethod
    def _is_newer(candidate: int, current: int) -> bool:
        distance = (candidate - current) & _FRAME_MASK
        return 0 < distance < _SERIAL_HALF_RANGE

    @staticmethod
    def _distance_behind(latest: int, candidate: int) -> int | None:
        distance = (latest - candidate) & _FRAME_MASK
        if 0 < distance < _SERIAL_HALF_RANGE:
            return distance
        return None

    def _flush_behind_watermark(self, session_uid: int, latest: int) -> list[PacketFrame]:
        completed: list[PacketFrame] = []
        ready: list[tuple[int, int]] = []
        for key in self._open:
            if key[0] != session_uid:
                continue
            distance = self._distance_behind(latest, key[1])
            if distance is not None and distance >= self.reorder_window_frames:
                ready.append(key)
        ready.sort(
            key=lambda key: self._distance_behind(latest, key[1]) or 0,
            reverse=True,
        )
        for key in ready:
            packets = self._open.pop(key)
            self._pending_packets -= len(packets)
            self._mark_emitted(key)
            completed.append(self._frame(key, packets))
        return completed

    def retire_session(self, session_uid: int) -> tuple[PacketFrame, ...]:
        completed: list[PacketFrame] = []
        latest = self._latest_frame_by_session.get(session_uid)
        keys = [key for key in self._open if key[0] == session_uid]
        if latest is not None:
            keys.sort(
                key=lambda key: self._distance_behind(latest, key[1]) or 0,
                reverse=True,
            )
        for key in keys:
            packets = self._open.pop(key)
            self._pending_packets -= len(packets)
            self._mark_emitted(key)
            completed.append(self._frame(key, packets))
        self._latest_frame_by_session.pop(session_uid, None)
        self._last_emitted_frame_by_session.pop(session_uid, None)
        return tuple(completed)

    def flush_session(self, session_uid: int) -> tuple[PacketFrame, ...]:
        """Emit pending frames for one session in order without retiring it."""
        keys = [key for key in self._open if key[0] == session_uid]
        latest = self._latest_frame_by_session.get(session_uid)
        if latest is not None:
            keys.sort(
                key=lambda key: self._distance_behind(latest, key[1]) or 0,
                reverse=True,
            )
        frames: list[PacketFrame] = []
        for key in keys:
            packets = self._open.pop(key)
            self._pending_packets -= len(packets)
            self._mark_emitted(key)
            frames.append(self._frame(key, packets))
        return tuple(frames)

    def add(self, packet: DecodedPacket) -> tuple[PacketFrame, ...]:
        key = (packet.header.session_uid, packet.header.overall_frame_identifier)
        completed: list[PacketFrame] = []
        latest = self._latest_frame_by_session.get(key[0])
        last_emitted = self._last_emitted_frame_by_session.get(key[0])
        if last_emitted is not None and not self._is_newer(key[1], last_emitted):
            self.late_packets_ignored += 1
            return ()
        if latest is None or self._is_newer(key[1], latest):
            latest = key[1]
            self._latest_frame_by_session[key[0]] = latest
            self._latest_frame_by_session.move_to_end(key[0])
            completed.extend(self._flush_behind_watermark(key[0], latest))
            while len(self._latest_frame_by_session) > self.max_tracked_sessions:
                retired_uid = next(iter(self._latest_frame_by_session))
                completed.extend(self.retire_session(retired_uid))
        elif self._distance_behind(latest, key[1]) is not None and (
            self._distance_behind(latest, key[1]) >= self.reorder_window_frames
        ):
            self.late_packets_ignored += 1
            return tuple(completed)
        else:
            self._latest_frame_by_session.move_to_end(key[0])

        if key in self._closed:
            self.late_packets_ignored += 1
            return tuple(completed)
        bucket = self._open.get(key)
        if bucket is None:
            while len(self._open) >= self.max_open_frames:
                eviction_key = self._eviction_key()
                if eviction_key[0] == key[0] and not self._is_newer(
                    key[1], eviction_key[1]
                ):
                    self.overflow_packets_dropped += 1
                    return tuple(completed)
                _, frame = self._evict_oldest()
                completed.append(frame)
                last_emitted = self._last_emitted_frame_by_session.get(key[0])
                if last_emitted is not None and not self._is_newer(
                    key[1], last_emitted
                ):
                    self.overflow_packets_dropped += 1
                    return tuple(completed)
            bucket = {}
            self._open[key] = bucket

        # The same packet ID can carry multiple events or per-car updates in one frame.
        # Only suppress byte-identical envelopes.
        packet_key = packet.wire_fingerprint
        if packet_key in bucket:
            self.duplicates_ignored += 1
            return tuple(completed)

        while self._pending_packets >= self.max_pending_packets:
            eviction_key = self._eviction_key()
            if eviction_key == key:
                if bucket:
                    _, frame = self._evict_oldest()
                    completed.append(frame)
                else:
                    self._open.pop(key)
                self.overflow_packets_dropped += 1
                return tuple(completed)
            if eviction_key[0] == key[0] and not self._is_newer(
                key[1], eviction_key[1]
            ):
                self.overflow_packets_dropped += 1
                return tuple(completed)
            _, frame = self._evict_oldest()
            completed.append(frame)
            if key not in self._open:
                self.overflow_packets_dropped += 1
                return tuple(completed)
        bucket[packet_key] = packet
        self._pending_packets += 1

        return tuple(completed)

    def flush(self) -> tuple[PacketFrame, ...]:
        grouped: OrderedDict[int, list[tuple[int, int]]] = OrderedDict()
        for key in self._open:
            grouped.setdefault(key[0], []).append(key)
        ordered_keys: list[tuple[int, int]] = []
        for session_uid, keys in grouped.items():
            latest = self._latest_frame_by_session.get(session_uid)
            if latest is not None:
                keys.sort(
                    key=lambda key: self._distance_behind(latest, key[1]) or 0,
                    reverse=True,
                )
            ordered_keys.extend(keys)
        frames = tuple(self._frame(key, self._open[key]) for key in ordered_keys)
        for key in self._open:
            self._mark_emitted(key)
        self._open.clear()
        self._pending_packets = 0
        self._latest_frame_by_session.clear()
        self._last_emitted_frame_by_session.clear()
        return frames
