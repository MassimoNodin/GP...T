from __future__ import annotations

import asyncio
import socket
from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from f1_engineer.api import replay_controller as replay_controller_module
from f1_engineer.api.app import create_app
from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.radio import command as radio_command
from f1_engineer.radio.controller import RadioController
from tests.helpers import make_datagram
from tests.test_car_lap_inventory import _participants_body
from tests.test_lap_tracking import SESSION_UID, _lap_packet, _session_packet
from tests.test_standalone_radio import scheduler
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


def _supported_capture(path) -> None:
    with CaptureWriter(path, metadata={"test": "supported-radio-replay"}) as writer:
        session = _session_packet()
        writer.write(replace(session, captured_at_ns=1_000_000_000, monotonic_ns=1_000_000_000))
        participants = make_datagram(
            packet_id=4,
            session_uid=SESSION_UID,
            frame=48,
            session_time=0.85,
            body=_participants_body(),
            sequence=48,
        )
        writer.write(
            replace(participants, captured_at_ns=1_100_000_000, monotonic_ns=1_100_000_000)
        )
        for frame in range(48, 56):
            timestamp = 1_100_000_000 + (frame - 48) * 100_000_000
            writer.write(
                replace(
                    _lap_packet(
                        frame=frame,
                        lap_number=3,
                        distance_m=0.2 + (frame - 48) * 0.2,
                        session_time=0.85 + (frame - 48) / 60,
                        sequence=frame,
                    ),
                    captured_at_ns=timestamp,
                    monotonic_ns=timestamp,
                )
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
                for row, leader_gap in zip(value["drivers"], (2000, 5000, 7000)):
                    row["lap"].update(
                        current_lap_number=value["drivers"][0]["lap"]["current_lap_number"],
                        delta_to_race_leader_ms=leader_gap,
                        pit_status_id=0,
                        result_status_id=2,
                    )
                radio_controller.feed(value, now=43)
                assert radio_controller.tasks.active()
                assert radio_controller.pace.best == prior_best
                assert radio_controller.conversation.turns == prior_turns
                radio_controller.handle_request("what's the gap to Leclerc?", now=43.1)
                assert "3.000 seconds behind you" in radio.pending[-1].text

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


def test_managed_replay_decodes_supported_packets_into_radio_answer(tmp_path, monkeypatch):
    database = tmp_path / "archive.sqlite3"
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    capture_path = recordings / "supported-radio-replay.f1ecap"
    _supported_capture(capture_path)
    observed = []
    acquisition_observer = replay_controller_module.AcquisitionObserver

    class RadioSnapshotObserver(acquisition_observer):
        def __init__(self):
            super().__init__()
            self.radio_pipeline = TelemetryPipeline()
            observed.append(self)

        def process(self, raw, *, delivery_monotonic_ns=None):
            super().process(raw, delivery_monotonic_ns=delivery_monotonic_ns)
            received_ns = delivery_monotonic_ns or raw.monotonic_ns
            self.radio_pipeline.process(
                replace(raw, captured_at_ns=received_ns, monotonic_ns=received_ns)
            )

    monkeypatch.setattr(replay_controller_module, "AcquisitionObserver", RadioSnapshotObserver)
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
                source = next(item for item in sources if item["display_name"] == capture_path.name)
                started = await client.post(
                    "/api/v1/replays/start",
                    json={"capture_id": source["capture_id"], "speed": 4},
                    headers=headers,
                )
                assert started.status_code == 202
                playback_id = started.json()["data"]["playback_id"]
                await _eventually(client, lambda value: value["state"] == "paused")
                resumed = await client.post(
                    f"/api/v1/replays/{playback_id}/resume", headers=headers
                )
                assert resumed.status_code == 202

                replay = await _eventually(client, lambda value: value["state"] == "completed")
                assert replay["datagrams_delivered"] == 10
                assert len(observed) == 1
                snapshot = observed[0].radio_pipeline.engineer_facts.snapshot()
                assert snapshot["status"] == "available" and snapshot["drivers"], snapshot
                snapshot.update(generation="a" * 32, session_id="b" * 32, age_s=0.1)

                radio, _, _ = scheduler()
                controller = RadioController(radio)
                now = snapshot["received_monotonic_ns"] / 1_000_000_000
                controller.feed(snapshot, now=now)
                controller.handle_request("where am I?", now=now + 0.1)
                assert "position" in radio.pending[-1].text.lower()
                answer = radio.pending[-1].text
                radio.tick(now=now + 0.11)
                voice = SimpleNamespace(repeat_reference=radio.capture_repeat_reference())
                radio_command.dispatch_voice_request("repeat that", controller, voice, now=now + 0.12)
                assert radio.pending[-1].text == answer
                radio.tick(now=now + 0.13)
                radio_command.dispatch_voice_request("stop speaking", controller, voice, now=now + 0.14)
                assert radio.active is None and not radio.pending
                radio_command.dispatch_submission(
                    {
                        "question": "where am I?",
                        "interpretation": {"status": "unavailable", "reason": "interpreter_unavailable"},
                    },
                    controller,
                    voice,
                    now=now + 0.15,
                )
                assert radio.pending[-1].text == "Say again."

                assert replay["source_stable"] is True

    asyncio.run(exercise())
