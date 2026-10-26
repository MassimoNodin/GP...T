from __future__ import annotations

import json
import sqlite3

import pytest

import f1_engineer.storage.run_summaries as run_summaries_module
from f1_engineer.storage.database import Database
from f1_engineer.storage.run_summaries import (
    ArchiveFilterLimitExceeded,
    MAX_PAGE_OFFSET,
    MAX_RUN_PAGE_SIZE,
    RunArchiveFilters,
    get_processing_run_detail,
    get_processing_run_summary,
    list_processing_run_summaries,
)


def _add_run(
    connection,
    run_id: str,
    *,
    status: str = "complete",
    capture_complete: bool = True,
    completion: dict[str, object] | None = None,
    metrics: dict[str, object] | None = None,
    error: str | None = None,
    byte_size: int = 4096,
) -> str:
    capture_hash = run_id[-64:].rjust(64, "0")
    connection.execute(
        """INSERT INTO captures(capture_sha256,source_path,byte_size,complete,
                  completion_json,metadata_json) VALUES (?,?,?,?,?,?)""",
        (
            capture_hash,
            "local-capture.f1ecap",
            byte_size,
            int(capture_complete),
            json.dumps(completion) if completion is not None else None,
            "{}",
        ),
    )
    connection.execute(
        """INSERT INTO processing_runs(run_id,capture_sha256,pipeline_version,
                  config_json,status,error,metrics_json) VALUES (?,?,?,?,?,?,?)""",
        (
            run_id,
            capture_hash,
            "player-traces-test",
            '{"reorder_window_frames":3}',
            status,
            error,
            json.dumps(metrics) if metrics is not None else None,
        ),
    )
    return capture_hash


def _add_session(
    connection,
    run_id: str,
    session_uid: str,
    *,
    latest_context: dict[str, object] | None,
    context_updates: tuple[dict[str, object], ...] = (),
    invalidations: tuple[tuple[int, str], ...] = (),
) -> str:
    session_key = f"{run_id}:{session_uid}"
    connection.execute(
        """INSERT INTO sessions(session_key,run_id,session_uid,packet_format,context_json)
             VALUES (?,?,?,?,?)""",
        (
            session_key,
            run_id,
            session_uid,
            2025,
            json.dumps(latest_context) if latest_context is not None else None,
        ),
    )
    for frame, context in enumerate(context_updates, start=1):
        connection.execute(
            "INSERT INTO session_contexts(session_key,effective_frame,context_json) VALUES (?,?,?)",
            (session_key, frame, json.dumps(context)),
        )
    for frame, reason in invalidations:
        connection.execute(
            "INSERT INTO session_context_invalidations(session_key,effective_frame,reason) VALUES (?,?,?)",
            (session_key, frame, reason),
        )
    return session_key


def _add_attempt(
    connection,
    *,
    attempt_key: str,
    session_key: str,
    attempt_number: int,
    disposition: str,
    game_valid: bool | None,
    reference_eligible: bool,
    reasons: list[str],
    context_segments: tuple[dict[str, object] | None, ...] = (),
) -> None:
    lap_time_ms = 80_000 if disposition == "completed" else None
    connection.execute(
        """INSERT INTO lap_attempts(attempt_key,session_key,car_index,attempt_number,
                  lap_number,disposition,start_frame_identifier,end_frame_identifier,
                  start_session_time_s,end_session_time_s,lap_time_ms,game_valid,
                  start_observed,pit_encountered,sample_count,reference_eligible,
                  exclusion_reasons_json,attempt_json)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            attempt_key,
            session_key,
            0,
            attempt_number,
            attempt_number,
            disposition,
            1,
            2,
            0.0,
            80.0 if lap_time_ms else None,
            lap_time_ms,
            None if game_valid is None else int(game_valid),
            1,
            0,
            2,
            int(reference_eligible),
            json.dumps(reasons),
            "{}",
        ),
    )
    for ordinal, context in enumerate(context_segments):
        connection.execute(
            """INSERT INTO lap_context_segments(attempt_key,ordinal,
                      from_frame_identifier,context_json) VALUES (?,?,?,?)""",
            (
                attempt_key,
                ordinal,
                ordinal + 1,
                json.dumps(context) if context is not None else None,
            ),
        )


def _seed_mixed_run(database_path) -> str:
    run_id = "a" * 64
    with Database(database_path) as db:
        with db.connection:
            connection = db.connection
            _add_run(
                connection,
                run_id,
                completion={
                    "status": "complete",
                    "received": 100,
                    "recorded": 98,
                    "queue_dropped": 2,
                    "socket_errors": 0,
                },
                metrics={
                    "summary": {
                        "packet_count": 98,
                        "malformed_packet_count": 3,
                        "session_count": 2,
                        "attempts": 3,
                        "samples": 20,
                        "lap_data_errors": 1,
                        "missing_car_telemetry_samples": 2,
                    },
                    "capture_quality": {
                        "import_late_packets_ignored": 1,
                        "import_frame_overflow_packets_dropped": 0,
                    },
                },
                byte_size=123_456,
            )
            tt_context = {
                "game_mode": "time_trial",
                "session_type": "time_trial",
                "track_name": "Melbourne",
            }
            tt_session = _add_session(
                connection,
                run_id,
                "18446744073709550001",
                latest_context=tt_context,
                context_updates=(tt_context,),
            )
            _add_attempt(
                connection,
                attempt_key=f"{tt_session}:0:1",
                session_key=tt_session,
                attempt_number=1,
                disposition="completed",
                game_valid=True,
                reference_eligible=True,
                reasons=[],
                context_segments=(tt_context,),
            )
            career_context = {
                "game_mode": "driver_career_25",
                "session_type": "practice_1",
                "track_name": "Shanghai",
            }
            latest_career_context = {**career_context, "session_type": "unknown"}
            career_session = _add_session(
                connection,
                run_id,
                "14237356543050158953",
                latest_context=latest_career_context,
                context_updates=(career_context, latest_career_context),
                invalidations=((30, "packet_format_changed"),),
            )
            _add_attempt(
                connection,
                attempt_key=f"{career_session}:0:1",
                session_key=career_session,
                attempt_number=1,
                disposition="completed",
                game_valid=False,
                reference_eligible=False,
                reasons=["game_marked_invalid"],
                context_segments=(career_context,),
            )
            _add_attempt(
                connection,
                attempt_key=f"{career_session}:0:2",
                session_key=career_session,
                attempt_number=2,
                disposition="partial",
                game_valid=None,
                reference_eligible=False,
                reasons=["capture_ended_before_lap_completion"],
                context_segments=(career_context, None),
            )
    return run_id


def test_run_summary_separates_capture_replay_and_reference_evidence(tmp_path) -> None:
    database_path = tmp_path / "summary.sqlite3"
    run_id = _seed_mixed_run(database_path)

    page = list_processing_run_summaries(database_path)
    summary = page["items"][0]
    capture = summary["capture"]
    processing = summary["processing"]
    totals = summary["totals"]

    assert page["total"] == 1
    assert summary["run_id"] == run_id
    assert capture["byte_size"] == 123_456
    assert capture["complete"] is True
    assert capture["footer_status"] == "complete"
    assert capture["recording_counters"]["queue_dropped"] == 2
    assert capture["recording_counters"]["unpersisted_on_shutdown"] is None
    assert processing["status"] == "complete"
    assert processing["pipeline_version"] == "player-traces-test"
    assert processing["import_counters"]["packet_count"] == 98
    assert processing["replay_counters"]["import_late_packets_ignored"] == 1
    assert totals["session_count"] == 2
    assert totals["attempt_count"] == 3
    assert totals["disposition_counts"] == {"completed": 2, "partial": 1}
    assert totals["game_validity_counts"] == {
        "valid": 1,
        "invalid": 1,
        "unknown": 1,
    }
    assert totals["stored_reference_eligible_count"] == 1
    assert totals["exclusion_reason_counts"] == [
        {"reason": "capture_ended_before_lap_completion", "attempt_count": 1},
        {"reason": "game_marked_invalid", "attempt_count": 1},
    ]
    assert "selected_reference" not in totals


def test_direct_run_summary_preserves_incomplete_footer_and_unknown_counters(tmp_path) -> None:
    database_path = tmp_path / "summary.sqlite3"
    run_id = "d" * 64
    with Database(database_path) as db:
        with db.connection:
            _add_run(
                db.connection,
                run_id,
                status="complete",
                capture_complete=False,
                completion={"status": "incomplete", "recovered_datagrams": 10},
                metrics={
                    "capture_quality": {
                        "import_late_packets_ignored": 2,
                    }
                },
            )

    summary = get_processing_run_summary(database_path, run_id)

    assert summary is not None
    assert summary["capture"]["complete"] is False
    assert summary["capture"]["footer_status"] == "incomplete"
    assert summary["capture"]["recording_counters"]["recovered_datagrams"] == 10
    assert summary["capture"]["recording_counters"]["recorded"] is None
    assert summary["processing"]["replay_counters"] == {
        "import_late_packets_ignored": 2,
        "import_frame_overflow_packets_dropped": None,
    }
    assert summary["totals"]["attempt_count"] == 0
    assert get_processing_run_summary(database_path, "missing") is None


def test_run_detail_labels_latest_context_and_pages_session_attempt_links(tmp_path) -> None:
    database_path = tmp_path / "summary.sqlite3"
    run_id = _seed_mixed_run(database_path)

    detail = get_processing_run_detail(
        database_path,
        run_id,
        session_limit=1,
        session_offset=1,
        attempt_limit=1,
        attempt_offset=1,
    )

    assert detail is not None
    assert detail["sessions"]["total"] == 2
    session = detail["sessions"]["items"][0]
    assert session["session_uid"] == "18446744073709550001"
    assert session["latest_context_snapshot"]["session_type"] == "time_trial"
    assert session["context_snapshot_status"] == "complete"
    assert session["context_update_count"] == 1
    assert session["attempt_count"] == 1
    assert detail["attempts"]["total"] == 3
    attempt = detail["attempts"]["items"][0]
    assert attempt["disposition"] == "partial"
    assert attempt["game_valid"] is None
    assert attempt["reference_eligible"] is False
    assert attempt["context_segment_count"] == 2
    assert attempt["missing_context_segment_count"] == 1
    assert attempt["first_context_snapshot"]["session_type"] == "practice_1"


def test_run_detail_distinguishes_incomplete_and_missing_context_snapshots(tmp_path) -> None:
    database_path = tmp_path / "summary.sqlite3"
    run_id = _seed_mixed_run(database_path)

    detail = get_processing_run_detail(database_path, run_id, session_limit=1)
    assert detail is not None
    assert detail["sessions"]["items"][0]["context_snapshot_status"] == "incomplete"

    with Database(database_path) as db:
        with db.connection:
            db.connection.execute(
                "UPDATE sessions SET context_json = NULL WHERE session_uid = ?",
                ("14237356543050158953",),
            )

    detail = get_processing_run_detail(database_path, run_id, session_limit=1)
    assert detail is not None
    assert detail["sessions"]["items"][0]["context_snapshot_status"] == "missing"


def test_run_summary_preserves_unknown_legacy_metrics_for_failed_run(tmp_path) -> None:
    database_path = tmp_path / "summary.sqlite3"
    run_id = "b" * 64
    with Database(database_path) as db:
        with db.connection:
            _add_run(
                db.connection,
                run_id,
                status="failed",
                capture_complete=False,
                completion=None,
                metrics=None,
                error="truncated capture",
            )

    detail = get_processing_run_detail(database_path, run_id)

    assert detail is not None
    summary = detail["summary"]
    assert summary["capture"]["complete"] is False
    assert summary["capture"]["footer_status"] is None
    assert summary["capture"]["recording_counters"] is None
    assert summary["processing"]["status"] == "failed"
    assert summary["processing"]["error"] == "truncated capture"
    assert summary["processing"]["metrics_available"] is False
    assert summary["processing"]["import_counters"] is None
    assert summary["processing"]["replay_counters"] is None
    assert summary["totals"]["attempt_count"] == 0
    assert summary["totals"]["exclusion_reason_counts"] == []


def test_run_summary_treats_non_string_footer_status_as_unknown(tmp_path) -> None:
    database_path = tmp_path / "summary.sqlite3"
    run_id = "c" * 64
    with Database(database_path) as db:
        with db.connection:
            _add_run(
                db.connection,
                run_id,
                completion={"status": {"unexpected": "object"}},
            )

    summary = list_processing_run_summaries(database_path)["items"][0]

    assert summary["capture"]["footer_status"] is None


def test_run_pages_are_bounded_and_ordered(tmp_path) -> None:
    database_path = tmp_path / "summary.sqlite3"
    with Database(database_path) as db:
        with db.connection:
            _add_run(db.connection, "1" * 64)
            _add_run(db.connection, "2" * 64)

    page = list_processing_run_summaries(database_path, limit=1, offset=1)

    assert page["total"] == 2
    assert page["limit"] == 1
    assert page["offset"] == 1
    assert page["items"][0]["run_id"] == "1" * 64
    with pytest.raises(ValueError, match="page limit"):
        list_processing_run_summaries(database_path, limit=MAX_RUN_PAGE_SIZE + 1)
    with pytest.raises(ValueError, match="page offset"):
        list_processing_run_summaries(database_path, offset=MAX_PAGE_OFFSET + 1)


def test_filtered_run_archive_requires_context_and_search_to_match_one_session(
    tmp_path,
) -> None:
    database_path = tmp_path / "filtered-archive.sqlite3"
    split_run = "abc" + "1" * 61
    matching_run = "def" + "2" * 61
    second_matching_run = "ghi" + "3" * 61
    with Database(database_path) as db:
        with db.connection:
            _add_run(db.connection, split_run)
            _add_run(db.connection, matching_run)
            _add_run(db.connection, second_matching_run)
            _add_session(
                db.connection,
                split_run,
                "12345",
                latest_context={
                    "packet_format": 2025,
                    "track_id": 10,
                    "session_type": "practice_1",
                },
            )
            _add_session(
                db.connection,
                split_run,
                "98765",
                latest_context={
                    "packet_format": 2025,
                    "track_id": 11,
                    "session_type": "race",
                },
            )
            _add_session(
                db.connection,
                matching_run,
                "54321",
                latest_context={
                    "packet_format": 2025,
                    "track_id": 10,
                    "session_type": "race_2",
                },
            )
            _add_session(
                db.connection,
                second_matching_run,
                "65432",
                latest_context={
                    "packet_format": 2025,
                    "track_id": 10,
                    "session_type": "race_3",
                },
            )

    same_row = list_processing_run_summaries(
        database_path,
        filters=RunArchiveFilters(
            packet_format=2025, track_id=10, session_category="race"
        ),
    )
    session_uid_match = list_processing_run_summaries(
        database_path,
        filters=RunArchiveFilters(
            q="987", session_category="race", packet_format=2025, track_id=11
        ),
    )
    mismatched_session_uid = list_processing_run_summaries(
        database_path,
        filters=RunArchiveFilters(
            q="987", session_category="race", packet_format=2025, track_id=10
        ),
    )
    run_id_search = list_processing_run_summaries(
        database_path,
        filters=RunArchiveFilters(
            q="abc", session_category="practice", packet_format=2025, track_id=10
        ),
    )
    filtered_page = list_processing_run_summaries(
        database_path,
        limit=1,
        offset=1,
        filters=RunArchiveFilters(
            packet_format=2025, track_id=10, session_category="race"
        ),
    )

    assert {item["run_id"] for item in same_row["items"]} == {
        matching_run,
        second_matching_run,
    }
    assert same_row["total"] == 2
    assert [item["run_id"] for item in filtered_page["items"]] == [matching_run]
    assert filtered_page["total"] == 2
    assert filtered_page["offset"] == 1
    assert [item["run_id"] for item in session_uid_match["items"]] == [split_run]
    assert mismatched_session_uid["items"] == []
    assert [item["run_id"] for item in run_id_search["items"]] == [split_run]
    assert same_row["filters"] == {
        "q": None,
        "packet_format": 2025,
        "track_id": 10,
        "session_category": "race",
        "started_from": None,
        "started_through": None,
    }


def test_filtered_run_archive_unknown_category_and_utc_processing_dates(tmp_path) -> None:
    database_path = tmp_path / "archive-dates.sqlite3"
    boundary_run = "a" * 64
    before_run = "b" * 64
    missing_context_run = "c" * 64
    malformed_context_run = "d" * 64
    unrecognized_context_run = "e" * 64
    deeply_nested_context_run = "f" * 64
    oversized_integer_context_run = "1" * 64
    with Database(database_path) as db:
        with db.connection:
            for run_id in (
                boundary_run,
                before_run,
                missing_context_run,
                malformed_context_run,
                unrecognized_context_run,
                deeply_nested_context_run,
                oversized_integer_context_run,
            ):
                _add_run(db.connection, run_id)
            db.connection.execute(
                "UPDATE processing_runs SET started_at_utc=? WHERE run_id=?",
                ("2026-10-04T23:30:00-01:00", boundary_run),
            )
            db.connection.execute(
                "UPDATE processing_runs SET started_at_utc=? WHERE run_id=?",
                ("2026-10-04T23:59:59+00:00", before_run),
            )
            for run_id in (
                missing_context_run,
                malformed_context_run,
                unrecognized_context_run,
                deeply_nested_context_run,
                oversized_integer_context_run,
            ):
                db.connection.execute(
                    "UPDATE processing_runs SET started_at_utc=? WHERE run_id=?",
                    ("2026-10-06T12:00:00+00:00", run_id),
                )
            _add_session(db.connection, missing_context_run, "101", latest_context=None)
            _add_session(db.connection, malformed_context_run, "102", latest_context=None)
            _add_session(
                db.connection,
                unrecognized_context_run,
                "103",
                latest_context={"packet_format": 2025, "track_id": 4, "session_type": "future_session"},
            )
            db.connection.execute(
                "UPDATE sessions SET context_json=? WHERE run_id=?",
                ("{malformed", malformed_context_run),
            )
            _add_session(
                db.connection,
                deeply_nested_context_run,
                "104",
                latest_context=None,
            )
            db.connection.execute(
                "UPDATE sessions SET context_json=? WHERE run_id=?",
                ("[" * 1100 + "0" + "]" * 1100, deeply_nested_context_run),
            )
            _add_session(
                db.connection,
                oversized_integer_context_run,
                "105",
                latest_context=None,
            )
            db.connection.execute(
                "UPDATE sessions SET context_json=? WHERE run_id=?",
                ("{" + '"value":' + "1" * 5000 + "}", oversized_integer_context_run),
            )

    date_page = list_processing_run_summaries(
        database_path,
        filters=RunArchiveFilters(
            started_from="2026-10-05", started_through="2026-10-05"
        ),
    )
    unknown_page = list_processing_run_summaries(
        database_path, filters=RunArchiveFilters(session_category="unknown")
    )

    assert [item["run_id"] for item in date_page["items"]] == [boundary_run]
    assert set(item["run_id"] for item in unknown_page["items"]) == {
        missing_context_run,
        malformed_context_run,
        unrecognized_context_run,
        deeply_nested_context_run,
        oversized_integer_context_run,
    }


def test_filtered_run_archive_abstains_at_candidate_session_context_and_vm_bounds(
    monkeypatch, tmp_path
) -> None:
    run_limit_db = tmp_path / "run-limit.sqlite3"
    first_run = "abc" + "1" * 61
    second_run = "abc" + "2" * 61
    with Database(run_limit_db) as db:
        with db.connection:
            _add_run(db.connection, first_run)
            _add_run(db.connection, second_run)
    monkeypatch.setattr("f1_engineer.storage.run_summaries.MAX_ARCHIVE_RUN_CANDIDATES", 1)
    with pytest.raises(ArchiveFilterLimitExceeded, match="archive_filter_limit_exceeded"):
        list_processing_run_summaries(
            run_limit_db, filters=RunArchiveFilters(q="abc")
        )

    session_limit_db = tmp_path / "session-limit.sqlite3"
    session_limit_run = "f" * 64
    with Database(session_limit_db) as db:
        with db.connection:
            _add_run(db.connection, session_limit_run)
            for uid in ("101", "102"):
                _add_session(
                    db.connection,
                    session_limit_run,
                    uid,
                    latest_context={"packet_format": 2025, "track_id": 1, "session_type": "race"},
                )
    monkeypatch.setattr("f1_engineer.storage.run_summaries.MAX_ARCHIVE_SESSION_ROWS", 1)
    with pytest.raises(ArchiveFilterLimitExceeded, match="archive_filter_limit_exceeded"):
        list_processing_run_summaries(
            session_limit_db, filters=RunArchiveFilters(session_category="race")
        )
    with pytest.raises(ArchiveFilterLimitExceeded, match="archive_filter_limit_exceeded"):
        list_processing_run_summaries(
            session_limit_db, filters=RunArchiveFilters(q="999")
        )
    with pytest.raises(ArchiveFilterLimitExceeded, match="archive_filter_limit_exceeded"):
        list_processing_run_summaries(
            session_limit_db,
            filters=RunArchiveFilters(q="999", session_category="race"),
        )

    context_limit_db = tmp_path / "context-limit.sqlite3"
    context_limit_run = "9" * 64
    with Database(context_limit_db) as db:
        with db.connection:
            _add_run(db.connection, context_limit_run)
            _add_session(
                db.connection,
                context_limit_run,
                "109",
                latest_context={
                    "packet_format": 2025,
                    "track_id": 1,
                    "session_type": "race",
                    "description": "too large for the test budget",
                },
            )
    monkeypatch.setattr(
        "f1_engineer.storage.run_summaries.MAX_ARCHIVE_CONTEXT_BYTES_PER_ROW", 16
    )
    with pytest.raises(ArchiveFilterLimitExceeded, match="archive_filter_limit_exceeded"):
        list_processing_run_summaries(
            context_limit_db, filters=RunArchiveFilters(session_category="race")
        )

    aggregate_context_db = tmp_path / "aggregate-context-limit.sqlite3"
    aggregate_context_run = "8" * 64
    with Database(aggregate_context_db) as db:
        with db.connection:
            _add_run(db.connection, aggregate_context_run)
            for uid in ("108", "110"):
                _add_session(
                    db.connection,
                    aggregate_context_run,
                    uid,
                    latest_context={
                        "packet_format": 2025,
                        "track_id": 1,
                        "session_type": "race",
                        "note": "small context row",
                    },
                )
    monkeypatch.setattr(
        "f1_engineer.storage.run_summaries.MAX_ARCHIVE_CONTEXT_BYTES_PER_ROW",
        64 * 1024,
    )
    monkeypatch.setattr(
        "f1_engineer.storage.run_summaries.MAX_ARCHIVE_CONTEXT_BYTES", 100
    )
    with pytest.raises(ArchiveFilterLimitExceeded, match="archive_filter_limit_exceeded"):
        list_processing_run_summaries(
            aggregate_context_db,
            filters=RunArchiveFilters(session_category="race"),
        )

    progress_db = tmp_path / "progress-limit.sqlite3"
    with Database(progress_db) as db:
        with db.connection:
            _add_run(db.connection, "7" * 64)
    monkeypatch.setattr(
        "f1_engineer.storage.run_summaries.ARCHIVE_SQL_PROGRESS_INTERVAL", 1
    )
    monkeypatch.setattr(
        "f1_engineer.storage.run_summaries.MAX_ARCHIVE_SQL_PROGRESS_CALLBACKS", 0
    )
    with pytest.raises(ArchiveFilterLimitExceeded, match="archive_filter_limit_exceeded"):
        list_processing_run_summaries(
            progress_db, filters=RunArchiveFilters(q="777")
        )


def test_filtered_run_archive_count_and_page_share_one_read_snapshot(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "archive-snapshot.sqlite3"
    first_run = "aaa" + "1" * 61
    concurrent_run = "aaa" + "2" * 61
    with Database(database_path) as db:
        with db.connection:
            _add_run(db.connection, first_run)

    original_builder = run_summaries_module._build_run_summary
    inserted = False

    def insert_concurrent_run(connection, row):
        nonlocal inserted
        if not inserted:
            writer = sqlite3.connect(database_path)
            try:
                capture_hash = "b" * 64
                writer.execute(
                    """INSERT INTO captures(capture_sha256,source_path,byte_size,complete,
                              metadata_json) VALUES (?,?,?,?,?)""",
                    (capture_hash, "concurrent.f1ecap", 10, 1, "{}"),
                )
                writer.execute(
                    """INSERT INTO processing_runs(run_id,capture_sha256,pipeline_version,
                              config_json,status) VALUES (?,?,?,?,?)""",
                    (concurrent_run, capture_hash, "test", "{}", "complete"),
                )
                writer.commit()
                inserted = True
            finally:
                writer.close()
        return original_builder(connection, row)

    monkeypatch.setattr(
        "f1_engineer.storage.run_summaries._build_run_summary",
        insert_concurrent_run,
    )
    page = list_processing_run_summaries(
        database_path, filters=RunArchiveFilters(q="aaa")
    )

    assert page["total"] == 1
    assert [item["run_id"] for item in page["items"]] == [first_run]
    assert list_processing_run_summaries(
        database_path, filters=RunArchiveFilters(q="aaa")
    )["total"] == 2
