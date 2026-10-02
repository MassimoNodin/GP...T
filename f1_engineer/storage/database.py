from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_VERSION = 3


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version INTEGER NOT NULL
);
INSERT OR IGNORE INTO schema_info(singleton, version) VALUES (1, 3);

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

CREATE INDEX IF NOT EXISTS idx_processing_runs_capture ON processing_runs(capture_sha256);
CREATE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(session_uid);
CREATE INDEX IF NOT EXISTS idx_lap_attempts_session ON lap_attempts(session_key, attempt_number);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
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
        if version != SCHEMA_VERSION:
            self.connection.close()
            raise ValueError(f"database schema {version} is not supported")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
