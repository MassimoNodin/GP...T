from __future__ import annotations

import gc
import json
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor

from f1_engineer.analysis.session_comparison import compare_session_laps
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore
from f1_engineer.pipeline import TelemetryPipeline
from tests.test_session_evidence import admitted_packets, completed_pair


def test_sustained_24_driver_publication_has_bounded_histories_and_concurrent_reads(tmp_path):
    store = EvidenceStore(tmp_path / "load.sqlite3")
    coordinator = SessionCoordinator(store, "load")
    timings = []
    read_count = 0
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending_read = None
        for raw in admitted_packets(frames=1000, car_count=24):
            started = time.perf_counter()
            coordinator.ingest(raw)
            timings.append(time.perf_counter() - started)
            if coordinator.sequence == 100:
                session, target, reference = completed_pair(store)
            if coordinator.sequence > 100 and coordinator.sequence % 100 == 0:
                if pending_read:
                    report = pending_read.result(timeout=10)
                    assert report["channels"]["brake"]["coverage"] > 0.9
                    read_count += 1
                pending_read = executor.submit(compare_session_laps, store, session, target["id"], reference["id"])
            assert len(coordinator.pipeline.laps.attempts) == 0
            assert len(coordinator.pipeline.sessions.context_history(coordinator.pipeline.sessions.current_session_uid)) <= 512
            assert len(coordinator.pipeline.car_lap_inventory.active_tenures()) <= 24
            assert coordinator.pipeline.frames._pending_packets <= coordinator.pipeline.frames.max_pending_packets
        if pending_read:
            pending_read.result(timeout=10)
            read_count += 1
    with store.connect() as database:
        attempt_count = database.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        staging_count = database.execute("SELECT COUNT(*) FROM staging").fetchone()[0]
        chunk_max_rows = database.execute("SELECT MAX(rows) FROM chunks").fetchone()[0]
        assert attempt_count >= 24 * 49
        assert staging_count <= 24 * 20
        assert chunk_max_rows <= 256
    assert read_count >= 10
    assert max(timings) < 5
    print("LOAD_BUDGET " + json.dumps({"drivers": 24, "frames": 1000, "datagrams": len(timings),
          "ingest_datagrams_per_s": len(timings) / sum(timings),
          "ingest_p95_ms": sorted(timings)[int(len(timings) * 0.95)] * 1000,
          "ingest_max_ms": max(timings) * 1000, "attempts": attempt_count,
          "staging_rows": staging_count, "concurrent_comparisons": read_count,
          "database_bytes": store.path.stat().st_size}, sort_keys=True))
    coordinator.close()


def test_streaming_diagnostics_are_bounded_without_changing_archive_defaults():
    pipeline = TelemetryPipeline()
    names = ("lap_data_decode_errors", "car_telemetry_decode_errors", "motion_decode_errors",
             "car_status_decode_errors", "car_damage_decode_errors", "participants_decode_errors",
             "session_history_decode_errors", "car_setups_decode_errors")
    for name in names:
        getattr(pipeline, name).extend(["unavailable"] * 1000)
        assert len(getattr(pipeline, name)) == 1000
    pipeline.release_consumed_history()
    for name in names:
        assert len(getattr(pipeline, name)) == 64


def test_python_state_retention_does_not_scale_with_lap_count(tmp_path):
    store = EvidenceStore(tmp_path / "memory.sqlite3")
    coordinator = SessionCoordinator(store, "memory")
    tracemalloc.start()
    checkpoints = []
    try:
        for raw in admitted_packets(frames=600, car_count=24):
            coordinator.ingest(raw)
            if coordinator.sequence in (400, 1200):
                gc.collect()
                checkpoints.append(tracemalloc.get_traced_memory()[0])
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        coordinator.close()
    assert checkpoints[1] - checkpoints[0] < 2 * 1024 * 1024
    assert peak < 32 * 1024 * 1024
    print("MEMORY_BUDGET " + json.dumps({"drivers": 24, "frames": 600,
          "retained_bytes_at_200_frames": checkpoints[0], "retained_bytes_at_600_frames": checkpoints[1],
          "python_peak_bytes": peak}, sort_keys=True))
