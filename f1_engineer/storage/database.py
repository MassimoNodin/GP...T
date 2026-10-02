from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 6


class DatabaseSchemaError(ValueError):
    """The configured database cannot be safely read by this application version."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version INTEGER NOT NULL
);
INSERT OR IGNORE INTO schema_info(singleton, version) VALUES (1, 6);

CREATE TABLE IF NOT EXISTS captures (
    capture_sha256 TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    complete INTEGER NOT NULL,
    completion_json TEXT,
    metadata_json TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS processing_runs (
    run_id TEXT PRIMARY KEY,
    capture_sha256 TEXT NOT NULL REFERENCES captures(capture_sha256),
    pipeline_version TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'complete', 'failed')),
    started_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at_utc TEXT,
    error TEXT,
    metrics_json TEXT,
    UNIQUE(capture_sha256, pipeline_version, config_json)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES processing_runs(run_id) ON DELETE CASCADE,
    session_uid TEXT NOT NULL,
    packet_format INTEGER NOT NULL,
    context_json TEXT,
    UNIQUE(run_id, session_uid)
);

CREATE TABLE IF NOT EXISTS session_contexts (
    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
    effective_frame INTEGER NOT NULL,
    context_json TEXT NOT NULL,
    PRIMARY KEY(session_key, effective_frame)
);

CREATE TABLE IF NOT EXISTS session_context_invalidations (
    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
    effective_frame INTEGER NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY(session_key, effective_frame)
);

CREATE TABLE IF NOT EXISTS driver_snapshots (
    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
    car_index INTEGER NOT NULL,
    effective_frame INTEGER NOT NULL,
    active_car_count INTEGER NOT NULL,
    participant_json TEXT NOT NULL,
    PRIMARY KEY(session_key, car_index, effective_frame)
);

CREATE TABLE IF NOT EXISTS lap_attempts (
    attempt_key TEXT PRIMARY KEY,
    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
    car_index INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL,
    lap_number INTEGER NOT NULL,
    disposition TEXT NOT NULL,
    start_frame_identifier INTEGER NOT NULL,
    end_frame_identifier INTEGER,
    start_session_time_s REAL NOT NULL,
    end_session_time_s REAL,
    lap_time_ms INTEGER,
    game_valid INTEGER,
    start_observed INTEGER NOT NULL,
    pit_encountered INTEGER NOT NULL,
    sample_count INTEGER NOT NULL,
    reference_eligible INTEGER NOT NULL,
    exclusion_reasons_json TEXT NOT NULL,
    attempt_json TEXT NOT NULL,
    UNIQUE(session_key, car_index, attempt_number)
);

CREATE TABLE IF NOT EXISTS lap_context_segments (
    attempt_key TEXT NOT NULL REFERENCES lap_attempts(attempt_key) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    from_frame_identifier INTEGER NOT NULL,
    context_json TEXT,
    PRIMARY KEY(attempt_key, ordinal)
);

CREATE TABLE IF NOT EXISTS telemetry_files (
    attempt_key TEXT PRIMARY KEY REFERENCES lap_attempts(attempt_key) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    ready INTEGER NOT NULL CHECK (ready IN (0, 1))
);

CREATE TABLE IF NOT EXISTS recording_sources (
    capture_id TEXT PRIMARY KEY,
    root_namespace TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    modified_ns INTEGER NOT NULL,
    discovered_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(root_namespace, relative_path)
);

CREATE TABLE IF NOT EXISTS import_jobs (
    job_id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL REFERENCES recording_sources(capture_id),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'complete', 'failed', 'interrupted')),
    phase TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count > 0),
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at_utc TEXT,
    finished_at_utc TEXT,
    result_json TEXT,
    failure_reason TEXT
);

CREATE TABLE IF NOT EXISTS recording_jobs (
    recording_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('starting', 'recording', 'stopping', 'complete', 'failed', 'interrupted')),
    staging_relative_path TEXT NOT NULL,
    final_relative_path TEXT NOT NULL,
    bind_host TEXT NOT NULL,
    bind_port INTEGER NOT NULL CHECK (bind_port BETWEEN 0 AND 65535),
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at_utc TEXT,
    finished_at_utc TEXT,
    summary_json TEXT,
    failure_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_import_jobs_capture ON import_jobs(capture_id, created_at_utc);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_import_job
    ON import_jobs((1)) WHERE status IN ('queued', 'running');
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_recording_job
    ON recording_jobs((1)) WHERE status IN ('starting', 'recording', 'stopping');

CREATE INDEX IF NOT EXISTS idx_processing_runs_capture ON processing_runs(capture_sha256);
CREATE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(session_uid);
CREATE INDEX IF NOT EXISTS idx_lap_attempts_session ON lap_attempts(session_key, attempt_number);
"""


class Database:
    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(f"database does not exist: {self.path}")
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        if read_only:
            try:
                version = self.connection.execute(
                    "SELECT version FROM schema_info WHERE singleton = 1"
                ).fetchone()[0]
            except (sqlite3.DatabaseError, TypeError, IndexError) as exc:
                self.connection.close()
                raise DatabaseSchemaError(
                    "database schema is unavailable for read-only access"
                ) from exc
            if version != SCHEMA_VERSION:
                self.connection.close()
                raise DatabaseSchemaError(
                    f"database schema {version} is not supported for read-only access"
                )
            return

        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(_SCHEMA)
        version = self.connection.execute(
            "SELECT version FROM schema_info WHERE singleton = 1"
        ).fetchone()[0]
        if version == 1:
            self.connection.execute("ALTER TABLE processing_runs ADD COLUMN metrics_json TEXT")
            self.connection.execute("UPDATE schema_info SET version = 2 WHERE singleton = 1")
            self.connection.commit()
            version = 2
        if version == 2:
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS session_context_invalidations (
                       session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
                       effective_frame INTEGER NOT NULL,
                       reason TEXT NOT NULL,
                       PRIMARY KEY(session_key, effective_frame))"""
            )
            self.connection.execute("UPDATE schema_info SET version = 3 WHERE singleton = 1")
            self.connection.commit()
            version = 3
        if version == 3:
            self.connection.execute("UPDATE schema_info SET version = 4 WHERE singleton = 1")
            self.connection.commit()
            version = 4
        if version == 4:
            columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(import_jobs)")
            }
            if "updated_at_utc" not in columns:
                self.connection.execute(
                    "ALTER TABLE import_jobs ADD COLUMN updated_at_utc TEXT"
                )
            legacy_jobs = self.connection.execute(
                "SELECT job_id, COALESCE(finished_at_utc, started_at_utc, created_at_utc) AS timestamp FROM import_jobs WHERE updated_at_utc IS NULL"
            ).fetchall()
            for row in legacy_jobs:
                raw_timestamp = str(row["timestamp"])
                try:
                    timestamp = datetime.fromisoformat(
                        raw_timestamp.replace("Z", "+00:00")
                    )
                except ValueError:
                    timestamp = datetime.now(timezone.utc)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                self.connection.execute(
                    "UPDATE import_jobs SET updated_at_utc=? WHERE job_id=?",
                    (
                        timestamp.astimezone(timezone.utc).isoformat(
                            timespec="microseconds"
                        ),
                        row["job_id"],
                    ),
                )
            self.connection.execute("UPDATE schema_info SET version = 5 WHERE singleton = 1")
            self.connection.commit()
            version = 5
        if version == 5:
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_recording_jobs_latest ON recording_jobs(updated_at_utc DESC)"
            )
            self.connection.execute("UPDATE schema_info SET version = 6 WHERE singleton = 1")
            self.connection.commit()
            version = 6
        if version != SCHEMA_VERSION:
            self.connection.close()
            raise ValueError(f"database schema {version} is not supported")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_import_jobs_latest ON import_jobs(capture_id, updated_at_utc DESC)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_recording_jobs_latest ON recording_jobs(updated_at_utc DESC)"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
