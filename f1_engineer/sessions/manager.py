from __future__ import annotations

from collections import OrderedDict
from functools import cmp_to_key

from ..udp.models import DecodedPacket, PacketFormat, SessionEvent
from .context import SessionContext

_FRAME_MASK = 0xFFFFFFFF
_SERIAL_HALF_RANGE = 0x80000000


class SessionTracker:
    def __init__(self, reorder_window_frames: int = 3) -> None:
        if not 1 <= reorder_window_frames < _SERIAL_HALF_RANGE:
            raise ValueError("reorder_window_frames must be a positive 32-bit frame distance")
        self.reorder_window_frames = reorder_window_frames
        self.current_session_uid: int | None = None
        self.current_packet_format: PacketFormat | None = None
        self._current_packet_frame_identifier: int | None = None
        self._format_change_frame_identifier: int | None = None
        self._current_context: SessionContext | None = None
        self._current_context_frame_identifier: int | None = None
        self._context_history_by_uid: OrderedDict[
            int, list[tuple[int, SessionContext | None]]
        ] = OrderedDict()
        self._retired_uids: OrderedDict[int, None] = OrderedDict()

    def observe(self, packet: DecodedPacket) -> tuple[SessionEvent, ...]:
        uid = packet.header.session_uid
        if uid == 0:
            return ()
        if self.current_session_uid == uid:
            frame_identifier = packet.header.overall_frame_identifier & _FRAME_MASK
            previous_frame = self._current_packet_frame_identifier
            frame_is_newer = previous_frame is None or self._is_newer(
                frame_identifier, previous_frame
            )
            if packet.packet_format is not self.current_packet_format:
                # Do not let a delayed packet from an earlier wire format invalidate
                # context decoded from a newer format in this same game session.
                if not frame_is_newer:
                    return ()
                self.current_packet_format = packet.packet_format
                self._current_packet_frame_identifier = frame_identifier
                self._format_change_frame_identifier = frame_identifier
                self._current_context = None
                self._current_context_frame_identifier = None
                self._append_context_history(uid, frame_identifier, None)
                return (SessionEvent("session_context_invalidated", uid),)
            if previous_frame is None or self._is_not_older(
                frame_identifier, previous_frame
            ):
                self._current_packet_frame_identifier = frame_identifier
            return ()
        if uid in self._retired_uids:
            # UDP can deliver a delayed datagram after a session transition.
            return ()

        previous = self.current_session_uid
        self.current_session_uid = uid
        self.current_packet_format = packet.packet_format
        self._current_packet_frame_identifier = (
            packet.header.overall_frame_identifier & _FRAME_MASK
        )
        self._format_change_frame_identifier = None
        self._current_context = None
        self._current_context_frame_identifier = None
        self._context_history_by_uid[uid] = [
            (packet.header.overall_frame_identifier & _FRAME_MASK, None)
        ]
        self._context_history_by_uid.move_to_end(uid)
        while len(self._context_history_by_uid) > 128:
            self._context_history_by_uid.popitem(last=False)
        events: list[SessionEvent] = []
        if previous is not None:
            self._retired_uids[previous] = None
            self._retired_uids.move_to_end(previous)
            while len(self._retired_uids) > 128:
                self._retired_uids.popitem(last=False)
            events.append(SessionEvent("session_ended", previous))
        events.append(SessionEvent("session_started", uid, previous))
        return tuple(events)

    def is_retired(self, session_uid: int) -> bool:
        return session_uid in self._retired_uids

    @staticmethod
    def _is_newer(candidate: int, current: int) -> bool:
        distance = (candidate - current) & _FRAME_MASK
        return 0 < distance < _SERIAL_HALF_RANGE

    @staticmethod
    def _is_not_older(candidate: int, current: int) -> bool:
        distance = (candidate - current) & _FRAME_MASK
        return distance == 0 or distance < _SERIAL_HALF_RANGE

    @property
    def current_context(self) -> SessionContext | None:
        return self._current_context

    @property
    def current_context_frame_identifier(self) -> int | None:
        return self._current_context_frame_identifier

    def context_at(
        self, frame_identifier: int, session_uid: int | None = None
    ) -> tuple[SessionContext | None, int | None]:
        """Return the newest known context at or before an overall frame."""
        frame_identifier &= _FRAME_MASK
        uid = self.current_session_uid if session_uid is None else session_uid
        context: SessionContext | None = None
        context_frame: int | None = None
        for update_frame, update_context in self._context_history_by_uid.get(uid or 0, ()):
            if not self._is_not_older(frame_identifier, update_frame):
                break
            context = update_context
            context_frame = update_frame if update_context is not None else None
        return context, context_frame

    def context_timeline(
        self,
        start_frame_identifier: int,
        end_frame_identifier: int,
        session_uid: int | None = None,
    ) -> tuple[tuple[int, SessionContext | None], ...]:
        """Return the context at the start frame and every later change through end."""
        start = start_frame_identifier & _FRAME_MASK
        end = end_frame_identifier & _FRAME_MASK
        uid = self.current_session_uid if session_uid is None else session_uid
        initial_context, _ = self.context_at(start, session_uid=uid)
        changes: list[tuple[int, SessionContext | None]] = [(start, initial_context)]
        for update_frame, update_context in self._context_history_by_uid.get(uid or 0, ()):
            if update_frame == start or not self._is_newer(update_frame, start):
                continue
            if self._is_not_older(end, update_frame):
                changes.append((update_frame, update_context))
        return tuple(changes)

    def update_context(self, context: SessionContext, frame_identifier: int) -> bool:
        """Apply a non-stale context update for the active session.

        Returns true only when the semantic context changed. Context packet frame IDs
        use the same wrapping 32-bit serial arithmetic as the frame assembler.
        """
        if (
            context.session_uid == 0
            or context.session_uid != self.current_session_uid
            or self.is_retired(context.session_uid)
            or context.packet_format is not self.current_packet_format
        ):
            return False

        frame_identifier &= _FRAME_MASK
        format_change_frame = self._format_change_frame_identifier
        if format_change_frame is not None and not self._is_not_older(
            frame_identifier, format_change_frame
        ):
            return False
        packet_watermark = self._current_packet_frame_identifier
        if packet_watermark is not None and not self._is_not_older(
            frame_identifier, packet_watermark
        ):
            distance_behind = (packet_watermark - frame_identifier) & _FRAME_MASK
            if distance_behind >= self.reorder_window_frames:
                return False
        previous_frame = self._current_context_frame_identifier
        is_historical = previous_frame is not None and not self._is_not_older(
            frame_identifier, previous_frame
        )
        if is_historical:
            distance_behind = (previous_frame - frame_identifier) & _FRAME_MASK
            if distance_behind >= self.reorder_window_frames:
                return False
        else:
            self._current_context_frame_identifier = frame_identifier
            semantic_change = context != self._current_context
            self._current_context = context
        self._append_context_history(context.session_uid, frame_identifier, context)
        return False if is_historical else semantic_change

    def _append_context_history(
        self,
        session_uid: int,
        frame_identifier: int,
        context: SessionContext | None,
    ) -> None:
        history = self._context_history_by_uid.setdefault(session_uid, [])
        event = (frame_identifier, context)
        if event in history:
            return
        if (
            context is not None
            and len(history) == 1
            and history[0][1] is None
            and self._is_newer(history[0][0], frame_identifier)
        ):
            # A delayed first Session packet can predate the packet that caused
            # this UID to be noticed. That initial unknown marker is only a
            # baseline and must not erase the earlier decoded context afterward.
            history.clear()
        history.append((frame_identifier, context))

        def compare(
            left: tuple[int, SessionContext | None],
            right: tuple[int, SessionContext | None],
        ) -> int:
            if left[0] == right[0]:
                return 0
            return 1 if self._is_newer(left[0], right[0]) else -1

        history.sort(key=cmp_to_key(compare))
        self._context_history_by_uid.move_to_end(session_uid)
