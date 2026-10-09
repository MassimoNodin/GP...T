from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


PROCESSOR_VERSION = "session-evidence-v1"
CHUNK_ROWS = 256
MAX_READ_ROWS = 20_000
MAX_READ_BYTES = 16 * 1024 * 1024
MAX_STAGED_ROWS_PER_CAR = 40_000


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class EvidenceUnavailable(ValueError):
    pass


class EvidenceStore:
    """Additive ledger; only committed, content-addressed chunks reach readers."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as database:
            version = database.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise EvidenceUnavailable("unsupported_evidence_schema")
            if version == 1:
                database.executescript("""
                    BEGIN IMMEDIATE;
                    ALTER TABLE sessions RENAME TO sessions_v1;
                    CREATE TABLE sessions (
                        id TEXT PRIMARY KEY, generation TEXT NOT NULL, uid TEXT NOT NULL,
                        lifecycle TEXT NOT NULL, acquisition TEXT NOT NULL, context TEXT,
                        occurrence INTEGER NOT NULL, UNIQUE(generation,uid,occurrence)
                    );
                    INSERT INTO sessions SELECT *,1 FROM sessions_v1;
                    DROP TABLE sessions_v1;
                    COMMIT;
                """)
            database.execute("PRAGMA journal_mode=WAL")
            database.executescript("""
                CREATE TABLE IF NOT EXISTS generations (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, version TEXT NOT NULL,
                    state TEXT NOT NULL, committed_sequence INTEGER NOT NULL DEFAULT 0,
                    gap_epoch INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS journal (
                    generation TEXT NOT NULL, sequence INTEGER NOT NULL, kind TEXT NOT NULL,
                    payload BLOB NOT NULL, metadata TEXT NOT NULL,
                    PRIMARY KEY (generation, sequence)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, generation TEXT NOT NULL, uid TEXT NOT NULL,
                    lifecycle TEXT NOT NULL, acquisition TEXT NOT NULL, context TEXT,
                    occurrence INTEGER NOT NULL, UNIQUE (generation, uid, occurrence)
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    generation TEXT NOT NULL, sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (generation, sequence, kind, ordinal)
                );
                CREATE TABLE IF NOT EXISTS bindings (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, epoch INTEGER NOT NULL,
                    format INTEGER NOT NULL, car INTEGER NOT NULL, tenure INTEGER NOT NULL,
                    start_frame INTEGER NOT NULL, end_frame INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS binding_owner ON bindings
                    (session, epoch, format, car, start_frame, end_frame);
                CREATE TABLE IF NOT EXISTS staging (
                    generation TEXT NOT NULL, uid TEXT NOT NULL, car INTEGER NOT NULL,
                    frame INTEGER NOT NULL, epoch INTEGER NOT NULL, format INTEGER NOT NULL,
                    sequence INTEGER NOT NULL, gap_epoch INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (generation, uid, car, frame)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    hash TEXT PRIMARY KEY, payload TEXT NOT NULL, rows INTEGER NOT NULL,
                    bytes INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, driver TEXT,
                    original_id TEXT NOT NULL, role TEXT NOT NULL, manifest TEXT NOT NULL,
                    payload TEXT NOT NULL, rows INTEGER NOT NULL, bytes INTEGER NOT NULL,
                    published_sequence INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS session_attempts ON attempts(session, id);
                CREATE TABLE IF NOT EXISTS dispositions (
                    attempt TEXT NOT NULL, sequence INTEGER NOT NULL, state TEXT NOT NULL,
                    reason TEXT NOT NULL, PRIMARY KEY (attempt, sequence)
                );
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS immutable_chunks BEFORE UPDATE ON chunks
                    BEGIN SELECT RAISE(ABORT, 'immutable evidence'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_attempts BEFORE UPDATE ON attempts
                    BEGIN SELECT RAISE(ABORT, 'immutable manifest'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_reports BEFORE UPDATE ON reports
                    BEGIN SELECT RAISE(ABORT, 'immutable comparison'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_journal BEFORE UPDATE ON journal
                    BEGIN SELECT RAISE(ABORT, 'immutable source journal'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_dispositions BEFORE UPDATE ON dispositions
                    BEGIN SELECT RAISE(ABORT, 'append-only disposition history'); END;
                CREATE TRIGGER IF NOT EXISTS retain_chunks BEFORE DELETE ON chunks
                    BEGIN SELECT RAISE(ABORT, 'immutable evidence'); END;
                CREATE TRIGGER IF NOT EXISTS retain_attempts BEFORE DELETE ON attempts
                    BEGIN SELECT RAISE(ABORT, 'immutable manifest'); END;
                CREATE TRIGGER IF NOT EXISTS retain_reports BEFORE DELETE ON reports
                    BEGIN SELECT RAISE(ABORT, 'immutable comparison'); END;
                CREATE TRIGGER IF NOT EXISTS retain_journal BEFORE DELETE ON journal
                    BEGIN SELECT RAISE(ABORT, 'immutable source journal'); END;
                CREATE TRIGGER IF NOT EXISTS retain_dispositions BEFORE DELETE ON dispositions
                    BEGIN SELECT RAISE(ABORT, 'append-only disposition history'); END;
                PRAGMA user_version=2;
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        database = sqlite3.connect(self.path, timeout=10)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA synchronous=FULL")
        database.execute("PRAGMA cache_size=-4096")
        try:
            with database:
                yield database
        finally:
            database.close()

    def sessions(self, *, limit: int = 100, after: str = "") -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("session page exceeds budget")
        with self.connect() as database:
            return [dict(row) for row in database.execute(
                "SELECT * FROM sessions WHERE id>? ORDER BY id LIMIT ?", (after, limit)
            )]

    def attempts(self, session: str, *, limit: int = 100, after: str = "") -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("attempt page exceeds budget")
        with self.connect() as database:
            return [self._attempt(database, row) for row in database.execute(
                "SELECT * FROM attempts WHERE session=? AND id>? ORDER BY id LIMIT ?",
                (session, after, limit),
            )]

    @staticmethod
    def _attempt(database: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["manifest"] = json.loads(result["manifest"])
        result["payload"] = json.loads(result["payload"])
        status = database.execute(
            "SELECT state,reason,sequence FROM dispositions WHERE attempt=? ORDER BY sequence DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        result["readiness"] = dict(status) if status else None
        return result

    def evidence(self, attempt_id: str, *, session: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self.connect() as database:
            database.execute("BEGIN")
            row = database.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None or (session is not None and row["session"] != session):
                raise EvidenceUnavailable("attempt_not_in_session")
            if row["rows"] > MAX_READ_ROWS or row["bytes"] > MAX_READ_BYTES:
                raise EvidenceUnavailable("analysis_read_budget_exceeded")
            attempt = self._attempt(database, row)
            manifest = attempt["manifest"]
            payload = attempt["payload"]
            if attempt["driver"]:
                binding = database.execute("SELECT * FROM bindings WHERE id=? AND session=?",
                                           (attempt["driver"], attempt["session"])).fetchone()
                if binding is None or binding["epoch"] != manifest["epoch"] or binding["format"] != manifest["format"]:
                    raise EvidenceUnavailable("driver_binding_mismatch")
                if (binding["car"] != payload["car_index"] or binding["start_frame"] > manifest["start_frame"]
                        or binding["end_frame"] <= manifest["end_frame"]):
                    raise EvidenceUnavailable("driver_ownership_range_mismatch")
            records: list[dict[str, Any]] = []
            total_bytes = 0
            for chunk_hash in attempt["manifest"]["chunks"]:
                chunk = database.execute("SELECT * FROM chunks WHERE hash=?", (chunk_hash,)).fetchone()
                if chunk is None or digest(chunk["payload"]) != chunk_hash:
                    raise EvidenceUnavailable("evidence_checksum_mismatch")
                if len(chunk["payload"].encode()) != chunk["bytes"]:
                    raise EvidenceUnavailable("evidence_byte_count_mismatch")
                total_bytes += chunk["bytes"]
                if total_bytes > MAX_READ_BYTES:
                    raise EvidenceUnavailable("analysis_read_budget_exceeded")
                content = json.loads(chunk["payload"])
                if len(content) != chunk["rows"] or len(content) > CHUNK_ROWS:
                    raise EvidenceUnavailable("evidence_row_count_mismatch")
                for sample in content:
                    if (str(sample["session_uid"]) != str(payload["session_uid"])
                            or sample["car_index"] != payload["car_index"]
                            or sample["lap_number"] != payload["lap_number"]
                            or sample["lifecycle_epoch"] != manifest["epoch"]
                            or sample["packet_format"] != manifest["format"]
                            or not manifest["start_frame"] <= sample["frame_ordinal"] <= manifest["end_frame"]):
                        raise EvidenceUnavailable("evidence_ownership_mismatch")
                records.extend(content)
                if len(records) > MAX_READ_ROWS:
                    raise EvidenceUnavailable("analysis_read_budget_exceeded")
            if len(records) != row["rows"] or total_bytes != row["bytes"]:
                raise EvidenceUnavailable("evidence_manifest_mismatch")
            return attempt, records

    def report(self, report_id: str) -> dict[str, Any]:
        with self.connect() as database:
            row = database.execute("SELECT payload FROM reports WHERE id=?", (report_id,)).fetchone()
            if row is None:
                raise EvidenceUnavailable("comparison_not_found")
            return json.loads(row["payload"])
