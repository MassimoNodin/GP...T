from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import Database


def _root_namespace(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def list_recording_sources(
    database_path: str | Path, recordings_root: str | Path
) -> list[dict[str, Any]]:
    root = Path(recordings_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    namespace = _root_namespace(root)
    discovered: list[tuple[str, str, int, int]] = []
    for candidate in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        if candidate.suffix.lower() != ".f1ecap" or candidate.is_symlink():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
            stat = resolved.stat()
        except OSError:
            continue
        relative_path = resolved.relative_to(root).as_posix()
        discovered.append(
            (relative_path, candidate.name, stat.st_mtime_ns, stat.st_size)
        )

    with Database(database_path) as db:
        connection = db.connection
        with connection:
            for relative_path, display_name, modified_ns, byte_size in discovered:
                row = connection.execute(
                    """SELECT capture_id FROM recording_sources
                         WHERE root_namespace=? AND relative_path=?""",
                    (namespace, relative_path),
                ).fetchone()
                if row is None:
                    capture_id = uuid.uuid4().hex
                    connection.execute(
                        """INSERT INTO recording_sources(
                                  capture_id, root_namespace, relative_path, display_name,
                                  byte_size, modified_ns)
                             VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            capture_id,
                            namespace,
                            relative_path,
                            display_name,
                            byte_size,
                            modified_ns,
                        ),
                    )
                else:
                    connection.execute(
                        """UPDATE recording_sources
                              SET display_name=?, byte_size=?, modified_ns=?,
                                  last_seen_at_utc=CURRENT_TIMESTAMP
                            WHERE capture_id=?""",
                        (display_name, byte_size, modified_ns, row["capture_id"]),
                    )
            rows = connection.execute(
                """SELECT s.capture_id, s.display_name, s.byte_size, s.modified_ns,
                          latest_job.job_id AS latest_job_id,
                          latest_job.status AS latest_job_status,
                          latest_job.result_json AS latest_job_result,
                          s.relative_path
                     FROM recording_sources s
                LEFT JOIN import_jobs latest_job
                       ON latest_job.job_id=(
                            SELECT j.job_id FROM import_jobs j
                             WHERE j.capture_id=s.capture_id
                             ORDER BY j.updated_at_utc DESC, j.rowid DESC LIMIT 1
                       )
                    WHERE s.root_namespace=?
                    ORDER BY s.display_name COLLATE NOCASE""",
                (namespace,),
            ).fetchall()
    discovered_paths = {relative_path for relative_path, *_ in discovered}
    return [
        {
            "capture_id": row["capture_id"],
            "display_name": row["display_name"],
            "byte_size": row["byte_size"],
            "modified_at_utc": datetime.fromtimestamp(
                row["modified_ns"] / 1_000_000_000, timezone.utc
            ).isoformat(),
            "latest_job_id": row["latest_job_id"],
            "latest_job_status": row["latest_job_status"],
            "latest_job_run_id": _completed_job_run_id(
                row["latest_job_status"], row["latest_job_result"]
            ),
            "available": row["relative_path"] in discovered_paths,
        }
        for row in rows
        if row["relative_path"] in discovered_paths or row["latest_job_id"] is not None
    ]


def _completed_job_run_id(status: object, result_json: object) -> str | None:
    if status != "complete" or not isinstance(result_json, str):
        return None
    try:
        result = json.loads(result_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict):
        return None
    run_id = result.get("run_id")
    if (
        isinstance(run_id, str)
        and len(run_id) == 64
        and all(character in "0123456789abcdef" for character in run_id)
    ):
        return run_id
    return None


def resolve_recording_source(
    database_path: str | Path,
    recordings_root: str | Path,
    capture_id: str,
) -> Path | None:
    root = Path(recordings_root).expanduser().resolve()
    try:
        with Database(database_path, read_only=True) as db:
            row = db.connection.execute(
                """SELECT relative_path FROM recording_sources
                     WHERE capture_id=? AND root_namespace=?""",
                (capture_id, _root_namespace(root)),
            ).fetchone()
    except (FileNotFoundError, ValueError):
        return None
    if row is None:
        return None
    relative_path = Path(row["relative_path"])
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    try:
        candidate = root / relative_path
        if candidate.is_symlink():
            return None
        source = candidate.resolve(strict=True)
    except OSError:
        return None
    if not source.is_relative_to(root) or not source.is_file():
        return None
    if source.suffix.lower() != ".f1ecap":
        return None
    return source


def create_import_job(
    database_path: str | Path, capture_id: str
) -> tuple[dict[str, Any], bool]:
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        source = connection.execute(
            "SELECT capture_id FROM recording_sources WHERE capture_id=?",
            (capture_id,),
        ).fetchone()
        if source is None:
            raise ValueError("capture_id_unavailable")
        active = connection.execute(
            """SELECT * FROM import_jobs
                 WHERE status IN ('queued', 'running')
                 ORDER BY created_at_utc DESC LIMIT 1"""
        ).fetchone()
        if active is not None:
            connection.commit()
            if active["capture_id"] != capture_id:
                raise ValueError("another_import_is_in_progress")
            return _job_record(active), False
        job_id = uuid.uuid4().hex
        connection.execute(
            """INSERT INTO import_jobs(job_id, capture_id, status, phase, updated_at_utc)
                 VALUES (?, ?, 'queued', 'queued', ?)""",
            (job_id, capture_id, _utc_now()),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM import_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
    assert row is not None
    return _job_record(row), True


def retry_import_job(
    database_path: str | Path, job_id: str
) -> dict[str, Any]:
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM import_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None:
            raise ValueError("import_job_unavailable")
        if row["status"] not in {"failed", "interrupted"}:
            raise ValueError("import_job_not_retryable")
        active = connection.execute(
            "SELECT 1 FROM import_jobs WHERE status IN ('queued', 'running') LIMIT 1"
        ).fetchone()
        if active is not None:
            raise ValueError("another_import_is_in_progress")
        connection.execute(
            """UPDATE import_jobs
                  SET status='queued', phase='queued', attempt_count=attempt_count+1,
                      started_at_utc=NULL, finished_at_utc=NULL,
                      result_json=NULL, failure_reason=NULL, updated_at_utc=?
                WHERE job_id=?""",
            (_utc_now(), job_id),
        )
        connection.commit()
        retried = connection.execute(
            "SELECT * FROM import_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
    assert retried is not None
    return _job_record(retried)


def update_import_job(
    database_path: str | Path,
    job_id: str,
    *,
    status: str,
    phase: str,
    result: dict[str, Any] | None = None,
    failure_reason: str | None = None,
    starting: bool = False,
    finished: bool = False,
) -> None:
    with Database(database_path) as db:
        with db.connection:
            db.connection.execute(
                """UPDATE import_jobs
                      SET status=?, phase=?, updated_at_utc=?,
                          started_at_utc=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE started_at_utc END,
                          finished_at_utc=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE finished_at_utc END,
                          result_json=?, failure_reason=?
                    WHERE job_id=?""",
                (
                    status,
                    phase,
                    _utc_now(),
                    int(starting),
                    int(finished),
                    json.dumps(result, separators=(",", ":"), sort_keys=True)
                    if result is not None
                    else None,
                    failure_reason,
                    job_id,
                ),
            )


def get_import_job(database_path: str | Path, job_id: str) -> dict[str, Any] | None:
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            "SELECT * FROM import_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
    return _job_record(row) if row is not None else None


def recover_abandoned_import_jobs(database_path: str | Path) -> None:
    if not Path(database_path).is_file():
        return
    with Database(database_path) as db:
        with db.connection:
            db.connection.execute(
                """UPDATE import_jobs
                      SET status='interrupted', phase='interrupted',
                          finished_at_utc=CURRENT_TIMESTAMP,
                          updated_at_utc=?,
                          failure_reason='api_restarted_before_import_completed'
                    WHERE status IN ('queued', 'running')"""
                ,
                (_utc_now(),)
            )


def _job_record(row: Any) -> dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "capture_id": row["capture_id"],
        "status": row["status"],
        "phase": row["phase"],
        "attempt_count": row["attempt_count"],
        "created_at_utc": row["created_at_utc"],
        "updated_at_utc": row["updated_at_utc"],
        "started_at_utc": row["started_at_utc"],
        "finished_at_utc": row["finished_at_utc"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "failure_reason": row["failure_reason"],
    }
