from __future__ import annotations

import os
from pathlib import Path

import pytest

import f1_engineer.storage.usage as usage_module
from f1_engineer.storage.usage import measure_storage_usage


def _write(path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_measurement_counts_configured_managed_files_and_no_others(tmp_path) -> None:
    database = tmp_path / "data" / "engineer.sqlite3"
    recordings = tmp_path / "captures"
    traces = database.with_name(database.name + ".traces")
    _write(database, b"db-data")
    _write(database.with_name(database.name + "-wal"), b"wal")
    _write(database.with_name(database.name + "-shm"), b"shm-data")
    recordings.mkdir(parents=True)
    _write(recordings / "session.f1ecap", b"capture")
    _write(recordings / (".f1e-recording-" + "a" * 32 + ".part"), b"staging")
    _write(recordings / "notes.txt", b"ignore this")
    _write(traces / "run-a" / "player.parquet", b"trace-data")
    _write(traces / "observations" / "chunk.tmp", b"chunk")
    _write(traces / "ignored.bin", b"another trace")

    result = measure_storage_usage(database, recordings)

    assert result["scopes"]["database"] == {
        "status": "available",
        "logical_bytes": len(b"db-datawalshm-data"),
        "regular_file_count": 3,
        "excluded_entry_count": 0,
        "reason": None,
    }
    assert result["scopes"]["finalized_captures"]["logical_bytes"] == len(b"capture")
    assert result["scopes"]["finalized_captures"]["regular_file_count"] == 1
    assert result["scopes"]["recorder_staging"]["logical_bytes"] == len(b"staging")
    assert result["scopes"]["imported_traces"]["logical_bytes"] == len(
        b"trace-datachunkanother trace"
    )
    assert result["scopes"]["imported_traces"]["regular_file_count"] == 3
    assert result["scopes"]["finalized_captures"]["excluded_entry_count"] == 2
    assert result["volumes"]["database_location"]["status"] == "available"
    assert result["volumes"]["recordings_location"]["status"] == "available"
    assert str(tmp_path) not in str(result)
    assert database.read_bytes() == b"db-data"


def test_missing_trace_namespace_is_zero_and_missing_required_locations_are_unavailable(tmp_path) -> None:
    database = tmp_path / "data.sqlite3"
    recordings = tmp_path / "recordings"
    _write(database, b"db")
    recordings.mkdir()

    result = measure_storage_usage(database, recordings)

    assert result["scopes"]["imported_traces"] == {
        "status": "available",
        "logical_bytes": 0,
        "regular_file_count": 0,
        "excluded_entry_count": 0,
        "reason": None,
    }
    missing = measure_storage_usage(tmp_path / "missing.sqlite3", tmp_path / "missing-recordings")
    assert missing["scopes"]["database"]["status"] == "unavailable"
    assert missing["scopes"]["finalized_captures"]["status"] == "unavailable"
    assert missing["scopes"]["recorder_staging"]["status"] == "unavailable"


def test_recording_scan_limit_never_returns_prefix_as_complete(tmp_path, monkeypatch) -> None:
    database = tmp_path / "data.sqlite3"
    recordings = tmp_path / "recordings"
    _write(database, b"db")
    recordings.mkdir()
    _write(recordings / "one.f1ecap", b"one")
    _write(recordings / "two.txt", b"two")
    monkeypatch.setattr(usage_module, "MAX_RECORDING_ROOT_ENTRIES", 1)

    result = measure_storage_usage(database, recordings)

    assert result["scopes"]["finalized_captures"]["status"] == "unavailable"
    assert result["scopes"]["finalized_captures"]["reason"] == "entry_limit_exceeded"
    assert result["scopes"]["finalized_captures"]["logical_bytes"] is None


def test_trace_entry_and_directory_depth_limits_make_scope_unavailable(tmp_path, monkeypatch) -> None:
    database = tmp_path / "data.sqlite3"
    recordings = tmp_path / "recordings"
    traces = database.with_name(database.name + ".traces")
    _write(database, b"db")
    recordings.mkdir()
    _write(traces / "a" / "b" / "trace.parquet", b"trace")

    monkeypatch.setattr(usage_module, "MAX_TRACE_DIRECTORY_DEPTH", 1)
    depth_result = measure_storage_usage(database, recordings)
    assert depth_result["scopes"]["imported_traces"]["reason"] == "directory_depth_limit_exceeded"

    monkeypatch.setattr(usage_module, "MAX_TRACE_DIRECTORY_DEPTH", 4)
    monkeypatch.setattr(usage_module, "MAX_TRACE_ENTRIES", 1)
    entry_result = measure_storage_usage(database, recordings)
    assert entry_result["scopes"]["imported_traces"]["reason"] == "entry_limit_exceeded"


@pytest.mark.skipif(os.name == "nt", reason="portable symlink creation is not guaranteed on Windows")
def test_trace_scan_does_not_follow_symlinks(tmp_path) -> None:
    database = tmp_path / "data.sqlite3"
    recordings = tmp_path / "recordings"
    traces = database.with_name(database.name + ".traces")
    target = tmp_path / "outside.parquet"
    _write(database, b"db")
    recordings.mkdir()
    _write(target, b"outside-data")
    _write(traces / "inside.parquet", b"in")
    (traces / "outside-link.parquet").symlink_to(target)

    result = measure_storage_usage(database, recordings)

    assert result["scopes"]["imported_traces"]["logical_bytes"] == 2
    assert result["scopes"]["imported_traces"]["regular_file_count"] == 1
    assert result["scopes"]["imported_traces"]["excluded_entry_count"] == 1


@pytest.mark.skipif(os.name == "nt", reason="portable symlink creation is not guaranteed on Windows")
def test_trace_scan_rejects_a_queued_directory_replaced_by_symlink(tmp_path, monkeypatch) -> None:
    database = tmp_path / "data.sqlite3"
    recordings = tmp_path / "recordings"
    traces = database.with_name(database.name + ".traces")
    queued_directory = traces / "run-a"
    outside = tmp_path / "outside"
    _write(database, b"db")
    recordings.mkdir()
    _write(queued_directory / "inside.parquet", b"inside")
    _write(outside / "secret.parquet", b"outside-content")

    original_scandir = os.scandir
    replaced = False

    class ReplaceAfterScan:
        def __init__(self, path):
            self.path = path
            self.scanner = original_scandir(path)

        def __enter__(self):
            return self.scanner.__enter__()

        def __exit__(self, exc_type, exc, traceback):
            nonlocal replaced
            result = self.scanner.__exit__(exc_type, exc, traceback)
            if Path(self.path) == traces and not replaced:
                moved = traces / "run-a-moved"
                queued_directory.rename(moved)
                queued_directory.symlink_to(outside, target_is_directory=True)
                replaced = True
            return result

    monkeypatch.setattr(os, "scandir", lambda path: ReplaceAfterScan(path))

    result = measure_storage_usage(database, recordings)

    assert result["scopes"]["imported_traces"]["status"] == "unavailable"
    assert result["scopes"]["imported_traces"]["reason"] == "directory_changed"
