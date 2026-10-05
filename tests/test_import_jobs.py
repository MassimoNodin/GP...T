from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from f1_engineer.api.app import create_app
from f1_engineer.api.import_controller import ImportController
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.storage.database import Database
from f1_engineer.storage.import_jobs import (
    create_import_job,
    get_import_job,
    list_recording_sources,
    recover_abandoned_import_jobs,
    resolve_recording_source,
    retry_import_job,
    update_import_job,
)


def _capture(path):
    with CaptureWriter(path, metadata={"test": True}):
        pass


def test_database_schema_v4_migrates_and_backfills_job_update_time(tmp_path):
    database = tmp_path / "archive.sqlite3"
    with Database(database) as db:
        with db.connection:
            db.connection.execute("DROP INDEX IF EXISTS idx_import_jobs_latest")
            db.connection.execute(
                "ALTER TABLE import_jobs DROP COLUMN updated_at_utc"
            )
            db.connection.execute(
                """INSERT INTO recording_sources(
                       capture_id, root_namespace, relative_path, display_name,
                       byte_size, modified_ns)
                     VALUES ('capture', 'root', 'old.f1ecap', 'old.f1ecap', 0, 0)"""
            )
            db.connection.execute(
                """INSERT INTO import_jobs(
                       job_id, capture_id, status, phase, created_at_utc)
                     VALUES ('job', 'capture', 'failed', 'failed', '2026-10-01 01:02:03')"""
            )
            db.connection.execute(
                "UPDATE schema_info SET version=4 WHERE singleton=1"
            )

    with Database(database) as db:
        version = db.connection.execute(
            "SELECT version FROM schema_info WHERE singleton=1"
        ).fetchone()[0]
        columns = {
            row["name"]
            for row in db.connection.execute("PRAGMA table_info(import_jobs)")
        }
        updated_at = db.connection.execute(
            "SELECT updated_at_utc FROM import_jobs WHERE job_id='job'"
        ).fetchone()[0]
        recording_table = db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recording_jobs'"
        ).fetchone()

    assert version == 12
    assert recording_table is not None
    assert "updated_at_utc" in columns
    assert updated_at == "2026-10-01T01:02:03.000000+00:00"


def test_database_schema_v5_migrates_to_recording_jobs(tmp_path):
    database = tmp_path / "archive.sqlite3"
    with Database(database) as db:
        with db.connection:
            db.connection.execute("DROP TABLE recording_jobs")
            db.connection.execute("UPDATE schema_info SET version=5 WHERE singleton=1")

    with Database(database) as db:
        version = db.connection.execute(
            "SELECT version FROM schema_info WHERE singleton=1"
        ).fetchone()[0]
        recording_table = db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recording_jobs'"
        ).fetchone()

    assert version == 12
    assert recording_table is not None


def test_database_schema_v7_migrates_bounded_lifecycle_metadata(tmp_path):
    database = tmp_path / "legacy-v7.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_info (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL);
        INSERT INTO schema_info(singleton, version) VALUES (1, 7);
        CREATE TABLE lifecycle_events (
            event_key TEXT PRIMARY KEY,
            session_key TEXT NOT NULL,
            event_ordinal INTEGER NOT NULL,
            frame_ordinal INTEGER NOT NULL,
            details_hex TEXT NOT NULL
        );
        """
    )
    connection.close()

    with Database(database) as db:
        version = db.connection.execute(
            "SELECT version FROM schema_info WHERE singleton=1"
        ).fetchone()[0]
        columns = {
            row["name"]
            for row in db.connection.execute("PRAGMA table_info(lifecycle_events)")
        }

    assert version == 12
    assert {"details_length_bytes", "details_truncated"} <= columns


def test_database_schema_v8_migrates_session_history_evidence(tmp_path):
    database = tmp_path / "legacy-v8.sqlite3"
    with Database(database) as db:
        with db.connection:
            db.connection.execute("DROP TABLE attempt_timing_evidence")
            db.connection.execute(
                "UPDATE schema_info SET version=8 WHERE singleton=1"
            )

    with Database(database) as db:
        version = db.connection.execute(
            "SELECT version FROM schema_info WHERE singleton=1"
        ).fetchone()[0]
        table = db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempt_timing_evidence'"
        ).fetchone()

    assert version == 12
    assert table is not None


def test_recording_sources_have_stable_opaque_ids_and_never_expose_paths(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    capture = root / "local-session.f1ecap"
    _capture(capture)

    first = list_recording_sources(database, root)
    second = list_recording_sources(database, root)

    assert len(first) == 1
    assert first[0]["capture_id"] == second[0]["capture_id"]
    assert len(first[0]["capture_id"]) == 32
    assert first[0]["display_name"] == capture.name
    assert first[0]["byte_size"] == capture.stat().st_size
    assert set(first[0]) == {
        "capture_id",
        "display_name",
        "byte_size",
        "modified_at_utc",
        "latest_job_id",
        "latest_job_status",
        "latest_job_run_id",
        "available",
    }
    assert first[0]["latest_job_id"] is None
    assert first[0]["latest_job_run_id"] is None
    assert first[0]["available"] is True
    assert str(tmp_path) not in repr(first)


def test_catalog_hides_stale_files_but_retains_missing_sources_with_jobs(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    capture = root / "local-session.f1ecap"
    _capture(capture)
    capture_id = list_recording_sources(database, root)[0]["capture_id"]
    capture.unlink()

    assert list_recording_sources(database, root) == []

    job, _ = create_import_job(database, capture_id)
    sources = list_recording_sources(database, root)

    assert len(sources) == 1
    assert sources[0]["available"] is False
    assert sources[0]["latest_job_id"] == job["job_id"]
    assert sources[0]["latest_job_status"] == "queued"


def test_recording_source_resolution_rejects_database_path_escape(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    capture = root / "local-session.f1ecap"
    outside = tmp_path / "outside.f1ecap"
    _capture(capture)
    _capture(outside)
    source = list_recording_sources(database, root)[0]

    with Database(database) as db:
        with db.connection:
            db.connection.execute(
                "UPDATE recording_sources SET relative_path=? WHERE capture_id=?",
                ("../outside.f1ecap", source["capture_id"]),
            )

    assert resolve_recording_source(database, root, source["capture_id"]) is None


def test_import_jobs_are_single_active_and_retry_only_terminal_failures(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "one.f1ecap")
    _capture(root / "two.f1ecap")
    sources = list_recording_sources(database, root)
    first_id, second_id = [source["capture_id"] for source in sources]

    first, created = create_import_job(database, first_id)
    same, created_again = create_import_job(database, first_id)
    assert created is True
    assert created_again is False
    assert same["job_id"] == first["job_id"]
    with pytest.raises(ValueError, match="another_import_is_in_progress"):
        create_import_job(database, second_id)
    with pytest.raises(ValueError, match="import_job_not_retryable"):
        retry_import_job(database, first["job_id"])

    update_import_job(
        database,
        first["job_id"],
        status="failed",
        phase="failed",
        failure_reason="capture_import_failed_retry_available",
        finished=True,
    )
    retried = retry_import_job(database, first["job_id"])
    assert retried["status"] == "queued"
    assert retried["attempt_count"] == 2


def test_inbox_chooses_the_job_with_latest_retry_timestamp(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "one.f1ecap")
    capture_id = list_recording_sources(database, root)[0]["capture_id"]
    first, _ = create_import_job(database, capture_id)
    update_import_job(
        database,
        first["job_id"],
        status="failed",
        phase="failed",
        finished=True,
    )
    later, _ = create_import_job(database, capture_id)
    update_import_job(
        database,
        later["job_id"],
        status="complete",
        phase="complete",
        finished=True,
    )
    retry_import_job(database, first["job_id"])

    source = list_recording_sources(database, root)[0]
    assert source["latest_job_id"] == first["job_id"]
    assert source["latest_job_status"] == "queued"


def test_recording_sources_expose_completed_job_run_id_for_evidence_link(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "one.f1ecap")
    capture_id = list_recording_sources(database, root)[0]["capture_id"]
    job, _ = create_import_job(database, capture_id)
    run_id = "a" * 64
    update_import_job(
        database,
        job["job_id"],
        status="complete",
        phase="complete",
        result={"run_id": run_id},
        finished=True,
    )

    source = list_recording_sources(database, root)[0]

    assert source["latest_job_status"] == "complete"
    assert source["latest_job_run_id"] == run_id


def test_worker_revalidates_source_identity_before_import(tmp_path, monkeypatch):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    capture = root / "one.f1ecap"
    _capture(capture)
    controller = ImportController(database, root)
    controller.start()
    assert controller.ready
    capture_id = list_recording_sources(database, root)[0]["capture_id"]

    worker_entered = threading.Event()
    release_worker = threading.Event()
    assert controller._executor is not None
    controller._executor.submit(
        lambda: (worker_entered.set(), release_worker.wait(timeout=5))
    )
    assert worker_entered.wait(timeout=2)
    job = controller.submit(capture_id)

    original_is_symlink = Path.is_symlink
    simulate_replacement = False

    def is_symlink(path):
        if path == capture and simulate_replacement:
            return True
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    simulate_replacement = True
    release_worker.set()
    try:
        for _ in range(100):
            current = controller.get(job["job_id"])
            if current and current["status"] in {"failed", "complete"}:
                break
            time.sleep(0.02)
        assert current is not None
        assert current["status"] == "failed"
        assert controller._progress == {}
    finally:
        release_worker.set()
        controller.close()


def test_restart_marks_queued_and_running_imports_interrupted(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "one.f1ecap")
    capture_id = list_recording_sources(database, root)[0]["capture_id"]
    job, _ = create_import_job(database, capture_id)
    update_import_job(
        database,
        job["job_id"],
        status="running",
        phase="reading_packets",
        starting=True,
    )

    recover_abandoned_import_jobs(database)
    recovered = get_import_job(database, job["job_id"])

    assert recovered is not None
    assert recovered["status"] == "interrupted"
    assert recovered["phase"] == "interrupted"
    assert recovered["failure_reason"] == "api_restarted_before_import_completed"


def test_api_requires_server_token_and_imports_a_local_capture(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "small.f1ecap")
    app = create_app(database, recordings_root=root, control_token="a" * 48)

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                sources_response = await client.get("/api/v1/recording-sources")
                assert sources_response.status_code == 200
                source = sources_response.json()["data"][0]
                assert "path" not in source

                denied = await client.post(
                    "/api/v1/import-jobs", json={"capture_id": source["capture_id"]}
                )
                assert denied.status_code == 403

                rejects_path = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": str(root / "small.f1ecap")},
                    headers={"authorization": "Bearer " + "a" * 48},
                )
                assert rejects_path.status_code == 422
                rejects_path_field = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": source["capture_id"], "path": str(root)},
                    headers={"authorization": "Bearer " + "a" * 48},
                )
                assert rejects_path_field.status_code == 422

                accepted = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": source["capture_id"]},
                    headers={"authorization": "Bearer " + "a" * 48},
                )
                assert accepted.status_code == 202
                job_id = accepted.json()["data"]["job_id"]
                for _ in range(100):
                    status = await client.get(f"/api/v1/import-jobs/{job_id}")
                    job = status.json()["data"]
                    if job["status"] in {"complete", "failed", "interrupted"}:
                        break
                    await asyncio.sleep(0.02)
                assert job["status"] == "complete"
                assert job["result"]["status"] == "complete"
                assert job["result"]["capture_sha256"]
                assert app.state.import_controller._progress == {}

    asyncio.run(exercise())
