from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from f1_engineer.api.app import create_app
from f1_engineer.api.import_controller import ImportController
import f1_engineer.api.import_controller as import_controller_module
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.storage.database import Database
from f1_engineer.storage.import_jobs import (
    MAX_IMPORT_QUEUE_WAITING,
    RecordingCatalogUnavailable,
    cancel_queued_import_job,
    claim_oldest_import_job,
    create_import_job,
    get_import_job,
    import_queue_snapshot,
    list_recording_sources,
    list_recording_sources_page,
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

    assert version == 15
    assert recording_table is not None
    assert "updated_at_utc" in columns
    assert updated_at == "2026-10-01T01:02:03.000000+00:00"


def test_schema_14_migration_preserves_jobs_and_rebuilds_queue_indexes(tmp_path):
    database = tmp_path / "schema-14.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    captures = [root / f"capture-{index}.f1ecap" for index in range(3)]
    for capture in captures:
        _capture(capture)
    sources = list_recording_sources(database, root)
    completed, _ = create_import_job(database, sources[0]["capture_id"])
    update_import_job(
        database,
        completed["job_id"],
        status="complete",
        phase="complete",
        result={"run_id": "run-preserved"},
        finished=True,
    )
    waiting, _ = create_import_job(database, sources[1]["capture_id"])
    failed, _ = create_import_job(database, sources[2]["capture_id"])
    update_import_job(
        database, failed["job_id"], status="failed", phase="failed", finished=True
    )
    expected_fields = {
        completed["job_id"]: (completed["capture_id"], "complete", "complete"),
        waiting["job_id"]: (waiting["capture_id"], "queued", "queued"),
        failed["job_id"]: (failed["capture_id"], "failed", "failed"),
    }

    # Recreate the actual v14 table shape and single-active-import constraint.
    with Database(database) as db:
        with db.connection:
            for index in (
                "idx_import_jobs_latest",
                "idx_import_jobs_queue",
                "idx_import_jobs_queue_order",
                "idx_one_running_import_job",
                "idx_one_active_import_per_capture",
            ):
                db.connection.execute(f"DROP INDEX IF EXISTS {index}")
            db.connection.execute(
                "CREATE TABLE import_jobs_v14 ("
                "job_id TEXT PRIMARY KEY, capture_id TEXT NOT NULL REFERENCES recording_sources(capture_id), "
                "status TEXT NOT NULL CHECK (status IN ('queued','running','complete','failed','interrupted')), "
                "phase TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count > 0), "
                "created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at_utc TEXT, "
                "finished_at_utc TEXT, result_json TEXT, failure_reason TEXT)"
            )
            db.connection.execute(
                "INSERT INTO import_jobs_v14 SELECT job_id,capture_id,status,phase,attempt_count,"
                "created_at_utc,updated_at_utc,started_at_utc,finished_at_utc,result_json,failure_reason "
                "FROM import_jobs"
            )
            db.connection.execute("DROP TABLE import_jobs")
            db.connection.execute("ALTER TABLE import_jobs_v14 RENAME TO import_jobs")
            db.connection.execute(
                "CREATE INDEX idx_import_jobs_capture ON import_jobs(capture_id, created_at_utc)"
            )
            db.connection.execute(
                "CREATE UNIQUE INDEX idx_one_active_import_job ON import_jobs((1)) "
                "WHERE status IN ('queued','running')"
            )
            db.connection.execute("UPDATE schema_info SET version=14 WHERE singleton=1")

    with Database(database) as db:
        version = db.connection.execute(
            "SELECT version FROM schema_info WHERE singleton=1"
        ).fetchone()[0]
        rows = db.connection.execute(
            "SELECT job_id,capture_id,status,phase,result_json,queue_order,"
            "source_root_namespace,source_metadata_version FROM import_jobs"
        ).fetchall()
        indexes = {
            row["name"]: row["unique"]
            for row in db.connection.execute("PRAGMA index_list(import_jobs)")
        }

    assert version == 15
    assert len(rows) == 3
    by_id = {row["job_id"]: row for row in rows}
    assert {
        job_id: (row["capture_id"], row["status"], row["phase"])
        for job_id, row in by_id.items()
    } == expected_fields
    assert by_id[completed["job_id"]]["result_json"] == '{"run_id":"run-preserved"}'
    assert [
        by_id[job_id]["queue_order"]
        for job_id in (waiting["job_id"], completed["job_id"], failed["job_id"])
    ] == [1, 2, 3]
    assert all(len(row["source_root_namespace"] or "") == 64 for row in rows)
    assert all(len(row["source_metadata_version"] or "") == 64 for row in rows)
    assert indexes["idx_one_running_import_job"] == 1
    assert indexes["idx_one_active_import_per_capture"] == 1
    assert indexes["idx_import_jobs_queue_order"] == 1
    assert "idx_one_active_import_job" not in indexes


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

    assert version == 15
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

    assert version == 15
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

    assert version == 15
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


def test_recording_source_pages_filter_literally_and_keep_off_page_selection(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    alpha = root / "Alpha.f1ecap"
    wildcard = root / "run_%_final.f1ecap"
    zulu = root / "zulu.f1ecap"
    for path in (alpha, wildcard, zulu):
        _capture(path)

    first_page = list_recording_sources_page(database, root, limit=1)
    assert first_page["total_count"] == 3
    assert first_page["items"][0]["display_name"] == "Alpha.f1ecap"
    assert first_page["has_more"] is True
    alpha_id = first_page["items"][0]["capture_id"]
    replacement = tmp_path / "replacement.tmp"
    replacement.write_bytes(b"replacement capture")
    os.replace(replacement, alpha)
    refreshed = list_recording_sources_page(database, root, limit=1)
    assert refreshed["items"][0]["capture_id"] == alpha_id
    assert refreshed["items"][0]["byte_size"] == len(b"replacement capture")

    wildcard_page = list_recording_sources_page(database, root, query="%_")
    assert [item["display_name"] for item in wildcard_page["items"]] == [
        "run_%_final.f1ecap"
    ]

    wildcard_id = next(
        item["capture_id"]
        for item in list_recording_sources(database, root)
        if item["display_name"] == wildcard.name
    )
    queued_job, _ = create_import_job(database, wildcard_id)
    filtered = list_recording_sources_page(
        database,
        root,
        latest_job_status="queued",
        availability="available",
        selected_capture_id=wildcard_id,
        limit=1,
        offset=1,
    )
    assert filtered["total_count"] == 1
    assert filtered["items"] == []
    assert filtered["selected_capture"]["capture_id"] == wildcard_id
    assert filtered["selected_capture"]["latest_job_id"] == queued_job["job_id"]

    wildcard.unlink()
    missing = list_recording_sources_page(
        database,
        root,
        availability="missing",
        selected_capture_id=wildcard_id,
    )
    assert missing["total_count"] == 1
    assert missing["items"][0]["available"] is False
    assert missing["selected_capture"]["available"] is False

    other_root = tmp_path / "other-recordings"
    other_root.mkdir()
    _capture(other_root / alpha.name)
    isolated = list_recording_sources_page(
        database, other_root, selected_capture_id=wildcard_id
    )
    assert isolated["total_count"] == 1
    assert isolated["selected_capture"] is None


def test_recording_source_page_repeats_non_ascii_capture_name_across_refreshes(
    tmp_path,
):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    capture = root / ("中" * 80 + ".f1ecap")
    _capture(capture)

    first = list_recording_sources_page(database, root)
    second = list_recording_sources_page(database, root)

    assert first["total_count"] == second["total_count"] == 1
    assert first["items"][0]["display_name"] == capture.name
    assert second["items"][0]["display_name"] == capture.name
    assert first["items"][0]["capture_id"] == second["items"][0]["capture_id"]


@pytest.mark.parametrize("column", ["byte_size", "modified_ns"])
def test_recording_source_page_rejects_unbounded_stale_numeric_metadata(
    tmp_path, column
):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    capture = root / "missing-after-registration.f1ecap"
    _capture(capture)
    source = list_recording_sources_page(database, root)["items"][0]
    capture.unlink()
    with Database(database) as db:
        db.connection.execute(
            f"UPDATE recording_sources SET {column}=? WHERE capture_id=?",
            ("x" * 100_000, source["capture_id"]),
        )
        db.connection.commit()

    with pytest.raises(
        RecordingCatalogUnavailable,
        match="recording_catalog_registration_invalid",
    ):
        list_recording_sources_page(database, root)


def test_recording_source_page_limits_are_atomic_and_job_result_is_bounded(
    tmp_path, monkeypatch
):
    import f1_engineer.storage.import_jobs as import_jobs_module

    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "one.f1ecap")
    source = list_recording_sources_page(database, root)["items"][0]
    job, _ = create_import_job(database, source["capture_id"])
    with Database(database) as db:
        db.connection.execute(
            """UPDATE import_jobs SET status='complete', phase='complete',
                   result_json=? WHERE job_id=?""",
            ("{" + "x" * 20_000 + "}", job["job_id"]),
        )
        db.connection.commit()

    selected = list_recording_sources_page(
        database, root, selected_capture_id=source["capture_id"]
    )["selected_capture"]
    assert selected["available"] is True
    assert selected["latest_job_status"] == "complete"
    assert selected["latest_job_run_id"] is None

    _capture(root / "two.f1ecap")
    monkeypatch.setattr(
        import_jobs_module, "MAX_RECORDING_CATALOG_REGISTERED_SOURCES", 1
    )
    with pytest.raises(
        RecordingCatalogUnavailable,
        match="recording_catalog_registered_source_limit",
    ):
        list_recording_sources_page(database, root)
    with Database(database, read_only=True) as db:
        registered_count = db.connection.execute(
            "SELECT COUNT(*) FROM recording_sources"
        ).fetchone()[0]
    assert registered_count == 1


def test_recording_source_page_rejects_oversized_directory_before_database_write(
    tmp_path, monkeypatch
):
    import f1_engineer.storage.import_jobs as import_jobs_module

    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    for index in range(3):
        (root / f"entry-{index}.txt").write_text("ignored")
    monkeypatch.setattr(
        import_jobs_module, "MAX_RECORDING_CATALOG_DIRECTORY_ENTRIES", 2
    )

    with pytest.raises(
        RecordingCatalogUnavailable,
        match="recording_catalog_directory_entry_limit",
    ):
        list_recording_sources_page(database, root)

    assert not database.exists()


def test_recording_source_page_ignores_symlinked_capture_entries(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "regular.f1ecap")
    outside = tmp_path / "outside.f1ecap"
    _capture(outside)
    link = root / "linked.f1ecap"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks are not available: {exc}")

    page = list_recording_sources_page(database, root)

    assert page["total_count"] == 1
    assert [item["display_name"] for item in page["items"]] == ["regular.f1ecap"]


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


def test_import_jobs_queue_distinct_captures_and_retry_terminal_failures(tmp_path):
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
    second, second_created = create_import_job(database, second_id)
    assert second_created is True
    assert second["status"] == "queued"
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
    with Database(database) as db:
        orders = dict(
            db.connection.execute(
                "SELECT job_id, queue_order FROM import_jobs"
            ).fetchall()
        )
    assert orders[first["job_id"]] > orders[second["job_id"]]


def test_import_queue_fifo_capacity_snapshot_and_waiting_only_cancel(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    for index in range(MAX_IMPORT_QUEUE_WAITING + 1):
        _capture(root / f"capture-{index:02d}.f1ecap")
    sources = list_recording_sources(database, root)

    jobs = [create_import_job(database, source["capture_id"])[0] for source in sources[:16]]
    with pytest.raises(ValueError, match="import_queue_full"):
        create_import_job(database, sources[16]["capture_id"])

    snapshot = import_queue_snapshot(database, "replay")
    assert snapshot["waiting_count"] == 16
    assert [row["queue_position"] for row in snapshot["waiting_jobs"]] == list(range(1, 17))
    assert "result" not in snapshot["waiting_jobs"][0]
    assert snapshot["blocking_reservation"] == "replay"

    cancelled, changed = cancel_queued_import_job(database, jobs[1]["job_id"])
    assert changed is True
    assert cancelled is not None and cancelled["status"] == "cancelled"
    claimed = claim_oldest_import_job(database)
    assert claimed is not None and claimed["job_id"] == jobs[0]["job_id"]
    running, changed = cancel_queued_import_job(database, claimed["job_id"])
    assert changed is False
    assert running is not None and running["status"] == "running"

    accepted, created = create_import_job(database, sources[16]["capture_id"])
    assert created is True
    assert accepted["status"] == "queued"
    requeued, created = create_import_job(database, sources[1]["capture_id"])
    assert created is True
    assert requeued["status"] == "queued"
    after_cancel = import_queue_snapshot(database, "import")
    assert after_cancel["waiting_count"] == 16
    assert [row["queue_position"] for row in after_cancel["waiting_jobs"]] == list(range(1, 17))
    assert after_cancel["running_job"]["job_id"] == claimed["job_id"]


def test_import_queue_snapshot_rejects_unbounded_database_text(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "large-phase.f1ecap")
    capture_id = list_recording_sources(database, root)[0]["capture_id"]
    job, _ = create_import_job(database, capture_id)
    with Database(database) as db, db.connection:
        db.connection.execute(
            "UPDATE import_jobs SET phase=? WHERE job_id=?",
            ("x" * 100_000, job["job_id"]),
        )

    with pytest.raises(ValueError, match="import_queue_snapshot_unavailable"):
        import_queue_snapshot(database, None)


def test_import_queue_cancel_and_claim_race_has_one_winner(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "race.f1ecap")
    capture_id = list_recording_sources(database, root)[0]["capture_id"]
    queued, _ = create_import_job(database, capture_id)
    barrier = threading.Barrier(3)
    outcomes = {}

    def cancel():
        barrier.wait()
        outcomes["cancel"] = cancel_queued_import_job(database, queued["job_id"])

    def claim():
        barrier.wait()
        outcomes["claim"] = claim_oldest_import_job(database)

    cancel_thread = threading.Thread(target=cancel)
    claim_thread = threading.Thread(target=claim)
    cancel_thread.start()
    claim_thread.start()
    barrier.wait()
    cancel_thread.join(timeout=5)
    claim_thread.join(timeout=5)
    assert not cancel_thread.is_alive()
    assert not claim_thread.is_alive()

    cancelled_job, cancelled = outcomes["cancel"]
    claimed_job = outcomes["claim"]
    if cancelled:
        assert cancelled_job["status"] == "cancelled"
        assert claimed_job is None
    else:
        assert cancelled_job["status"] == "running"
        assert claimed_job["job_id"] == queued["job_id"]


@pytest.mark.parametrize("reservation", ["recording", "replay", "upload"])
def test_waiting_import_starts_only_after_reservation_release(tmp_path, reservation):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "queued.f1ecap")
    controller = ImportController(database, root)
    controller.start()
    assert controller.ready
    controller.enable_dispatch()
    try:
        assert controller.reserve_operation(reservation)
        capture_id = list_recording_sources(database, root)[0]["capture_id"]
        queued = controller.submit(capture_id, queue_if_busy=True)
        assert queued["status"] == "queued"
        assert controller.get(queued["job_id"])["status"] == "queued"
        queue = controller.queue_snapshot()
        assert queue["waiting_count"] == 1
        assert queue["blocking_reservation"] == reservation
        controller.release_operation(reservation)
        for _ in range(200):
            current = controller.get(queued["job_id"])
            if current and current["status"] in {"failed", "complete"}:
                break
            time.sleep(0.02)
        assert current is not None and current["status"] == "complete"
    finally:
        controller.release_operation(reservation)
        controller.close()


def test_waiting_import_fails_if_source_metadata_changes_before_dispatch(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    capture = root / "mutable.f1ecap"
    _capture(capture)
    controller = ImportController(database, root)
    controller.start()
    assert controller.ready
    controller.enable_dispatch()
    try:
        assert controller.reserve_operation("upload")
        capture_id = list_recording_sources(database, root)[0]["capture_id"]
        queued = controller.submit(capture_id, queue_if_busy=True)
        assert queued["status"] == "queued"
        capture.write_bytes(b"replacement with changed metadata")
        controller.release_operation("upload")
        for _ in range(100):
            current = controller.get(queued["job_id"])
            if current and current["status"] in {"failed", "complete"}:
                break
            time.sleep(0.02)
        assert current is not None and current["status"] == "failed"
        assert current["failure_reason"] == "capture_source_changed_refresh_catalog"
    finally:
        controller.release_operation("upload")
        controller.close()


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


def test_restart_preserves_waiting_imports_and_interrupts_the_running_job(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "one.f1ecap")
    _capture(root / "two.f1ecap")
    sources = list_recording_sources(database, root)
    job, _ = create_import_job(database, sources[0]["capture_id"])
    waiting, _ = create_import_job(database, sources[1]["capture_id"])
    update_import_job(
        database,
        job["job_id"],
        status="running",
        phase="reading_packets",
        starting=True,
    )

    recover_abandoned_import_jobs(database)
    recovered = get_import_job(database, job["job_id"])
    with Database(database) as db:
        waiting_order = db.connection.execute(
            "SELECT queue_order FROM import_jobs WHERE job_id=?",
            (waiting["job_id"],),
        ).fetchone()[0]

    assert recovered is not None
    assert recovered["status"] == "interrupted"
    assert recovered["phase"] == "interrupted"
    assert recovered["failure_reason"] == "api_restarted_before_import_completed"
    assert get_import_job(database, waiting["job_id"])["status"] == "queued"
    with Database(database) as db:
        assert db.connection.execute(
            "SELECT queue_order FROM import_jobs WHERE job_id=?",
            (waiting["job_id"],),
        ).fetchone()[0] == waiting_order


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


def test_api_opt_in_queue_snapshot_and_protected_waiting_cancellation(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "waiting.f1ecap")
    token = "b" * 48
    app = create_app(database, recordings_root=root, control_token=token)

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                source = (await client.get("/api/v1/recording-sources")).json()["data"][0]
                controller = app.state.import_controller
                assert controller.reserve_operation("upload")
                headers = {"authorization": "Bearer " + token}

                legacy = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": source["capture_id"]},
                    headers=headers,
                )
                assert legacy.status_code == 409

                queued = await client.post(
                    "/api/v1/import-jobs",
                    json={"capture_id": source["capture_id"], "queue_if_busy": True},
                    headers=headers,
                )
                assert queued.status_code == 202
                job_id = queued.json()["data"]["job_id"]
                assert queued.json()["data"]["status"] == "queued"

                snapshot = await client.get("/api/v1/import-jobs/queue")
                assert snapshot.status_code == 200
                assert snapshot.json()["data"]["waiting_count"] == 1
                assert snapshot.json()["data"]["blocking_reservation"] == "upload"
                assert "result" not in snapshot.json()["data"]["waiting_jobs"][0]

                denied = await client.post(f"/api/v1/import-jobs/{job_id}/cancel")
                assert denied.status_code == 403
                cancelled = await client.post(
                    f"/api/v1/import-jobs/{job_id}/cancel", headers=headers
                )
                assert cancelled.status_code == 200
                assert cancelled.json()["data"]["status"] == "cancelled"
                repeated = await client.post(
                    f"/api/v1/import-jobs/{job_id}/cancel", headers=headers
                )
                assert repeated.status_code == 409
                controller.release_operation("upload")

    asyncio.run(exercise())


def test_import_controller_releases_claim_after_source_resolver_exception(
    tmp_path, monkeypatch
):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "first.f1ecap")
    _capture(root / "second.f1ecap")
    controller = ImportController(database, root)
    controller.start()
    assert controller.ready
    controller.enable_dispatch()
    original_resolver = import_controller_module.resolve_pinned_recording_source
    first_call = True

    def fail_first_resolution(*args, **kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            raise RuntimeError("simulated catalog read failure")
        return original_resolver(*args, **kwargs)

    monkeypatch.setattr(
        import_controller_module,
        "resolve_pinned_recording_source",
        fail_first_resolution,
    )
    try:
        assert controller.reserve_operation("upload")
        sources = list_recording_sources(database, root)
        first = controller.submit(sources[0]["capture_id"], queue_if_busy=True)
        second = controller.submit(sources[1]["capture_id"], queue_if_busy=True)
        controller.release_operation("upload")

        for _ in range(250):
            first_current = controller.get(first["job_id"])
            second_current = controller.get(second["job_id"])
            if (
                first_current is not None
                and second_current is not None
                and first_current["status"] == "failed"
                and second_current["status"] == "complete"
            ):
                break
            time.sleep(0.02)

        assert first_current is not None
        assert first_current["status"] == "failed"
        assert first_current["failure_reason"] == "capture_source_changed_refresh_catalog"
        assert second_current is not None and second_current["status"] == "complete"
        assert controller.current_operation_reservation is None
    finally:
        controller.release_operation("upload")
        controller.close()


def test_import_controller_closing_rejects_new_claims_and_keeps_waiters(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "queued.f1ecap")
    _capture(root / "retry.f1ecap")
    controller = ImportController(database, root)
    controller.start()
    assert controller.ready
    controller.enable_dispatch()
    try:
        sources = list_recording_sources(database, root)
        assert controller.reserve_operation("upload")
        queued = controller.submit(sources[0]["capture_id"], queue_if_busy=True)
        failed, _ = create_import_job(database, sources[1]["capture_id"])
        update_import_job(
            database,
            failed["job_id"],
            status="failed",
            phase="failed",
            finished=True,
        )

        controller.stop_dispatch()
        controller.release_operation("upload")
        with pytest.raises(ValueError, match="import_controller_shutting_down"):
            controller.submit(sources[1]["capture_id"])
        with pytest.raises(ValueError, match="import_controller_shutting_down"):
            controller.retry(failed["job_id"])
        assert controller.reserve_operation("replay") is False
        assert controller.get(queued["job_id"])["status"] == "queued"
        assert controller.get(failed["job_id"])["status"] == "failed"
        assert controller.current_operation_reservation is None
    finally:
        controller.release_operation("upload")
        controller.close()


def test_failed_immediate_claim_releases_reservation_and_keeps_job_queued(
    tmp_path, monkeypatch
):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "claim-failure.f1ecap")
    controller = ImportController(database, root)
    controller.start()
    assert controller.ready
    monkeypatch.setattr(import_controller_module, "claim_oldest_import_job", lambda _path: None)
    try:
        capture_id = list_recording_sources(database, root)[0]["capture_id"]
        with pytest.raises(ValueError, match="import_queue_claim_failed"):
            controller.submit(capture_id)
        assert controller.current_operation_reservation is None
        assert list_recording_sources(database, root)[0]["latest_job_status"] == "queued"
    finally:
        controller.close()


def test_api_remains_readable_when_another_instance_owns_import_control(tmp_path):
    database = tmp_path / "archive.sqlite3"
    root = tmp_path / "recordings"
    root.mkdir()
    _capture(root / "read-only.f1ecap")
    list_recording_sources(database, root)
    owner = ImportController(database, root)
    owner.start()
    assert owner.ready
    app = create_app(database, recordings_root=root)

    async def exercise():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                response = await client.get("/api/v1/recording-sources")
                assert response.status_code == 200
                assert response.json()["status"] == "ok"

    try:
        asyncio.run(exercise())
    finally:
        owner.close()
