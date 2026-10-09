from __future__ import annotations

import asyncio
import os
import socket
import threading

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from f1_engineer.api.app import create_app
from f1_engineer.api.recording_download import (
    RecordingDownloadResponse,
    recording_download_content_disposition,
)
from f1_engineer.api import recording_download as recording_download_api
from f1_engineer.storage.import_jobs import list_recording_sources_page
from f1_engineer.storage.recording_download import (
    RecordingDownloadError,
    RecordingDownloadService,
)
from f1_engineer.storage import recording_download as recording_download_storage


def _source(tmp_path, name="sample.f1ecap", content=b"capture-bytes"):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    capture = root / name
    capture.write_bytes(content)
    page = list_recording_sources_page(database, root)
    return database, root, capture, page["items"][0]


def test_recording_download_content_disposition_encodes_header_controls():
    value = recording_download_content_disposition(
        'race\r\nX-Injected: yes".f1ecap'
    )

    assert "\r" not in value
    assert "\n" not in value
    assert "%0D%0A" in value
    assert value.startswith('attachment; filename="capture.f1ecap";')


def test_recording_download_streams_exact_open_capture_in_bounded_chunks(tmp_path):
    payload = bytes(range(256)) * 1024
    database, root, _capture, source = _source(tmp_path, content=payload)
    service = RecordingDownloadService(database, root)

    opened = service.open_download(source["capture_id"], source["download_version"])
    chunks = []
    try:
        while chunk := opened.read_chunk():
            assert len(chunk) <= 64 * 1024
            chunks.append(chunk)
    finally:
        opened.close()

    assert b"".join(chunks) == payload
    reopened = service.open_download(
        source["capture_id"], source["download_version"]
    )
    reopened.close()


def test_recording_download_requires_current_metadata_version_and_size_cap(
    tmp_path,
):
    database, root, capture, source = _source(tmp_path)
    service = RecordingDownloadService(database, root)
    capture.write_bytes(b"replacement-with-a-new-size")

    with pytest.raises(RecordingDownloadError, match="recording_source_changed"):
        service.open_download(source["capture_id"], source["download_version"])

    updated = list_recording_sources_page(database, root)["items"][0]
    limited = RecordingDownloadService(database, root, max_download_bytes=2)
    with pytest.raises(RecordingDownloadError, match="recording_download_size_limit"):
        limited.open_download(updated["capture_id"], updated["download_version"])

    available_again = service.open_download(
        updated["capture_id"], updated["download_version"]
    )
    available_again.close()


def test_recording_download_rejects_reparse_or_symlink_replacement(tmp_path):
    database, root, capture, source = _source(tmp_path)
    outside = tmp_path / "outside.f1ecap"
    outside.write_bytes(b"outside-file")
    capture.unlink()
    try:
        capture.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    service = RecordingDownloadService(database, root)
    with pytest.raises(RecordingDownloadError, match="recording_source_unavailable"):
        service.open_download(source["capture_id"], source["download_version"])


def test_recording_download_rejects_ancestor_symlink_swap(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX descriptor-relative directory opens are unavailable")

    configured = tmp_path / "configured"
    root = configured / "recordings"
    root.mkdir(parents=True)
    capture = root / "sample.f1ecap"
    capture.write_bytes(b"trusted-capture")
    database = tmp_path / "archive.sqlite3"
    list_recording_sources_page(database, root)
    service = RecordingDownloadService(database, root)

    outside = tmp_path / "outside"
    outside_root = outside / "recordings"
    outside_root.mkdir(parents=True)
    (outside_root / capture.name).write_bytes(b"attacker-capture")
    outside_source = list_recording_sources_page(database, outside_root)["items"][0]

    moved_configured = tmp_path / "configured-original"
    configured.rename(moved_configured)
    try:
        configured.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        moved_configured.rename(configured)
        pytest.skip(f"symlink creation is unavailable: {exc}")
    try:
        with pytest.raises(
            RecordingDownloadError,
            match="recording_source_(?:unavailable|changed)",
        ):
            service.open_download(
                outside_source["capture_id"], outside_source["download_version"]
            )
    finally:
        configured.unlink()
        moved_configured.rename(configured)


def test_recording_download_does_not_block_if_regular_file_becomes_fifo(
    tmp_path, monkeypatch
):
    if os.name == "nt" or not hasattr(os, "mkfifo"):
        pytest.skip("POSIX FIFO replacement is unavailable")

    _database, root, capture, source = _source(tmp_path)
    real_open = os.open
    replaced = False

    def replace_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if path == capture.name and dir_fd is not None and not replaced:
            replaced = True
            capture.unlink()
            os.mkfifo(capture)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(recording_download_storage.os, "open", replace_before_open)
    try:
        with pytest.raises(
            RecordingDownloadError, match="recording_source_unavailable"
        ):
            recording_download_storage._open_posix_file(root, capture.name, None)
    finally:
        if capture.exists():
            capture.unlink()


def test_recording_download_caps_concurrency_and_releases_slots(tmp_path):
    database, root, _capture, source = _source(tmp_path)
    service = RecordingDownloadService(database, root)
    first = service.open_download(
        source["capture_id"], source["download_version"]
    )
    second = service.open_download(
        source["capture_id"], source["download_version"]
    )
    try:
        with pytest.raises(
            RecordingDownloadError,
            match="recording_download_limit_reached",
        ):
            service.open_download(source["capture_id"], source["download_version"])
    finally:
        first.close()
        second.close()

    recovered = service.open_download(
        source["capture_id"], source["download_version"]
    )
    recovered.close()


def test_recording_download_response_closes_handle_after_disconnect(tmp_path):
    database, root, _capture, source = _source(tmp_path)
    service = RecordingDownloadService(database, root)
    opened = service.open_download(source["capture_id"], source["download_version"])
    response = RecordingDownloadResponse(
        opened,
        headers={"Content-Length": str(opened.byte_size)},
    )

    async def run_response():
        async def receive():
            await asyncio.sleep(60)

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                raise RuntimeError("client disconnected")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/download",
            "raw_path": b"/download",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8765),
        }
        await response(scope, receive, send)

    with pytest.raises(RuntimeError, match="client disconnected"):
        asyncio.run(run_response())

    assert opened.stream.closed
    first = service.open_download(
        source["capture_id"], source["download_version"]
    )
    second = service.open_download(
        source["capture_id"], source["download_version"]
    )
    try:
        with pytest.raises(
            RecordingDownloadError,
            match="recording_download_limit_reached",
        ):
            service.open_download(source["capture_id"], source["download_version"])
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    ("stall_timeout", "deadline"),
    [(0.05, 1.0), (1.0, 0.05)],
    ids=("stalled-send", "whole-response-deadline"),
)
def test_recording_download_transfer_timeout_releases_both_slots(
    tmp_path, monkeypatch, stall_timeout, deadline
):
    database, root, _capture, source = _source(tmp_path)
    service = RecordingDownloadService(database, root)
    opened = service.open_download(source["capture_id"], source["download_version"])
    response = RecordingDownloadResponse(opened, headers={})
    monkeypatch.setattr(
        recording_download_api,
        "RECORDING_DOWNLOAD_STALL_TIMEOUT_SECONDS",
        stall_timeout,
    )
    monkeypatch.setattr(
        recording_download_api, "RECORDING_DOWNLOAD_DEADLINE_SECONDS", deadline
    )

    async def run_response():
        async def receive():
            await asyncio.sleep(60)

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                await asyncio.sleep(60)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/download",
            "raw_path": b"/download",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8765),
        }
        await response(scope, receive, send)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run_response())

    assert opened.stream.closed
    first = service.open_download(
        source["capture_id"], source["download_version"]
    )
    second = service.open_download(
        source["capture_id"], source["download_version"]
    )
    first.close()
    second.close()


def test_recording_download_api_protects_and_streams_selected_source(tmp_path):
    database, root, capture, _source_record = _source(
        tmp_path, name="上海 race #1.f1ecap"
    )
    token = "test-local-control-token-which-is-long-enough"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.bind(("127.0.0.1", 0))
        recording_port = udp_socket.getsockname()[1]
    app = create_app(
        database,
        recordings_root=root,
        control_token=token,
        recording_port=recording_port,
    )

    with TestClient(app) as client:
        legacy = client.get("/api/v1/recording-sources")
        assert legacy.status_code == 200
        assert "download_version" not in legacy.json()["data"][0]

        page = client.get("/api/v1/recording-sources/page").json()["data"]
        source = page["items"][0]
        path = (
            f"/api/v1/recording-sources/{source['capture_id']}/download"
            f"?version={source['download_version']}"
        )
        unauthorized = client.get(path)
        assert unauthorized.status_code == 401

        response = client.get(
            path, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.content == capture.read_bytes()
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"].startswith(
            'attachment; filename="capture.f1ecap"; filename*=UTF-8\'\''
        )
        assert "%E4%B8%8A%E6%B5%B7" in response.headers["content-disposition"]

        stale = client.get(
            path + "&version=" + source["download_version"],
            headers={"Authorization": f"Bearer {token}"},
        )
        assert stale.status_code == 422
        ranged = client.get(
            path,
            headers={"Authorization": f"Bearer {token}", "Range": "bytes=0-1"},
        )
        assert ranged.status_code == 416
