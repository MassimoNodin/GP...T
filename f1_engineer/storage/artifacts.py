"""Bounded, registration-backed inventories of one processing run's artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import Database


MAX_RUN_ARTIFACT_PAGE_SIZE = 50
MAX_RUN_ARTIFACT_PAGE_OFFSET = 100_000
MAX_RUN_ARTIFACTS = 100_000
MAX_RUN_ARTIFACT_IDENTITY_BYTES = 512
MAX_RUN_ARTIFACT_PATH_BYTES = 4 * 1024
MAX_RUN_ARTIFACT_PAGE_BYTES = 128 * 1024
MAX_RUN_ARTIFACT_PATH_COMPONENTS = 8
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991

_RUN_ID = re.compile(r"^[a-f0-9]{64}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SESSION_UID = re.compile(r"^(?:0|[1-9][0-9]{0,19})$")
_RUN_STATUSES = {"processing", "complete", "failed"}
_ARTIFACT_KINDS = {"all", "player_trace", "car_observation_chunk"}
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class RunArtifactInventoryUnavailable(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def list_processing_run_artifacts(
    database_path: str | Path,
    run_id: str,
    *,
    kind: str = "all",
    limit: int = MAX_RUN_ARTIFACT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any] | None:
    """Return one bounded metadata-only page for the exact registered run."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise RunArtifactInventoryUnavailable("processing_run_id_invalid")
    if not isinstance(kind, str) or kind not in _ARTIFACT_KINDS:
        raise RunArtifactInventoryUnavailable("processing_run_artifact_kind_invalid")
    if type(limit) is not int or not 1 <= limit <= MAX_RUN_ARTIFACT_PAGE_SIZE:
        raise RunArtifactInventoryUnavailable("processing_run_artifact_limit_invalid")
    if type(offset) is not int or not 0 <= offset <= MAX_RUN_ARTIFACT_PAGE_OFFSET:
        raise RunArtifactInventoryUnavailable("processing_run_artifact_offset_invalid")

    database = Path(database_path).resolve(strict=False)
    run_artifacts = _run_artifact_cte()
    with Database(database, read_only=True) as db:
        connection = db.connection
        connection.text_factory = _decode_sqlite_text_safely
        connection.execute("BEGIN")
        metadata_snapshot_at = _utc_now()
        run = connection.execute(
            """SELECT r.run_id,
                      CASE WHEN LENGTH(CAST(r.capture_sha256 AS BLOB)) <= ?
                           THEN r.capture_sha256 END AS capture_sha256,
                      LENGTH(CAST(r.capture_sha256 AS BLOB)) AS capture_sha256_bytes,
                      CASE WHEN LENGTH(CAST(r.status AS BLOB)) <= ?
                           THEN r.status END AS processing_status,
                      LENGTH(CAST(r.status AS BLOB)) AS processing_status_bytes
                 FROM processing_runs r WHERE r.run_id=?""",
            (MAX_RUN_ARTIFACT_IDENTITY_BYTES, MAX_RUN_ARTIFACT_IDENTITY_BYTES, run_id),
        ).fetchone()
        if run is None:
            return None

        preflight = connection.execute(
            f"""{run_artifacts}
                SELECT 1 FROM run_artifacts LIMIT ?""",
            (run_id, run_id, MAX_RUN_ARTIFACTS + 1),
        ).fetchall()
        if len(preflight) > MAX_RUN_ARTIFACTS:
            raise RunArtifactInventoryUnavailable(
                "processing_run_artifact_inventory_limit_exceeded"
            )

        count_row = connection.execute(
            f"""{run_artifacts}
                SELECT COUNT(*) AS total FROM run_artifacts
                 WHERE (? = 'all' OR artifact_kind = ?)""",
            (run_id, run_id, kind, kind),
        ).fetchone()
        if count_row is None or type(count_row["total"]) is not int:
            raise RunArtifactInventoryUnavailable(
                "processing_run_artifact_inventory_unavailable"
            )
        total = int(count_row["total"])
        rows = connection.execute(
            f"""{run_artifacts}
                SELECT artifact_kind, registration_rowid, artifact_key,
                       artifact_key_bytes, session_uid, session_uid_bytes,
                       attempt_number, car_index, packet_format, lifecycle_epoch,
                       chunk_ordinal, schema_version, row_count, stored_sha256,
                       stored_sha256_bytes, ready, relative_path,
                       relative_path_bytes
                  FROM run_artifacts
                 WHERE (? = 'all' OR artifact_kind = ?)
                 ORDER BY kind_order,
                          CASE WHEN LENGTH(CAST(primary_key AS BLOB)) <= {MAX_RUN_ARTIFACT_IDENTITY_BYTES}
                               THEN primary_key END,
                          registration_rowid
                 LIMIT ? OFFSET ?""",
            (
                run_id,
                run_id,
                kind,
                kind,
                limit,
                offset,
            ),
        ).fetchall()

        run_reasons: list[str] = []
        capture_sha256 = _checked_sha256(
            run["capture_sha256"], run["capture_sha256_bytes"]
        )
        if capture_sha256 is None:
            run_reasons.append("capture_sha256_invalid")
        processing_status = run["processing_status"]
        if (
            not isinstance(processing_status, str)
            or type(run["processing_status_bytes"]) is not int
            or run["processing_status_bytes"] > MAX_RUN_ARTIFACT_IDENTITY_BYTES
            or processing_status not in _RUN_STATUSES
        ):
            processing_status = None
            run_reasons.append("processing_status_invalid")

    result = {
        "run_id": run_id,
        "capture_sha256": capture_sha256,
        "processing_status": processing_status,
        "run_metadata_reasons": run_reasons,
        "query": {"kind": kind, "limit": limit, "offset": offset},
        "total": total,
        "items": [
            _artifact_record(database, run_id, row)
            for row in rows
        ],
        "has_more": offset + len(rows) < total,
        "metadata_snapshot_at_utc": metadata_snapshot_at,
        "filesystem_observed_at_utc": _utc_now(),
    }
    if len(json.dumps(result, separators=(",", ":"), ensure_ascii=True).encode("utf-8")) > MAX_RUN_ARTIFACT_PAGE_BYTES:
        raise RunArtifactInventoryUnavailable(
            "processing_run_artifact_page_size_limit_exceeded"
        )
    return result


def _run_artifact_cte() -> str:
    return f"""WITH run_artifacts AS (
        SELECT 'player_trace' AS artifact_kind, 0 AS kind_order,
               t.attempt_key AS primary_key, t.rowid AS registration_rowid,
               CASE WHEN LENGTH(CAST(t.attempt_key AS BLOB)) <= {MAX_RUN_ARTIFACT_IDENTITY_BYTES}
                    THEN t.attempt_key END AS artifact_key,
               LENGTH(CAST(t.attempt_key AS BLOB)) AS artifact_key_bytes,
               CASE WHEN LENGTH(CAST(s.session_uid AS BLOB)) <= {MAX_RUN_ARTIFACT_IDENTITY_BYTES}
                    THEN s.session_uid END AS session_uid,
               LENGTH(CAST(s.session_uid AS BLOB)) AS session_uid_bytes,
               CASE WHEN typeof(l.attempt_number) = 'integer'
                    THEN l.attempt_number END AS attempt_number,
               CASE WHEN typeof(l.car_index) = 'integer'
                    THEN l.car_index END AS car_index,
               NULL AS packet_format, NULL AS lifecycle_epoch, NULL AS chunk_ordinal,
               CASE WHEN typeof(t.schema_version) = 'integer'
                    THEN t.schema_version END AS schema_version,
               CASE WHEN typeof(t.row_count) = 'integer'
                    THEN t.row_count END AS row_count,
               CASE WHEN LENGTH(CAST(t.sha256 AS BLOB)) <= {MAX_RUN_ARTIFACT_IDENTITY_BYTES}
                    THEN t.sha256 END AS stored_sha256,
               LENGTH(CAST(t.sha256 AS BLOB)) AS stored_sha256_bytes,
               CASE WHEN typeof(t.ready) = 'integer' THEN t.ready END AS ready,
               CASE WHEN LENGTH(CAST(t.relative_path AS BLOB)) <= {MAX_RUN_ARTIFACT_PATH_BYTES}
                    THEN t.relative_path END AS relative_path,
               LENGTH(CAST(t.relative_path AS BLOB)) AS relative_path_bytes
          FROM telemetry_files t
          JOIN lap_attempts l USING(attempt_key)
          JOIN sessions s USING(session_key)
         WHERE s.run_id = ?
        UNION ALL
        SELECT 'car_observation_chunk' AS artifact_kind, 1 AS kind_order,
               c.chunk_key AS primary_key, c.rowid AS registration_rowid,
               CASE WHEN LENGTH(CAST(c.chunk_key AS BLOB)) <= {MAX_RUN_ARTIFACT_IDENTITY_BYTES}
                    THEN c.chunk_key END AS artifact_key,
               LENGTH(CAST(c.chunk_key AS BLOB)) AS artifact_key_bytes,
               CASE WHEN LENGTH(CAST(s.session_uid AS BLOB)) <= {MAX_RUN_ARTIFACT_IDENTITY_BYTES}
                    THEN s.session_uid END AS session_uid,
               LENGTH(CAST(s.session_uid AS BLOB)) AS session_uid_bytes,
               NULL AS attempt_number, NULL AS car_index,
               CASE WHEN typeof(c.packet_format) = 'integer'
                    THEN c.packet_format END AS packet_format,
               CASE WHEN typeof(c.lifecycle_epoch) = 'integer'
                    THEN c.lifecycle_epoch END AS lifecycle_epoch,
               CASE WHEN typeof(c.chunk_ordinal) = 'integer'
                    THEN c.chunk_ordinal END AS chunk_ordinal,
               CASE WHEN typeof(c.schema_version) = 'integer'
                    THEN c.schema_version END AS schema_version,
               CASE WHEN typeof(c.row_count) = 'integer'
                    THEN c.row_count END AS row_count,
               CASE WHEN LENGTH(CAST(c.sha256 AS BLOB)) <= {MAX_RUN_ARTIFACT_IDENTITY_BYTES}
                    THEN c.sha256 END AS stored_sha256,
               LENGTH(CAST(c.sha256 AS BLOB)) AS stored_sha256_bytes,
               CASE WHEN typeof(c.ready) = 'integer' THEN c.ready END AS ready,
               CASE WHEN LENGTH(CAST(c.relative_path AS BLOB)) <= {MAX_RUN_ARTIFACT_PATH_BYTES}
                    THEN c.relative_path END AS relative_path,
               LENGTH(CAST(c.relative_path AS BLOB)) AS relative_path_bytes
          FROM car_observation_chunks c
          JOIN sessions s USING(session_key)
         WHERE s.run_id = ?
    )"""


def _artifact_record(database: Path, run_id: str, row: Any) -> dict[str, Any]:
    kind = row["artifact_kind"]
    registered_key = row["artifact_key"]
    registered_key_bytes = row["artifact_key_bytes"]
    metadata_reasons: list[str] = []
    registered_key_utf8_bytes = _utf8_byte_length(registered_key)
    if (
        isinstance(registered_key, str)
        and type(registered_key_bytes) is int
        and registered_key_bytes == registered_key_utf8_bytes
        and registered_key_bytes <= MAX_RUN_ARTIFACT_IDENTITY_BYTES
    ):
        artifact_id_material = (
            b"f1e-artifact-v1\0" + kind.encode("ascii") + b"\0" + registered_key.encode("utf-8")
        )
    else:
        artifact_id_material = (
            b"f1e-invalid-artifact-v1\0"
            + kind.encode("ascii")
            + b"\0"
            + str(row["registration_rowid"]).encode("ascii")
        )
        metadata_reasons.append("artifact_identity_invalid")
        registered_key = None
    artifact_id = hashlib.sha256(artifact_id_material).hexdigest()

    session_uid = _checked_session_uid(row["session_uid"], row["session_uid_bytes"])
    if session_uid is None:
        metadata_reasons.append("session_uid_invalid")
    attempt_number = _checked_integer(row["attempt_number"], minimum=1)
    car_index = _checked_integer(row["car_index"], minimum=0, maximum=23)
    packet_format = _checked_integer(row["packet_format"], minimum=1, maximum=65_535)
    lifecycle_epoch = _checked_integer(row["lifecycle_epoch"], minimum=0)
    chunk_ordinal = _checked_integer(row["chunk_ordinal"], minimum=0)
    if kind == "player_trace":
        if attempt_number is None:
            metadata_reasons.append("attempt_number_invalid")
        if car_index is None:
            metadata_reasons.append("car_index_invalid")
    else:
        if packet_format is None:
            metadata_reasons.append("packet_format_invalid")
        if lifecycle_epoch is None:
            metadata_reasons.append("lifecycle_epoch_invalid")
        if chunk_ordinal is None:
            metadata_reasons.append("chunk_ordinal_invalid")

    schema_version = _checked_integer(row["schema_version"], minimum=1, maximum=65_535)
    if schema_version is None:
        metadata_reasons.append("schema_version_invalid")
    row_count = _checked_integer(row["row_count"], minimum=0)
    if row_count is None:
        metadata_reasons.append("row_count_invalid")
    stored_sha256 = _checked_sha256(row["stored_sha256"], row["stored_sha256_bytes"])
    if stored_sha256 is None:
        metadata_reasons.append("stored_sha256_invalid")
    ready = row["ready"]
    if type(ready) is int and ready == 1:
        registration_readiness = "ready"
    elif type(ready) is int and ready == 0:
        registration_readiness = "not_ready"
    else:
        registration_readiness = "unknown"
        metadata_reasons.append("registration_readiness_invalid")

    relative_path = row["relative_path"]
    path_bytes = row["relative_path_bytes"]
    relative_path_utf8_bytes = _utf8_byte_length(relative_path)
    if (
        not isinstance(relative_path, str)
        or type(path_bytes) is not int
        or path_bytes != relative_path_utf8_bytes
        or path_bytes > MAX_RUN_ARTIFACT_PATH_BYTES
    ):
        relative_path = None
        metadata_reasons.append("registered_path_invalid")
    if kind == "player_trace" and registered_key is not None:
        attempt_key: str | None = registered_key
    else:
        attempt_key = None

    availability, filesystem_reason, observed_size = _observe_artifact_file(
        database, run_id, relative_path
    )
    if filesystem_reason == "registered_path_invalid" and "registered_path_invalid" not in metadata_reasons:
        metadata_reasons.append("registered_path_invalid")

    return {
        "artifact_id": artifact_id,
        "artifact_kind": kind,
        "run_id": run_id,
        "session_uid": session_uid,
        "attempt_key": attempt_key,
        "attempt_number": attempt_number if kind == "player_trace" else None,
        "car_index": car_index if kind == "player_trace" else None,
        "packet_format": packet_format if kind == "car_observation_chunk" else None,
        "lifecycle_epoch": lifecycle_epoch if kind == "car_observation_chunk" else None,
        "chunk_ordinal": chunk_ordinal if kind == "car_observation_chunk" else None,
        "schema_version": schema_version,
        "row_count": row_count,
        "stored_sha256": stored_sha256,
        "registration_readiness": registration_readiness,
        "filesystem_availability": availability,
        "filesystem_reason": filesystem_reason,
        "observed_size_bytes": observed_size,
        "checksum_verification": "not_performed",
        "metadata_reasons": metadata_reasons,
    }


def _checked_sha256(value: object, byte_count: object) -> str | None:
    utf8_bytes = _utf8_byte_length(value)
    if (
        not isinstance(value, str)
        or type(byte_count) is not int
        or byte_count != utf8_bytes
        or byte_count > MAX_RUN_ARTIFACT_IDENTITY_BYTES
        or not _SHA256.fullmatch(value)
    ):
        return None
    return value


def _checked_session_uid(value: object, byte_count: object) -> str | None:
    utf8_bytes = _utf8_byte_length(value)
    if (
        not isinstance(value, str)
        or type(byte_count) is not int
        or byte_count != utf8_bytes
        or byte_count > MAX_RUN_ARTIFACT_IDENTITY_BYTES
        or not _SESSION_UID.fullmatch(value)
    ):
        return None
    try:
        return value if int(value) <= 18_446_744_073_709_551_615 else None
    except ValueError:
        return None


def _utf8_byte_length(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        return None


def _decode_sqlite_text_safely(value: bytes) -> str | bytes:
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return value


def _checked_integer(
    value: object, *, minimum: int, maximum: int = 2_147_483_647
) -> int | None:
    if type(value) is not int or not minimum <= value <= maximum:
        return None
    return value


def _observe_artifact_file(
    database: Path, run_id: str, relative_path: str | None
) -> tuple[str, str | None, int | None]:
    if relative_path is None:
        return "unavailable", "registered_path_invalid", None
    parts = relative_path.split("/")
    namespace_name = f"{database.name}.traces"
    if (
        len(parts) < 3
        or len(parts) > MAX_RUN_ARTIFACT_PATH_COMPONENTS
        or parts[0] != namespace_name
        or parts[1] != run_id
        or any(not _safe_component(part) for part in parts)
        or not parts[-1].lower().endswith(".parquet")
    ):
        return "unavailable", "registered_path_invalid", None

    namespace = database.parent / namespace_name
    run_root = namespace / run_id
    try:
        namespace_info = os.stat(namespace, follow_symlinks=False)
    except FileNotFoundError:
        return "unavailable", "trace_namespace_missing", None
    except PermissionError:
        return "unavailable", "permission_denied", None
    except OSError:
        return "unavailable", "filesystem_error", None
    if _is_reparse_point(namespace_info) or not stat.S_ISDIR(namespace_info.st_mode):
        return "unavailable", "trace_namespace_unavailable", None
    try:
        resolved_namespace = namespace.resolve(strict=True)
    except (OSError, RuntimeError):
        return "unavailable", "trace_namespace_unavailable", None
    if not _same_path(namespace, resolved_namespace):
        return "unavailable", "trace_namespace_outside_database_root", None

    try:
        run_info = os.stat(run_root, follow_symlinks=False)
    except FileNotFoundError:
        return "unavailable", "run_directory_missing", None
    except PermissionError:
        return "unavailable", "permission_denied", None
    except OSError:
        return "unavailable", "filesystem_error", None
    if _is_reparse_point(run_info) or not stat.S_ISDIR(run_info.st_mode):
        return "unavailable", "run_directory_unavailable", None
    try:
        resolved_run_root = run_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return "unavailable", "run_directory_unavailable", None
    if not _within_root(resolved_namespace, resolved_run_root):
        return "unavailable", "run_directory_outside_namespace", None

    current = run_root
    remaining = parts[2:]
    for index, component in enumerate(remaining):
        current = current / component
        final_component = index == len(remaining) - 1
        try:
            info = os.stat(current, follow_symlinks=False)
        except FileNotFoundError:
            return (
                ("missing", "artifact_file_missing", None)
                if final_component
                else ("unavailable", "artifact_directory_missing", None)
            )
        except PermissionError:
            return "unavailable", "permission_denied", None
        except OSError:
            return "unavailable", "filesystem_error", None
        if _is_reparse_point(info):
            return "unavailable", "artifact_path_reparse_point", None
        if final_component:
            if not stat.S_ISREG(info.st_mode):
                return "unavailable", "artifact_not_regular_file", None
        elif not stat.S_ISDIR(info.st_mode):
            return "unavailable", "artifact_parent_not_directory", None

    try:
        resolved_file = current.resolve(strict=True)
    except (OSError, RuntimeError):
        return "unavailable", "filesystem_error", None
    if not _within_root(resolved_run_root, resolved_file):
        return "unavailable", "artifact_path_outside_run", None
    try:
        final_info = os.stat(current, follow_symlinks=False)
    except FileNotFoundError:
        return "missing", "artifact_file_missing", None
    except PermissionError:
        return "unavailable", "permission_denied", None
    except OSError:
        return "unavailable", "filesystem_error", None
    if _is_reparse_point(final_info):
        return "unavailable", "artifact_path_reparse_point", None
    if not stat.S_ISREG(final_info.st_mode):
        return "unavailable", "artifact_not_regular_file", None
    observed_size = max(0, int(final_info.st_size))
    if observed_size > MAX_SAFE_JSON_INTEGER:
        return "present", "file_size_out_of_range", None
    return "present", None, observed_size


def _safe_component(value: str) -> bool:
    if (
        not value
        or value in {".", ".."}
        or value[-1] in {".", " "}
        or ":" in value
        or "\\" in value
        or any(ord(char) < 32 or char in '<>"|?*' for char in value)
    ):
        return False
    stem = value.split(".", 1)[0].upper()
    return stem not in _WINDOWS_RESERVED_NAMES


def _is_reparse_point(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
    )


def _within_root(root: Path, candidate: Path) -> bool:
    try:
        normalized_root = os.path.normcase(os.path.abspath(os.fspath(root)))
        normalized_candidate = os.path.normcase(os.path.abspath(os.fspath(candidate)))
        return os.path.commonpath(
            (normalized_root, normalized_candidate)
        ) == normalized_root
    except ValueError:
        return False


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(
            os.path.abspath(os.fspath(right))
        )
    except (OSError, ValueError):
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
