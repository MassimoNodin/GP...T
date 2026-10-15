from __future__ import annotations

import asyncio
import socket
from dataclasses import replace
from copy import deepcopy

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from f1_engineer.api.app import create_app
from f1_engineer.recording.capture import CaptureWriter
from tests.helpers import make_datagram
from tests.test_radio_target_pace import REQUEST, ready


TOKEN = "radio-replay-regression-test-token-000000000000000"


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _bounded_capture(path, packet_count: int = 4) -> None:
    with CaptureWriter(path, metadata={"test": "radio-replay-regression"}) as writer:
        for sequence in range(packet_count):
            raw = make_datagram(
                packet_id=255,
                packet_version=255,
                body=b"bounded radio replay",
                sequence=sequence,
            )
            writer.write(
                replace(raw, monotonic_ns=5_000_000_000 + sequence * 5_000_000_000)
            )


async def _eventually(client, predicate, timeout_s: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        snapshot = (await client.get("/api/v1/replays/current")).json()["data"]
        if snapshot is not None and predicate(snapshot):
            return snapshot
        await asyncio.sleep(0.01)
    raise AssertionError("managed replay did not reach the expected state")


def test_managed_replay_pause_preserves_radio_state_until_session_changes(tmp_path):
    database = tmp_path / "archive.sqlite3"
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    capture_path = recordings / "bounded-radio-replay.f1ecap"
    _bounded_capture(capture_path)
    radio_controller, radio, _, live_snapshot = ready()
    radio_controller.handle_request(REQUEST, now=12)
    radio.cancel()
    prior_best = deepcopy(radio_controller.pace.best)
    prior_turns = deepcopy(radio_controller.conversation.turns)
    assert radio_controller.tasks.active()
    assert prior_best

    app = create_app(
        database,
        recordings_root=recordings,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=_free_udp_port(),
    )

    async def exercise():
        headers = {"authorization": f"Bearer {TOKEN}"}
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                sources = (await client.get("/api/v1/recording-sources")).json()["data"]
                source = next(
                    item for item in sources if item["display_name"] == capture_path.name
                )
                started = await client.post(
                    "/api/v1/replays/start",
                    json={"capture_id": source["capture_id"], "speed": 4},
                    headers=headers,
                )
                assert started.status_code == 202
                playback_id = started.json()["data"]["playback_id"]
                assert started.json()["data"]["state"] in {"starting", "paused"}

                await _eventually(client, lambda value: value["state"] == "paused")
                resumed = await client.post(
                    f"/api/v1/replays/{playback_id}/resume", headers=headers
                )
                assert resumed.status_code == 202
                await _eventually(client, lambda value: value["datagrams_delivered"] >= 1)
                paused = await client.post(
                    f"/api/v1/replays/{playback_id}/pause", headers=headers
                )
                assert paused.status_code == 202
                held = await _eventually(client, lambda value: value["state"] == "paused")
                assert 1 <= held["datagrams_delivered"] < 4

                radio_controller.feed(
                    {"status": "unavailable", "reason": "replay_paused"}, now=42
                )
                assert radio_controller._player is None
                assert radio_controller.pace.best == prior_best
                assert radio_controller.tasks.active()
                assert radio_controller.conversation.turns == prior_turns

                resumed = await client.post(
                    f"/api/v1/replays/{playback_id}/resume", headers=headers
                )
                assert resumed.status_code == 202
                value = deepcopy(live_snapshot)
                value["events"] = []
                value["event_cursor"] += 5
                value["event_floor"] = value["event_cursor"]
                radio_controller.feed(value, now=43)
                assert radio_controller.tasks.active()
                assert radio_controller.pace.best == prior_best
                assert radio_controller.conversation.turns == prior_turns

                value["scope"]["session_uid"] += 1
                radio_controller.feed(value, now=44)
                assert not radio_controller.tasks.active()
                assert not radio_controller.pace.best
                assert not radio_controller.conversation.turns

                stopped = await client.post(
                    f"/api/v1/replays/{playback_id}/stop", headers=headers
                )
                assert stopped.status_code == 202
                final = await _eventually(
                    client, lambda snapshot: snapshot["state"] == "stopped"
                )
                assert final["source_stable"] is True

    asyncio.run(exercise())
