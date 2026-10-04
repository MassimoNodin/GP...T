from __future__ import annotations

import asyncio
import socket
import threading
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from f1_engineer.api.app import create_app
from f1_engineer.api import import_controller as import_controller_module
from f1_engineer.api import recording_controller as recording_controller_module
from f1_engineer.recording.capture import CaptureReader, CaptureWriter
from f1_engineer.storage.database import Database
from f1_engineer.storage.recordings import (
    create_recording_job,
    get_recording_job,
    get_recording_job_paths,
    update_recording_job,
)
from tests.helpers import make_datagram


TOKEN = "r" * 48


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _auth() -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}"}


def test_recording_start_stop_is_idempotent_and_publishes_to_inbox(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    port = _free_udp_port()
    root.mkdir()
    with CaptureWriter(root / "importable.f1ecap"):
        pass
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=port,
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                assert (await client.get("/api/v1/recordings/current")).json()["data"] is None
                assert (await client.post("/api/v1/recordings/start")).status_code == 403

                started = await client.post("/api/v1/recordings/start", headers=_auth())
                assert started.status_code == 202
                first_job = started.json()["data"]
                assert first_job["status"] in {"starting", "recording"}
                assert first_job["published"] is False
                assert first_job["progress"]["live_telemetry"]["status"] == "waiting"
                assert first_job["progress"]["live_car_status"]["status"] == "waiting"

                repeated = await client.post("/api/v1/recordings/start", headers=_auth())
                assert repeated.status_code == 202
                assert repeated.json()["data"]["recording_id"] == first_job["recording_id"]

                sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    raw = make_datagram(packet_id=1, body=b"", sequence=91)
                    sender.sendto(raw.payload, ("127.0.0.1", port))
                    raw_unknown = make_datagram(
                        packet_id=255, packet_version=255, body=b"future", sequence=92
                    )
                    sender.sendto(raw_unknown.payload, ("127.0.0.1", port))
                    for _ in range(100):
                        current = (
                            await client.get("/api/v1/recordings/current")
                        ).json()["data"]
                        if current and current["progress"]["recorded"] >= 2:
                            break
                        await asyncio.sleep(0.01)
                    assert current["progress"]["recorded"] == 2
                finally:
                    sender.close()

                existing_source = (
                    await client.get("/api/v1/recording-sources")
                ).json()["data"][0]
                busy = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": existing_source["capture_id"]},
                    headers=_auth(),
                )
                assert busy.status_code == 409

                stopped = await client.post(
                    f"/api/v1/recordings/{first_job['recording_id']}/stop",
                    headers=_auth(),
                )
                assert stopped.status_code == 202
                assert stopped.json()["data"]["status"] == "stopping"

                for _ in range(100):
                    completed = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if completed["status"] in {"complete", "failed", "interrupted"}:
                        break
                    await asyncio.sleep(0.01)
                assert completed["status"] == "complete"
                assert completed["published"] is True
                assert completed["summary"]["received"] == 2
                assert completed["summary"]["recorded"] == 2
                assert completed["progress"] is None
                assert "completed_lap_attempts" not in completed["summary"]
                assert "lap_attempts" not in completed["summary"]

                files = list(root.glob("f1e-*.f1ecap"))
                assert len(files) == 1
                with CaptureReader(files[0]) as reader:
                    datagrams = list(reader)
                    assert reader.complete
                    assert reader.completion["status"] == "complete"
                    assert (
                        completed["summary"]["completed_frames"]
                        == reader.completion["completed_frames"]
                    )
                    assert (
                        completed["summary"]["session_context_decode_errors"]
                        == reader.completion["session_context_decode_errors"]
                    )
                assert [item.payload for item in datagrams] == [
                    raw.payload,
                    raw_unknown.payload,
                ]

                inbox = await client.get("/api/v1/recording-sources")
                assert inbox.status_code == 200
                assert any(item["display_name"] == files[0].name for item in inbox.json()["data"])

                repeated_stops = await asyncio.gather(
                    *(
                        client.post(
                            f"/api/v1/recordings/{first_job['recording_id']}/stop",
                            headers=_auth(),
                        )
                        for _ in range(8)
                    )
                )
                assert all(response.status_code == 202 for response in repeated_stops)
                assert all(
                    response.json()["data"]["status"]
                    in {"stopping", "complete"}
                    for response in repeated_stops
                )
                final_status = (
                    await client.get("/api/v1/recordings/current")
                ).json()["data"]
                assert final_status["status"] == "complete"

    asyncio.run(exercise())


def test_recording_start_is_rejected_while_import_is_running(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original_import = import_controller_module.import_capture

    def delayed_import(*args, **kwargs):
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("recording test did not release the import")
        return original_import(*args, **kwargs)

    monkeypatch.setattr(import_controller_module, "import_capture", delayed_import)
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    with CaptureWriter(root / "ready.f1ecap"):
        pass
    port = _free_udp_port()
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=port,
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                source = (
                    await client.get("/api/v1/recording-sources")
                ).json()["data"][0]
                imported = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": source["capture_id"]},
                    headers=_auth(),
                )
                assert imported.status_code == 202
                assert await asyncio.to_thread(entered.wait, 2)
                repeated_import = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": source["capture_id"]},
                    headers=_auth(),
                )
                assert repeated_import.status_code == 202
                assert (
                    repeated_import.json()["data"]["job_id"]
                    == imported.json()["data"]["job_id"]
                )
                recording = await client.post(
                    "/api/v1/recordings/start", headers=_auth()
                )
                assert recording.status_code == 409
                assert recording.json()["reason"] == "another_local_operation_is_in_progress"
                release.set()
                job_id = imported.json()["data"]["job_id"]
                for _ in range(100):
                    status = (
                        await client.get(f"/api/v1/import-jobs/{job_id}")
                    ).json()["data"]
                    if status["status"] in {"complete", "failed", "interrupted"}:
                        break
                    await asyncio.sleep(0.01)
                assert status["status"] == "complete"

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_completed_import_keeps_reservation_until_worker_releases_it(
    tmp_path, monkeypatch
):
    worker_at_release = threading.Event()
    allow_release = threading.Event()
    original_release = import_controller_module.ImportController.release_operation

    def delayed_release(controller, operation):
        if (
            operation == "import"
            and threading.current_thread().name.startswith("f1-capture-import")
        ):
            worker_at_release.set()
            if not allow_release.wait(timeout=5):
                raise TimeoutError("import reservation test did not release the worker")
        return original_release(controller, operation)

    monkeypatch.setattr(
        import_controller_module.ImportController,
        "release_operation",
        delayed_release,
    )
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    with CaptureWriter(root / "first.f1ecap"):
        pass
    with CaptureWriter(root / "second.f1ecap"):
        pass
    app = create_app(database, recordings_root=root, control_token=TOKEN)

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                sources = (await client.get("/api/v1/recording-sources")).json()["data"]
                first = next(item for item in sources if item["display_name"] == "first.f1ecap")
                second = next(item for item in sources if item["display_name"] == "second.f1ecap")
                imported = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": first["capture_id"]},
                    headers=_auth(),
                )
                job_id = imported.json()["data"]["job_id"]
                assert await asyncio.to_thread(worker_at_release.wait, 4)
                completed = (
                    await client.get(f"/api/v1/import-jobs/{job_id}")
                ).json()["data"]
                assert completed["status"] == "complete"

                next_import = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": second["capture_id"]},
                    headers=_auth(),
                )
                assert next_import.status_code == 409
                assert (
                    next_import.json()["reason"]
                    == "another_local_operation_is_in_progress"
                )
                recording = await client.post(
                    "/api/v1/recordings/start", headers=_auth()
                )
                assert recording.status_code == 409
                assert (
                    recording.json()["reason"]
                    == "another_local_operation_is_in_progress"
                )
                allow_release.set()

    try:
        asyncio.run(exercise())
    finally:
        allow_release.set()


def test_stop_while_waiting_finalizes_an_empty_capture(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    port = _free_udp_port()
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=port,
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                started = await client.post("/api/v1/recordings/start", headers=_auth())
                recording_id = started.json()["data"]["recording_id"]
                stopped = await client.post(
                    f"/api/v1/recordings/{recording_id}/stop", headers=_auth()
                )
                assert stopped.status_code == 202
                for _ in range(100):
                    current = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if current["status"] in {"complete", "failed", "interrupted"}:
                        break
                    await asyncio.sleep(0.01)
                assert current["status"] == "complete"
                assert current["summary"]["recorded"] == 0
                assert current["published"] is True
                capture = next(root.glob("f1e-*.f1ecap"))
                with CaptureReader(capture) as reader:
                    assert list(reader) == []
                    assert reader.complete

    asyncio.run(exercise())


def test_stop_waits_for_in_progress_disk_write_before_publication(tmp_path, monkeypatch):
    write_started = threading.Event()
    release_write = threading.Event()
    original_write = CaptureWriter.write

    def delayed_write(writer, raw):
        write_started.set()
        if not release_write.wait(timeout=5):
            raise TimeoutError("recording test did not release the pending write")
        original_write(writer, raw)

    monkeypatch.setattr(CaptureWriter, "write", delayed_write)
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    port = _free_udp_port()
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=port,
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                started = await client.post("/api/v1/recordings/start", headers=_auth())
                recording_id = started.json()["data"]["recording_id"]
                for _ in range(100):
                    current = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if current["progress"]["state"] == "recording":
                        break
                    await asyncio.sleep(0.01)
                assert current["progress"]["state"] == "recording"
                raw = make_datagram(packet_id=255, body=b"queued")
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(raw.payload, ("127.0.0.1", port))
                    assert await asyncio.to_thread(write_started.wait, 2)
                    stopping = await client.post(
                        f"/api/v1/recordings/{recording_id}/stop", headers=_auth()
                    )
                    assert stopping.status_code == 202
                    assert stopping.json()["data"]["status"] == "stopping"
                    release_write.set()

                for _ in range(100):
                    result = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if result["status"] in {"complete", "failed", "interrupted"}:
                        break
                    await asyncio.sleep(0.01)
                assert result["status"] == "complete"
                capture = next(root.glob("f1e-*.f1ecap"))
                with CaptureReader(capture) as reader:
                    datagrams = list(reader)
                    assert reader.complete
                assert [item.payload for item in datagrams] == [raw.payload]

    try:
        asyncio.run(exercise())
    finally:
        release_write.set()


def test_publication_collision_never_replaces_an_existing_capture(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    port = _free_udp_port()
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=port,
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                started = await client.post("/api/v1/recordings/start", headers=_auth())
                recording_id = started.json()["data"]["recording_id"]
                paths = get_recording_job_paths(database, recording_id)
                assert paths is not None
                final_path = root / paths["final_relative_path"]
                with CaptureWriter(final_path):
                    pass
                polled = (
                    await client.get("/api/v1/recordings/current")
                ).json()["data"]
                assert polled["recording_id"] == recording_id
                assert polled["status"] in {"starting", "recording"}
                await client.post(
                    f"/api/v1/recordings/{recording_id}/stop", headers=_auth()
                )
                for _ in range(100):
                    result = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if result["status"] in {"complete", "failed", "interrupted"}:
                        break
                    await asyncio.sleep(0.01)
                assert result["status"] == "failed"
                assert result["failure_reason"] == "capture_publication_collision"
                with CaptureReader(final_path) as reader:
                    assert list(reader) == []
                    assert reader.complete
                assert (root / paths["staging_relative_path"]).exists()

    asyncio.run(exercise())


def test_staging_cleanup_failure_keeps_published_capture_complete(
    tmp_path, monkeypatch
):
    original_unlink = Path.unlink

    def fail_staging_cleanup(path, *args, **kwargs):
        if path.name.startswith(".f1e-recording-") and path.suffix == ".part":
            raise OSError("simulated staging cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_staging_cleanup)
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    port = _free_udp_port()
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=port,
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                started = await client.post("/api/v1/recordings/start", headers=_auth())
                recording_id = started.json()["data"]["recording_id"]
                paths = get_recording_job_paths(database, recording_id)
                assert paths is not None
                stopped = await client.post(
                    f"/api/v1/recordings/{recording_id}/stop", headers=_auth()
                )
                assert stopped.status_code == 202

                for _ in range(100):
                    current = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if current["status"] in {"complete", "failed", "interrupted"}:
                        break
                    await asyncio.sleep(0.01)

                assert current["status"] == "complete"
                assert current["published"] is True
                assert (root / paths["final_relative_path"]).is_file()
                assert (root / paths["staging_relative_path"]).is_file()

    try:
        asyncio.run(exercise())
    finally:
        monkeypatch.undo()


def test_status_request_reconciles_post_publication_database_failure(
    tmp_path, monkeypatch
):
    original_update = recording_controller_module.update_recording_job
    complete_updates = 0

    def fail_first_complete_updates(database_path, recording_id, **kwargs):
        nonlocal complete_updates
        if kwargs.get("status") == "complete":
            complete_updates += 1
            if complete_updates <= 2:
                raise OSError("simulated transient database failure")
        return original_update(database_path, recording_id, **kwargs)

    monkeypatch.setattr(
        recording_controller_module,
        "update_recording_job",
        fail_first_complete_updates,
    )
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    port = _free_udp_port()
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=port,
    )

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                started = await client.post("/api/v1/recordings/start", headers=_auth())
                recording_id = started.json()["data"]["recording_id"]
                await client.post(
                    f"/api/v1/recordings/{recording_id}/stop", headers=_auth()
                )

                for _ in range(100):
                    current = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if current["status"] == "complete":
                        break
                    await asyncio.sleep(0.01)

                assert current["status"] == "complete"
                assert current["published"] is True
                assert complete_updates >= 3
                assert len(list(root.glob("f1e-*.f1ecap"))) == 1

    asyncio.run(exercise())


def test_restart_marks_recording_interrupted_and_preserves_staging(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    staging = root / ".f1e-recording-preserved.part"
    staging.write_bytes(b"partial capture bytes")
    with Database(database):
        pass
    job, created = create_recording_job(
        database,
        recording_id="b" * 32,
        staging_relative_path=staging.name,
        final_relative_path="f1e-interrupted.f1ecap",
        bind_host="127.0.0.1",
        bind_port=20777,
    )
    assert created
    update_recording_job(database, job["recording_id"], status="recording", starting=True)

    app = create_app(database, recordings_root=root, control_token=TOKEN)

    async def recover():
        async with app.router.lifespan_context(app):
            recovered = get_recording_job(database, job["recording_id"])
            assert recovered is not None
            assert recovered["status"] == "interrupted"
            assert recovered["failure_reason"] == "api_restarted_before_recording_completed"
            assert staging.read_bytes() == b"partial capture bytes"

    asyncio.run(recover())
