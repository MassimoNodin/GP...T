from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import socket
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from f1_engineer.api.app import create_app
from f1_engineer.api import replay_controller as replay_controller_module
from f1_engineer.api.replay_controller import ReplayController, _file_identity
from f1_engineer.recording.capture import CaptureReader, CaptureWriter
from f1_engineer.storage.database import Database
from f1_engineer.udp.source import _ReplayClock
from tests.helpers import make_datagram


TOKEN = "diagnostic-replay-test-token-000000000000000000"


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _capture(
    path,
    *,
    complete: bool,
    packet_count: int = 0,
    interval_ns: int = 5_000_000_000,
) -> None:
    with CaptureWriter(path, metadata={"test": "diagnostic-replay"}) as writer:
        for sequence in range(packet_count):
            raw = make_datagram(
                packet_id=255,
                packet_version=255,
                body=b"opaque replay packet",
                sequence=sequence,
            )
            writer.write(
                replace(raw, monotonic_ns=5_000_000_000 + sequence * interval_ns)
            )
        if not complete:
            writer.close({"status": "incomplete", "reason": "test_truncation"})


async def _eventually(client, predicate, *, timeout_s: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        value = (await client.get("/api/v1/replays/current")).json()["data"]
        if value is not None and predicate(value):
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("replay did not reach the expected state")


def test_replay_completes_without_modifying_source_or_creating_capture(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    source = root / "validation.f1ecap"
    _capture(source, complete=False, packet_count=3, interval_ns=1_000_000)
    original_bytes = source.read_bytes()
    original_sha256 = hashlib.sha256(original_bytes).hexdigest()
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=_free_udp_port(),
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                assert (await client.get("/api/v1/replays/current")).json()["data"] is None
                assert (
                    await client.post(
                        "/api/v1/replays/start",
                        json={"capture_id": "0" * 32, "speed": 1},
                    )
                ).status_code == 403

                captures = (await client.get("/api/v1/recording-sources")).json()["data"]
                capture = next(item for item in captures if item["display_name"] == source.name)
                invalid_speed = await client.post(
                    "/api/v1/replays/start",
                    json={"capture_id": capture["capture_id"], "speed": 3},
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert invalid_speed.status_code == 422

                started = await client.post(
                    "/api/v1/replays/start",
                    json={"capture_id": capture["capture_id"], "speed": 4},
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert started.status_code == 202
                assert started.json()["data"]["source_kind"] == "replay"
                paused = await _eventually(
                    client, lambda value: value["state"] == "paused"
                )
                assert paused["datagrams_delivered"] == 0
                unauthorized_pause = await client.post(
                    f"/api/v1/replays/{paused['playback_id']}/pause"
                )
                assert unauthorized_pause.status_code == 403
                repeated_start = await client.post(
                    "/api/v1/replays/start",
                    json={"capture_id": capture["capture_id"], "speed": 4},
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert repeated_start.status_code == 202
                assert repeated_start.json()["data"]["playback_id"] == paused["playback_id"]
                resumed = await client.post(
                    f"/api/v1/replays/{paused['playback_id']}/resume",
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert resumed.status_code == 202
                completed = await _eventually(
                    client, lambda value: value["state"] == "completed"
                )
                assert completed["datagrams_delivered"] == 3
                assert completed["capture_complete"] is False
                assert completed["capture_completion"]["status"] == "incomplete"
                assert completed["source_stable"] is True
                assert source.read_bytes() == original_bytes
                assert hashlib.sha256(source.read_bytes()).hexdigest() == original_sha256
                assert sorted(path.name for path in root.glob("*.f1ecap")) == [source.name]
                with CaptureReader(source) as reader:
                    list(reader)
                    assert not reader.complete
                    assert reader.completion["reason"] == "test_truncation"

    asyncio.run(exercise())

    with Database(database) as db:
        assert db.connection.execute("SELECT COUNT(*) FROM import_jobs").fetchone()[0] == 0
        assert db.connection.execute("SELECT COUNT(*) FROM recording_jobs").fetchone()[0] == 0


def test_replay_reserves_local_operations_and_stop_interrupts_pacing(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    source = root / "paced.f1ecap"
    _capture(source, complete=True, packet_count=10)
    original_bytes = source.read_bytes()
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=_free_udp_port(),
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                captures = (await client.get("/api/v1/recording-sources")).json()["data"]
                capture = next(item for item in captures if item["display_name"] == source.name)
                started = await client.post(
                    "/api/v1/replays/start",
                    json={"capture_id": capture["capture_id"], "speed": 0.5},
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert started.status_code == 202
                playback_id = started.json()["data"]["playback_id"]
                paused = await _eventually(
                    client, lambda value: value["state"] == "paused"
                )
                assert paused["datagrams_delivered"] == 0
                stepped = await client.post(
                    f"/api/v1/replays/{playback_id}/step",
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert stepped.status_code == 202
                active = await _eventually(
                    client,
                    lambda value: value["state"] == "paused"
                    and value["datagrams_delivered"] == 1,
                )

                recording = await client.post(
                    "/api/v1/recordings/start",
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert recording.status_code == 409

                import_request = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": capture["capture_id"]},
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert import_request.status_code == 409

                resumed = await client.post(
                    f"/api/v1/replays/{playback_id}/resume",
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert resumed.status_code == 202
                active = await _eventually(
                    client, lambda value: value["state"] == "playing"
                )
                assert active["datagrams_delivered"] == 1

                stopped = await client.post(
                    f"/api/v1/replays/{playback_id}/stop",
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert stopped.status_code == 202
                ended = await _eventually(
                    client, lambda value: value["state"] == "stopped"
                )
                assert ended["datagrams_delivered"] == 1
                assert ended["capture_complete"] is None
                assert source.read_bytes() == original_bytes

    asyncio.run(exercise())


def test_replay_pause_acknowledgement_holds_delivery_and_step_releases_one_packet(
    tmp_path,
):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    source = root / "steppable.f1ecap"
    _capture(source, complete=True, packet_count=8, interval_ns=100_000_000)
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=_free_udp_port(),
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                captures = (await client.get("/api/v1/recording-sources")).json()["data"]
                capture = next(item for item in captures if item["display_name"] == source.name)
                headers = {"authorization": f"Bearer {TOKEN}"}
                started = await client.post(
                    "/api/v1/replays/start",
                    json={"capture_id": capture["capture_id"], "speed": 1},
                    headers=headers,
                )
                assert started.status_code == 202
                playback_id = started.json()["data"]["playback_id"]
                paused = await _eventually(
                    client, lambda value: value["state"] == "paused"
                )
                assert paused["datagrams_delivered"] == 0

                first_step = await client.post(
                    f"/api/v1/replays/{playback_id}/step", headers=headers
                )
                assert first_step.status_code == 202
                paused = await _eventually(
                    client,
                    lambda value: value["state"] == "paused"
                    and value["datagrams_delivered"] == 1,
                )
                assert paused["datagrams_delivered"] == 1

                second_step = await client.post(
                    f"/api/v1/replays/{playback_id}/step", headers=headers
                )
                assert second_step.status_code == 202
                paused = await _eventually(
                    client,
                    lambda value: value["state"] == "paused"
                    and value["datagrams_delivered"] == 2,
                )
                assert paused["datagrams_delivered"] == 2

                resumed = await client.post(
                    f"/api/v1/replays/{playback_id}/resume", headers=headers
                )
                assert resumed.status_code == 202
                playing = await _eventually(
                    client, lambda value: value["state"] == "playing"
                )
                assert playing["datagrams_delivered"] == 2

                pausing = await client.post(
                    f"/api/v1/replays/{playback_id}/pause", headers=headers
                )
                assert pausing.status_code == 202
                paused = await _eventually(
                    client, lambda value: value["state"] == "paused"
                )
                delivered_at_ack = paused["datagrams_delivered"]
                await asyncio.sleep(0.25)
                stable = (await client.get("/api/v1/replays/current")).json()["data"]
                assert stable["state"] == "paused"
                assert stable["datagrams_delivered"] == delivered_at_ack

                step = await client.post(
                    f"/api/v1/replays/{playback_id}/step", headers=headers
                )
                assert step.status_code == 202
                stepped = await _eventually(
                    client,
                    lambda value: value["state"] == "paused"
                    and value["datagrams_delivered"] == delivered_at_ack + 1,
                )
                assert stepped["datagrams_delivered"] == delivered_at_ack + 1
                await client.post(
                    f"/api/v1/replays/{playback_id}/stop", headers=headers
                )
                await _eventually(client, lambda value: value["state"] == "stopped")

    asyncio.run(exercise())


def test_replay_clock_resumes_from_stepped_capture_position():
    clock = _ReplayClock(start_paused=True)
    clock.start(1_000_000)
    clock.advance_to(100_000_000_000)

    # A long pause must not be added to the capture timeline.
    clock.resume(500_000_000_000)
    assert clock.elapsed(500_000_000_000) == 100_000_000_000
    assert clock.elapsed(501_000_000_000) == 101_000_000_000

    clock.pause(502_000_000_000)
    assert clock.elapsed(900_000_000_000) == 102_000_000_000
    clock.resume(900_000_000_000)
    assert clock.elapsed(901_000_000_000) == 103_000_000_000


@pytest.mark.parametrize("shutdown_race", ["loop_closed", "queued_control_cancelled"])
def test_replay_control_stop_race_is_unavailable_and_keeps_stopping_state(
    monkeypatch, tmp_path, shutdown_race
):
    controller = ReplayController(
        tmp_path / "archive.sqlite3",
        tmp_path / "recordings",
        SimpleNamespace(ready=True),
    )
    controller.start()
    playback_id = "a" * 32
    controller._playback_id = playback_id
    controller._snapshot = {"playback_id": playback_id, "state": "playing"}
    controller._source = SimpleNamespace(pause=lambda: None)
    controller._loop = object()
    captured_coroutines = []

    def stop_during_scheduling(coro, loop):
        captured_coroutines.append(coro)
        controller.stop_replay(playback_id)
        if shutdown_race == "loop_closed":
            raise RuntimeError("Event loop is closed")
        coro.close()
        future = concurrent.futures.Future()
        future.cancel()
        return future

    monkeypatch.setattr(
        "f1_engineer.api.replay_controller.asyncio.run_coroutine_threadsafe",
        stop_during_scheduling,
    )

    with pytest.raises(ValueError, match="replay_controller_unavailable"):
        controller.pause_replay(playback_id)

    assert controller._snapshot["state"] == "stopping"
    assert inspect.getcoroutinestate(captured_coroutines[0]) == inspect.CORO_CLOSED


def test_replay_stop_during_eof_finalization_publishes_terminal_state(
    monkeypatch, tmp_path
):
    source_path = tmp_path / "finalizing.f1ecap"
    _capture(source_path, complete=True, packet_count=1)
    operation_controller = SimpleNamespace(ready=True, released=None)
    operation_controller.release_operation = lambda name: setattr(
        operation_controller, "released", name
    )
    controller = ReplayController(
        tmp_path / "archive.sqlite3",
        tmp_path / "recordings",
        operation_controller,
    )
    controller.start()
    playback_id = "b" * 32
    controller._playback_id = playback_id
    controller._snapshot = {"playback_id": playback_id, "state": "starting"}
    raw = make_datagram(
        packet_id=255,
        packet_version=255,
        body=b"opaque replay packet",
        sequence=0,
    )

    class StopAtEofObserver:
        latest_context = None

        def process(self, datagram, *, delivery_monotonic_ns):
            pass

        def finish(self):
            stopping = controller.stop_replay(playback_id)
            assert stopping["state"] == "stopping"
            assert controller._stop_requested

        def live_telemetry_snapshot(self):
            return {"status": "waiting", "reason": None, "age_ms": None}

        def live_car_status_snapshot(self):
            return {"status": "waiting", "reason": None, "age_ms": None}

        def live_lap_timing_snapshot(self):
            return {"status": "waiting", "reason": None, "age_ms": None}

    class ImmediateReplaySource:
        complete = True
        completion = {"status": "complete"}

        def __init__(self, *args, on_control_state, **kwargs):
            on_control_state("paused")

        async def packets(self):
            yield raw

    monkeypatch.setattr(
        replay_controller_module, "AcquisitionObserver", StopAtEofObserver
    )
    monkeypatch.setattr(replay_controller_module, "ReplaySource", ImmediateReplaySource)

    controller._run_replay(
        playback_id,
        "c" * 32,
        source_path,
        _file_identity(source_path),
        1.0,
    )

    assert controller._snapshot["state"] == "stopped"
    assert controller._snapshot["capture_complete"] is True
    assert controller._snapshot["capture_completion"] == {"status": "complete"}
    assert controller._snapshot["source_stable"] is True
    assert operation_controller.released == "replay"
