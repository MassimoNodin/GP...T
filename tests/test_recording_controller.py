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
    create_recording_group,
    create_recording_group_segment,
    create_recording_job,
    get_recording_group,
    get_recording_job,
    get_recording_job_paths,
    mark_recording_segment_started,
    recover_abandoned_recording_groups,
    request_recording_group_transition,
    settle_recording_segment_complete,
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
                assert first_job["progress"]["live_lap_timing"]["status"] == "waiting"

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


def test_background_reconciler_settles_post_publication_database_failure(
    tmp_path, monkeypatch
):
    original_settle = recording_controller_module.settle_recording_segment_complete
    settlement_attempts = 0

    def fail_first_settlements(database_path, recording_id, **kwargs):
        nonlocal settlement_attempts
        settlement_attempts += 1
        if settlement_attempts <= 2:
            raise OSError("simulated transient database failure")
        return original_settle(database_path, recording_id, **kwargs)

    monkeypatch.setattr(
        recording_controller_module,
        "settle_recording_segment_complete",
        fail_first_settlements,
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

                for _ in range(400):
                    current = (
                        await client.get("/api/v1/recordings/current")
                    ).json()["data"]
                    if current["status"] == "complete":
                        break
                    await asyncio.sleep(0.01)

                assert current["status"] == "complete"
                assert current["published"] is True
                assert settlement_attempts >= 3
                assert len(list(root.glob("f1e-*.f1ecap"))) == 1

    asyncio.run(exercise())


def test_delayed_pause_settlement_rechecks_group_after_resume_finishes(
    tmp_path, monkeypatch
):
    retry_committed_pause = threading.Event()
    allow_retry_bookkeeping = threading.Event()
    second_catalog_registration_started = threading.Event()
    allow_second_catalog_registration = threading.Event()
    original_settle = recording_controller_module.settle_recording_segment_complete
    original_list_sources = recording_controller_module.list_recording_sources
    first_recording_id: str | None = None
    first_segment_attempts = 0
    catalog_registration_count = 0

    def delay_retry_bookkeeping(database_path, recording_id, **kwargs):
        nonlocal first_segment_attempts
        if recording_id == first_recording_id:
            first_segment_attempts += 1
            if first_segment_attempts <= 2:
                raise OSError("simulated transient database failure")
            result = original_settle(database_path, recording_id, **kwargs)
            if first_segment_attempts == 3:
                retry_committed_pause.set()
                if not allow_retry_bookkeeping.wait(timeout=8):
                    raise TimeoutError("stale-settlement test did not release reconciliation")
            return result
        return original_settle(database_path, recording_id, **kwargs)

    def delay_second_catalog_registration(database_path, recordings_root):
        nonlocal catalog_registration_count
        catalog_registration_count += 1
        if catalog_registration_count == 1:
            second_catalog_registration_started.set()
            if not allow_second_catalog_registration.wait(timeout=8):
                raise TimeoutError("catalog-registration test did not release worker")
        return original_list_sources(database_path, recordings_root)

    monkeypatch.setattr(
        recording_controller_module,
        "settle_recording_segment_complete",
        delay_retry_bookkeeping,
    )
    monkeypatch.setattr(
        recording_controller_module,
        "list_recording_sources",
        delay_second_catalog_registration,
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

    async def wait_for(client, path, predicate):
        for _ in range(300):
            response = await client.get(path)
            assert response.status_code == 200
            data = response.json()["data"]
            if predicate(data):
                return data
            await asyncio.sleep(0.01)
        raise AssertionError(f"timed out waiting for {path}")

    async def exercise():
        nonlocal first_recording_id
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                started = await client.post(
                    "/api/v1/recording-groups/start", headers=_auth()
                )
                first_group = started.json()["data"]
                group_id = first_group["group_id"]
                first_recording_id = first_group["current_recording_id"]
                assert first_recording_id
                group = await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "recording",
                )
                pause = await client.post(
                    f"/api/v1/recording-groups/{group_id}/pause",
                    headers=_auth(),
                    json={
                        "expected_revision": group["transition_revision"],
                        "expected_recording_id": first_recording_id,
                    },
                )
                assert pause.status_code == 202
                assert await asyncio.to_thread(retry_committed_pause.wait, 4)
                paused = await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "paused",
                )

                resumed = await client.post(
                    f"/api/v1/recording-groups/{group_id}/resume",
                    headers=_auth(),
                    json={
                        "expected_revision": paused["transition_revision"],
                        "expected_recording_id": first_recording_id,
                    },
                )
                assert resumed.status_code == 202
                group = await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "recording"
                    and item["segment_count"] == 2,
                )
                second_recording_id = group["current_recording_id"]
                assert second_recording_id and second_recording_id != first_recording_id
                raw = make_datagram(packet_id=255, body=b"resumed-segment")
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(raw.payload, ("127.0.0.1", port))
                    await wait_for(
                        client,
                        "/api/v1/recordings/current",
                        lambda item: item["recording_id"] == second_recording_id
                        and item["progress"] is not None
                        and item["progress"]["recorded"] == 1,
                    )
                stopped = await client.post(
                    f"/api/v1/recording-groups/{group_id}/stop", headers=_auth()
                )
                assert stopped.status_code == 202
                complete = await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "complete",
                )
                assert complete["segment_count"] == 2
                assert await asyncio.to_thread(
                    second_catalog_registration_started.wait, 4
                )

                allow_retry_bookkeeping.set()
                controller = app.state.recording_controller
                for _ in range(200):
                    with controller._lock:
                        settlement_pending = controller._settlement_pending is not None
                    if not settlement_pending:
                        break
                    await asyncio.sleep(0.01)
                assert not settlement_pending
                blocked_start = await client.post(
                    "/api/v1/recording-groups/start", headers=_auth()
                )
                assert blocked_start.status_code == 409
                assert not allow_second_catalog_registration.is_set()
                allow_second_catalog_registration.set()
                for _ in range(100):
                    next_started = await client.post(
                        "/api/v1/recording-groups/start", headers=_auth()
                    )
                    if next_started.status_code == 202:
                        break
                    assert next_started.status_code == 409
                    await asyncio.sleep(0.02)
                assert next_started.status_code == 202
                next_group = next_started.json()["data"]
                await client.post(
                    f"/api/v1/recording-groups/{next_group['group_id']}/stop",
                    headers=_auth(),
                )
                await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "complete",
                )

    try:
        asyncio.run(exercise())
    finally:
        allow_retry_bookkeeping.set()
        allow_second_catalog_registration.set()


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


def test_recording_group_pause_resume_creates_independent_segments(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    with CaptureWriter(root / "ready-to-import.f1ecap"):
        pass
    port = _free_udp_port()
    app = create_app(
        database,
        recordings_root=root,
        control_token=TOKEN,
        recording_host="127.0.0.1",
        recording_port=port,
    )

    async def wait_for(client, path, predicate):
        for _ in range(200):
            response = await client.get(path)
            assert response.status_code == 200
            data = response.json()["data"]
            if predicate(data):
                return data
            await asyncio.sleep(0.01)
        raise AssertionError(f"timed out waiting for {path}")

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                started = await client.post(
                    "/api/v1/recording-groups/start", headers=_auth()
                )
                assert started.status_code == 202
                group = started.json()["data"]
                assert group["segment_count"] == 1
                group_id = group["group_id"]
                group = await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "recording",
                )
                segment_one_id = group["current_recording_id"]
                assert segment_one_id

                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    pause = await client.post(
                        f"/api/v1/recording-groups/{group_id}/pause",
                        headers=_auth(),
                        json={
                            "expected_revision": group["transition_revision"],
                            "expected_recording_id": segment_one_id,
                        },
                    )
                    assert pause.status_code == 202
                    paused = await wait_for(
                        client,
                        "/api/v1/recording-groups/current",
                        lambda item: item["status"] == "paused",
                    )

                    page_one = await client.get(
                        f"/api/v1/recording-groups/{group_id}/segments?limit=50&offset=0"
                    )
                    assert page_one.status_code == 200
                    segment_one = page_one.json()["data"]["items"][0]
                    assert segment_one["recording_id"] == segment_one_id
                    assert segment_one["status"] == "complete"
                    assert segment_one["published"] is True
                    assert segment_one["summary"]["recorded"] == 0

                    sources = (await client.get("/api/v1/recording-sources")).json()["data"]
                    import_source = next(
                        source for source in sources
                        if source["display_name"] == "ready-to-import.f1ecap"
                    )
                    blocked_import = await client.post(
                        "/api/v1/import-jobs",
                        headers=_auth(),
                        json={"capture_id": import_source["capture_id"]},
                    )
                    assert blocked_import.status_code == 409
                    assert blocked_import.json()["reason"] == "another_local_operation_is_in_progress"

                    resumed = await client.post(
                        f"/api/v1/recording-groups/{group_id}/resume",
                        headers=_auth(),
                        json={
                            "expected_revision": paused["transition_revision"],
                            "expected_recording_id": segment_one_id,
                        },
                    )
                    assert resumed.status_code == 202
                    assert resumed.json()["data"]["status"] == "resuming"
                    group = await wait_for(
                        client,
                        "/api/v1/recording-groups/current",
                        lambda item: item["status"] == "recording"
                        and item["segment_count"] == 2,
                    )
                    segment_two_id = group["current_recording_id"]
                    assert segment_two_id and segment_two_id != segment_one_id
                    raw_one = make_datagram(packet_id=255, body=b"segment-two")
                    sender.sendto(raw_one.payload, ("127.0.0.1", port))
                    await wait_for(
                        client,
                        "/api/v1/recordings/current",
                        lambda item: item["recording_id"] == segment_two_id
                        and item["progress"] is not None
                        and item["progress"]["recorded"] == 1,
                    )
                    pause_again = await client.post(
                        f"/api/v1/recording-groups/{group_id}/pause",
                        headers=_auth(),
                        json={
                            "expected_revision": group["transition_revision"],
                            "expected_recording_id": segment_two_id,
                        },
                    )
                    assert pause_again.status_code == 202
                    paused_again = await wait_for(
                        client,
                        "/api/v1/recording-groups/current",
                        lambda item: item["status"] == "paused",
                    )
                    resumed_again = await client.post(
                        f"/api/v1/recording-groups/{group_id}/resume",
                        headers=_auth(),
                        json={
                            "expected_revision": paused_again["transition_revision"],
                            "expected_recording_id": segment_two_id,
                        },
                    )
                    assert resumed_again.status_code == 202
                    group = await wait_for(
                        client,
                        "/api/v1/recording-groups/current",
                        lambda item: item["status"] == "recording"
                        and item["segment_count"] == 3,
                    )
                    segment_three_id = group["current_recording_id"]
                    assert segment_three_id not in {segment_one_id, segment_two_id}
                    raw_two = make_datagram(packet_id=255, body=b"segment-three")
                    sender.sendto(raw_two.payload, ("127.0.0.1", port))
                    await wait_for(
                        client,
                        "/api/v1/recordings/current",
                        lambda item: item["recording_id"] == segment_three_id
                        and item["progress"] is not None
                        and item["progress"]["recorded"] == 1,
                    )

                stopped = await client.post(
                    f"/api/v1/recording-groups/{group_id}/stop", headers=_auth()
                )
                assert stopped.status_code == 202
                complete = await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "complete",
                )
                assert complete["segment_count"] == 3
                page = await client.get(
                    f"/api/v1/recording-groups/{group_id}/segments?limit=50&offset=0"
                )
                segments = page.json()["data"]["items"]
                assert [item["segment_ordinal"] for item in segments] == [1, 2, 3]
                assert [item["status"] for item in segments] == ["complete"] * 3
                assert [item["summary"]["recorded"] for item in segments] == [0, 1, 1]

                events = (
                    await client.get(f"/api/v1/recording-groups/{group_id}/events")
                ).json()["data"]
                kinds = [event["event_kind"] for event in events]
                assert kinds.count("pause_acknowledged") == 2
                assert kinds.count("acquisition_started") == 3

                captures = sorted(root.glob("f1e-*.f1ecap"))
                assert len(captures) == 3
                for capture_path in captures:
                    with CaptureReader(capture_path) as reader:
                        datagrams = list(reader)
                        assert reader.complete
                        assert reader.metadata["recording_group_id"] == group_id
                        ordinal = reader.metadata["segment_ordinal"]
                        assert reader.metadata["segment_ordinal"] == ordinal
                        if ordinal == 1:
                            assert reader.metadata["recording_id"] == segment_one_id
                            expected_payloads = []
                        else:
                            if ordinal == 2:
                                assert reader.metadata["recording_id"] == segment_two_id
                                expected_payloads = [raw_one.payload]
                            else:
                                assert ordinal == 3
                                assert reader.metadata["recording_id"] == segment_three_id
                                expected_payloads = [raw_two.payload]
                    assert [item.payload for item in datagrams] == expected_payloads

    asyncio.run(exercise())


def test_stopping_paused_group_keeps_reservation_until_worker_exits(
    tmp_path, monkeypatch
):
    worker_at_catalog = threading.Event()
    allow_worker_exit = threading.Event()
    original_list_sources = recording_controller_module.list_recording_sources

    def delayed_catalog(*args, **kwargs):
        if threading.current_thread().name == "f1-managed-udp-recording-segment":
            worker_at_catalog.set()
            if not allow_worker_exit.wait(timeout=5):
                raise TimeoutError("paused-stop test did not release the segment worker")
        return original_list_sources(*args, **kwargs)

    monkeypatch.setattr(
        recording_controller_module, "list_recording_sources", delayed_catalog
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

    async def wait_for(client, path, predicate):
        for _ in range(200):
            response = await client.get(path)
            assert response.status_code == 200
            data = response.json()["data"]
            if predicate(data):
                return data
            await asyncio.sleep(0.01)
        raise AssertionError(f"timed out waiting for {path}")

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                started = await client.post(
                    "/api/v1/recording-groups/start", headers=_auth()
                )
                group = started.json()["data"]
                group_id = group["group_id"]
                group = await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "recording",
                )
                pause = await client.post(
                    f"/api/v1/recording-groups/{group_id}/pause",
                    headers=_auth(),
                    json={
                        "expected_revision": group["transition_revision"],
                        "expected_recording_id": group["current_recording_id"],
                    },
                )
                assert pause.status_code == 202
                await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "paused",
                )
                assert await asyncio.to_thread(worker_at_catalog.wait, 2)

                stop_task = asyncio.create_task(
                    client.post(
                        f"/api/v1/recording-groups/{group_id}/stop",
                        headers=_auth(),
                    )
                )
                await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "complete",
                )
                blocked_start = await client.post(
                    "/api/v1/recording-groups/start", headers=_auth()
                )
                assert blocked_start.status_code == 409
                assert blocked_start.json()["reason"] == "another_local_operation_is_in_progress"

                allow_worker_exit.set()
                stopped = await stop_task
                assert stopped.status_code == 202
                next_started = await client.post(
                    "/api/v1/recording-groups/start", headers=_auth()
                )
                assert next_started.status_code == 202
                next_group = next_started.json()["data"]
                await client.post(
                    f"/api/v1/recording-groups/{next_group['group_id']}/stop",
                    headers=_auth(),
                )
                await wait_for(
                    client,
                    "/api/v1/recording-groups/current",
                    lambda item: item["status"] == "complete",
                )

    try:
        asyncio.run(exercise())
    finally:
        allow_worker_exit.set()


def test_recovered_stop_rebuilds_group_counters_from_completed_segments(tmp_path):
    database = tmp_path / "archive.sqlite3"
    with Database(database):
        pass
    group_id = "c" * 32
    first_id = "d" * 32
    second_id = "e" * 32
    group, first, created = create_recording_group(
        database,
        group_id=group_id,
        recording_id=first_id,
        staging_relative_path="first.part",
        final_relative_path="first.f1ecap",
        bind_host="127.0.0.1",
        bind_port=20777,
    )
    assert created
    mark_recording_segment_started(database, first_id)
    group = get_recording_group(database, group_id)
    assert group is not None
    pausing = request_recording_group_transition(
        database,
        group_id,
        target_status="pausing",
        event_kind="pause_requested",
        expected_status="recording",
        expected_revision=group["transition_revision"],
        expected_recording_id=first_id,
    )
    first_summary = {"received": 10, "recorded": 9, "queue_dropped": 1}
    _, paused = settle_recording_segment_complete(
        database, first_id, summary=first_summary
    )
    assert pausing["status"] == "pausing"
    assert paused is not None and paused["status"] == "paused"
    _, second = create_recording_group_segment(
        database,
        group_id=group_id,
        expected_revision=paused["transition_revision"],
        expected_recording_id=first_id,
        recording_id=second_id,
        staging_relative_path="second.part",
        final_relative_path="second.f1ecap",
    )
    mark_recording_segment_started(database, second_id)
    group = get_recording_group(database, group_id)
    assert group is not None
    request_recording_group_transition(
        database,
        group_id,
        target_status="stopping",
        event_kind="stop_requested",
        expected_status="recording",
        expected_revision=group["transition_revision"],
        expected_recording_id=second_id,
    )
    second_summary = {"received": 4, "recorded": 4, "queue_dropped": 0}
    update_recording_job(
        database,
        second_id,
        status="complete",
        summary=second_summary,
        finished=True,
    )

    recover_abandoned_recording_groups(database)

    recovered = get_recording_group(database, group_id)
    assert recovered is not None
    assert recovered["status"] == "complete"
    assert recovered["summary"] == {
        "segment_count": 2,
        "received": 14,
        "recorded": 13,
        "queue_dropped": 1,
    }
