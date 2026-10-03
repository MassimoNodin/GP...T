from __future__ import annotations

import json

import pytest

from f1_engineer.storage.database import Database
from f1_engineer.storage.run_summaries import (
    MAX_PAGE_OFFSET,
    MAX_RUN_PAGE_SIZE,
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
