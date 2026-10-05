from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import Database


_ACTIVE = ("starting", "recording", "stopping")
_GROUP_ACTIVE = ("starting", "recording", "pausing", "paused", "resuming", "stopping")
_GROUP_TERMINAL = ("complete", "failed", "interrupted")
MAX_RECORDING_GROUP_SEGMENTS = 256
MAX_RECORDING_GROUP_SEGMENT_PAGE = 50
MAX_RECORDING_GROUP_EVENTS = 1025


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def create_recording_job(
    database_path: str | Path,
    *,
    staging_relative_path: str,
    final_relative_path: str,
    bind_host: str,
    bind_port: int,
    recording_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute(
            "SELECT * FROM recording_jobs WHERE status IN ('starting','recording','stopping') ORDER BY created_at_utc DESC LIMIT 1"
        ).fetchone()
        if active is not None:
            connection.commit()
            return _recording_record(active), False
        recording_id = recording_id or uuid.uuid4().hex
        connection.execute(
            """INSERT INTO recording_jobs(
                   recording_id, status, staging_relative_path, final_relative_path,
                   bind_host, bind_port, updated_at_utc)
               VALUES (?, 'starting', ?, ?, ?, ?, ?)""",
            (
                recording_id,
                staging_relative_path,
                final_relative_path,
                bind_host,
                bind_port,
                _utc_now(),
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM recording_jobs WHERE recording_id=?", (recording_id,)
        ).fetchone()
    assert row is not None
    return _recording_record(row), True


def create_recording_group(
    database_path: str | Path,
    *,
    group_id: str,
    recording_id: str,
    staging_relative_path: str,
    final_relative_path: str,
    bind_host: str,
    bind_port: int,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute(
            "SELECT * FROM recording_groups WHERE status IN ('starting','recording','pausing','paused','resuming','stopping') ORDER BY created_at_utc DESC LIMIT 1"
        ).fetchone()
        if active is not None:
            connection.commit()
            group = _group_record(active)
            segment = None
            segment_id = group["current_recording_id"] or group["last_recording_id"]
            if segment_id:
                row = connection.execute(
                    "SELECT * FROM recording_jobs WHERE recording_id=?",
                    (segment_id,),
                ).fetchone()
                segment = _recording_record(row) if row is not None else None
            return group, segment or {}, False

        now = _utc_now()
        connection.execute(
            """INSERT INTO recording_groups(
                   group_id, status, bind_host, bind_port, transition_revision,
                   updated_at_utc, current_recording_id, last_recording_id, segment_count)
               VALUES (?, 'starting', ?, ?, 0, ?, ?, ?, 1)""",
            (group_id, bind_host, bind_port, now, recording_id, recording_id),
        )
        connection.execute(
            """INSERT INTO recording_jobs(
                   recording_id, status, staging_relative_path, final_relative_path,
                   bind_host, bind_port, updated_at_utc, group_id, segment_ordinal)
               VALUES (?, 'starting', ?, ?, ?, ?, ?, ?, 1)""",
            (
                recording_id,
                staging_relative_path,
                final_relative_path,
                bind_host,
                bind_port,
                now,
                group_id,
            ),
        )
        _insert_group_event(
            connection,
            group_id,
            "start_requested",
            now,
            recording_id,
            0,
        )
        connection.commit()
        group_row = connection.execute(
            "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
        ).fetchone()
        segment_row = connection.execute(
            "SELECT * FROM recording_jobs WHERE recording_id=?", (recording_id,)
        ).fetchone()
    assert group_row is not None and segment_row is not None
    return _group_record(group_row), _recording_record(segment_row), True


def create_recording_group_segment(
    database_path: str | Path,
    *,
    group_id: str,
    expected_revision: int,
    expected_recording_id: str,
    recording_id: str,
    staging_relative_path: str,
    final_relative_path: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        group = connection.execute(
            "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
        ).fetchone()
        if group is None:
            raise ValueError("recording_group_unavailable")
        if (
            group["status"] != "paused"
            or group["transition_revision"] != expected_revision
            or group["last_recording_id"] != expected_recording_id
        ):
            connection.rollback()
            raise ValueError("recording_group_transition_conflict")
        ordinal = int(group["segment_count"]) + 1
        if ordinal > MAX_RECORDING_GROUP_SEGMENTS:
            connection.rollback()
            raise ValueError("recording_group_segment_limit_reached")
        now = _utc_now()
        revision = int(group["transition_revision"]) + 1
        updated = connection.execute(
            """UPDATE recording_groups
                  SET status='resuming', transition_revision=?, updated_at_utc=?,
                      current_recording_id=?, last_recording_id=?, segment_count=?
                WHERE group_id=? AND status='paused' AND transition_revision=?""",
            (
                revision,
                now,
                recording_id,
                recording_id,
                ordinal,
                group_id,
                expected_revision,
            ),
        ).rowcount
        if updated != 1:
            connection.rollback()
            raise ValueError("recording_group_transition_conflict")
        connection.execute(
            """INSERT INTO recording_jobs(
                   recording_id, status, staging_relative_path, final_relative_path,
                   bind_host, bind_port, updated_at_utc, group_id, segment_ordinal)
               VALUES (?, 'starting', ?, ?, ?, ?, ?, ?, ?)""",
            (
                recording_id,
                staging_relative_path,
                final_relative_path,
                group["bind_host"],
                group["bind_port"],
                now,
                group_id,
                ordinal,
            ),
        )
        _insert_group_event(
            connection,
            group_id,
            "resume_requested",
            now,
            recording_id,
            revision,
            {"segment_ordinal": ordinal},
        )
        group_row = connection.execute(
            "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
        ).fetchone()
        segment_row = connection.execute(
            "SELECT * FROM recording_jobs WHERE recording_id=?", (recording_id,)
        ).fetchone()
        connection.commit()
    assert group_row is not None and segment_row is not None
    return _group_record(group_row), _recording_record(segment_row)


def request_recording_group_transition(
    database_path: str | Path,
    group_id: str,
    *,
    target_status: str,
    event_kind: str,
    expected_status: str | tuple[str, ...],
    expected_revision: int | None = None,
    expected_recording_id: str | None = None,
) -> dict[str, Any]:
    allowed_statuses = (expected_status,) if isinstance(expected_status, str) else expected_status
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
        ).fetchone()
        if row is None:
            raise ValueError("recording_group_unavailable")
        current = _group_record(row)
        if current["status"] in _GROUP_TERMINAL:
            connection.commit()
            return current
        if current["status"] not in allowed_statuses:
            connection.rollback()
            raise ValueError("recording_group_transition_conflict")
        if expected_revision is not None and current["transition_revision"] != expected_revision:
            connection.rollback()
            raise ValueError("recording_group_transition_conflict")
        if (
            expected_recording_id is not None
            and current["current_recording_id"] != expected_recording_id
            and current["last_recording_id"] != expected_recording_id
        ):
            connection.rollback()
            raise ValueError("recording_group_transition_conflict")

        now = _utc_now()
        revision = current["transition_revision"] + 1
        paused_stop = current["status"] == "paused" and target_status == "complete"
        connection.execute(
            """UPDATE recording_groups
                  SET status=?, transition_revision=?, updated_at_utc=?,
                      finished_at_utc=CASE WHEN ? THEN ? ELSE finished_at_utc END,
                      current_recording_id=CASE WHEN ? THEN NULL ELSE current_recording_id END,
                      summary_json=CASE WHEN ? THEN ? ELSE summary_json END
                WHERE group_id=?""",
            (
                target_status,
                revision,
                now,
                int(target_status in _GROUP_TERMINAL),
                now,
                int(paused_stop),
                int(paused_stop),
                _aggregate_group_summary(connection, group_id) if paused_stop else None,
                group_id,
            ),
        )
        if target_status in {"pausing", "stopping"}:
            segment_id = current["current_recording_id"]
            if not segment_id:
                connection.rollback()
                raise ValueError("recording_group_transition_conflict")
            updated_segment = connection.execute(
                """UPDATE recording_jobs SET status='stopping', updated_at_utc=?
                     WHERE recording_id=? AND group_id=?
                       AND status IN ('starting','recording','stopping')""",
                (now, segment_id, group_id),
            ).rowcount
            if updated_segment != 1:
                connection.rollback()
                raise ValueError("recording_group_transition_conflict")
        _insert_group_event(
            connection,
            group_id,
            event_kind,
            now,
            current["current_recording_id"] or current["last_recording_id"],
            revision,
        )
        updated = connection.execute(
            "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
        ).fetchone()
        connection.commit()
    assert updated is not None
    return _group_record(updated)


def get_recording_group(
    database_path: str | Path, group_id: str
) -> dict[str, Any] | None:
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
        ).fetchone()
    return _group_record(row) if row is not None else None


def get_current_recording_group(database_path: str | Path) -> dict[str, Any] | None:
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT * FROM recording_groups
                 ORDER BY CASE WHEN status IN ('starting','recording','pausing','paused','resuming','stopping') THEN 0 ELSE 1 END,
                          updated_at_utc DESC LIMIT 1"""
        ).fetchone()
    return _group_record(row) if row is not None else None


def list_recording_group_segments(
    database_path: str | Path,
    group_id: str,
    *,
    limit: int = MAX_RECORDING_GROUP_SEGMENT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    if not 1 <= limit <= MAX_RECORDING_GROUP_SEGMENT_PAGE:
        raise ValueError("recording_group_segment_limit_invalid")
    if not 0 <= offset <= MAX_RECORDING_GROUP_SEGMENTS:
        raise ValueError("recording_group_segment_offset_invalid")
    with Database(database_path, read_only=True) as db:
        group_row = db.connection.execute(
            "SELECT segment_count FROM recording_groups WHERE group_id=?",
            (group_id,),
        ).fetchone()
        rows = db.connection.execute(
            """SELECT * FROM recording_jobs WHERE group_id=?
                 ORDER BY segment_ordinal LIMIT ? OFFSET ?""",
            (group_id, limit, offset),
        ).fetchall()
    return {
        "items": [_recording_record(row) for row in rows],
        "total_count": int(group_row["segment_count"]) if group_row is not None else 0,
        "limit": limit,
        "offset": offset,
        "has_more": len(rows) == limit
        and offset + len(rows) < (int(group_row["segment_count"]) if group_row else 0),
    }


def list_recording_group_events(
    database_path: str | Path,
    group_id: str,
    *,
    limit: int = MAX_RECORDING_GROUP_EVENTS,
) -> list[dict[str, Any]]:
    if not 1 <= limit <= MAX_RECORDING_GROUP_EVENTS:
        raise ValueError("recording_group_event_limit_invalid")
    with Database(database_path, read_only=True) as db:
        rows = db.connection.execute(
            """SELECT event_ordinal, event_kind, event_at_utc, recording_id,
                      transition_revision, details_json
                 FROM recording_group_events WHERE group_id=?
                 ORDER BY event_ordinal LIMIT ?""",
            (group_id, limit),
        ).fetchall()
    return [
        {
            "event_ordinal": row["event_ordinal"],
            "event_kind": row["event_kind"],
            "event_at_utc": row["event_at_utc"],
            "recording_id": row["recording_id"],
            "transition_revision": row["transition_revision"],
            "details": json.loads(row["details_json"]) if row["details_json"] else None,
        }
        for row in rows
    ]


def mark_recording_segment_started(
    database_path: str | Path, recording_id: str
) -> dict[str, Any] | None:
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        segment = connection.execute(
            "SELECT * FROM recording_jobs WHERE recording_id=?", (recording_id,)
        ).fetchone()
        if segment is None:
            connection.rollback()
            return None
        now = _utc_now()
        connection.execute(
            """UPDATE recording_jobs SET status='recording', updated_at_utc=?,
                      started_at_utc=COALESCE(started_at_utc, ?)
                WHERE recording_id=? AND status IN ('starting','recording')""",
            (now, now, recording_id),
        )
        group_id = segment["group_id"]
        if group_id:
            group = connection.execute(
                "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            if group is not None and group["current_recording_id"] == recording_id:
                revision = int(group["transition_revision"])
                if group["status"] in {"starting", "resuming"}:
                    revision += 1
                    connection.execute(
                        """UPDATE recording_groups
                              SET status='recording', transition_revision=?, updated_at_utc=?,
                                  started_at_utc=COALESCE(started_at_utc, ?)
                            WHERE group_id=?""",
                        (revision, now, now, group_id),
                    )
                _insert_group_event(
                    connection,
                    group_id,
                    "acquisition_started",
                    now,
                    recording_id,
                    revision,
                )
        row = connection.execute(
            "SELECT * FROM recording_jobs WHERE recording_id=?", (recording_id,)
        ).fetchone()
        connection.commit()
    return _recording_record(row) if row is not None else None


def settle_recording_segment_complete(
    database_path: str | Path,
    recording_id: str,
    *,
    summary: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        segment = connection.execute(
            "SELECT * FROM recording_jobs WHERE recording_id=?", (recording_id,)
        ).fetchone()
        if segment is None:
            connection.rollback()
            return None, None
        now = _utc_now()
        encoded_summary = json.dumps(summary, separators=(",", ":"), sort_keys=True)
        connection.execute(
            """UPDATE recording_jobs
                  SET status='complete', updated_at_utc=?, finished_at_utc=?,
                      summary_json=?, failure_reason=NULL
                WHERE recording_id=?""",
            (now, now, encoded_summary, recording_id),
        )
        group_id = segment["group_id"]
        group_record = None
        if group_id:
            group = connection.execute(
                "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            if group is None:
                connection.rollback()
                raise ValueError("recording_group_unavailable")
            status = group["status"]
            revision = int(group["transition_revision"])
            target = None
            event_kind = None
            if status == "pausing":
                target = "paused"
                event_kind = "pause_acknowledged"
            elif status in {"stopping", "starting", "recording", "resuming"}:
                target = "complete"
                event_kind = "complete"
            if target is not None and group["current_recording_id"] == recording_id:
                revision += 1
                terminal = target in _GROUP_TERMINAL
                connection.execute(
                    """UPDATE recording_groups
                          SET status=?, transition_revision=?, updated_at_utc=?,
                              finished_at_utc=CASE WHEN ? THEN ? ELSE finished_at_utc END,
                              current_recording_id=NULL, last_recording_id=?, summary_json=?
                        WHERE group_id=?""",
                    (
                        target,
                        revision,
                        now,
                        int(terminal),
                        now,
                        recording_id,
                        _aggregate_group_summary(connection, group_id),
                        group_id,
                    ),
                )
                _insert_group_event(
                    connection, group_id, event_kind, now, recording_id, revision
                )
            group_row = connection.execute(
                "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            group_record = _group_record(group_row) if group_row is not None else None
        segment_row = connection.execute(
            "SELECT * FROM recording_jobs WHERE recording_id=?", (recording_id,)
        ).fetchone()
        connection.commit()
    return (
        _recording_record(segment_row) if segment_row is not None else None,
        group_record,
    )


def settle_recording_segment_failed(
    database_path: str | Path,
    recording_id: str,
    *,
    summary: dict[str, Any] | None,
    failure_reason: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        segment = connection.execute(
            "SELECT * FROM recording_jobs WHERE recording_id=?", (recording_id,)
        ).fetchone()
        if segment is None:
            connection.rollback()
            return None, None
        now = _utc_now()
        connection.execute(
            """UPDATE recording_jobs SET status='failed', updated_at_utc=?,
                      finished_at_utc=?, summary_json=?, failure_reason=?
                WHERE recording_id=?""",
            (
                now,
                now,
                json.dumps(summary, separators=(",", ":"), sort_keys=True)
                if summary is not None
                else None,
                failure_reason,
                recording_id,
            ),
        )
        group_id = segment["group_id"]
        group_record = None
        if group_id:
            group = connection.execute(
                "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            if group is not None and group["current_recording_id"] == recording_id:
                revision = int(group["transition_revision"]) + 1
                aggregate = _aggregate_group_summary(connection, group_id)
                connection.execute(
                    """UPDATE recording_groups SET status='failed', transition_revision=?,
                              updated_at_utc=?, finished_at_utc=?, current_recording_id=NULL,
                              last_recording_id=?, summary_json=?, failure_reason=?
                        WHERE group_id=?""",
                    (
                        revision,
                        now,
                        now,
                        recording_id,
                        aggregate,
                        failure_reason,
                        group_id,
                    ),
                )
                _insert_group_event(
                    connection,
                    group_id,
                    "failed",
                    now,
                    recording_id,
                    revision,
                    {"failure_reason": failure_reason},
                )
                group_row = connection.execute(
                    "SELECT * FROM recording_groups WHERE group_id=?", (group_id,)
                ).fetchone()
                group_record = _group_record(group_row) if group_row is not None else None
        segment_row = connection.execute(
            "SELECT * FROM recording_jobs WHERE recording_id=?", (recording_id,)
        ).fetchone()
        connection.commit()
    return (
        _recording_record(segment_row) if segment_row is not None else None,
        group_record,
    )


def recover_abandoned_recording_groups(database_path: str | Path) -> None:
    if not Path(database_path).is_file():
        return
    with Database(database_path) as db:
        connection = db.connection
        now = _utc_now()
        with connection:
            active_groups = connection.execute(
                "SELECT * FROM recording_groups WHERE status IN ('starting','recording','pausing','paused','resuming','stopping')"
            ).fetchall()
            for group in active_groups:
                revision = int(group["transition_revision"]) + 1
                current_recording_id = group["current_recording_id"]
                current_segment = (
                    connection.execute(
                        "SELECT status FROM recording_jobs WHERE recording_id=? AND group_id=?",
                        (current_recording_id, group["group_id"]),
                    ).fetchone()
                    if current_recording_id
                    else None
                )
                recovered_stop = (
                    group["status"] == "stopping"
                    and current_segment is not None
                    and current_segment["status"] == "complete"
                )
                target_status = "complete" if recovered_stop else "interrupted"
                reason = None if recovered_stop else "api_restarted_before_recording_completed"
                connection.execute(
                    """UPDATE recording_jobs
                          SET status='interrupted', updated_at_utc=?,
                              finished_at_utc=COALESCE(finished_at_utc, ?),
                              failure_reason='api_restarted_before_recording_completed'
                        WHERE group_id=? AND status IN ('starting','recording','stopping')""",
                    (now, now, group["group_id"]),
                )
                aggregate_summary = _aggregate_group_summary(
                    connection, group["group_id"]
                )
                connection.execute(
                    """UPDATE recording_groups
                          SET status='interrupted', transition_revision=?, updated_at_utc=?,
                              finished_at_utc=?, current_recording_id=NULL,
                              failure_reason=?, summary_json=?
                        WHERE group_id=?""",
                    (revision, now, now, reason, aggregate_summary, group["group_id"]),
                )
                if recovered_stop:
                    connection.execute(
                        "UPDATE recording_groups SET status='complete' WHERE group_id=?",
                        (group["group_id"],),
                    )
                _insert_group_event(
                    connection,
                    group["group_id"],
                    target_status,
                    now,
                    current_recording_id,
                    revision,
                    {"recovered_after_restart": True, **({"failure_reason": reason} if reason else {})},
                )
            connection.execute(
                """UPDATE recording_jobs
                      SET status='interrupted', finished_at_utc=CURRENT_TIMESTAMP,
                          updated_at_utc=?,
                          failure_reason='api_restarted_before_recording_completed'
                    WHERE group_id IS NULL
                      AND status IN ('starting','recording','stopping')""",
                (now,),
            )


def _insert_group_event(
    connection: Any,
    group_id: str,
    event_kind: str,
    event_at_utc: str,
    recording_id: str | None,
    transition_revision: int,
    details: dict[str, Any] | None = None,
) -> None:
    row = connection.execute(
        "SELECT COALESCE(MAX(event_ordinal), -1) + 1 FROM recording_group_events WHERE group_id=?",
        (group_id,),
    ).fetchone()
    connection.execute(
        """INSERT INTO recording_group_events(
               group_id, event_ordinal, event_kind, event_at_utc, recording_id,
               transition_revision, details_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            group_id,
            int(row[0]),
            event_kind,
            event_at_utc,
            recording_id,
            transition_revision,
            json.dumps(details, separators=(",", ":"), sort_keys=True)
            if details is not None
            else None,
        ),
    )


def _aggregate_group_summary(connection: Any, group_id: str) -> str:
    rows = connection.execute(
        "SELECT summary_json FROM recording_jobs WHERE group_id=? AND summary_json IS NOT NULL ORDER BY segment_ordinal",
        (group_id,),
    ).fetchall()
    totals: dict[str, int] = {"segment_count": len(rows)}
    aggregate_keys = (
        "received",
        "recorded",
        "queue_dropped",
        "socket_errors",
        "unpersisted_on_shutdown",
        "elapsed_ms",
    )
    for row in rows:
        try:
            summary = json.loads(row["summary_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(summary, dict):
            continue
        for key in aggregate_keys:
            value = summary.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[key] = totals.get(key, 0) + value
    return json.dumps(totals, separators=(",", ":"), sort_keys=True)


def _group_record(row: Any) -> dict[str, Any]:
    return {
        "group_id": row["group_id"],
        "status": row["status"],
        "bind_host": row["bind_host"],
        "bind_port": row["bind_port"],
        "transition_revision": row["transition_revision"],
        "created_at_utc": row["created_at_utc"],
        "updated_at_utc": row["updated_at_utc"],
        "started_at_utc": row["started_at_utc"],
        "finished_at_utc": row["finished_at_utc"],
        "current_recording_id": row["current_recording_id"],
        "last_recording_id": row["last_recording_id"],
        "segment_count": row["segment_count"],
        "summary": json.loads(row["summary_json"]) if row["summary_json"] else None,
        "failure_reason": row["failure_reason"],
    }


def update_recording_job(
    database_path: str | Path,
    recording_id: str,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    failure_reason: str | None = None,
    starting: bool = False,
    finished: bool = False,
) -> None:
    with Database(database_path) as db:
        with db.connection:
            db.connection.execute(
                """UPDATE recording_jobs
                      SET status=?, updated_at_utc=?,
                          started_at_utc=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE started_at_utc END,
                          finished_at_utc=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE finished_at_utc END,
                          summary_json=?, failure_reason=?
                    WHERE recording_id=?""",
                (
                    status,
                    _utc_now(),
                    int(starting),
                    int(finished),
                    json.dumps(summary, separators=(",", ":"), sort_keys=True)
                    if summary is not None
                    else None,
                    failure_reason,
                    recording_id,
                ),
            )


def get_recording_job(
    database_path: str | Path, recording_id: str
) -> dict[str, Any] | None:
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            "SELECT * FROM recording_jobs WHERE recording_id=?", (recording_id,)
        ).fetchone()
    return _recording_record(row) if row is not None else None


def get_recording_job_paths(
    database_path: str | Path, recording_id: str
) -> dict[str, Any] | None:
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT staging_relative_path, final_relative_path, group_id, segment_ordinal
                 FROM recording_jobs WHERE recording_id=?""",
            (recording_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "staging_relative_path": row["staging_relative_path"],
        "final_relative_path": row["final_relative_path"],
        "group_id": row["group_id"],
        "segment_ordinal": row["segment_ordinal"],
    }


def get_current_recording_job(database_path: str | Path) -> dict[str, Any] | None:
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT * FROM recording_jobs
                 ORDER BY CASE WHEN status IN ('starting','recording','stopping') THEN 0 ELSE 1 END,
                          updated_at_utc DESC LIMIT 1"""
        ).fetchone()
    return _recording_record(row) if row is not None else None


def list_active_recording_job_paths(database_path: str | Path) -> list[dict[str, str]]:
    with Database(database_path, read_only=True) as db:
        rows = db.connection.execute(
            """SELECT recording_id, staging_relative_path, final_relative_path,
                      group_id, segment_ordinal
                 FROM recording_jobs
                WHERE status IN ('starting','recording','stopping')"""
        ).fetchall()
    return [
        {
            "recording_id": row["recording_id"],
            "staging_relative_path": row["staging_relative_path"],
            "final_relative_path": row["final_relative_path"],
            "group_id": row["group_id"],
            "segment_ordinal": row["segment_ordinal"],
        }
        for row in rows
    ]


def recover_abandoned_recordings(database_path: str | Path) -> None:
    if not Path(database_path).is_file():
        return
    with Database(database_path) as db:
        with db.connection:
            db.connection.execute(
                """UPDATE recording_jobs
                      SET status='interrupted', finished_at_utc=CURRENT_TIMESTAMP,
                          updated_at_utc=?,
                          failure_reason='api_restarted_before_recording_completed'
                    WHERE group_id IS NULL
                      AND status IN ('starting','recording','stopping')""",
                (_utc_now(),),
            )


def _recording_record(row: Any) -> dict[str, Any]:
    return {
        "recording_id": row["recording_id"],
        "status": row["status"],
        "bind_host": row["bind_host"],
        "bind_port": row["bind_port"],
        "created_at_utc": row["created_at_utc"],
        "updated_at_utc": row["updated_at_utc"],
        "started_at_utc": row["started_at_utc"],
        "finished_at_utc": row["finished_at_utc"],
        "summary": json.loads(row["summary_json"]) if row["summary_json"] else None,
        "failure_reason": row["failure_reason"],
        "published": row["status"] == "complete",
        "group_id": row["group_id"],
        "segment_ordinal": row["segment_ordinal"],
    }
