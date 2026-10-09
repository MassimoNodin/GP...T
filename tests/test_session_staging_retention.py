from __future__ import annotations

import json
import sqlite3
import time

import pytest

from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore, MAX_STAGED_ROWS_PER_CAR
from tests.test_session_evidence import admitted_packets
from tests.test_lap_tracking import SESSION_UID


def stage(coordinator, database, frame, *, uid="100", car=0, payload="{}"):
    coordinator._stage_observation(database, (
        coordinator.generation, uid, car, frame, 0, 2025, frame, 0, payload,
    ))
    coordinator._prune_staging(database, uid, car)


def test_sparse_duplicate_and_out_of_order_rows_preserve_exact_row_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("f1_engineer.processing.coordinator.MAX_STAGED_ROWS_PER_CAR", 4)
    coordinator = SessionCoordinator(EvidenceStore(tmp_path / "cap.sqlite3"), "cap")
    try:
        with coordinator._transaction() as database:
            for frame in (1, 100, 300, 900, 300, 50, 1000):
                stage(coordinator, database, frame, payload=json.dumps({"frame": frame}))
                actual = [row[0] for row in database.execute("SELECT frame FROM staging ORDER BY frame")]
                assert len(actual) <= 4
                assert coordinator._staging_counts[("100", 0)] == len(actual)
            assert actual == [100, 300, 900, 1000]
            stage(coordinator, database, 300, payload='{"updated":true}')
            assert json.loads(database.execute("SELECT payload FROM staging WHERE frame=300").fetchone()[0]) == {"updated": True}
    finally:
        coordinator.close()


def test_below_cap_has_no_repeated_count_or_prune_sql(tmp_path):
    coordinator = SessionCoordinator(EvidenceStore(tmp_path / "scans.sqlite3"), "scans")
    statements = []
    coordinator._writer.set_trace_callback(statements.append)
    try:
        with coordinator._transaction() as database:
            for frame in range(100):
                stage(coordinator, database, frame)
        assert sum("SELECT COUNT(*) FROM staging" in statement for statement in statements) == 1
        assert not any("DELETE FROM staging" in statement for statement in statements)
    finally:
        coordinator.close()


def test_rollback_invalidates_counts_and_retry_reads_durable_state(tmp_path):
    coordinator = SessionCoordinator(EvidenceStore(tmp_path / "rollback.sqlite3"), "rollback")
    try:
        with coordinator._transaction() as database:
            stage(coordinator, database, 1)
        with pytest.raises(RuntimeError):
            with coordinator._transaction() as database:
                stage(coordinator, database, 2)
                raise RuntimeError("publication failure")
        assert not coordinator._staging_counts
        with coordinator._transaction() as database:
            stage(coordinator, database, 3)
            assert coordinator._staging_counts[("100", 0)] == 2
    finally:
        coordinator.close()


def test_cache_eviction_recounts_without_cross_session_or_car_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr("f1_engineer.processing.coordinator.MAX_STAGED_ROWS_PER_CAR", 2)
    coordinator = SessionCoordinator(EvidenceStore(tmp_path / "cache.sqlite3"), "cache")
    try:
        with coordinator._transaction() as database:
            for uid in range(150):
                stage(coordinator, database, 1, uid=str(uid))
            assert len(coordinator._staging_counts) == 128
            for frame in (2, 3):
                stage(coordinator, database, frame, uid="0")
            stage(coordinator, database, 1, uid="0", car=1)
            assert coordinator._staging_counts[("0", 0)] == 2
            assert database.execute("SELECT COUNT(*) FROM staging WHERE uid='1'").fetchone()[0] == 1
            assert database.execute("SELECT COUNT(*) FROM staging WHERE uid='0' AND car=1").fetchone()[0] == 1
    finally:
        coordinator.close()


def test_invalid_staging_rows_still_fail_instead_of_being_ignored(tmp_path):
    coordinator = SessionCoordinator(EvidenceStore(tmp_path / "invalid.sqlite3"), "invalid")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with coordinator._transaction() as database:
                stage(coordinator, database, 1, payload=None)
        assert not coordinator._staging_counts
        assert coordinator._writer.execute("SELECT COUNT(*) FROM staging").fetchone()[0] == 0
    finally:
        coordinator.close()


def test_publication_and_restart_counts_match_committed_rows(tmp_path):
    store = EvidenceStore(tmp_path / "restart.sqlite3")
    coordinator = SessionCoordinator(store, "restart")
    try:
        for raw in admitted_packets(frames=50):
            coordinator.ingest(raw)
        with store.connect() as database:
            for (uid, car), count in coordinator._staging_counts.items():
                assert count == database.execute(
                    "SELECT COUNT(*) FROM staging WHERE generation=? AND uid=? AND car=?",
                    (coordinator.generation, uid, car),
                ).fetchone()[0]
    finally:
        coordinator.close()
    recovered = SessionCoordinator(store, "restart")
    try:
        assert not recovered._staging_counts
        with recovered._transaction() as database:
            total = 0
            for car in range(22):
                count = recovered._staging_count(database, str(SESSION_UID), car)
                total += count
                assert count == database.execute(
                    "SELECT COUNT(*) FROM staging WHERE generation=? AND uid=? AND car=?",
                    (recovered.generation, str(SESSION_UID), car),
                ).fetchone()[0]
            assert total > 0
    finally:
        recovered.close()


def test_all_driver_pruning_benchmark_matches_legacy_rows_with_less_sql_work(tmp_path):
    measurements = {}
    resulting_rows = {}
    for strategy in ("legacy", "cached"):
        coordinator = SessionCoordinator(EvidenceStore(tmp_path / f"{strategy}.sqlite3"), strategy)
        try:
            with coordinator._transaction() as database:
                database.executemany("INSERT INTO staging VALUES (?,?,?,?,?,?,?,?,?)", (
                    (coordinator.generation, "100", car, frame * 2, 0, 2025, frame, 0, "{}")
                    for car in range(22) for frame in range(6232)
                ))
                for car in range(22):
                    coordinator._staging_count(database, "100", car)
            progress_calls = 0

            def progress():
                nonlocal progress_calls
                progress_calls += 1
                return 0

            coordinator._writer.set_progress_handler(progress, 1000)
            started = time.perf_counter()
            with coordinator._transaction() as database:
                for frame in range(50):
                    for car in range(22):
                        coordinator._stage_observation(database, (
                            coordinator.generation, "100", car, 13000 + frame * 2, 0, 2025, frame, 0, "{}",
                        ))
                        if strategy == "cached":
                            coordinator._prune_staging(database, "100", car)
                        else:
                            database.execute("""DELETE FROM staging WHERE generation=? AND uid=? AND car=? AND frame <
                                COALESCE((SELECT frame FROM staging WHERE generation=? AND uid=? AND car=?
                                ORDER BY frame DESC LIMIT 1 OFFSET ?),-1)""",
                                (coordinator.generation, "100", car, coordinator.generation, "100", car,
                                 MAX_STAGED_ROWS_PER_CAR - 1))
            elapsed = time.perf_counter() - started
            coordinator._writer.set_progress_handler(None, 0)
            resulting_rows[strategy] = [tuple(row) for row in coordinator._writer.execute(
                "SELECT uid,car,frame,payload FROM staging ORDER BY uid,car,frame"
            )]
            measurements[strategy] = {"elapsed_s": elapsed, "sql_progress_calls": progress_calls}
        finally:
            coordinator.close()
    assert resulting_rows["cached"] == resulting_rows["legacy"]
    assert measurements["cached"]["sql_progress_calls"] * 20 < measurements["legacy"]["sql_progress_calls"]
    print("STAGING_PRUNING_BUDGET " + json.dumps({"drivers": 22, "initial_rows_per_car": 6232,
          "frames": 50, "measurements": measurements}, sort_keys=True))
