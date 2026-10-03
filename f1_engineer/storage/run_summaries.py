from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .database import Database


DEFAULT_RUN_PAGE_SIZE = 10
DEFAULT_SESSION_PAGE_SIZE = 20
DEFAULT_ATTEMPT_PAGE_SIZE = 20
DEFAULT_LIFECYCLE_EVENT_PAGE_SIZE = 50
MAX_RUN_PAGE_SIZE = 50
MAX_CHILD_PAGE_SIZE = 100
MAX_PAGE_OFFSET = (1 << 63) - 1
_RECORDING_COUNTERS = (
    "received",
    "recorded",
    "recovered_datagrams",
    "queue_dropped",
    "unpersisted_on_shutdown",
    "socket_errors",
)
_OBSERVER_COUNTERS = (
    "late_packets_ignored",
    "frame_overflow_packets_dropped",
    "lap_data_decode_errors",
    "session_context_decode_errors",
)
_REPLAY_COUNTERS = (
    "import_late_packets_ignored",
    "import_frame_overflow_packets_dropped",
)
_IMPORT_SUMMARY_COUNTERS = (
    "packet_count",
    "malformed_packet_count",
    "session_count",
    "participant_packets",
    "attempts",
    "samples",
    "missing_car_telemetry_samples",
    "lap_data_packets",
    "lap_data_errors",
    "car_telemetry_errors",
    "participant_errors",
    "motion_packets",
    "motion_decode_errors",
    "player_motion_samples",
    "missing_player_motion_samples",
    "car_status_packets",
    "car_status_decode_errors",
    "player_car_status_samples",
    "missing_player_car_status_samples",
    "import_late_packets_ignored",
    "import_frame_overflow_packets_dropped",
    "event_packets",
    "event_decode_errors",
    "lifecycle_events",
    "lifecycle_events_dropped",
    "session_history_packets_admitted",
    "session_history_packets_decoded",
    "session_history_non_player_packets",
    "session_history_player_index_mismatches",
    "session_history_decode_errors",
    "session_history_packets_dropped",
    "session_history_candidates",
    "session_history_association_work",
    "session_history_matched_attempts",
    "session_history_ambiguous_attempts",
    "session_history_conflicting_attempts",
    "session_history_unavailable_attempts",
    "session_history_truncated_attempts",
    "session_history_truncated_sessions",
)


def list_processing_run_summaries(
    database_path: str | Path,
    *,
    limit: int = DEFAULT_RUN_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, object]:
    """Return one bounded page of run-level capture and persisted attempt evidence."""
    _validate_page(limit, offset, maximum=MAX_RUN_PAGE_SIZE)
    with Database(database_path, read_only=True) as db:
        total = int(
            db.connection.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0]
        )
        rows = db.connection.execute(
            """SELECT r.run_id, r.capture_sha256, r.pipeline_version, r.config_json,
                      r.status, r.started_at_utc, r.finished_at_utc, r.error,
                      r.metrics_json, c.byte_size, c.complete, c.completion_json
                 FROM processing_runs r
                 LEFT JOIN captures c USING(capture_sha256)
                 ORDER BY COALESCE(r.finished_at_utc, r.started_at_utc) DESC,
                          r.run_id DESC
                 LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        items = [_build_run_summary(db.connection, row) for row in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def get_processing_run_detail(
    database_path: str | Path,
    run_id: str,
    *,
    session_limit: int = DEFAULT_SESSION_PAGE_SIZE,
    session_offset: int = 0,
    attempt_limit: int = DEFAULT_ATTEMPT_PAGE_SIZE,
    attempt_offset: int = 0,
    lifecycle_event_limit: int = DEFAULT_LIFECYCLE_EVENT_PAGE_SIZE,
    lifecycle_event_offset: int = 0,
) -> dict[str, object] | None:
    """Return a run summary and bounded session/attempt navigation pages."""
    _validate_page(session_limit, session_offset, maximum=MAX_CHILD_PAGE_SIZE)
    _validate_page(attempt_limit, attempt_offset, maximum=MAX_CHILD_PAGE_SIZE)
    _validate_page(
        lifecycle_event_limit, lifecycle_event_offset, maximum=MAX_CHILD_PAGE_SIZE
    )
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT r.run_id, r.capture_sha256, r.pipeline_version, r.config_json,
                      r.status, r.started_at_utc, r.finished_at_utc, r.error,
                      r.metrics_json, c.byte_size, c.complete, c.completion_json
                 FROM processing_runs r
                 LEFT JOIN captures c USING(capture_sha256)
                WHERE r.run_id = ?""",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        summary = _build_run_summary(db.connection, row)
        sessions = _list_run_sessions(
            db.connection, run_id, limit=session_limit, offset=session_offset
        )
        attempts = _list_run_attempts(
            db.connection, run_id, limit=attempt_limit, offset=attempt_offset
        )
        lifecycle_events = _list_run_lifecycle_events(
            db.connection,
            run_id,
            limit=lifecycle_event_limit,
            offset=lifecycle_event_offset,
        )
    return {
        "summary": summary,
        "sessions": sessions,
        "attempts": attempts,
        "lifecycle_events": lifecycle_events,
    }


def get_processing_run_summary(
    database_path: str | Path, run_id: str
) -> dict[str, object] | None:
    """Return persisted run evidence without loading session or attempt pages."""
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT r.run_id, r.capture_sha256, r.pipeline_version, r.config_json,
                      r.status, r.started_at_utc, r.finished_at_utc, r.error,
                      r.metrics_json, c.byte_size, c.complete, c.completion_json
                 FROM processing_runs r
                 LEFT JOIN captures c USING(capture_sha256)
                WHERE r.run_id = ?""",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return _build_run_summary(db.connection, row)


def list_processing_run_lifecycle_events(
    database_path: str | Path,
    run_id: str,
    *,
    limit: int = DEFAULT_LIFECYCLE_EVENT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, object] | None:
    """Return one bounded ordinal page of a run's persisted lifecycle evidence."""
    _validate_page(limit, offset, maximum=MAX_CHILD_PAGE_SIZE)
    with Database(database_path, read_only=True) as db:
        exists = db.connection.execute(
            "SELECT 1 FROM processing_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if exists is None:
            return None
        return _list_run_lifecycle_events(
            db.connection, run_id, limit=limit, offset=offset
        )


def _build_run_summary(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> dict[str, object]:
    metrics = _json_object(row["metrics_json"])
    import_summary = metrics.get("summary") if metrics is not None else None
    capture_quality = metrics.get("capture_quality") if metrics is not None else None
    completion = _json_object(row["completion_json"])
    config = _json_object(row["config_json"])
    totals = _run_totals(connection, str(row["run_id"]))

    capture_complete = row["complete"]
    return {
        "run_id": row["run_id"],
        "capture": {
            "sha256": row["capture_sha256"],
            "byte_size": _nonnegative_integer(row["byte_size"]),
            "complete": (
                bool(capture_complete) if capture_complete is not None else None
            ),
            "footer_status": (
                _status_string(completion.get("status"))
                if completion is not None
                else None
            ),
            "recording_counters": _counter_subset(
                completion, _RECORDING_COUNTERS
            ),
            "recording_observer_counters": _counter_subset(
                completion, _OBSERVER_COUNTERS
            ),
        },
        "processing": {
            "status": row["status"],
            "pipeline_version": row["pipeline_version"],
            "config": config,
            "started_at_utc": row["started_at_utc"],
            "finished_at_utc": row["finished_at_utc"],
            "error": row["error"],
            "metrics_available": metrics is not None,
            "import_counters": _counter_subset(import_summary, _IMPORT_SUMMARY_COUNTERS),
            "replay_counters": _counter_subset(capture_quality, _REPLAY_COUNTERS),
            "capture_quality_available": isinstance(capture_quality, dict),
            "lifecycle_evidence": _lifecycle_metrics(capture_quality),
        },
        "totals": totals,
    }


def _run_totals(connection: sqlite3.Connection, run_id: str) -> dict[str, object]:
    session_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    )
    rows = connection.execute(
        """SELECT l.disposition, l.game_valid, l.reference_eligible, COUNT(*) AS count
             FROM lap_attempts l JOIN sessions s USING(session_key)
            WHERE s.run_id = ?
            GROUP BY l.disposition, l.game_valid, l.reference_eligible""",
        (run_id,),
    ).fetchall()
    disposition_counts: Counter[str] = Counter()
    validity_counts = {"valid": 0, "invalid": 0, "unknown": 0}
    attempt_count = 0
    eligible_count = 0
    for row in rows:
        count = int(row["count"])
        attempt_count += count
        disposition_counts[str(row["disposition"])] += count
        if row["game_valid"] is None:
            validity_counts["unknown"] += count
        elif bool(row["game_valid"]):
            validity_counts["valid"] += count
        else:
            validity_counts["invalid"] += count
        if bool(row["reference_eligible"]):
            eligible_count += count

    exclusion_counts, exclusions_complete = _run_exclusion_reason_counts(
        connection, run_id
    )
    lifecycle_counts = connection.execute(
        """SELECT COUNT(*) AS event_count,
                  SUM(CASE WHEN cause='flashback' THEN 1 ELSE 0 END) AS flashback_count,
                  SUM(CASE WHEN cause='session_time_regression' THEN 1 ELSE 0 END) AS regression_count,
                  SUM(CASE WHEN evidence_status!='verified' THEN 1 ELSE 0 END) AS uncertain_count
             FROM lifecycle_events e JOIN sessions s USING(session_key)
            WHERE s.run_id = ?""",
        (run_id,),
    ).fetchone()
    return {
        "session_count": session_count,
        "attempt_count": attempt_count,
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "game_validity_counts": validity_counts,
        "stored_reference_eligible_count": eligible_count,
        "exclusion_reason_counts": exclusion_counts,
        "exclusion_reasons_complete": exclusions_complete,
        "lifecycle_event_count": int(lifecycle_counts["event_count"] or 0),
        "flashback_event_count": int(lifecycle_counts["flashback_count"] or 0),
        "session_time_regression_count": int(lifecycle_counts["regression_count"] or 0),
        "uncertain_lifecycle_event_count": int(lifecycle_counts["uncertain_count"] or 0),
    }


def _run_exclusion_reason_counts(
    connection: sqlite3.Connection, run_id: str
) -> tuple[list[dict[str, object]], bool]:
    counts: Counter[str] = Counter()
    complete = True
    cursor = connection.execute(
        """SELECT l.exclusion_reasons_json
             FROM lap_attempts l JOIN sessions s USING(session_key)
            WHERE s.run_id = ?""",
        (run_id,),
    )
    while batch := cursor.fetchmany(256):
        for row in batch:
            try:
                reasons = json.loads(row["exclusion_reasons_json"])
            except (TypeError, json.JSONDecodeError):
                complete = False
                continue
            if not isinstance(reasons, list) or any(
                not isinstance(reason, str) for reason in reasons
            ):
                complete = False
                continue
            counts.update(reasons)
    result = [
        {"reason": reason, "attempt_count": count}
        for reason, count in sorted(counts.items())
    ]
    return result, complete


def _list_run_sessions(
    connection: sqlite3.Connection, run_id: str, *, limit: int, offset: int
) -> dict[str, object]:
    total = int(
        connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    )
    rows = connection.execute(
        """SELECT s.session_key, s.session_uid, s.packet_format, s.context_json,
                  (SELECT COUNT(*) FROM session_contexts c
                    WHERE c.session_key = s.session_key) AS context_update_count,
                  (SELECT COUNT(*) FROM session_context_invalidations i
                    WHERE i.session_key = s.session_key) AS context_invalidation_count,
                  (SELECT COUNT(*) FROM lap_attempts l
                    WHERE l.session_key = s.session_key) AS attempt_count
                  ,(SELECT COUNT(*) FROM lifecycle_events e
                    WHERE e.session_key = s.session_key) AS lifecycle_event_count
             FROM sessions s
            WHERE s.run_id = ?
            ORDER BY s.session_uid
            LIMIT ? OFFSET ?""",
        (run_id, limit, offset),
    ).fetchall()
    items = []
    for row in rows:
        context = _json_object(row["context_json"])
        items.append(
            {
                "session_key": row["session_key"],
                "session_uid": row["session_uid"],
                "packet_format": row["packet_format"],
                "latest_context_snapshot": context,
                "context_snapshot_status": _context_snapshot_status(context),
                "context_update_count": int(row["context_update_count"]),
                "context_invalidation_count": int(row["context_invalidation_count"]),
                "attempt_count": int(row["attempt_count"]),
                "lifecycle_event_count": int(row["lifecycle_event_count"]),
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _list_run_attempts(
    connection: sqlite3.Connection, run_id: str, *, limit: int, offset: int
) -> dict[str, object]:
    total = int(
        connection.execute(
            """SELECT COUNT(*) FROM lap_attempts l
                 JOIN sessions s USING(session_key) WHERE s.run_id = ?""",
            (run_id,),
        ).fetchone()[0]
    )
    rows = connection.execute(
        """SELECT l.attempt_key, l.session_key, s.session_uid, l.car_index,
                  l.attempt_number, l.disposition, l.lap_time_ms, l.game_valid,
                  l.reference_eligible, l.sample_count, l.exclusion_reasons_json,
                  l.start_frame_ordinal, l.end_frame_ordinal, l.superseded,
                  l.lifecycle_assessed,
                  e.evidence_json AS timing_evidence_json,
                  (SELECT c.context_json FROM lap_context_segments c
                    WHERE c.attempt_key = l.attempt_key AND c.ordinal = 0) AS first_context_json,
                  (SELECT COUNT(*) FROM lap_context_segments c
                    WHERE c.attempt_key = l.attempt_key) AS context_segment_count,
                  (SELECT COUNT(*) FROM lap_context_segments c
                    WHERE c.attempt_key = l.attempt_key AND c.context_json IS NULL)
                    AS missing_context_segment_count
             FROM lap_attempts l JOIN sessions s USING(session_key)
             LEFT JOIN attempt_timing_evidence e USING(attempt_key)
            WHERE s.run_id = ?
            ORDER BY s.session_uid, l.car_index, l.attempt_number
            LIMIT ? OFFSET ?""",
        (run_id, limit, offset),
    ).fetchall()
    items = []
    for row in rows:
        raw_reasons = _json_value(row["exclusion_reasons_json"])
        reasons = (
            [reason for reason in raw_reasons if isinstance(reason, str)]
            if isinstance(raw_reasons, list)
            else None
        )
        game_valid = row["game_valid"]
        items.append(
            {
                "attempt_key": row["attempt_key"],
                "session_key": row["session_key"],
                "session_uid": row["session_uid"],
                "car_index": row["car_index"],
                "attempt_number": row["attempt_number"],
                "disposition": row["disposition"],
                "lap_time_ms": row["lap_time_ms"],
                "game_valid": (
                    None if game_valid is None else bool(game_valid)
                ),
                "reference_eligible": bool(row["reference_eligible"]),
                "sample_count": row["sample_count"],
                "start_frame_ordinal": row["start_frame_ordinal"],
                "end_frame_ordinal": row["end_frame_ordinal"],
                "superseded": (
                    None if row["superseded"] is None else bool(row["superseded"])
                ),
                "lifecycle_assessed": bool(row["lifecycle_assessed"]),
                "timing_evidence": (
                    _json_object(row["timing_evidence_json"])
                    or {
                        "status": "unavailable",
                        "reasons": ["not_available_for_legacy_import"],
                    }
                ),
                "exclusion_reasons": reasons,
                "first_context_snapshot": _json_object(row["first_context_json"]),
                "context_segment_count": int(row["context_segment_count"]),
                "missing_context_segment_count": int(row["missing_context_segment_count"]),
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _list_run_lifecycle_events(
    connection: sqlite3.Connection, run_id: str, *, limit: int, offset: int
) -> dict[str, object]:
    total = int(
        connection.execute(
            """SELECT COUNT(*) FROM lifecycle_events e
                 JOIN sessions s USING(session_key) WHERE s.run_id = ?""",
            (run_id,),
        ).fetchone()[0]
    )
    rows = connection.execute(
        """SELECT e.*, s.session_uid FROM lifecycle_events e
                 JOIN sessions s USING(session_key)
                WHERE s.run_id = ?
                ORDER BY s.session_uid, e.event_ordinal
                LIMIT ? OFFSET ?""",
        (run_id, limit, offset),
    ).fetchall()
    items = [
        {
            "event_ordinal": int(row["event_ordinal"]),
            "session_uid": row["session_uid"],
            "frame_ordinal": int(row["frame_ordinal"]),
            "current_frame_identifier": int(row["current_frame_identifier"]),
            "current_overall_frame_identifier": int(
                row["current_overall_frame_identifier"]
            ),
            "packet_format": int(row["packet_format"]),
            "packet_version": row["packet_version"],
            "event_code": row["event_code"],
            "event_kind": row["event_kind"],
            "session_time_s": float(row["session_time_s"]),
            "target_game_frame_identifier": row["target_game_frame_identifier"],
            "target_session_time_s": row["target_session_time_s"],
            "prior_session_time_s": row["prior_session_time_s"],
            "cause": row["cause"],
            "evidence_status": row["evidence_status"],
            "details_hex": row["details_hex"],
            "details_length_bytes": int(row["details_length_bytes"]),
            "details_truncated": bool(row["details_truncated"]),
            "duplicate_count": int(row["duplicate_count"]),
        }
        for row in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _counter_subset(value: object, keys: tuple[str, ...]) -> dict[str, int | None] | None:
    if not isinstance(value, dict):
        return None
    return {key: _nonnegative_integer(value.get(key)) for key in keys}


def _lifecycle_metrics(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_counts = value.get("event_code_counts")
    counts: dict[str, int] = {}
    if isinstance(raw_counts, dict):
        for code, count in raw_counts.items():
            if isinstance(code, str) and (parsed := _nonnegative_integer(count)) is not None:
                counts[code] = parsed
    version = value.get("lifecycle_analysis_version")
    return {
        "analysis_version": version if isinstance(version, str) else None,
        "event_packets_decoded": _nonnegative_integer(value.get("event_packets_decoded")),
        "event_decode_errors": _nonnegative_integer(value.get("event_decode_errors")),
        "lifecycle_event_count": _nonnegative_integer(value.get("lifecycle_event_count")),
        "lifecycle_events_dropped": _nonnegative_integer(value.get("lifecycle_events_dropped")),
        "lifecycle_reconciliation_work": _nonnegative_integer(
            value.get("lifecycle_reconciliation_work")
        ),
        "lifecycle_reconciliation_truncated_session_count": _nonnegative_integer(
            value.get("lifecycle_reconciliation_truncated_session_count")
        ),
        "event_code_counts": counts,
    }


def _nonnegative_integer(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _status_string(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _json_object(value: object) -> dict[str, Any] | None:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, dict) else None


def _context_snapshot_status(context: dict[str, Any] | None) -> str:
    if context is None:
        return "missing"
    required = ("game_mode", "session_type", "track_name")
    if all(
        isinstance(context.get(field), str)
        and bool(context[field])
        and context[field] != "unknown"
        for field in required
    ):
        return "complete"
    return "incomplete"


def _json_value(value: object) -> Any:
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _validate_page(limit: int, offset: int, *, maximum: int) -> None:
    if not isinstance(limit, int) or limit < 1 or limit > maximum:
        raise ValueError(f"page limit must be between 1 and {maximum}")
    if not isinstance(offset, int) or offset < 0 or offset > MAX_PAGE_OFFSET:
        raise ValueError(f"page offset must be between 0 and {MAX_PAGE_OFFSET}")
