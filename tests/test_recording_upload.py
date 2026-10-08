from __future__ import annotations

import hashlib
import asyncio
import os
import socket
import struct
import threading
import time

import pytest
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient
from starlette.requests import Request

from f1_engineer.api.app import create_app
from f1_engineer.api import recording_upload as upload_api
from f1_engineer.recording.capture import CaptureReader, CaptureWriter
from f1_engineer.storage.database import Database
from f1_engineer.storage.import_jobs import list_recording_sources_page
from f1_engineer.storage import recording_upload as upload_storage
from f1_engineer.storage.recording_upload import RecordingUploadError
from tests.helpers import make_datagram


TOKEN = "test-local-control-token-which-is-long-enough"


def _capture_bytes(tmp_path, *, incomplete: bool = False) -> bytes:
    path = tmp_path / "source.f1ecap"
    with CaptureWriter(path, {"track": "melbourne"}) as writer:
        writer.write(make_datagram(sequence=4, body=b"upload-validation"))
    content = path.read_bytes()
    if incomplete:
        footer = (
            b"\x02"
            + struct.pack("!I", len(b'{"status":"complete"}'))
            + b'{"status":"complete"}'
        )
        assert content.endswith(footer)
        content = content[: -len(footer)]
    return content


def _app(tmp_path, *, max_bytes=None):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.bind(("127.0.0.1", 0))
        port = udp_socket.getsockname()[1]
    kwargs = {"max_recording_upload_bytes": max_bytes} if max_bytes else {}
    return database, root, create_app(
        database,
        automatic_acquisition=False,
        recordings_root=root,
        control_token=TOKEN,
        recording_port=port,
        **kwargs,
    )


def _post(client, content, *, filename="melbourne practice.f1ecap", headers=None):
    request_headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/octet-stream",
        "X-Capture-Upload-Filename": __import__("urllib.parse").parse.quote(
            filename, safe=""
        ),
    }
    request_headers.update(headers or {})
    return client.post(
        "/api/v1/recording-sources/upload",
        content=content,
        headers=request_headers,
    )


def test_recording_upload_registers_exact_bytes_and_returns_scoped_receipt(tmp_path):
    database, root, app = _app(tmp_path)
    original = _capture_bytes(tmp_path)

    with TestClient(app) as client:
        response = _post(client, original)
        assert response.status_code == 201
        body = response.json()
        receipt = body["data"]
        assert body["status"] == "ok"
        assert receipt["byte_size"] == len(original)
        assert receipt["sha256"] == hashlib.sha256(original).hexdigest()
        assert receipt["verification_scope"] == "capture_header_and_transferred_bytes"
        assert receipt["display_name"].startswith("f1e-upload-")
        assert receipt["display_name"].endswith("-melbourne practice.f1ecap")
        stored = root / receipt["display_name"]
        assert stored.read_bytes() == original
        assert not list(root.glob(".*.f1e-uploading"))

        page = list_recording_sources_page(
            database,
            root,
            selected_capture_id=receipt["capture_id"],
        )
        assert page["selected_capture"]["capture_id"] == receipt["capture_id"]
        assert page["selected_capture"]["display_name"] == receipt["display_name"]
        with Database(database, read_only=True) as db:
            assert db.connection.execute("SELECT COUNT(*) FROM import_jobs").fetchone()[0] == 0


def test_recording_upload_accepts_chunked_body_without_content_length(tmp_path):
    _database, root, app = _app(tmp_path)
    original = _capture_bytes(tmp_path)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/octet-stream",
        "X-Capture-Upload-Filename": "chunked.f1ecap",
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recording-sources/upload",
            content=iter([original[:9], original[9:31], original[31:]]),
            headers=headers,
        )
        assert response.status_code == 201
        receipt = response.json()["data"]
        assert receipt["byte_size"] == len(original)
        assert (root / receipt["display_name"]).read_bytes() == original


def test_recording_upload_accepts_header_valid_incomplete_capture_unchanged(tmp_path):
    _database, root, app = _app(tmp_path)
    original = _capture_bytes(tmp_path, incomplete=True)
    with CaptureReader(tmp_path / "source.f1ecap") as reader:
        assert reader.metadata["schema_version"] == 1

    with TestClient(app) as client:
        response = _post(client, original, filename="recovered.f1ecap")
        assert response.status_code == 201
        receipt = response.json()["data"]
        assert (root / receipt["display_name"]).read_bytes() == original


@pytest.mark.parametrize(
    ("content_type", "filename", "expected_status", "expected_reason"),
    [
        ("application/json", "capture.f1ecap", 415, "recording_upload_content_type_invalid"),
        ("application/octet-stream", "notes.txt", 422, "recording_upload_filename_invalid"),
    ],
)
def test_recording_upload_rejects_invalid_request_metadata(
    tmp_path, content_type, filename, expected_status, expected_reason
):
    _database, root, app = _app(tmp_path)
    with TestClient(app) as client:
        response = _post(
            client,
            b"ignored",
            filename=filename,
            headers={"Content-Type": content_type},
        )
    assert response.status_code == expected_status
    assert response.json()["reason"] == expected_reason
    assert not list(root.glob("*.f1ecap"))


def test_recording_upload_requires_local_control_authorization(tmp_path):
    _database, root, app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recording-sources/upload",
            content=b"untrusted",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Capture-Upload-Filename": "capture.f1ecap",
            },
        )
    assert response.status_code == 401
    assert response.json()["reason"] == "local_control_unauthorized"
    assert not list(root.glob(".*.f1e-uploading"))


def test_recording_upload_enforces_configured_length_cap_before_reading(tmp_path):
    _database, root, app = _app(tmp_path, max_bytes=16)
    with TestClient(app) as client:
        response = _post(client, b"x" * 17)
    assert response.status_code == 413
    assert response.json()["reason"] == "recording_upload_size_limit"
    assert not list(root.glob(".*.f1e-uploading"))


def test_recording_upload_enforces_actual_size_without_declared_length(tmp_path):
    _database, root, app = _app(tmp_path, max_bytes=16)
    with TestClient(app):
        upload = app.state.recording_upload_service.begin_upload("capture.f1ecap")
        with pytest.raises(RecordingUploadError, match="recording_upload_size_limit"):
            upload.write_chunk(b"x" * 17)
        upload.abort()
    assert not list(root.glob(".*.f1e-uploading"))


def test_recording_upload_rejects_bad_capture_header_and_releases_operation(tmp_path):
    _database, root, app = _app(tmp_path)
    with TestClient(app) as client:
        response = _post(client, b"not a capture")
        assert response.status_code == 422
        assert response.json()["reason"] == "recording_upload_capture_header_invalid"
        assert not list(root.glob(".*.f1e-uploading"))

        original = _capture_bytes(tmp_path)
        accepted = _post(client, original)
        assert accepted.status_code == 201


@pytest.mark.parametrize("operation", ["recording", "replay", "import"])
def test_recording_upload_conflicts_with_reserved_local_operations(tmp_path, operation):
    _database, root, app = _app(tmp_path)
    original = _capture_bytes(tmp_path)
    with TestClient(app) as client:
        controller = app.state.import_controller
        assert controller.reserve_operation(operation)
        try:
            response = _post(client, original)
        finally:
            controller.release_operation(operation)
    assert response.status_code == 409
    assert response.json()["reason"] == "another_local_operation_is_in_progress"
    assert not list(root.glob(".*.f1e-uploading"))


def test_recording_upload_reservation_blocks_replay_until_aborted(tmp_path):
    _database, root, app = _app(tmp_path)
    with TestClient(app):
        service = app.state.recording_upload_service
        upload = service.begin_upload("capture.f1ecap")
        assert not app.state.import_controller.reserve_operation("replay")
        upload.write_chunk(b"partial")
        upload.abort()
        assert app.state.import_controller.reserve_operation("replay")
        app.state.import_controller.release_operation("replay")
    assert not list(root.glob(".*.f1e-uploading"))


def test_recording_upload_startup_removes_only_its_stale_regular_staging_files(tmp_path):
    _database, root, app = _app(tmp_path)
    root.mkdir()
    stale = root / ("." + "a" * 32 + ".f1e-uploading")
    stale.write_bytes(b"partial")
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve", encoding="utf-8")
    link = root / ("." + "b" * 32 + ".f1e-uploading")
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        link = None

    with TestClient(app):
        assert not stale.exists()
        assert outside.read_text(encoding="utf-8") == "preserve"
        if link is not None:
            assert link.is_symlink()


def test_recording_upload_rejects_destination_collision_without_overwrite(
    tmp_path, monkeypatch
):
    _database, root, app = _app(tmp_path)
    token = "d" * 32
    existing = root / f"f1e-upload-{token}-capture.f1ecap"
    root.mkdir()
    existing.write_bytes(b"keep this capture")
    values = iter(["c" * 32, token, "e" * 32])
    monkeypatch.setattr(
        "f1_engineer.storage.recording_upload.uuid.uuid4",
        lambda: type("Id", (), {"hex": next(values)})(),
    )
    with TestClient(app):
        upload = app.state.recording_upload_service.begin_upload("capture.f1ecap")
        upload.write_chunk(_capture_bytes(tmp_path))
        with pytest.raises(RecordingUploadError, match="recording_upload_publish"):
            upload.finish()
    assert existing.read_bytes() == b"keep this capture"
    assert not list(root.glob(".*.f1e-uploading"))


def test_recording_upload_catalog_discovery_race_preserves_one_identity(
    tmp_path, monkeypatch
):
    database, root, app = _app(tmp_path)
    original = _capture_bytes(tmp_path)
    register = upload_storage.register_recording_source
    observed_ids: list[str] = []

    def discover_before_registration(
        database_path,
        recordings_root,
        relative_path,
        *,
        byte_size,
        modified_ns,
        expected_root_identity=None,
        expected_file_identity=None,
    ):
        page = list_recording_sources_page(
            database_path, recordings_root, query=relative_path
        )
        observed_ids.extend(item["capture_id"] for item in page["items"])
        return register(
            database_path,
            recordings_root,
            relative_path,
            byte_size=byte_size,
            modified_ns=modified_ns,
            expected_root_identity=expected_root_identity,
            expected_file_identity=expected_file_identity,
        )

    monkeypatch.setattr(
        upload_storage, "register_recording_source", discover_before_registration
    )
    with TestClient(app) as client:
        response = _post(client, original)
        assert response.status_code == 201
        receipt = response.json()["data"]
        assert observed_ids == [receipt["capture_id"]]
        selected = list_recording_sources_page(
            database,
            root,
            selected_capture_id=receipt["capture_id"],
        )["selected_capture"]
        assert selected["display_name"] == receipt["display_name"]


def test_recording_upload_rejects_staging_symlink_swap(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX dir-fd staging is covered on Windows by reparse checks")
    _database, root, app = _app(tmp_path)
    external = tmp_path / "outside.txt"
    external.write_bytes(b"preserve outside data")
    original = _capture_bytes(tmp_path)

    with TestClient(app):
        upload = app.state.recording_upload_service.begin_upload("capture.f1ecap")
        upload.write_chunk(original)
        stage = root / upload.stage_name
        moved = root / (upload.stage_name + ".saved")
        stage.rename(moved)
        stage.symlink_to(external)
        with pytest.raises(RecordingUploadError, match="recording_upload_file_unavailable"):
            upload.finish()
        moved.unlink()

    assert external.read_bytes() == b"preserve outside data"
    assert not list(root.glob("*.f1ecap"))


def test_recording_upload_rejects_recordings_root_symlink_swap(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX no-follow directory traversal is covered separately")
    _database, root, app = _app(tmp_path)
    backup = tmp_path / "recordings-original"
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    with TestClient(app):
        root.rename(backup)
        root.symlink_to(redirected, target_is_directory=True)
        try:
            with pytest.raises(RecordingUploadError, match="recording_upload"):
                app.state.recording_upload_service.begin_upload("capture.f1ecap")
        finally:
            root.unlink()
            backup.rename(root)
    assert not list(redirected.iterdir())


def test_registration_keeps_the_frozen_namespace_when_root_becomes_a_symlink(
    tmp_path,
):
    if os.name == "nt":
        pytest.skip("Windows root handles prevent replacement during upload")
    _database, root, app = _app(tmp_path)
    backup = tmp_path / "recordings-original"
    with TestClient(app):
        upload = app.state.recording_upload_service.begin_upload("capture.f1ecap")
        upload.write_chunk(_capture_bytes(tmp_path))
        root.rename(backup)
        root.symlink_to(backup, target_is_directory=True)
        try:
            with pytest.raises(
                RecordingUploadError,
                match="recording_catalog_registration_invalid",
            ):
                upload.finish()
        finally:
            root.unlink()
            for published in backup.glob("f1e-upload-*.f1ecap"):
                published.unlink()
            backup.rename(root)
    assert not list(root.glob("*.f1ecap"))


def test_windows_upload_pins_root_ancestors_marker_and_staging_entry(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows handle-pinning behavior is platform-specific")
    _database, root, app = _app(tmp_path)
    root.parent.mkdir(parents=True, exist_ok=True)
    with TestClient(app):
        upload = app.state.recording_upload_service.begin_upload("capture.f1ecap")
        marker = root / ".f1e-upload-root"
        stage = root / upload.stage_name
        backup = root.with_name(root.name + "-backup")
        parent_backup = root.parent.with_name(root.parent.name + "-backup")

        assert marker.read_bytes() == b"F1-ENGINEER-RECORDING-ROOT-V1\n"
        with pytest.raises(OSError):
            marker.unlink()
        with pytest.raises(OSError):
            marker.open("ab")
        with pytest.raises(OSError):
            stage.rename(root / (upload.stage_name + ".moved"))
        with pytest.raises(OSError):
            root.rename(backup)
        with pytest.raises(OSError):
            root.parent.rename(parent_backup)

        upload.abort()
        root.rename(backup)
        backup.rename(root)


def test_recording_upload_stall_and_deadline_failures_release_staging(
    tmp_path, monkeypatch
):
    _database, root, app = _app(tmp_path)

    async def run_transfer(timeout_kind: str) -> None:
        with TestClient(app):
            upload = app.state.recording_upload_service.begin_upload("capture.f1ecap")
            calls = 0

            async def receive():
                nonlocal calls
                calls += 1
                if calls == 1:
                    return {
                        "type": "http.request",
                        "body": b"x",
                        "more_body": True,
                    }
                await asyncio.sleep(10 if timeout_kind == "cancel" else 0.02)
                return {"type": "http.request", "body": b"", "more_body": False}

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/upload",
                "raw_path": b"/upload",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 8765),
            }
            request = Request(scope, receive)
            if timeout_kind == "stall":
                monkeypatch.setattr(upload_api, "MAX_RECORDING_UPLOAD_STALL_SECONDS", 0.001)
                monkeypatch.setattr(upload_api, "MAX_RECORDING_UPLOAD_DEADLINE_SECONDS", 1)
                reason = "recording_upload_stalled"
            elif timeout_kind == "deadline":
                monkeypatch.setattr(upload_api, "MAX_RECORDING_UPLOAD_STALL_SECONDS", 1)
                monkeypatch.setattr(upload_api, "MAX_RECORDING_UPLOAD_DEADLINE_SECONDS", 0.001)
                reason = "recording_upload_deadline_exceeded"
            else:
                monkeypatch.setattr(upload_api, "MAX_RECORDING_UPLOAD_STALL_SECONDS", 1)
                monkeypatch.setattr(upload_api, "MAX_RECORDING_UPLOAD_DEADLINE_SECONDS", 1)
                reason = "cancel"
            try:
                if timeout_kind == "cancel":
                    task = asyncio.create_task(
                        upload_api.receive_recording_upload(request, upload)
                    )
                    await asyncio.sleep(0.001)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(
                        upload_api.RecordingUploadTransferError, match=reason
                    ):
                        await upload_api.receive_recording_upload(request, upload)
            finally:
                upload.abort()

    asyncio.run(run_transfer("stall"))
    assert not list(root.glob(".*.f1e-uploading"))
    asyncio.run(run_transfer("deadline"))
    assert not list(root.glob(".*.f1e-uploading"))
    asyncio.run(run_transfer("cancel"))
    assert not list(root.glob(".*.f1e-uploading"))


def test_deadline_returns_while_finalizer_drains_and_retains_reservation(
    tmp_path, monkeypatch
):
    _database, root, app = _app(tmp_path)
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(upload_api, "MAX_RECORDING_UPLOAD_STALL_SECONDS", 1)
    monkeypatch.setattr(upload_api, "MAX_RECORDING_UPLOAD_DEADLINE_SECONDS", 0.2)

    async def run_transfer() -> None:
        with TestClient(app):
            service = app.state.recording_upload_service
            upload = service.begin_upload("capture.f1ecap")
            original_finish = service._finish

            def delayed_finish(value):
                started.set()
                release.wait(timeout=3)
                return original_finish(value)

            service._finish = delayed_finish

            async def receive():
                return {
                    "type": "http.request",
                    "body": _capture_bytes(tmp_path),
                    "more_body": False,
                }

            request = Request(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": "/upload",
                    "raw_path": b"/upload",
                    "query_string": b"",
                    "headers": [],
                    "client": ("127.0.0.1", 12345),
                    "server": ("127.0.0.1", 8765),
                },
                receive,
            )
            task = asyncio.create_task(
                upload_api.receive_recording_upload(request, upload)
            )
            assert await asyncio.to_thread(started.wait, 1)
            before = time.monotonic()
            with pytest.raises(
                upload_api.RecordingUploadTransferError,
                match="recording_upload_deadline_exceeded",
            ):
                await task
            assert time.monotonic() - before < 0.5
            assert not upload._closed
            assert not app.state.import_controller.reserve_operation("replay")
            release.set()
            drain_deadline = time.monotonic() + 2
            while time.monotonic() < drain_deadline and not upload._closed:
                await asyncio.sleep(0.01)
            assert upload._closed
            assert not list(root.glob(".*.f1e-uploading"))
            assert not list(root.glob("*.f1ecap"))
            assert app.state.import_controller.reserve_operation("replay")
            app.state.import_controller.release_operation("replay")

    try:
        asyncio.run(run_transfer())
    finally:
        release.set()
