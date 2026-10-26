from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 15


class DatabaseSchemaError(ValueError):
    """The configured database cannot be safely read by this application version."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version INTEGER NOT NULL
);
INSERT OR IGNORE INTO schema_info(singleton, version) VALUES (1, 14);

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

CREATE TABLE IF NOT EXISTS lifecycle_events (
    event_key TEXT PRIMARY KEY,
    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
    event_ordinal INTEGER NOT NULL,
    frame_ordinal INTEGER NOT NULL,
    current_frame_identifier INTEGER NOT NULL,
    current_overall_frame_identifier INTEGER NOT NULL,
    packet_format INTEGER NOT NULL,
    packet_version INTEGER,
    event_code TEXT,
    event_kind TEXT NOT NULL,
    session_time_s REAL NOT NULL,
    target_game_frame_identifier INTEGER,
    target_session_time_s REAL,
    prior_session_time_s REAL,
    cause TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    details_hex TEXT NOT NULL,
    details_length_bytes INTEGER NOT NULL DEFAULT 0,
    details_truncated INTEGER NOT NULL DEFAULT 0 CHECK (details_truncated IN (0, 1)),
    duplicate_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(session_key, event_ordinal)
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

CREATE TABLE IF NOT EXISTS player_participant_observations (
    run_id TEXT NOT NULL REFERENCES processing_runs(run_id) ON DELETE CASCADE,
    session_uid TEXT NOT NULL,
    frame_ordinal INTEGER NOT NULL,
    frame_identifier INTEGER NOT NULL,
    overall_frame_identifier INTEGER NOT NULL,
    packet_format INTEGER,
    association_epoch INTEGER NOT NULL,
    association_scope_assessable INTEGER NOT NULL CHECK
        (association_scope_assessable IN (0, 1)),
    player_car_index INTEGER,
    session_time_s REAL,
    status TEXT NOT NULL CHECK (status IN ('observed', 'unavailable', 'truncated')),
    reason TEXT,
    active_car_count INTEGER,
    participant_json TEXT,
    source_packet_count INTEGER NOT NULL,
    PRIMARY KEY(run_id, session_uid, frame_ordinal)
);

CREATE TABLE IF NOT EXISTS player_car_setup_observations (
    run_id TEXT NOT NULL REFERENCES processing_runs(run_id) ON DELETE CASCADE,
    session_uid TEXT NOT NULL,
    frame_ordinal INTEGER NOT NULL,
    frame_identifier INTEGER NOT NULL,
    overall_frame_identifier INTEGER NOT NULL,
    packet_format INTEGER,
    association_epoch INTEGER NOT NULL,
    association_scope_assessable INTEGER NOT NULL CHECK
        (association_scope_assessable IN (0, 1)),
    player_car_index INTEGER,
    session_time_s REAL,
    status TEXT NOT NULL CHECK (status IN ('observed', 'unavailable', 'truncated')),
    reason TEXT,
    setup_json TEXT,
    next_front_wing_value REAL,
    source_packet_count INTEGER NOT NULL,
    PRIMARY KEY(run_id, session_uid, frame_ordinal)
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
    start_frame_ordinal INTEGER,
    end_frame_ordinal INTEGER,
    superseded INTEGER CHECK (superseded IN (0, 1) OR superseded IS NULL),
    lifecycle_assessed INTEGER NOT NULL DEFAULT 0 CHECK (lifecycle_assessed IN (0, 1)),
    UNIQUE(session_key, car_index, attempt_number)
);

CREATE TABLE IF NOT EXISTS attempt_lifecycle_links (
    attempt_key TEXT NOT NULL REFERENCES lap_attempts(attempt_key) ON DELETE CASCADE,
    event_key TEXT NOT NULL REFERENCES lifecycle_events(event_key) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    PRIMARY KEY(attempt_key, event_key, relation)
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

CREATE TABLE IF NOT EXISTS car_observation_chunks (
    chunk_key TEXT PRIMARY KEY,
    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
    packet_format INTEGER NOT NULL,
    lifecycle_epoch INTEGER NOT NULL,
    chunk_ordinal INTEGER NOT NULL,
    relative_path TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    ready INTEGER NOT NULL CHECK (ready IN (0, 1)),
    UNIQUE(session_key, packet_format, lifecycle_epoch, chunk_ordinal)
);

CREATE TABLE IF NOT EXISTS car_observation_slots (
    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
    packet_format INTEGER NOT NULL,
    lifecycle_epoch INTEGER NOT NULL,
    car_index INTEGER NOT NULL,
    observation_count INTEGER NOT NULL,
    car_telemetry_count INTEGER NOT NULL,
    motion_count INTEGER NOT NULL,
    nonzero_speed_count INTEGER NOT NULL,
    header_player_count INTEGER NOT NULL,
    first_frame_ordinal INTEGER NOT NULL,
    last_frame_ordinal INTEGER NOT NULL,
    PRIMARY KEY(session_key, packet_format, lifecycle_epoch, car_index)
);

CREATE TABLE IF NOT EXISTS car_slot_tenures (
    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
    packet_format INTEGER NOT NULL,
    lifecycle_epoch INTEGER NOT NULL,
    car_index INTEGER NOT NULL CHECK (car_index BETWEEN 0 AND 23),
    tenure_ordinal INTEGER NOT NULL CHECK (tenure_ordinal > 0),
    start_frame_ordinal INTEGER NOT NULL CHECK (start_frame_ordinal > 0),
    end_frame_ordinal_exclusive INTEGER NOT NULL
        CHECK (end_frame_ordinal_exclusive > start_frame_ordinal),
    participant_frame_identifier INTEGER NOT NULL,
    participant_wire_fingerprint TEXT NOT NULL
        CHECK (length(participant_wire_fingerprint) = 64),
    participant_identity_fingerprint TEXT NOT NULL
        CHECK (length(participant_identity_fingerprint) = 64),
    close_reason TEXT NOT NULL,
    PRIMARY KEY(session_key, packet_format, lifecycle_epoch, car_index, tenure_ordinal)
);

CREATE TABLE IF NOT EXISTS observed_car_lap_attempts (
    attempt_key TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    packet_format INTEGER NOT NULL,
    lifecycle_epoch INTEGER NOT NULL,
    car_index INTEGER NOT NULL CHECK (car_index BETWEEN 0 AND 23),
    tenure_ordinal INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    lap_number INTEGER NOT NULL CHECK (lap_number >= 0),
    disposition TEXT NOT NULL CHECK (disposition IN ('completed', 'partial', 'abandoned')),
    lap_time_ms INTEGER,
    game_valid INTEGER CHECK (game_valid IN (0, 1) OR game_valid IS NULL),
    start_observed INTEGER NOT NULL CHECK (start_observed IN (0, 1)),
    pit_encountered INTEGER NOT NULL CHECK (pit_encountered IN (0, 1)),
    sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
    start_frame_ordinal INTEGER NOT NULL CHECK (start_frame_ordinal > 0),
    end_frame_ordinal INTEGER,
    completion_frame_ordinal INTEGER,
    exclusion_reasons_json TEXT NOT NULL,
    context_segments_json TEXT NOT NULL,
    diagnostic_only INTEGER NOT NULL CHECK (diagnostic_only = 1),
    FOREIGN KEY(session_key, packet_format, lifecycle_epoch, car_index, tenure_ordinal)
        REFERENCES car_slot_tenures(
            session_key, packet_format, lifecycle_epoch, car_index, tenure_ordinal
        ) ON DELETE CASCADE,
    UNIQUE(session_key, packet_format, lifecycle_epoch, car_index, tenure_ordinal, attempt_number)
);

CREATE TABLE IF NOT EXISTS attempt_timing_evidence (
    attempt_key TEXT PRIMARY KEY REFERENCES lap_attempts(attempt_key) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('matched', 'ambiguous', 'conflicting', 'unavailable', 'truncated')),
    evidence_json TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS recording_groups (
    group_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN (
        'starting', 'recording', 'pausing', 'paused', 'resuming',
        'stopping', 'complete', 'failed', 'interrupted'
    )),
    bind_host TEXT NOT NULL,
    bind_port INTEGER NOT NULL CHECK (bind_port BETWEEN 0 AND 65535),
    transition_revision INTEGER NOT NULL DEFAULT 0 CHECK (transition_revision >= 0),
    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at_utc TEXT,
    finished_at_utc TEXT,
    current_recording_id TEXT,
    last_recording_id TEXT,
    segment_count INTEGER NOT NULL DEFAULT 0 CHECK (segment_count BETWEEN 0 AND 256),
    summary_json TEXT,
    failure_reason TEXT
);

CREATE TABLE IF NOT EXISTS recording_group_events (
    group_id TEXT NOT NULL REFERENCES recording_groups(group_id) ON DELETE CASCADE,
    event_ordinal INTEGER NOT NULL CHECK (event_ordinal >= 0),
    event_kind TEXT NOT NULL CHECK (event_kind IN (
        'start_requested', 'acquisition_started', 'pause_requested',
        'pause_acknowledged', 'resume_requested', 'stop_requested',
        'complete', 'failed', 'interrupted'
    )),
    event_at_utc TEXT NOT NULL,
    recording_id TEXT,
    transition_revision INTEGER NOT NULL,
    details_json TEXT,
    PRIMARY KEY(group_id, event_ordinal)
);
CREATE INDEX IF NOT EXISTS idx_recording_group_events_latest
    ON recording_group_events(group_id, event_ordinal DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_recording_group
    ON recording_groups((1)) WHERE status IN (
        'starting', 'recording', 'pausing', 'paused', 'resuming', 'stopping'
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
    failure_reason TEXT,
    group_id TEXT REFERENCES recording_groups(group_id),
    segment_ordinal INTEGER CHECK (segment_ordinal BETWEEN 1 AND 256)
);

CREATE INDEX IF NOT EXISTS idx_import_jobs_capture ON import_jobs(capture_id, created_at_utc);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_recording_job
    ON recording_jobs((1)) WHERE status IN ('starting', 'recording', 'stopping');

CREATE INDEX IF NOT EXISTS idx_processing_runs_capture ON processing_runs(capture_sha256);
CREATE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(session_uid);
CREATE INDEX IF NOT EXISTS idx_lap_attempts_session ON lap_attempts(session_key, attempt_number);
CREATE INDEX IF NOT EXISTS idx_lifecycle_events_session_ordinal
    ON lifecycle_events(session_key, event_ordinal);
CREATE INDEX IF NOT EXISTS idx_lifecycle_events_session_frame
    ON lifecycle_events(session_key, frame_ordinal);
CREATE INDEX IF NOT EXISTS idx_attempt_lifecycle_event ON attempt_lifecycle_links(event_key);
CREATE INDEX IF NOT EXISTS idx_car_observation_chunks_session
    ON car_observation_chunks(session_key, packet_format, lifecycle_epoch);
CREATE INDEX IF NOT EXISTS idx_car_observation_slots_session
    ON car_observation_slots(session_key, car_index);
CREATE INDEX IF NOT EXISTS idx_car_slot_tenures_slot
    ON car_slot_tenures(session_key, car_index, start_frame_ordinal);
CREATE INDEX IF NOT EXISTS idx_observed_car_lap_attempts_slot
    ON observed_car_lap_attempts(session_key, car_index, start_frame_ordinal);
CREATE INDEX IF NOT EXISTS idx_player_participant_observations_scope
    ON player_participant_observations(
        run_id, session_uid, packet_format, association_epoch, frame_ordinal
    );
CREATE INDEX IF NOT EXISTS idx_player_car_setup_observations_scope
    ON player_car_setup_observations(
        run_id, session_uid, packet_format, association_epoch, frame_ordinal
    );
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
        if version == 6:
            columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(lap_attempts)")
            }
            additions = (
                ("start_frame_ordinal", "INTEGER"),
                ("end_frame_ordinal", "INTEGER"),
                ("superseded", "INTEGER"),
                ("lifecycle_assessed", "INTEGER NOT NULL DEFAULT 0"),
            )
            for name, declaration in additions:
                if name not in columns:
                    self.connection.execute(
                        f"ALTER TABLE lap_attempts ADD COLUMN {name} {declaration}"
                    )
            self.connection.execute("UPDATE schema_info SET version = 7 WHERE singleton = 1")
            self.connection.commit()
            version = 7
        if version == 7:
            columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(lifecycle_events)")
            }
            additions = (
                ("details_length_bytes", "INTEGER NOT NULL DEFAULT 0"),
                ("details_truncated", "INTEGER NOT NULL DEFAULT 0"),
            )
            for name, declaration in additions:
                if name not in columns:
                    self.connection.execute(
                        f"ALTER TABLE lifecycle_events ADD COLUMN {name} {declaration}"
                    )
            self.connection.execute("UPDATE schema_info SET version = 8 WHERE singleton = 1")
            self.connection.commit()
            version = 8
        if version == 8:
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS attempt_timing_evidence (
                       attempt_key TEXT PRIMARY KEY REFERENCES lap_attempts(attempt_key) ON DELETE CASCADE,
                       status TEXT NOT NULL CHECK (status IN
                           ('matched', 'ambiguous', 'conflicting', 'unavailable', 'truncated')),
                       evidence_json TEXT NOT NULL)"""
            )
            self.connection.execute(
                "UPDATE schema_info SET version = 9 WHERE singleton = 1"
            )
            self.connection.commit()
            version = 9
        if version == 9:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS car_observation_chunks (
                    chunk_key TEXT PRIMARY KEY,
                    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
                    packet_format INTEGER NOT NULL,
                    lifecycle_epoch INTEGER NOT NULL,
                    chunk_ordinal INTEGER NOT NULL,
                    relative_path TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    row_count INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    quality_json TEXT NOT NULL,
                    ready INTEGER NOT NULL CHECK (ready IN (0, 1)),
                    UNIQUE(session_key, packet_format, lifecycle_epoch, chunk_ordinal)
                );
                CREATE TABLE IF NOT EXISTS car_observation_slots (
                    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
                    packet_format INTEGER NOT NULL,
                    lifecycle_epoch INTEGER NOT NULL,
                    car_index INTEGER NOT NULL,
                    observation_count INTEGER NOT NULL,
                    car_telemetry_count INTEGER NOT NULL,
                    motion_count INTEGER NOT NULL,
                    nonzero_speed_count INTEGER NOT NULL,
                    header_player_count INTEGER NOT NULL,
                    first_frame_ordinal INTEGER NOT NULL,
                    last_frame_ordinal INTEGER NOT NULL,
                    PRIMARY KEY(session_key, packet_format, lifecycle_epoch, car_index)
                );
                CREATE INDEX IF NOT EXISTS idx_car_observation_chunks_session
                    ON car_observation_chunks(session_key, packet_format, lifecycle_epoch);
                CREATE INDEX IF NOT EXISTS idx_car_observation_slots_session
                    ON car_observation_slots(session_key, car_index);
                UPDATE schema_info SET version = 10 WHERE singleton = 1;
                """
            )
            self.connection.commit()
            version = 10
        if version == 10:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS player_participant_observations (
                    run_id TEXT NOT NULL REFERENCES processing_runs(run_id) ON DELETE CASCADE,
                    session_uid TEXT NOT NULL,
                    frame_ordinal INTEGER NOT NULL,
                    frame_identifier INTEGER NOT NULL,
                    overall_frame_identifier INTEGER NOT NULL,
                    packet_format INTEGER,
                    association_epoch INTEGER NOT NULL,
                    association_scope_assessable INTEGER NOT NULL CHECK
                        (association_scope_assessable IN (0, 1)),
                    player_car_index INTEGER,
                    session_time_s REAL,
                    status TEXT NOT NULL CHECK
                        (status IN ('observed', 'unavailable', 'truncated')),
                    reason TEXT,
                    active_car_count INTEGER,
                    participant_json TEXT,
                    source_packet_count INTEGER NOT NULL,
                    PRIMARY KEY(run_id, session_uid, frame_ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_player_participant_observations_scope
                    ON player_participant_observations(
                        run_id, session_uid, packet_format,
                        association_epoch, frame_ordinal
                    );
                UPDATE schema_info SET version = 11 WHERE singleton = 1;
                """
            )
            self.connection.commit()
            version = 11
        if version == 11:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS player_car_setup_observations (
                    run_id TEXT NOT NULL REFERENCES processing_runs(run_id) ON DELETE CASCADE,
                    session_uid TEXT NOT NULL,
                    frame_ordinal INTEGER NOT NULL,
                    frame_identifier INTEGER NOT NULL,
                    overall_frame_identifier INTEGER NOT NULL,
                    packet_format INTEGER,
                    association_epoch INTEGER NOT NULL,
                    association_scope_assessable INTEGER NOT NULL CHECK
                        (association_scope_assessable IN (0, 1)),
                    player_car_index INTEGER,
                    session_time_s REAL,
                    status TEXT NOT NULL CHECK
                        (status IN ('observed', 'unavailable', 'truncated')),
                    reason TEXT,
                    setup_json TEXT,
                    next_front_wing_value REAL,
                    source_packet_count INTEGER NOT NULL,
                    PRIMARY KEY(run_id, session_uid, frame_ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_player_car_setup_observations_scope
                    ON player_car_setup_observations(
                        run_id, session_uid, packet_format,
                        association_epoch, frame_ordinal
                    );
                UPDATE schema_info SET version = 12 WHERE singleton = 1;
                """
            )
            self.connection.commit()
            version = 12
        if version == 12:
            columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(recording_jobs)")
            }
            if "group_id" not in columns:
                self.connection.execute(
                    "ALTER TABLE recording_jobs ADD COLUMN group_id TEXT REFERENCES recording_groups(group_id)"
                )
            if "segment_ordinal" not in columns:
                self.connection.execute(
                    "ALTER TABLE recording_jobs ADD COLUMN segment_ordinal INTEGER"
                )
            self.connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_recording_group_segment ON recording_jobs(group_id, segment_ordinal) WHERE group_id IS NOT NULL"
            )
            self.connection.execute(
                "UPDATE schema_info SET version = 13 WHERE singleton = 1"
            )
            self.connection.commit()
            version = 13
        if version == 13:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS car_slot_tenures (
                    session_key TEXT NOT NULL REFERENCES sessions(session_key) ON DELETE CASCADE,
                    packet_format INTEGER NOT NULL,
                    lifecycle_epoch INTEGER NOT NULL,
                    car_index INTEGER NOT NULL CHECK (car_index BETWEEN 0 AND 23),
                    tenure_ordinal INTEGER NOT NULL CHECK (tenure_ordinal > 0),
                    start_frame_ordinal INTEGER NOT NULL CHECK (start_frame_ordinal > 0),
                    end_frame_ordinal_exclusive INTEGER NOT NULL
                        CHECK (end_frame_ordinal_exclusive > start_frame_ordinal),
                    participant_frame_identifier INTEGER NOT NULL,
                    participant_wire_fingerprint TEXT NOT NULL
                        CHECK (length(participant_wire_fingerprint) = 64),
                    participant_identity_fingerprint TEXT NOT NULL
                        CHECK (length(participant_identity_fingerprint) = 64),
                    close_reason TEXT NOT NULL,
                    PRIMARY KEY(session_key, packet_format, lifecycle_epoch, car_index, tenure_ordinal)
                );
                CREATE TABLE IF NOT EXISTS observed_car_lap_attempts (
                    attempt_key TEXT PRIMARY KEY,
                    session_key TEXT NOT NULL,
                    packet_format INTEGER NOT NULL,
                    lifecycle_epoch INTEGER NOT NULL,
                    car_index INTEGER NOT NULL CHECK (car_index BETWEEN 0 AND 23),
                    tenure_ordinal INTEGER NOT NULL,
                    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                    lap_number INTEGER NOT NULL CHECK (lap_number >= 0),
                    disposition TEXT NOT NULL CHECK (disposition IN ('completed', 'partial', 'abandoned')),
                    lap_time_ms INTEGER,
                    game_valid INTEGER CHECK (game_valid IN (0, 1) OR game_valid IS NULL),
                    start_observed INTEGER NOT NULL CHECK (start_observed IN (0, 1)),
                    pit_encountered INTEGER NOT NULL CHECK (pit_encountered IN (0, 1)),
                    sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
                    start_frame_ordinal INTEGER NOT NULL CHECK (start_frame_ordinal > 0),
                    end_frame_ordinal INTEGER,
                    completion_frame_ordinal INTEGER,
                    exclusion_reasons_json TEXT NOT NULL,
                    context_segments_json TEXT NOT NULL,
                    diagnostic_only INTEGER NOT NULL CHECK (diagnostic_only = 1),
                    FOREIGN KEY(session_key, packet_format, lifecycle_epoch, car_index, tenure_ordinal)
                        REFERENCES car_slot_tenures(
                            session_key, packet_format, lifecycle_epoch, car_index, tenure_ordinal
                        ) ON DELETE CASCADE,
                    UNIQUE(session_key, packet_format, lifecycle_epoch, car_index, tenure_ordinal, attempt_number)
                );
                CREATE INDEX IF NOT EXISTS idx_car_slot_tenures_slot
                    ON car_slot_tenures(session_key, car_index, start_frame_ordinal);
                CREATE INDEX IF NOT EXISTS idx_observed_car_lap_attempts_slot
                    ON observed_car_lap_attempts(session_key, car_index, start_frame_ordinal);
                UPDATE schema_info SET version = 14 WHERE singleton = 1;
                """
            )
            self.connection.commit()
            version = 14
        if version == 14:
            # Serialize upgrades before reading any rows. A second process may
            # have completed this migration while this connection was opening.
            self.connection.execute("BEGIN IMMEDIATE")
            locked_version = self.connection.execute(
                "SELECT version FROM schema_info WHERE singleton = 1"
            ).fetchone()[0]
            if locked_version != 14:
                self.connection.commit()
                if locked_version != SCHEMA_VERSION:
                    self.connection.close()
                    raise ValueError(
                        f"database schema {locked_version} is not supported"
                    )
                return
            rows = self.connection.execute(
                """SELECT j.rowid AS row_id, j.job_id, j.capture_id, j.status,
                          j.created_at_utc, s.root_namespace, s.relative_path,
                          s.byte_size, s.modified_ns
                     FROM import_jobs j
                     LEFT JOIN recording_sources s ON s.capture_id=j.capture_id
                    ORDER BY CASE j.status WHEN 'running' THEN 0
                                           WHEN 'queued' THEN 1 ELSE 2 END,
                             j.created_at_utc, j.rowid"""
            ).fetchall()
            pinned_rows: list[tuple[object, ...]] = []
            for queue_order, row in enumerate(rows, start=1):
                namespace = row["root_namespace"]
                relative_path = row["relative_path"]
                byte_size = row["byte_size"]
                modified_ns = row["modified_ns"]
                version_pin = None
                if (
                    isinstance(namespace, str)
                    and re.fullmatch(r"[a-f0-9]{64}", namespace) is not None
                    and isinstance(relative_path, str)
                    and isinstance(byte_size, int)
                    and not isinstance(byte_size, bool)
                    and isinstance(modified_ns, int)
                    and not isinstance(modified_ns, bool)
                ):
                    version_pin = recording_source_metadata_version(
                        namespace,
                        row["capture_id"],
                        relative_path,
                        byte_size,
                        modified_ns,
                    )
                pinned_rows.append(
                    (
                        queue_order,
                        namespace
                        if isinstance(namespace, str)
                        and re.fullmatch(r"[a-f0-9]{64}", namespace) is not None
                        else None,
                        version_pin,
                        row["job_id"],
                    )
                )
            self.connection.execute("DROP INDEX IF EXISTS idx_one_active_import_job")
            self.connection.execute("DROP INDEX IF EXISTS idx_import_jobs_capture")
            self.connection.execute("DROP INDEX IF EXISTS idx_import_jobs_latest")
            self.connection.execute(
                """CREATE TABLE import_jobs_v15 (
                    job_id TEXT PRIMARY KEY,
                    capture_id TEXT NOT NULL REFERENCES recording_sources(capture_id),
                    status TEXT NOT NULL CHECK (status IN (
                        'queued', 'running', 'complete', 'failed', 'interrupted', 'cancelled')),
                    phase TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count > 0),
                    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at_utc TEXT,
                    finished_at_utc TEXT,
                    result_json TEXT,
                    failure_reason TEXT,
                    queue_order INTEGER NOT NULL DEFAULT 0 CHECK (queue_order >= 0),
                    source_root_namespace TEXT CHECK (
                        source_root_namespace IS NULL OR (
                            length(source_root_namespace)=64
                            AND source_root_namespace NOT GLOB '*[^0-9a-f]*'
                        )
                    ),
                    source_metadata_version TEXT CHECK (
                        source_metadata_version IS NULL OR (
                            length(source_metadata_version)=64
                            AND source_metadata_version NOT GLOB '*[^0-9a-f]*'
                        )
                    )
                )"""
            )
            self.connection.execute(
                """INSERT INTO import_jobs_v15(
                       job_id, capture_id, status, phase, attempt_count,
                       created_at_utc, updated_at_utc, started_at_utc,
                       finished_at_utc, result_json, failure_reason,
                       queue_order, source_root_namespace, source_metadata_version)
                   SELECT job_id, capture_id, status, phase, attempt_count,
                          created_at_utc, updated_at_utc, started_at_utc,
                          finished_at_utc, result_json, failure_reason,
                          0, NULL, NULL
                     FROM import_jobs"""
            )
            self.connection.executemany(
                """UPDATE import_jobs_v15
                      SET queue_order=?, source_root_namespace=?, source_metadata_version=?
                    WHERE job_id=?""",
                pinned_rows,
            )
            self.connection.execute("DROP TABLE import_jobs")
            self.connection.execute("ALTER TABLE import_jobs_v15 RENAME TO import_jobs")
            self.connection.execute(
                "CREATE INDEX idx_import_jobs_capture ON import_jobs(capture_id, created_at_utc)"
            )
            self.connection.execute(
                "CREATE INDEX idx_import_jobs_queue ON import_jobs(status, queue_order)"
            )
            self.connection.execute(
                "CREATE UNIQUE INDEX idx_import_jobs_queue_order ON import_jobs(queue_order)"
            )
            self.connection.execute(
                "CREATE INDEX idx_import_jobs_latest ON import_jobs(capture_id, updated_at_utc DESC)"
            )
            self.connection.execute(
                "CREATE UNIQUE INDEX idx_one_running_import_job ON import_jobs((1)) WHERE status='running'"
            )
            self.connection.execute(
                "CREATE UNIQUE INDEX idx_one_active_import_per_capture ON import_jobs(capture_id) WHERE status IN ('queued', 'running')"
            )
            self.connection.execute(
                "UPDATE schema_info SET version = 15 WHERE singleton = 1"
            )
            self.connection.commit()
            version = 15
        if version != SCHEMA_VERSION:
            self.connection.close()
            raise ValueError(f"database schema {version} is not supported")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_import_jobs_latest ON import_jobs(capture_id, updated_at_utc DESC)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_import_jobs_queue ON import_jobs(status, queue_order)"
        )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_import_jobs_queue_order ON import_jobs(queue_order)"
        )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_running_import_job ON import_jobs((1)) WHERE status='running'"
        )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_import_per_capture ON import_jobs(capture_id) WHERE status IN ('queued', 'running')"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_recording_jobs_latest ON recording_jobs(updated_at_utc DESC)"
        )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_recording_group_segment ON recording_jobs(group_id, segment_ordinal) WHERE group_id IS NOT NULL"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def recording_source_metadata_version(
    namespace: str,
    capture_id: str,
    relative_path: str,
    byte_size: int,
    modified_ns: int,
) -> str:
    """Return the shared stable catalog metadata identity for one capture."""
    payload = json.dumps(
        [namespace, capture_id, relative_path, byte_size, modified_ns],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(b"recording-download-v1\0" + payload).hexdigest()
