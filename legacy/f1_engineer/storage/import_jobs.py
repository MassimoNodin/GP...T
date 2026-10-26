from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import Database, recording_source_metadata_version


MAX_RECORDING_CATALOG_DIRECTORY_ENTRIES = 4_096
MAX_RECORDING_CATALOG_REGISTERED_SOURCES = 10_000
MAX_RECORDING_CATALOG_PAGE_SIZE = 50
MAX_RECORDING_CATALOG_OFFSET = 100_000
MAX_RECORDING_CATALOG_QUERY_LENGTH = 128
MAX_RECORDING_CATALOG_RESPONSE_BYTES = 131_072
MAX_RECORDING_CATALOG_RESULT_JSON_LENGTH = 16_384
_RECORDING_CATALOG_STATUSES = {
    "all",
    "none",
    "queued",
    "running",
    "complete",
    "failed",
    "interrupted",
    "cancelled",
}
_RECORDING_CATALOG_AVAILABILITY = {"all", "available", "missing"}


class RecordingCatalogUnavailable(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _root_namespace(root: Path) -> str:
    stable_path = _absolute_recordings_root(root)
    return hashlib.sha256(str(stable_path).encode("utf-8")).hexdigest()


def _absolute_recordings_root(root: str | Path) -> Path:
    """Normalize a configured path without following later symlink/reparse swaps."""
    expanded = Path(root).expanduser()
    return Path(os.path.abspath(expanded))


def _open_recordings_root(root: Path) -> tuple[int | None, os.stat_result]:
    """Open or inspect every root component without accepting reparse points."""
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if os.name == "nt":
        if root.drive.startswith("\\\\"):
            raise RecordingCatalogUnavailable("recording_catalog_root_unavailable")
        current = Path(root.anchor)
        root_stat = current.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or getattr(root_stat, "st_file_attributes", 0) & reparse_attribute
        ):
            raise RecordingCatalogUnavailable("recording_catalog_root_unavailable")
        for component in root.parts[1:]:
            current /= component
            root_stat = current.stat(follow_symlinks=False)
            if (
                not stat.S_ISDIR(root_stat.st_mode)
                or getattr(root_stat, "st_file_attributes", 0) & reparse_attribute
            ):
                raise RecordingCatalogUnavailable("recording_catalog_root_unavailable")
        return None, root_stat

    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    if any(not hasattr(os, name) for name in required) or not root.is_absolute():
        raise RecordingCatalogUnavailable("recording_catalog_root_unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(root.anchor, flags)
    try:
        for component in root.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        root_stat = os.fstat(descriptor)
        if not stat.S_ISDIR(root_stat.st_mode) or not root_stat.st_ino:
            raise RecordingCatalogUnavailable("recording_catalog_root_unavailable")
        return descriptor, root_stat
    except Exception:
        os.close(descriptor)
        raise


def recording_root_namespace(root: Path) -> str:
    """Return the stable namespace used by registrations for a configured root."""
    return _root_namespace(root)


def register_recording_source(
    database_path: str | Path,
    recordings_root: str | Path,
    relative_path: str,
    *,
    byte_size: int,
    modified_ns: int,
    expected_root_identity: tuple[int, int] | None = None,
    expected_file_identity: tuple[int, int] | None = None,
) -> str:
    """Register one newly published direct-child capture and return its identity."""
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or len(relative_path) > 512
        or Path(relative_path).name != relative_path
        or "/" in relative_path
        or "\\" in relative_path
        or Path(relative_path).suffix.lower() != ".f1ecap"
        or isinstance(byte_size, bool)
        or not isinstance(byte_size, int)
        or byte_size < 0
        or isinstance(modified_ns, bool)
        or not isinstance(modified_ns, int)
        or modified_ns < 0
    ):
        raise RecordingCatalogUnavailable("recording_catalog_registration_invalid")
    root = _absolute_recordings_root(recordings_root)

    def verify_published_identity() -> None:
        root_fd: int | None = None
        try:
            root_fd, root_stat = _open_recordings_root(root)
            if root_fd is None:
                candidate = root / relative_path
                file_stat = candidate.stat(follow_symlinks=False)
            else:
                file_stat = os.stat(relative_path, dir_fd=root_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(root_stat.st_mode)
                or getattr(root_stat, "st_file_attributes", 0) & getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
                )
                or (
                    expected_root_identity is not None
                    and (root_stat.st_dev, root_stat.st_ino) != expected_root_identity
                )
                or not stat.S_ISREG(file_stat.st_mode)
                or getattr(file_stat, "st_file_attributes", 0) & getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
                )
                or file_stat.st_size != byte_size
                or file_stat.st_mtime_ns != modified_ns
                or (
                    expected_file_identity is not None
                    and (file_stat.st_dev, file_stat.st_ino) != expected_file_identity
                )
            ):
                raise RecordingCatalogUnavailable(
                    "recording_catalog_registration_invalid"
                )
        except OSError as exc:
            raise RecordingCatalogUnavailable(
                "recording_catalog_registration_invalid"
            ) from exc
        finally:
            if root_fd is not None:
                os.close(root_fd)

    if (expected_root_identity is None) != (expected_file_identity is None):
        raise RecordingCatalogUnavailable("recording_catalog_registration_invalid")
    verify_published_identity()

    namespace = _root_namespace(root)
    try:
        with Database(database_path) as db:
            connection = db.connection
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT capture_id FROM recording_sources
                     WHERE root_namespace=? AND relative_path=?""",
                (namespace, relative_path),
            ).fetchone()
            if existing is not None:
                capture_id = existing["capture_id"]
                if (
                    not isinstance(capture_id, str)
                    or re.fullmatch(r"[a-f0-9]{32}", capture_id) is None
                ):
                    connection.rollback()
                    raise RecordingCatalogUnavailable(
                        "recording_catalog_registration_invalid"
                    )
                verify_published_identity()
                connection.commit()
                return capture_id

            registered_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM recording_sources WHERE root_namespace=?",
                    (namespace,),
                ).fetchone()[0]
            )
            if registered_count >= MAX_RECORDING_CATALOG_REGISTERED_SOURCES:
                connection.rollback()
                raise RecordingCatalogUnavailable(
                    "recording_catalog_registered_source_limit"
                )
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
                    relative_path,
                    byte_size,
                    modified_ns,
                ),
            )
            verify_published_identity()
            connection.commit()
            return capture_id
    except RecordingCatalogUnavailable:
        raise
    except Exception as exc:
        raise RecordingCatalogUnavailable(
            "recording_catalog_registration_failed"
        ) from exc


def recording_download_version(
    namespace: str,
    capture_id: str,
    relative_path: str,
    byte_size: int,
    modified_ns: int,
) -> str:
    """Return a stable metadata identity for one observed recording file."""
    return recording_source_metadata_version(
        namespace, capture_id, relative_path, byte_size, modified_ns
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def list_recording_sources(
    database_path: str | Path, recordings_root: str | Path
) -> list[dict[str, Any]]:
    root = _absolute_recordings_root(recordings_root)
    namespace = _root_namespace(root)
    discovered = _bounded_recording_source_discovery(root)

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


def list_recording_sources_page(
    database_path: str | Path,
    recordings_root: str | Path,
    *,
    limit: int = 25,
    offset: int = 0,
    query: str = "",
    latest_job_status: str = "all",
    availability: str = "all",
    selected_capture_id: str | None = None,
) -> dict[str, Any]:
    """Discover and return one bounded recording-catalog page."""
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_RECORDING_CATALOG_PAGE_SIZE
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= MAX_RECORDING_CATALOG_OFFSET
        or not isinstance(query, str)
        or len(query) > MAX_RECORDING_CATALOG_QUERY_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in query)
        or not isinstance(latest_job_status, str)
        or latest_job_status not in _RECORDING_CATALOG_STATUSES
        or not isinstance(availability, str)
        or availability not in _RECORDING_CATALOG_AVAILABILITY
        or (
            selected_capture_id is not None
            and (
                not isinstance(selected_capture_id, str)
                or re.fullmatch(r"[a-f0-9]{32}", selected_capture_id) is None
            )
        )
    ):
        raise RecordingCatalogUnavailable("recording_catalog_query_invalid")

    root = _absolute_recordings_root(recordings_root)
    discovered = _bounded_recording_source_discovery(root)
    discovered_paths = {relative_path for relative_path, *_ in discovered}
    namespace = _root_namespace(root)

    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM recording_sources WHERE root_namespace=?",
                    (namespace,),
                ).fetchone()[0]
            )
            if existing_count > MAX_RECORDING_CATALOG_REGISTERED_SOURCES:
                raise RecordingCatalogUnavailable(
                    "recording_catalog_registered_source_limit"
                )
            invalid_registration = connection.execute(
                """SELECT 1 FROM recording_sources
                    WHERE root_namespace=? AND (
                        typeof(capture_id) != 'text' OR length(capture_id) != 32
                        OR capture_id GLOB '*[^0123456789abcdef]*'
                        OR typeof(relative_path) != 'text'
                        OR length(relative_path) NOT BETWEEN 1 AND 4096
                        OR length(CAST(relative_path AS BLOB)) > 16384
                        OR typeof(display_name) != 'text'
                        OR length(display_name) NOT BETWEEN 1 AND 512
                        OR length(CAST(display_name AS BLOB)) > 2048
                        OR typeof(byte_size) != 'integer' OR byte_size < 0
                        OR typeof(modified_ns) != 'integer' OR modified_ns < 0
                    ) LIMIT 1""",
                (namespace,),
            ).fetchone()
            if invalid_registration is not None:
                raise RecordingCatalogUnavailable(
                    "recording_catalog_registration_invalid"
                )
            registered_paths = {
                row[0]
                for row in connection.execute(
                    """SELECT relative_path FROM recording_sources
                        WHERE root_namespace=?""",
                    (namespace,),
                ).fetchall()
            }
            new_paths = discovered_paths - registered_paths
            if (
                existing_count + len(new_paths)
                > MAX_RECORDING_CATALOG_REGISTERED_SOURCES
            ):
                raise RecordingCatalogUnavailable(
                    "recording_catalog_registered_source_limit"
                )

            connection.execute(
                "CREATE TEMP TABLE recording_catalog_discovered_paths "
                "(relative_path TEXT PRIMARY KEY)"
            )
            connection.executemany(
                "INSERT INTO recording_catalog_discovered_paths(relative_path) VALUES (?)",
                ((relative_path,) for relative_path in sorted(discovered_paths)),
            )
            connection.executemany(
                """INSERT INTO recording_sources(
                          capture_id, root_namespace, relative_path, display_name,
                          byte_size, modified_ns)
                     VALUES (?, ?, ?, ?, ?, ?)
                     ON CONFLICT(root_namespace, relative_path) DO UPDATE SET
                         display_name=excluded.display_name,
                         byte_size=excluded.byte_size,
                         modified_ns=excluded.modified_ns,
                         last_seen_at_utc=CURRENT_TIMESTAMP""",
                (
                    (
                        uuid.uuid4().hex,
                        namespace,
                        relative_path,
                        display_name,
                        byte_size,
                        modified_ns,
                    )
                    for relative_path, display_name, modified_ns, byte_size in discovered
                ),
            )

            clauses = ["s.root_namespace=?"]
            parameters: list[object] = [namespace]
            if query:
                clauses.append(
                    "(instr(lower(s.display_name), lower(?)) > 0 "
                    "OR instr(s.capture_id, lower(?)) > 0)"
                )
                parameters.extend((query, query))
            if latest_job_status == "none":
                clauses.append("latest_job.job_id IS NULL")
            elif latest_job_status != "all":
                clauses.append("latest_job.status=?")
                parameters.append(latest_job_status)
            if availability == "available":
                clauses.append(
                    "EXISTS (SELECT 1 FROM recording_catalog_discovered_paths d "
                    "WHERE d.relative_path=s.relative_path)"
                )
            elif availability == "missing":
                clauses.append(
                    "NOT EXISTS (SELECT 1 FROM recording_catalog_discovered_paths d "
                    "WHERE d.relative_path=s.relative_path)"
                )

            join = """LEFT JOIN import_jobs latest_job
                   ON latest_job.job_id=(
                        SELECT j.job_id FROM import_jobs j
                         WHERE j.capture_id=s.capture_id
                         ORDER BY j.updated_at_utc DESC, j.rowid DESC LIMIT 1
                   )"""
            where = " AND ".join(clauses)
            total_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM recording_sources s {join} WHERE {where}",
                    parameters,
                ).fetchone()[0]
            )
            base_select = f"""SELECT s.capture_id, s.display_name, s.byte_size,
                                      s.modified_ns, s.relative_path,
                                      CASE
                                          WHEN typeof(latest_job.job_id)='text'
                                           AND length(latest_job.job_id)=32
                                           AND latest_job.job_id NOT GLOB '*[^0123456789abcdef]*'
                                          THEN latest_job.job_id
                                          ELSE NULL
                                      END AS latest_job_id,
                                      CASE
                                          WHEN typeof(latest_job.status)='text'
                                           AND latest_job.status IN (
                                               'queued', 'running', 'complete',
                                               'failed', 'interrupted', 'cancelled')
                                          THEN latest_job.status
                                          ELSE NULL
                                      END AS latest_job_status,
                                      CASE
                                          WHEN typeof(latest_job.result_json)='text'
                                           AND length(CAST(latest_job.result_json AS BLOB)) <= ?
                                          THEN latest_job.result_json
                                          ELSE NULL
                                      END AS latest_job_result
                                 FROM recording_sources s {join} WHERE """
            page_parameters = (
                MAX_RECORDING_CATALOG_RESULT_JSON_LENGTH,
                *parameters,
                limit,
                offset,
            )
            page_rows = connection.execute(
                base_select
                + where
                + " ORDER BY s.display_name COLLATE NOCASE, "
                "s.display_name COLLATE BINARY, s.capture_id LIMIT ? OFFSET ?",
                page_parameters,
            ).fetchall()
            selected_row = None
            if selected_capture_id is not None:
                selected_row = connection.execute(
                    base_select + "s.root_namespace=? AND s.capture_id=?",
                    (MAX_RECORDING_CATALOG_RESULT_JSON_LENGTH, namespace, selected_capture_id),
                ).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    items = [
        _recording_catalog_source(row, discovered_paths, namespace)
        for row in page_rows
    ]
    selected_source = (
        _recording_catalog_source(selected_row, discovered_paths, namespace)
        if selected_row is not None
        else None
    )
    result = {
        "items": items,
        "selected_capture": selected_source,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < total_count,
        "filters": {
            "query": query,
            "latest_job_status": latest_job_status,
            "availability": availability,
            "selected_capture_id": selected_capture_id,
        },
    }
    if len(json.dumps(result, separators=(",", ":"), ensure_ascii=True).encode("utf-8")) > MAX_RECORDING_CATALOG_RESPONSE_BYTES:
        raise RecordingCatalogUnavailable("recording_catalog_response_limit")
    return result


def _bounded_recording_source_discovery(
    root: Path,
) -> list[tuple[str, str, int, int]]:
    try:
        root = _absolute_recordings_root(root)
        root_fd, _root_stat_value = _open_recordings_root(root)
        discovered: list[tuple[str, str, int, int]] = []
        entry_count = 0
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        scan_target: int | Path = root_fd if root_fd is not None else root
        try:
            with os.scandir(scan_target) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_RECORDING_CATALOG_DIRECTORY_ENTRIES:
                        raise RecordingCatalogUnavailable(
                            "recording_catalog_directory_entry_limit"
                        )
                    if Path(entry.name).suffix.lower() != ".f1ecap":
                        continue
                    entry_stat = (
                        os.stat(entry.name, dir_fd=root_fd, follow_symlinks=False)
                        if root_fd is not None
                        else entry.stat(follow_symlinks=False)
                    )
                    if (
                        stat.S_ISLNK(entry_stat.st_mode)
                        or getattr(entry_stat, "st_file_attributes", 0) & reparse_attribute
                        or not stat.S_ISREG(entry_stat.st_mode)
                    ):
                        continue
                    if root_fd is not None:
                        file_stat = entry_stat
                        relative_path = entry.name
                    else:
                        candidate = Path(entry.path)
                        resolved = candidate.resolve(strict=True)
                        if not resolved.is_relative_to(root):
                            continue
                        file_stat = resolved.stat()
                        if not stat.S_ISREG(file_stat.st_mode):
                            continue
                        relative_path = resolved.relative_to(root).as_posix()
                    if (
                        not relative_path
                        or len(relative_path) > 4_096
                        or not entry.name
                        or len(entry.name) > 512
                        or file_stat.st_size < 0
                        or file_stat.st_mtime_ns < 0
                    ):
                        raise RecordingCatalogUnavailable(
                            "recording_catalog_registration_invalid"
                        )
                    discovered.append(
                        (
                            relative_path,
                            entry.name,
                            file_stat.st_mtime_ns,
                            file_stat.st_size,
                        )
                    )
        finally:
            if root_fd is not None:
                os.close(root_fd)
        return sorted(discovered, key=lambda item: (item[1].casefold(), item[1], item[0]))
    except RecordingCatalogUnavailable:
        raise
    except OSError as exc:
        raise RecordingCatalogUnavailable(
            "recording_catalog_discovery_failed"
        ) from exc


def _recording_catalog_source(
    row: Any, discovered_paths: set[str], namespace: str
) -> dict[str, Any]:
    capture_id = row["capture_id"]
    display_name = row["display_name"]
    relative_path = row["relative_path"]
    byte_size = row["byte_size"]
    modified_ns = row["modified_ns"]
    if (
        not isinstance(capture_id, str)
        or re.fullmatch(r"[a-f0-9]{32}", capture_id) is None
        or not isinstance(display_name, str)
        or not display_name
        or len(display_name) > 512
        or not isinstance(relative_path, str)
        or not relative_path
        or len(relative_path) > 4_096
        or isinstance(byte_size, bool)
        or not isinstance(byte_size, int)
        or byte_size < 0
        or isinstance(modified_ns, bool)
        or not isinstance(modified_ns, int)
        or modified_ns < 0
    ):
        raise RecordingCatalogUnavailable("recording_catalog_registration_invalid")
    latest_job_id = row["latest_job_id"]
    latest_job_status = row["latest_job_status"]
    if (
        not isinstance(latest_job_id, str)
        or re.fullmatch(r"[a-f0-9]{32}", latest_job_id) is None
        or latest_job_status
        not in {"queued", "running", "complete", "failed", "interrupted", "cancelled"}
    ):
        latest_job_id = None
        latest_job_status = None
    latest_job_run_id = _bounded_completed_job_run_id(
        latest_job_status, row["latest_job_result"]
    )
    try:
        modified_at_utc = datetime.fromtimestamp(
            modified_ns / 1_000_000_000, timezone.utc
        ).isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise RecordingCatalogUnavailable(
            "recording_catalog_registration_invalid"
        ) from exc
    return {
        "capture_id": capture_id,
        "display_name": display_name,
        "byte_size": byte_size,
        "modified_at_utc": modified_at_utc,
        "latest_job_id": latest_job_id,
        "latest_job_status": latest_job_status,
        "latest_job_run_id": latest_job_run_id,
        "available": relative_path in discovered_paths,
        "download_version": recording_download_version(
            namespace, capture_id, relative_path, byte_size, modified_ns
        ),
    }


def _bounded_completed_job_run_id(status: object, result_json: object) -> str | None:
    if (
        status != "complete"
        or not isinstance(result_json, str)
    ):
        return None
    try:
        if len(result_json.encode("utf-8")) > MAX_RECORDING_CATALOG_RESULT_JSON_LENGTH:
            return None
    except UnicodeEncodeError:
        return None
    try:
        result = json.loads(result_json)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    if not isinstance(result, dict):
        return None
    run_id = result.get("run_id")
    return (
        run_id
        if isinstance(run_id, str)
        and len(run_id) == 64
        and all(character in "0123456789abcdef" for character in run_id)
        else None
    )


def resolve_recording_source(
    database_path: str | Path,
    recordings_root: str | Path,
    capture_id: str,
) -> Path | None:
    root = _absolute_recordings_root(recordings_root)
    root_fd: int | None = None
    try:
        root_fd, _root_stat = _open_recordings_root(root)
        if root_fd is not None:
            os.close(root_fd)
            root_fd = None
        with Database(database_path, read_only=True) as db:
            row = db.connection.execute(
                """SELECT relative_path FROM recording_sources
                     WHERE capture_id=? AND root_namespace=?""",
                (capture_id, _root_namespace(root)),
            ).fetchone()
    except (FileNotFoundError, ValueError):
        return None
    except RecordingCatalogUnavailable:
        return None
    finally:
        if root_fd is not None:
            os.close(root_fd)
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


def recording_source_pin(
    database_path: str | Path,
    recordings_root: str | Path,
    capture_id: str,
) -> dict[str, Any] | None:
    """Read and verify the selected catalog metadata before accepting a job."""
    root = _absolute_recordings_root(recordings_root)
    namespace = _root_namespace(root)
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT root_namespace, relative_path, byte_size, modified_ns
                 FROM recording_sources
                WHERE capture_id=? AND root_namespace=?""",
            (capture_id, namespace),
        ).fetchone()
    if row is None:
        return None
    source = resolve_recording_source(database_path, root, capture_id)
    if source is None:
        return None
    try:
        source_stat = source.stat()
    except OSError:
        return None
    if (
        source_stat.st_size != row["byte_size"]
        or source_stat.st_mtime_ns != row["modified_ns"]
    ):
        raise ValueError("capture_source_changed_refresh_catalog")
    return {
        "root_namespace": namespace,
        "metadata_version": recording_source_metadata_version(
            namespace,
            capture_id,
            row["relative_path"],
            row["byte_size"],
            row["modified_ns"],
        ),
        "relative_path": row["relative_path"],
        "byte_size": row["byte_size"],
        "modified_ns": row["modified_ns"],
    }


def resolve_pinned_recording_source(
    database_path: str | Path,
    recordings_root: str | Path,
    capture_id: str,
    source_root_namespace: str | None,
    source_metadata_version: str | None,
) -> Path | None:
    """Resolve a job only while both its catalog pin and actual file still match."""
    root = _absolute_recordings_root(recordings_root)
    namespace = _root_namespace(root)
    if (
        source_root_namespace != namespace
        or not isinstance(source_metadata_version, str)
        or re.fullmatch(r"[a-f0-9]{64}", source_metadata_version) is None
    ):
        return None
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT root_namespace, relative_path, byte_size, modified_ns
                 FROM recording_sources
                WHERE capture_id=? AND root_namespace=?""",
            (capture_id, namespace),
        ).fetchone()
    if row is None:
        return None
    current_version = recording_source_metadata_version(
        namespace,
        capture_id,
        row["relative_path"],
        row["byte_size"],
        row["modified_ns"],
    )
    if current_version != source_metadata_version:
        return None
    source = resolve_recording_source(database_path, root, capture_id)
    if source is None:
        return None
    try:
        source_stat = source.stat()
    except OSError:
        return None
    if (
        source_stat.st_size != row["byte_size"]
        or source_stat.st_mtime_ns != row["modified_ns"]
    ):
        return None
    return source


MAX_IMPORT_QUEUE_WAITING = 16
MAX_IMPORT_QUEUE_RESPONSE_BYTES = 65_536


def enqueue_import_job(
    database_path: str | Path,
    capture_id: str,
    source_root_namespace: str,
    source_metadata_version: str,
) -> tuple[dict[str, Any], bool]:
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        source = connection.execute(
            """SELECT capture_id, relative_path, byte_size, modified_ns FROM recording_sources
                 WHERE capture_id=? AND root_namespace=?""",
            (capture_id, source_root_namespace),
        ).fetchone()
        if source is None:
            raise ValueError("capture_id_unavailable")
        expected_version = recording_source_metadata_version(
            source_root_namespace,
            capture_id,
            source["relative_path"],
            source["byte_size"],
            source["modified_ns"],
        )
        if expected_version != source_metadata_version:
            raise ValueError("capture_source_changed_refresh_catalog")
        active = connection.execute(
            """SELECT * FROM import_jobs
                 WHERE capture_id=? AND status IN ('queued', 'running')
                 ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,
                          queue_order LIMIT 1""",
            (capture_id,),
        ).fetchone()
        if active is not None:
            connection.commit()
            return _job_record(active), False
        waiting_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM import_jobs WHERE status='queued'"
            ).fetchone()[0]
        )
        if waiting_count > MAX_IMPORT_QUEUE_WAITING:
            connection.rollback()
            raise ValueError("import_queue_capacity_inconsistent")
        if waiting_count >= MAX_IMPORT_QUEUE_WAITING:
            connection.rollback()
            raise ValueError("import_queue_full")
        queue_order = int(
            connection.execute(
                "SELECT COALESCE(MAX(queue_order), 0) + 1 FROM import_jobs"
            ).fetchone()[0]
        )
        job_id = uuid.uuid4().hex
        connection.execute(
            """INSERT INTO import_jobs(
                   job_id, capture_id, status, phase, updated_at_utc,
                   queue_order, source_root_namespace, source_metadata_version)
                 VALUES (?, ?, 'queued', 'queued', ?, ?, ?, ?)""",
            (
                job_id,
                capture_id,
                _utc_now(),
                queue_order,
                source_root_namespace,
                source_metadata_version,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM import_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
    assert row is not None
    return _job_record(row), True


def retry_import_job(
    database_path: str | Path,
    job_id: str,
    source_root_namespace: str | None = None,
    source_metadata_version: str | None = None,
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
        source = connection.execute(
            """SELECT root_namespace, relative_path, byte_size, modified_ns
                 FROM recording_sources WHERE capture_id=?
                   AND (? IS NULL OR root_namespace=?)""",
            (row["capture_id"], source_root_namespace, source_root_namespace),
        ).fetchone()
        if source is None:
            raise ValueError("capture_id_unavailable")
        source_root_namespace = source["root_namespace"]
        if source_metadata_version is None:
            source_metadata_version = recording_source_metadata_version(
                source_root_namespace,
                row["capture_id"],
                source["relative_path"],
                source["byte_size"],
                source["modified_ns"],
            )
        elif source_metadata_version != recording_source_metadata_version(
            source_root_namespace,
            row["capture_id"],
            source["relative_path"],
            source["byte_size"],
            source["modified_ns"],
        ):
            raise ValueError("capture_source_changed_refresh_catalog")
        active = connection.execute(
            """SELECT 1 FROM import_jobs WHERE capture_id=?
                 AND status IN ('queued', 'running') LIMIT 1""",
            (row["capture_id"],),
        ).fetchone()
        if active is not None:
            raise ValueError("import_capture_already_active")
        waiting_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM import_jobs WHERE status='queued'"
            ).fetchone()[0]
        )
        if waiting_count >= MAX_IMPORT_QUEUE_WAITING:
            raise ValueError("import_queue_full")
        queue_order = int(
            connection.execute(
                "SELECT COALESCE(MAX(queue_order), 0) + 1 FROM import_jobs"
            ).fetchone()[0]
        )
        connection.execute(
            """UPDATE import_jobs
                  SET status='queued', phase='queued', attempt_count=attempt_count+1,
                      started_at_utc=NULL, finished_at_utc=NULL,
                      result_json=NULL, failure_reason=NULL, updated_at_utc=?,
                      queue_order=?, source_root_namespace=?, source_metadata_version=?
                WHERE job_id=?""",
            (
                _utc_now(),
                queue_order,
                source_root_namespace,
                source_metadata_version,
                job_id,
            ),
        )
        connection.commit()
        retried = connection.execute(
            "SELECT * FROM import_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
    assert retried is not None
    return _job_record(retried)


def create_import_job(
    database_path: str | Path, capture_id: str
) -> tuple[dict[str, Any], bool]:
    """Compatibility helper for storage callers that already selected a capture."""
    with Database(database_path, read_only=True) as db:
        source = db.connection.execute(
            """SELECT root_namespace, relative_path, byte_size, modified_ns
                 FROM recording_sources WHERE capture_id=?""",
            (capture_id,),
        ).fetchone()
    if source is None:
        raise ValueError("capture_id_unavailable")
    version = recording_source_metadata_version(
        source["root_namespace"],
        capture_id,
        source["relative_path"],
        source["byte_size"],
        source["modified_ns"],
    )
    return enqueue_import_job(
        database_path, capture_id, source["root_namespace"], version
    )


def active_import_job_for_capture(
    database_path: str | Path, capture_id: str
) -> dict[str, Any] | None:
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT * FROM import_jobs WHERE capture_id=?
                 AND status IN ('queued', 'running')
                 ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,
                          queue_order LIMIT 1""",
            (capture_id,),
        ).fetchone()
    return _job_record(row) if row is not None else None


def has_queued_import_jobs(database_path: str | Path) -> bool:
    with Database(database_path, read_only=True) as db:
        return db.connection.execute(
            "SELECT 1 FROM import_jobs WHERE status='queued' LIMIT 1"
        ).fetchone() is not None


def claim_oldest_import_job(database_path: str | Path) -> dict[str, Any] | None:
    """Atomically make the oldest durable waiter the single running import."""
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT * FROM import_jobs WHERE status='queued'
                 ORDER BY queue_order ASC, created_at_utc ASC, rowid ASC LIMIT 1"""
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute(
            """UPDATE import_jobs
                  SET status='running', phase='starting',
                      started_at_utc=CURRENT_TIMESTAMP, updated_at_utc=?
                WHERE job_id=? AND status='queued'""",
            (_utc_now(), row["job_id"]),
        )
        claimed = connection.execute(
            "SELECT * FROM import_jobs WHERE job_id=?", (row["job_id"],)
        ).fetchone()
        connection.commit()
    return _job_record(claimed) if claimed is not None else None


def cancel_queued_import_job(
    database_path: str | Path, job_id: str
) -> tuple[dict[str, Any] | None, bool]:
    """Cancel only if the job is still waiting; claim and cancel serialize in SQLite."""
    with Database(database_path) as db:
        connection = db.connection
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM import_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None:
            connection.commit()
            return None, False
        cancelled = False
        if row["status"] == "queued":
            cursor = connection.execute(
                """UPDATE import_jobs
                      SET status='cancelled', phase='cancelled',
                          finished_at_utc=CURRENT_TIMESTAMP, updated_at_utc=?,
                          failure_reason=NULL
                    WHERE job_id=? AND status='queued'""",
                (_utc_now(), job_id),
            )
            cancelled = cursor.rowcount == 1
        result = connection.execute(
            "SELECT * FROM import_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        connection.commit()
    return (_job_record(result) if result is not None else None), cancelled


def import_queue_snapshot(
    database_path: str | Path, blocking_reservation: str | None
) -> dict[str, Any]:
    """Return a result-only-free snapshot bounded before it reaches the API."""
    projected_columns = """CASE WHEN typeof(job_id)='text' AND length(job_id)=32
            AND job_id NOT GLOB '*[^0-9a-f]*' THEN job_id END AS job_id,
        CASE WHEN typeof(capture_id)='text' AND length(capture_id)=32
            AND capture_id NOT GLOB '*[^0-9a-f]*' THEN capture_id END AS capture_id,
        status,
        CASE WHEN typeof(phase)='text'
            AND length(CAST(phase AS BLOB)) BETWEEN 1 AND 64
            AND phase NOT GLOB '*[^a-zA-Z0-9_]*' THEN phase END AS phase,
        CASE WHEN typeof(attempt_count)='integer'
            AND attempt_count BETWEEN 1 AND 1000000 THEN attempt_count END AS attempt_count,
        CASE WHEN typeof(created_at_utc)='text'
            AND length(CAST(created_at_utc AS BLOB)) BETWEEN 1 AND 40
            THEN created_at_utc END AS created_at_utc,
        CASE WHEN typeof(updated_at_utc)='text'
            AND length(CAST(updated_at_utc AS BLOB)) BETWEEN 1 AND 40
            THEN updated_at_utc END AS updated_at_utc"""
    with Database(database_path, read_only=True) as db:
        db.connection.execute("BEGIN")
        running_rows = db.connection.execute(
            f"SELECT {projected_columns} FROM import_jobs "
            "WHERE status='running' LIMIT 2"
        ).fetchall()
        waiting = db.connection.execute(
            f"SELECT {projected_columns} FROM import_jobs WHERE status='queued' "
            "ORDER BY queue_order ASC, created_at_utc ASC, rowid ASC LIMIT ?",
            (MAX_IMPORT_QUEUE_WAITING + 1,),
        ).fetchall()
        waiting_count = int(
            db.connection.execute(
                "SELECT COUNT(*) FROM import_jobs WHERE status='queued'"
            ).fetchone()[0]
        )
        db.connection.commit()

    if waiting_count > MAX_IMPORT_QUEUE_WAITING:
        raise ValueError("import_queue_capacity_inconsistent")
    if len(running_rows) > 1 or len(waiting) != waiting_count:
        raise ValueError("import_queue_snapshot_unavailable")

    def compact(row: Any, position: int | None) -> dict[str, Any]:
        item = {
            "job_id": row["job_id"],
            "capture_id": row["capture_id"],
            "status": row["status"],
            "phase": row["phase"],
            "attempt_count": row["attempt_count"],
            "created_at_utc": row["created_at_utc"],
            "updated_at_utc": row["updated_at_utc"],
            "queue_position": position,
        }
        if any(value is None for key, value in item.items() if key != "queue_position"):
            raise ValueError("import_queue_snapshot_unavailable")
        return item

    result = {
        "waiting_count": waiting_count,
        "running_job": compact(running_rows[0], None) if running_rows else None,
        "waiting_jobs": [compact(row, index) for index, row in enumerate(waiting[:16], 1)],
        "blocking_reservation": blocking_reservation or "idle",
    }
    if len(json.dumps(result, separators=(",", ":"), ensure_ascii=True).encode("utf-8")) > MAX_IMPORT_QUEUE_RESPONSE_BYTES:
        raise ValueError("import_queue_response_limit")
    return result


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
                    WHERE status='running'"""
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
        "source_root_namespace": row["source_root_namespace"],
        "source_metadata_version": row["source_metadata_version"],
    }
