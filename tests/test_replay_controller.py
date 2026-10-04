from __future__ import annotations

import asyncio
import hashlib
import socket
from dataclasses import replace

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from f1_engineer.api.app import create_app
from f1_engineer.recording.capture import CaptureReader, CaptureWriter
from f1_engineer.storage.database import Database
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
                active = await _eventually(
                    client, lambda value: value["datagrams_delivered"] >= 1
                )
                assert active["state"] == "playing"

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

                stopped = await client.post(
                    f"/api/v1/replays/{playback_id}/stop",
                    headers={"authorization": f"Bearer {TOKEN}"},
                )
                assert stopped.status_code == 202
                ended = await _eventually(
                    client, lambda value: value["state"] == "stopped"
                )
                assert ended["datagrams_delivered"] < 10
                assert ended["capture_complete"] is None
                assert source.read_bytes() == original_bytes

    asyncio.run(exercise())
