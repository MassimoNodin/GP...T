from dataclasses import replace

import pytest

from f1_engineer.sessions.manager import SessionTracker
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.session_context import SessionContextDecoder
from tests.test_session_context import _recorded_packet


@pytest.mark.parametrize("origin", [0, 0xFFFFFFF0])
def test_indexed_context_queries_match_serial_history(origin):
    context = SessionContextDecoder().decode(PacketDecoder().decode(_recorded_packet())).context
    tracker = SessionTracker()
    tracker.current_session_uid = context.session_uid
    history = [
        ((origin + offset) & 0xFFFFFFFF, value)
        for offset, value in [
            (0, None), (5, context), (5, replace(context, weather_id=2)),
            (10, None), (15, context), (25, replace(context, track_id=17)),
        ]
    ]
    tracker._context_history_by_uid[context.session_uid] = history
    for start_offset in [-2, 0, 3, 5, 6, 10, 11, 15, 24, 25, 30, 0x80000005]:
        start = (origin + start_offset) & 0xFFFFFFFF
        expected_context, expected_frame = None, None
        for frame, value in history:
            if not tracker._is_not_older(start, frame):
                break
            expected_context = value
            expected_frame = frame if value is not None else None
        assert tracker.context_at(start) == (expected_context, expected_frame)
        for end_offset in [-2, 0, 5, 10, 20, 25, 30, 0x80000015]:
            end = (origin + end_offset) & 0xFFFFFFFF
            expected = [(start, expected_context)]
            expected.extend(
                (frame, value) for frame, value in history
                if frame != start and tracker._is_newer(frame, start)
                and tracker._is_not_older(end, frame)
            )
            assert tracker.context_timeline(start, end) == tuple(expected)


def test_context_lookup_does_not_scan_entire_history(monkeypatch):
    tracker = SessionTracker()
    tracker.current_session_uid = 1
    tracker._context_history_by_uid[1] = [(frame, None) for frame in range(8192)]
    comparisons = []
    original = tracker._is_not_older

    def counted(candidate, current):
        comparisons.append((candidate, current))
        return original(candidate, current)

    monkeypatch.setattr(tracker, "_is_not_older", counted)
    assert tracker.context_timeline(8000, 8002) == ((8000, None), (8001, None), (8002, None))
    assert len(comparisons) < 50
