from __future__ import annotations

import asyncio
import socket
import struct
import time
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from f1_engineer.api.app import create_app
from f1_engineer.recording.service import _AcquisitionObserver
from f1_engineer.recording.capture import CaptureReader
from tests.helpers import make_datagram
from tests.test_lap_tracking import SESSION_UID, _lap_packet, _session_packet


_TELEMETRY_CAR = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")
_CONTROL_TOKEN = "live-monitor-test-token"


def _telemetry_packet(
    *,
    frame: int,
    sequence: int,
    player_car_index: int = 0,
    packet_format: int = 2025,
    packet_version: int = 1,
    throttle: float = 0.75,
    brake: float = 0.2,
):
    records = []
    for car_index in range(22):
        fields = (
            (100 + car_index, throttle, 0.0, brake, 0, 0, 11_000, 0, 0, 0)
            + (0,) * 12
            + (95,)
            + (1.0,) * 4
            + (0,) * 4
        )
        records.append(
            _TELEMETRY_CAR.pack(*fields)
        )
    return make_datagram(
        packet_format=packet_format,
        packet_id=6,
        packet_version=packet_version,
        session_uid=SESSION_UID,
        frame=frame,
        player_car_index=player_car_index,
        body=b"".join(records) + bytes((0, 255, 0)),
        sequence=sequence,
    )


def _advance(
    frame: int,
    sequence: int,
    *,
    session_uid: int = SESSION_UID,
    player_car_index: int = 0,
    packet_format: int = 2025,
):
    return make_datagram(
        packet_format=packet_format,
        packet_id=255,
        session_uid=session_uid,
        frame=frame,
        player_car_index=player_car_index,
        body=b"advance-watermark",
        sequence=sequence,
    )


def _process(observer: _AcquisitionObserver, raw):
    observer.process(replace(raw, monotonic_ns=time.monotonic_ns()))


def _queue_frame(
    observer: _AcquisitionObserver,
    *,
    frame: int,
    sequence: int,
    player_car_index: int,
) -> None:
    _process(
        observer,
        _telemetry_packet(
            frame=frame,
            sequence=sequence,
            player_car_index=player_car_index,
        ),
    )
    _process(
        observer,
        _lap_packet(
            frame=frame,
            lap_number=3,
            distance_m=120.0,
            session_time=float(frame),
            current_lap_time_ms=frame * 10,
            sequence=sequence + 1,
            player_car_index=player_car_index,
            active_car_index=player_car_index,
        ),
    )


def _session_packet_for_mode(*, race: bool):
    body = bytearray(_session_packet().payload[29:])
    if race:
        body[6] = 15
        body[665] = 27
        body[666] = 1
    return make_datagram(
        packet_id=1,
        session_uid=SESSION_UID,
        frame=1,
        body=bytes(body),
    )


def _publish_frame(
    observer: _AcquisitionObserver,
    *,
    frame: int = 10,
    sequence: int = 10,
    player_car_index: int = 0,
    invalid: int = 0,
    telemetry: bool = True,
    throttle: float = 0.75,
    brake: float = 0.2,
) -> None:
    if telemetry:
        _process(observer,
            _telemetry_packet(
                frame=frame,
                sequence=sequence,
                player_car_index=player_car_index,
                throttle=throttle,
                brake=brake,
            )
        )
    _process(observer,
        _lap_packet(
            frame=frame,
            lap_number=3,
            distance_m=120.0,
            session_time=12.0,
            current_lap_time_ms=34_567,
            invalid=invalid,
            pit_status=2,
            driver_status=2,
            sequence=sequence + 1,
            player_car_index=player_car_index,
            active_car_index=player_car_index,
        )
    )
    _process(
        observer,
        _advance(
            frame + 1,
            sequence + 2,
            player_car_index=player_car_index,
        ),
    )


def test_live_monitor_joins_reordered_player_packets_and_uses_canonical_values():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(
        observer,
        frame=100,
        player_car_index=1,
        invalid=1,
        throttle=0.0,
        brake=0.0,
    )

    live = observer.live_telemetry_snapshot()

    assert live["status"] == "fresh"
    assert live["session_uid"] == str(SESSION_UID)
    assert live["frame_identifier"] == 100
    assert live["player_car_index"] == 1
    assert live["lap_number"] == 3
    assert live["lap_time_ms"] == 34_567
    assert live["game_invalid"] is True
    assert live["pit_status_id"] == 2
    assert live["driver_status_id"] == 2
    assert live["speed_kph"] == 101
    assert live["gear"] == 0
    assert live["engine_rpm"] == 11_000
    assert live["throttle"] == 0
    assert live["brake"] == 0


def test_live_monitor_marks_missing_same_frame_car_telemetry_unavailable():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100, telemetry=False)

    live = observer.live_telemetry_snapshot()

    assert live["status"] == "unavailable"
    assert live["reason"] == "same_frame_car_telemetry_unavailable"
    assert live["lap_number"] == 3
    assert live["speed_kph"] is None
    assert live["throttle"] is None
    assert live["brake"] is None


def test_live_monitor_supports_race_and_unknown_context_without_policy_changes():
    time_trial = _AcquisitionObserver(reorder_window_frames=1)
    _process(time_trial, _session_packet_for_mode(race=False))
    _publish_frame(time_trial, frame=100)

    race = _AcquisitionObserver(reorder_window_frames=1)
    _process(race, _session_packet_for_mode(race=True))
    _publish_frame(race, frame=100)

    unknown = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(unknown, frame=100)

    assert time_trial.live_telemetry_snapshot()["status"] == "fresh"
    assert race.live_telemetry_snapshot()["status"] == "fresh"
    assert unknown.live_telemetry_snapshot()["status"] == "fresh"


def test_live_monitor_expires_sample_using_monotonic_receive_time():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)
    sample = observer.live_telemetry_snapshot()
    observed_ns = sample["_observed_monotonic_ns"]

    at_limit = observer.live_telemetry_snapshot(observed_ns + 500_000_000)
    after_limit = observer.live_telemetry_snapshot(observed_ns + 501_000_000)

    assert at_limit["status"] == "fresh"
    assert at_limit["age_ms"] == 500
    assert after_limit["status"] == "stale"
    assert after_limit["age_ms"] == 501


def test_live_monitor_resets_on_player_and_session_change():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)
    assert observer.live_telemetry_snapshot()["status"] == "fresh"

    _process(observer,
        _telemetry_packet(frame=110, sequence=20, player_car_index=1)
    )
    assert observer.live_telemetry_snapshot()["status"] == "waiting"
    _process(observer,
        _lap_packet(
            frame=110,
            lap_number=4,
            distance_m=5.0,
            current_lap_time_ms=100,
            session_time=14.0,
            sequence=21,
            player_car_index=1,
            active_car_index=1,
        )
    )
    _process(observer, _advance(111, 22, player_car_index=1))
    assert observer.live_telemetry_snapshot()["player_car_index"] == 1

    _process(observer,
        make_datagram(
            packet_id=255,
            session_uid=SESSION_UID + 1,
            frame=1,
            body=b"new session",
            sequence=23,
        )
    )
    live = observer.live_telemetry_snapshot()
    assert live["status"] == "waiting"
    assert "player_car_index" not in live


def test_live_monitor_reports_unsupported_packet_version_and_rejects_old_format():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)

    _process(observer,
        _telemetry_packet(
            frame=110,
            sequence=20,
            packet_format=2026,
            packet_version=2,
        )
    )
    _process(observer, _advance(111, 21, packet_format=2026))
    assert observer.live_telemetry_snapshot()["status"] == "unsupported"

    _process(observer,
        make_datagram(
            packet_id=6,
            session_uid=SESSION_UID,
            frame=109,
            body=b"late old format",
            sequence=21,
        )
    )
    assert observer.live_telemetry_snapshot()["status"] == "unsupported"


def test_live_monitor_duplicate_envelopes_do_not_change_snapshot():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    telemetry = _telemetry_packet(frame=100, sequence=10)
    _process(observer, telemetry)
    _process(observer, telemetry)
    _process(observer,
        _lap_packet(
            frame=100,
            lap_number=1,
            distance_m=1.0,
            current_lap_time_ms=100,
            session_time=1.0,
            sequence=11,
        )
    )
    _process(observer, _advance(101, 12))

    assert observer.frames.duplicates_ignored == 1
    assert observer.live_telemetry_snapshot()["status"] == "fresh"


def test_live_monitor_player_change_barrier_rejects_older_buffered_snapshot():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    _queue_frame(observer, frame=97, sequence=1, player_car_index=1)
    _process(observer, _advance(100, 3, player_car_index=1))
    assert observer.live_telemetry_snapshot()["frame_identifier"] == 97

    _queue_frame(observer, frame=100, sequence=10, player_car_index=1)
    _queue_frame(observer, frame=101, sequence=20, player_car_index=0)
    _queue_frame(observer, frame=102, sequence=30, player_car_index=1)
    _process(observer, _advance(103, 40, player_car_index=1))

    live = observer.live_telemetry_snapshot()
    assert live["status"] == "waiting"
    assert "frame_identifier" not in live


def test_live_monitor_discards_buffered_packets_from_previous_wire_format():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    _queue_frame(observer, frame=97, sequence=1, player_car_index=0)
    _process(observer, _advance(100, 3))
    assert observer.live_telemetry_snapshot()["frame_identifier"] == 97

    _queue_frame(observer, frame=101, sequence=10, player_car_index=0)
    _process(
        observer,
        make_datagram(
            packet_format=2026,
            packet_id=200,
            session_uid=SESSION_UID,
            frame=104,
            sequence=20,
        ),
    )

    live = observer.live_telemetry_snapshot()
    assert observer.sessions.current_packet_format.value == 2026
    assert live["status"] == "waiting"
    assert "frame_identifier" not in live


def test_live_monitor_malformed_new_lap_clears_previous_values():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)
    assert observer.live_telemetry_snapshot()["status"] == "fresh"

    malformed = _lap_packet(
        frame=102,
        lap_number=4,
        distance_m=130.0,
        session_time=14.0,
        sequence=20,
    )
    _process(observer, _telemetry_packet(frame=102, sequence=19))
    _process(observer, replace(malformed, payload=malformed.payload[:-1]))
    _process(observer, _advance(103, 21))

    live = observer.live_telemetry_snapshot()
    assert live["status"] == "unavailable"
    assert live["reason"] == "lap_data_decode_failed"
    assert live["frame_identifier"] == 102
    assert live["lap_number"] is None
    assert live["speed_kph"] is None


def test_live_monitor_ignores_delayed_unsupported_and_uid_zero_packets():
    observer = _AcquisitionObserver(reorder_window_frames=1)
    _publish_frame(observer, frame=100)
    original = observer.live_telemetry_snapshot()

    _process(
        observer,
        _telemetry_packet(frame=100, sequence=30, packet_version=2),
    )
    _process(
        observer,
        make_datagram(
            packet_id=6,
            packet_version=2,
            session_uid=0,
            frame=200,
            sequence=31,
        ),
    )

    live = observer.live_telemetry_snapshot()
    assert live["status"] == original["status"]
    assert live["frame_identifier"] == original["frame_identifier"]
    assert live["speed_kph"] == original["speed_kph"]


def test_live_monitor_duplicate_does_not_refresh_receive_provenance():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    telemetry = _telemetry_packet(frame=100, sequence=10)
    lap = _lap_packet(
        frame=100,
        lap_number=2,
        distance_m=10.0,
        session_time=11.0,
        current_lap_time_ms=1_234,
        sequence=11,
    )
    received_ns = time.monotonic_ns()
    observer.process(replace(telemetry, monotonic_ns=received_ns))
    observer.process(replace(lap, monotonic_ns=received_ns))
    _process(observer, _advance(101, 12))
    _process(observer, _advance(102, 13))
    observer.process(replace(telemetry, monotonic_ns=received_ns + 900_000_000))
    observer.process(replace(lap, monotonic_ns=received_ns + 900_000_000))
    _process(observer, _advance(103, 14))

    live = observer.live_telemetry_snapshot(received_ns + 900_000_000)
    assert observer.frames.duplicates_ignored == 2
    assert live["status"] == "stale"
    assert live["age_ms"] == 900


def test_live_monitor_malformed_telemetry_does_not_refresh_selected_values():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    received_ns = time.monotonic_ns()
    telemetry = _telemetry_packet(frame=100, sequence=10)
    lap = _lap_packet(
        frame=100,
        lap_number=2,
        distance_m=10.0,
        session_time=11.0,
        current_lap_time_ms=1_234,
        sequence=11,
    )
    malformed = _telemetry_packet(frame=100, sequence=12, throttle=0.5)
    observer.process(replace(telemetry, monotonic_ns=received_ns))
    observer.process(replace(lap, monotonic_ns=received_ns))
    observer.process(
        replace(malformed, payload=malformed.payload[:-1], monotonic_ns=received_ns + 900_000_000)
    )
    _process(observer, _advance(101, 13))
    _process(observer, _advance(102, 14))
    _process(observer, _advance(103, 15))

    live = observer.live_telemetry_snapshot(received_ns + 900_000_000)
    assert live["status"] == "stale"
    assert live["age_ms"] == 900
    assert live["throttle"] == 0.75


def test_live_monitor_never_marks_missing_receive_provenance_fresh():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    _publish_frame(observer, frame=100, sequence=1)

    for index in range(observer._max_receive_time_entries + 8):
        _process(
            observer,
            _telemetry_packet(
                frame=101,
                sequence=10 + index,
                throttle=(index + 1) / (observer._max_receive_time_entries + 9),
            ),
        )

    _process(observer, _advance(102, 5))
    _process(observer, _advance(103, 6))

    live = observer.live_telemetry_snapshot()
    assert live["status"] == "unavailable"
    assert live["reason"] == "receive_provenance_unavailable"
    assert live["age_ms"] is None
    assert live["throttle"] == 0.75


def test_live_monitor_requires_timestamp_for_every_selected_packet():
    observer = _AcquisitionObserver(reorder_window_frames=3)
    telemetry = _telemetry_packet(frame=100, sequence=10)
    lap = _lap_packet(
        frame=100,
        lap_number=2,
        distance_m=10.0,
        session_time=11.0,
        current_lap_time_ms=1_234,
        sequence=11,
    )
    _process(observer, telemetry)
    _process(observer, lap)
    _process(observer, _advance(101, 12))
    _process(observer, _advance(102, 13))
    telemetry_packet = observer.decoder.decode(telemetry)
    observer._receive_times.pop(
        (SESSION_UID, 100, telemetry_packet.wire_fingerprint)
    )
    _process(observer, _advance(103, 14))

    live = observer.live_telemetry_snapshot()
    assert live["status"] == "unavailable"
    assert live["reason"] == "receive_provenance_unavailable"
    assert live["age_ms"] is None


def test_managed_recording_exposes_freshness_and_hides_finished_live_state(
    tmp_path: Path,
):
    database = tmp_path / "archive.sqlite3"
    recordings_root = tmp_path / "recordings"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    app = create_app(
        database,
        recordings_root=recordings_root,
        control_token=_CONTROL_TOKEN,
        recording_host="127.0.0.1",
        recording_port=port,
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                started = await client.post(
                    "/api/v1/recordings/start",
                    headers={"authorization": f"Bearer {_CONTROL_TOKEN}"},
                )
                assert started.status_code == 202
                recording_id = started.json()["data"]["recording_id"]

                payloads = [
                    _telemetry_packet(frame=100, sequence=10).payload,
                    _lap_packet(
                        frame=100,
                        lap_number=2,
                        distance_m=10.0,
                        session_time=11.0,
                        current_lap_time_ms=1_234,
                        sequence=11,
                    ).payload,
                    _advance(103, 12).payload,
                ]
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    for payload in payloads:
                        sender.sendto(payload, ("127.0.0.1", port))

                for _ in range(100):
                    current = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    live = current["progress"]["live_telemetry"]
                    if live["frame_identifier"] == 100:
                        break
                    await asyncio.sleep(0.01)

                assert live["status"] == "fresh"
                assert live["speed_kph"] == 100
                assert live["lap_time_ms"] == 1_234
                assert "_observed_monotonic_ns" not in live

                await asyncio.sleep(0.55)
                stale = (
                    await client.get("/api/v1/recordings/current")
                ).json()["data"]["progress"]["live_telemetry"]
                assert stale["status"] == "stale"
                assert stale["age_ms"] > 500

                stopped = await client.post(
                    f"/api/v1/recordings/{recording_id}/stop",
                    headers={"authorization": f"Bearer {_CONTROL_TOKEN}"},
                )
                assert stopped.status_code == 202
                for _ in range(100):
                    completed = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if completed["status"] in {
                        "complete",
                        "failed",
                        "interrupted",
                    }:
                        break
                    await asyncio.sleep(0.01)

                assert completed["status"] == "complete"
                assert completed["progress"] is None
                capture = next(recordings_root.glob("f1e-*.f1ecap"))
                with CaptureReader(capture) as reader:
                    recorded = list(reader)
                assert [item.payload for item in recorded] == payloads

    asyncio.run(exercise())
