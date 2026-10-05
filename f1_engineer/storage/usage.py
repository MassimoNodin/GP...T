"""Bounded, metadata-only measurements of application-managed storage."""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_RECORDING_ROOT_ENTRIES = 10_000
MAX_TRACE_ENTRIES = 100_000
MAX_TRACE_DIRECTORY_DEPTH = 4
_STAGING_NAME = re.compile(r"^\.f1e-recording-[0-9a-f]{32}\.part$")
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def measure_storage_usage(database_path: str | Path, recordings_root: str | Path) -> dict[str, Any]:
    """Measure configured managed paths without opening their contents."""
    database = Path(database_path)
    recordings = Path(recordings_root)
    started = _utc_now()

    database_scope = _measure_database(database)
    finalized_scope, staging_scope = _measure_recordings(recordings)
    trace_scope = _measure_trace_namespace(database.with_name(database.name + ".traces"))
    database_volume = _measure_volume(database.parent)
    recordings_volume = _measure_volume(recordings)

    return {
        "measurement_started_at_utc": started,
        "measurement_completed_at_utc": _utc_now(),
        "measurement_note": "Sizes are logical file sizes measured over an interval; files may change during measurement.",
        "scopes": {
            "database": database_scope,
            "finalized_captures": finalized_scope,
            "recorder_staging": staging_scope,
            "imported_traces": trace_scope,
        },
        "volumes": {
            "database_location": database_volume,
            "recordings_location": recordings_volume,
        },
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _scope(
    *,
    logical_bytes: int | None = None,
    regular_file_count: int | None = None,
    excluded_entry_count: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if reason is not None:
        return {
            "status": "unavailable",
            "logical_bytes": None,
            "regular_file_count": None,
            "excluded_entry_count": None,
            "reason": reason,
        }
    return {
        "status": "available",
        "logical_bytes": logical_bytes,
        "regular_file_count": regular_file_count,
        "excluded_entry_count": excluded_entry_count,
        "reason": None,
    }


def _volume(*, total_bytes: int | None = None, free_bytes: int | None = None, reason: str | None = None) -> dict[str, Any]:
    if reason is not None:
        return {"status": "unavailable", "total_bytes": None, "free_bytes": None, "reason": reason}
    return {"status": "available", "total_bytes": total_bytes, "free_bytes": free_bytes, "reason": None}


def _is_reparse_point(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
    )


def _within_root(root: Path, candidate: Path) -> bool:
    root_abs = os.path.normcase(os.path.abspath(os.fspath(root)))
    candidate_abs = os.path.normcase(os.path.abspath(os.fspath(candidate)))
    try:
        return os.path.commonpath((root_abs, candidate_abs)) == root_abs
    except ValueError:
        return False


def _directory_identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _resolved_within_root(resolved_root: Path, candidate: Path) -> bool:
    try:
        resolved_candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return _within_root(resolved_root, resolved_candidate)


def _error_reason(error: OSError, *, missing_reason: str) -> str:
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, FileNotFoundError) or error.errno == errno.ENOENT:
        return missing_reason
    return "filesystem_error"


def _measure_database(database: Path) -> dict[str, Any]:
    root = database.parent
    try:
        root_info = os.stat(root, follow_symlinks=False)
        if _is_reparse_point(root_info) or not stat.S_ISDIR(root_info.st_mode):
            return _scope(reason="database_location_unavailable")
        if not _within_root(root, database):
            return _scope(reason="path_outside_configured_root")
        database_info = os.stat(database, follow_symlinks=False)
    except OSError as error:
        return _scope(reason=_error_reason(error, missing_reason="database_unavailable"))

    if _is_reparse_point(database_info) or not stat.S_ISREG(database_info.st_mode):
        return _scope(reason="database_not_regular_file")

    total = database_info.st_size
    count = 1
    excluded = 0
    for suffix in _SQLITE_SIDECARS:
        sidecar = database.with_name(database.name + suffix)
        try:
            info = os.stat(sidecar, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as error:
            return _scope(reason=_error_reason(error, missing_reason="file_disappeared"))
        if _is_reparse_point(info) or not stat.S_ISREG(info.st_mode):
            excluded += 1
            continue
        if not _within_root(root, sidecar):
            return _scope(reason="path_outside_configured_root")
        total += info.st_size
        count += 1
    return _scope(logical_bytes=total, regular_file_count=count, excluded_entry_count=excluded)


def _checked_directory(path: Path, *, missing_reason: str) -> str | None:
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError as error:
        return _error_reason(error, missing_reason=missing_reason)
    if _is_reparse_point(info) or not stat.S_ISDIR(info.st_mode):
        return "directory_unavailable"
    return None


def _measure_recordings(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root_reason = _checked_directory(root, missing_reason="recordings_root_missing")
    if root_reason is not None:
        return _scope(reason=root_reason), _scope(reason=root_reason)

    totals = {"finalized": 0, "staging": 0}
    counts = {"finalized": 0, "staging": 0}
    excluded = {"finalized": 0, "staging": 0}
    inspected = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                inspected += 1
                if inspected > MAX_RECORDING_ROOT_ENTRIES:
                    reason = "entry_limit_exceeded"
                    return _scope(reason=reason), _scope(reason=reason)
                if not _within_root(root, Path(entry.path)):
                    reason = "path_outside_configured_root"
                    return _scope(reason=reason), _scope(reason=reason)
                is_finalized = entry.name.lower().endswith(".f1ecap")
                is_staging = _STAGING_NAME.fullmatch(entry.name) is not None
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as error:
                    reason = _error_reason(error, missing_reason="file_disappeared")
                    return _scope(reason=reason), _scope(reason=reason)

                regular_file = not _is_reparse_point(info) and stat.S_ISREG(info.st_mode)
                if is_finalized and regular_file:
                    totals["finalized"] += info.st_size
                    counts["finalized"] += 1
                else:
                    excluded["finalized"] += 1
                if is_staging and regular_file:
                    totals["staging"] += info.st_size
                    counts["staging"] += 1
                else:
                    excluded["staging"] += 1
    except OSError as error:
        reason = _error_reason(error, missing_reason="recordings_root_missing")
        return _scope(reason=reason), _scope(reason=reason)

    return (
        _scope(
            logical_bytes=totals["finalized"],
            regular_file_count=counts["finalized"],
            excluded_entry_count=excluded["finalized"],
        ),
        _scope(
            logical_bytes=totals["staging"],
            regular_file_count=counts["staging"],
            excluded_entry_count=excluded["staging"],
        ),
    )


def _measure_trace_namespace(root: Path) -> dict[str, Any]:
    try:
        root_info = os.stat(root, follow_symlinks=False)
    except FileNotFoundError:
        return _scope(logical_bytes=0, regular_file_count=0, excluded_entry_count=0)
    except OSError as error:
        return _scope(reason=_error_reason(error, missing_reason="trace_namespace_missing"))
    if _is_reparse_point(root_info) or not stat.S_ISDIR(root_info.st_mode):
        return _scope(reason="trace_namespace_unavailable")
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return _scope(reason="trace_namespace_unavailable")

    total = 0
    files = 0
    excluded = 0
    inspected = 0
    pending = [(root, 0, _directory_identity(root_info))]
    try:
        while pending:
            directory, depth, expected_identity = pending.pop()
            try:
                current_info = os.stat(directory, follow_symlinks=False)
            except OSError as error:
                return _scope(reason=_error_reason(error, missing_reason="file_disappeared"))
            if (
                _is_reparse_point(current_info)
                or not stat.S_ISDIR(current_info.st_mode)
                or _directory_identity(current_info) != expected_identity
            ):
                return _scope(reason="directory_changed")
            if not _resolved_within_root(resolved_root, directory):
                return _scope(reason="path_outside_configured_root")
            with os.scandir(directory) as entries:
                for entry in entries:
                    inspected += 1
                    if inspected > MAX_TRACE_ENTRIES:
                        return _scope(reason="entry_limit_exceeded")
                    candidate = Path(entry.path)
                    if not _within_root(root, candidate):
                        return _scope(reason="path_outside_configured_root")
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError as error:
                        return _scope(
                            reason=_error_reason(error, missing_reason="file_disappeared")
                        )
                    if _is_reparse_point(info):
                        excluded += 1
                    elif stat.S_ISREG(info.st_mode):
                        if not _resolved_within_root(resolved_root, candidate):
                            return _scope(reason="path_outside_configured_root")
                        total += info.st_size
                        files += 1
                    elif stat.S_ISDIR(info.st_mode):
                        if depth >= MAX_TRACE_DIRECTORY_DEPTH:
                            return _scope(reason="directory_depth_limit_exceeded")
                        try:
                            directory_info = os.stat(candidate, follow_symlinks=False)
                        except OSError as error:
                            return _scope(
                                reason=_error_reason(
                                    error, missing_reason="file_disappeared"
                                )
                            )
                        if (
                            _is_reparse_point(directory_info)
                            or not stat.S_ISDIR(directory_info.st_mode)
                            or (
                                info.st_ino != 0
                                and _directory_identity(info)
                                != _directory_identity(directory_info)
                            )
                        ):
                            return _scope(reason="directory_changed")
                        if not _resolved_within_root(resolved_root, candidate):
                            return _scope(reason="path_outside_configured_root")
                        pending.append(
                            (
                                candidate,
                                depth + 1,
                                _directory_identity(directory_info),
                            )
                        )
                    else:
                        excluded += 1
    except OSError as error:
        return _scope(reason=_error_reason(error, missing_reason="file_disappeared"))
    return _scope(logical_bytes=total, regular_file_count=files, excluded_entry_count=excluded)


def _measure_volume(location: Path) -> dict[str, Any]:
    reason = _checked_directory(location, missing_reason="volume_location_missing")
    if reason is not None:
        return _volume(reason=reason)
    try:
        usage = shutil.disk_usage(location)
    except (OSError, NotImplementedError):
        return _volume(reason="volume_unavailable")
    return _volume(total_bytes=usage.total, free_bytes=usage.free)
