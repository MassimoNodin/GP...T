from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import Database


_ACTIVE = ("starting", "recording", "stopping")


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
) -> dict[str, str] | None:
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT staging_relative_path, final_relative_path
                 FROM recording_jobs WHERE recording_id=?""",
            (recording_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "staging_relative_path": row["staging_relative_path"],
        "final_relative_path": row["final_relative_path"],
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
            """SELECT recording_id, staging_relative_path, final_relative_path
                 FROM recording_jobs
                WHERE status IN ('starting','recording','stopping')"""
        ).fetchall()
    return [
        {
            "recording_id": row["recording_id"],
            "staging_relative_path": row["staging_relative_path"],
            "final_relative_path": row["final_relative_path"],
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
                    WHERE status IN ('starting','recording','stopping')""",
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
    }
