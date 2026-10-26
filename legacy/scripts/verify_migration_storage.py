"""Bounded real-filesystem SQLite ENOSPC acceptance check."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid


MARKER_NAME = ".f1-migration-disposable-target"
MARKER_CONTENT = "F1-ENGINEER-MIGRATION-DISPOSABLE-TARGET-V1\n"
MAX_CAPACITY_BYTES = 1024 * 1024 * 1024
FILL_CHUNK_BYTES = 1024 * 1024


class SafetyError(ValueError):
    pass


def validate_target(target: Path, *, isolated: bool, max_bytes: int) -> Path:
    if not isolated:
        raise SafetyError("--isolated is required")
    if not 1 <= max_bytes <= MAX_CAPACITY_BYTES:
        raise SafetyError("--max-bytes must be between 1 and 1 GiB")
    requested = Path(os.path.abspath(target))
    if any(parent.is_symlink() for parent in (requested, *requested.parents)):
        raise SafetyError("target must not be a symlink")
    if not requested.exists() or not requested.is_dir():
        raise SafetyError("target must be an existing directory")
    resolved = requested.resolve(strict=True)
    if resolved == Path(resolved.anchor):
        raise SafetyError("filesystem root is refused")
    if any(item.is_symlink() for item in resolved.iterdir()):
        raise SafetyError("target contains a symlink")
    marker = resolved / MARKER_NAME
    if not marker.is_file() or marker.read_text(encoding="utf-8") != MARKER_CONTENT:
        raise SafetyError(f"target must contain exact marker {MARKER_NAME}")
    entries = list(resolved.iterdir())
    if len(entries) != 1 or entries[0] != marker:
        raise SafetyError("target must be empty except for its exact marker")
    if marker.is_symlink():
        raise SafetyError("marker must not be a symlink")
    capacity = shutil.disk_usage(resolved).total
    if capacity > max_bytes:
        raise SafetyError("filesystem exceeds --max-bytes; refusing to fill it")
    return resolved


def _write_filler(path: Path, limit: int, owned: dict[Path, tuple[int, int]]) -> int:
    written = 0
    block = b"F" * FILL_CHUNK_BYTES
    chunk_size = len(block)
    try:
        filesystem_block_size = os.statvfs(path.parent).f_frsize or 4096
    except (AttributeError, OSError):
        filesystem_block_size = 4096
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        details = os.fstat(descriptor)
        owned[path] = (details.st_dev, details.st_ino)
        while written < limit:
            size = min(chunk_size, limit - written)
            no_space = False
            try:
                count = os.write(descriptor, block[:size])
            except OSError as exc:
                if exc.errno != errno.ENOSPC:
                    raise
                count = 0
                no_space = True
            written += count
            try:
                os.fsync(descriptor)
            except OSError as exc:
                if exc.errno != errno.ENOSPC:
                    raise
                no_space = True
            if no_space and chunk_size > filesystem_block_size:
                chunk_size = max(filesystem_block_size, chunk_size // 2)
            elif no_space:
                break
            if shutil.disk_usage(path.parent).free == 0:
                break
    finally:
        os.close(descriptor)
    return written


def _create_exclusive(path: Path) -> tuple[int, tuple[int, int]]:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    details = os.fstat(descriptor)
    return descriptor, (details.st_dev, details.st_ino)


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    return details.st_dev, details.st_ino


def _ensure_owned_sidecars(paths: tuple[Path, ...], owned: dict[Path, tuple[int, int]]) -> None:
    for path in paths:
        current = _identity(path)
        expected = owned.get(path)
        if current is not None:
            if expected is None or current != expected:
                raise FileExistsError(f"SQLite sidecar path collision: {path.name}")
            continue
        descriptor, identity = _create_exclusive(path)
        owned[path] = identity
        os.close(descriptor)


def _cleanup_owned(owned: dict[Path, tuple[int, int]]) -> None:
    for path in list(owned):
        _unlink_owned(path, owned)


def _unlink_owned(path: Path, owned: dict[Path, tuple[int, int]]) -> bool:
    expected_identity = owned.pop(path, None)
    if expected_identity is None or _identity(path) != expected_identity:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _verify_application_pipeline(directory: Path, *, max_bytes: int) -> dict[str, object]:
    from f1_engineer.processing.coordinator import SessionCoordinator
    from f1_engineer.processing.evidence import EvidenceStore, digest

    nonce = uuid.uuid4().hex
    database_path = directory / f"migration-pipeline-{nonce}.sqlite3"
    filler_path = directory / f"migration-pipeline-filler-{nonce}.bin"
    source = f"migration-storage-verification-{nonce}"
    sidecars = (Path(str(database_path) + "-wal"), Path(str(database_path) + "-shm"))
    lock_path = database_path.with_name(database_path.name + "." + digest(source)[:16] + ".lock")
    owned: dict[Path, tuple[int, int]] = {}
    coordinator = None
    report: dict[str, object] = {
        "scope": "SessionCoordinator/EvidenceStore journal admission and recovery with synthetic admitted_packets fixture",
    }
    descriptor, identity = _create_exclusive(database_path)
    owned[database_path] = identity
    os.close(descriptor)
    try:
        if lock_path.exists() or lock_path.is_symlink():
            raise FileExistsError("application pipeline database sidecar or lock path collision")
        _ensure_owned_sidecars(sidecars, owned)
        store = EvidenceStore(database_path)
        _ensure_owned_sidecars(sidecars, owned)
        try:
            from tests.test_session_evidence import admitted_packets
        except ModuleNotFoundError as exc:
            raise RuntimeError("application-pipeline mode requires the repository test/application dependencies") from exc

        descriptor, lock_identity = _create_exclusive(lock_path)
        owned[lock_path] = lock_identity
        os.close(descriptor)
        coordinator = SessionCoordinator(store, source)
        _ensure_owned_sidecars(sidecars, owned)
        app_journal_mode = coordinator._writer.execute("PRAGMA journal_mode").fetchone()[0]
        app_synchronous = coordinator._writer.execute("PRAGMA synchronous").fetchone()[0]
        if app_journal_mode.lower() != "wal" or app_synchronous != 2:
            raise RuntimeError("EvidenceStore coordinator is not using WAL and synchronous=FULL")
        report["journal_mode"] = app_journal_mode
        report["synchronous"] = app_synchronous
        packets = list(admitted_packets())
        if len(packets) < 64:
            raise RuntimeError("synthetic admitted_packets fixture must provide at least 64 packets")
        coordinator.journal_batch(packets[:32])
        coordinator.publish_pending()
        admitted_before = coordinator.admitted_sequence
        if admitted_before != 32:
            raise RuntimeError("application pipeline did not commit its initial 32 packet batch")
        _write_filler(filler_path, max_bytes, owned)
        oversized = replace(packets[32], payload=b"x" * 65535)
        saw_full = False
        try:
            coordinator.journal_batch((packets[32], oversized))
        except sqlite3.OperationalError as exc:
            if "full" not in str(exc).lower() and getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_FULL:
                raise
            saw_full = True
        report["observed_sqlite_full"] = saw_full
        if not saw_full or not coordinator.failed or coordinator.admitted_sequence != admitted_before:
            raise RuntimeError("failed application journal batch was not atomically rejected")
        coordinator.close()
        coordinator = None
        _unlink_owned(filler_path, owned)

        _ensure_owned_sidecars(sidecars, owned)
        recovered_store = EvidenceStore(database_path)
        _ensure_owned_sidecars(sidecars, owned)
        recovered = SessionCoordinator(recovered_store, source)
        try:
            if recovered.sequence != admitted_before or recovered.admitted_sequence != admitted_before:
                raise RuntimeError("application coordinator did not recover the original admitted sequence")
            recovered.journal_batch(packets[32:64])
            recovered.publish_pending()
            if recovered.sequence != 64 or recovered.admitted_sequence != 64:
                raise RuntimeError("application coordinator did not publish the recovered 32 packet batch")
        finally:
            recovered.close()
        _ensure_owned_sidecars(sidecars, owned)
        with recovered_store.connect() as database:
            integrity = database.execute("PRAGMA quick_check").fetchone()[0]
            journal_rows = database.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
        if integrity != "ok" or journal_rows != 64:
            raise RuntimeError("recovered application journal failed integrity or row-count validation")
        report.update({
            "recovered": True,
            "integrity_check": integrity,
            "journal_rows": journal_rows,
            "admitted_sequence": 64,
        })
        return report
    finally:
        if coordinator is not None:
            coordinator.close()
        _cleanup_owned(owned)


def run(target: Path, *, isolated: bool, max_bytes: int, application_pipeline: bool = False) -> dict[str, object]:
    directory = validate_target(target, isolated=isolated, max_bytes=max_bytes)
    nonce = uuid.uuid4().hex
    database_path = directory / f"migration-enospc-{nonce}.sqlite3"
    filler_path = directory / f"migration-filler-{nonce}.bin"
    report: dict[str, object] = {
        "scope": "SQLite WAL/FULL transaction atomicity, integrity, and reopen recovery",
        "max_bytes": max_bytes,
    }
    owned: dict[Path, tuple[int, int]] = {}
    try:
        descriptor, database_identity = _create_exclusive(database_path)
        owned[database_path] = database_identity
        os.close(descriptor)
        sidecars = (Path(str(database_path) + "-wal"), Path(str(database_path) + "-shm"))
        _ensure_owned_sidecars(sidecars, owned)
        with closing(sqlite3.connect(database_path)) as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("PRAGMA synchronous=FULL")
            journal_mode = database.execute("PRAGMA journal_mode").fetchone()[0]
            synchronous = database.execute("PRAGMA synchronous").fetchone()[0]
            if journal_mode.lower() != "wal" or synchronous != 2:
                raise RuntimeError("SQLite did not activate WAL and synchronous=FULL")
            report["journal_mode"] = journal_mode
            report["synchronous"] = synchronous
            database.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value BLOB NOT NULL)")
            database.execute("INSERT INTO records(value) VALUES (?)", (b"baseline",))
            database.commit()
            _ensure_owned_sidecars(sidecars, owned)
            filled = _write_filler(filler_path, max_bytes, owned)
            report["filler_bytes"] = filled
            available_bytes = shutil.disk_usage(directory).free
            report["free_bytes_after_filler"] = available_bytes
            saw_full = False
            demand_bytes = min(max_bytes, available_bytes + 256 * 1024)
            report["transaction_demand_bytes"] = demand_bytes
            payload = b"x" * min(FILL_CHUNK_BYTES, demand_bytes)
            remaining = demand_bytes
            try:
                with database:
                    database.execute("BEGIN IMMEDIATE")
                    while remaining:
                        piece = payload[:min(len(payload), remaining)]
                        database.execute("INSERT INTO records(value) VALUES (?)", (piece,))
                        remaining -= len(piece)
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_FULL:
                    raise
                saw_full = True
            _unlink_owned(filler_path, owned)
        report["observed_enospc"] = saw_full
        if not saw_full:
            raise RuntimeError("filesystem did not produce SQLite SQLITE_FULL within the supplied bound")
        _unlink_owned(filler_path, owned)
        _ensure_owned_sidecars(sidecars, owned)
        with closing(sqlite3.connect(database_path)) as database, database:
            database.execute("PRAGMA synchronous=FULL")
            result = database.execute("PRAGMA integrity_check").fetchone()[0]
            count = database.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            if result != "ok" or count != 1:
                raise RuntimeError("failed transaction was not atomic or integrity check failed")
            database.execute("INSERT INTO records(value) VALUES (?)", (b"recovered",))
            _ensure_owned_sidecars(sidecars, owned)
        _ensure_owned_sidecars(sidecars, owned)
        with closing(sqlite3.connect(database_path)) as database, database:
            check = database.execute("PRAGMA integrity_check").fetchone()[0]
            count = database.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        if check != "ok" or count != 2:
            raise RuntimeError("SQLite recovery transaction or post-reopen integrity check failed")
        report.update({"integrity_check": check, "rows_after_recovery": count, "recovered": True})
        if application_pipeline:
            _cleanup_owned(owned)
            validate_target(target, isolated=isolated, max_bytes=max_bytes)
            report["application_pipeline"] = _verify_application_pipeline(directory, max_bytes=max_bytes)
        return report
    finally:
        _cleanup_owned(owned)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="already mounted disposable filesystem directory")
    parser.add_argument("--isolated", action="store_true", help="confirm target is disposable and isolated")
    parser.add_argument("--max-bytes", type=int, required=True, help="maximum filler bytes; filesystem and value must be <= 1 GiB")
    parser.add_argument("--application-pipeline", action="store_true", help="also exercise SessionCoordinator/EvidenceStore ENOSPC and recovery")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.target, isolated=args.isolated, max_bytes=args.max_bytes,
                             application_pipeline=args.application_pipeline), sort_keys=True))
    except (OSError, SafetyError, RuntimeError, sqlite3.Error) as exc:
        print(f"migration storage verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
