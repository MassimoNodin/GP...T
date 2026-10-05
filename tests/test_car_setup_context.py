from __future__ import annotations

import json

import pytest

from f1_engineer.sessions.setup_context import PlayerCarSetupObservation
from f1_engineer.storage import importer as importer_module
from f1_engineer.storage.importer import _PlayerCarSetupObservationWriter
from f1_engineer.storage.car_setup_context import (
    MAX_PLAYER_CAR_SETUP_ATTEMPT_OBSERVATIONS,
    load_attempt_player_car_setup_context,
)
from f1_engineer.storage.database import Database


ATTEMPT_KEY = "setup-attempt"
RUN_ID = "b" * 64
SESSION_KEY = f"{RUN_ID}:42"
ATTEMPT_SCOPE = {
    "start_association_packet_format": 2025,
    "association_packet_format": 2025,
    "start_association_epoch": 0,
    "association_epoch": 0,
    "association_scope_assessable": True,
}


def _seed(database_path, *, end_ordinal: int | None = 30) -> None:
    capture_sha = "a" * 64
    metrics = {
        "capture_quality": {
            "player_car_setup_observation_run_truncated": False,
        }
    }
    with Database(database_path) as db, db.connection:
        db.connection.execute(
            """INSERT INTO captures(capture_sha256,source_path,byte_size,complete,
                      completion_json,metadata_json) VALUES (?,?,?,?,?,?)""",
            (capture_sha, "capture.f1ecap", 1, 1, "{}", "{}"),
        )
        db.connection.execute(
            """INSERT INTO processing_runs(run_id,capture_sha256,pipeline_version,
                      config_json,status,metrics_json) VALUES (?,?,?,?,?,?)""",
            (RUN_ID, capture_sha, "test-pipeline", "{}", "complete", json.dumps(metrics)),
        )
        db.connection.execute(
            """INSERT INTO sessions(session_key,run_id,session_uid,packet_format,context_json)
                 VALUES (?,?,?,?,?)""",
            (SESSION_KEY, RUN_ID, "42", 2025, "{}"),
        )
        db.connection.execute(
            """INSERT INTO lap_attempts(attempt_key,session_key,car_index,attempt_number,
                      lap_number,disposition,start_frame_identifier,end_frame_identifier,
                      start_session_time_s,end_session_time_s,lap_time_ms,game_valid,
                      start_observed,pit_encountered,sample_count,reference_eligible,
                      exclusion_reasons_json,attempt_json,start_frame_ordinal,
                      end_frame_ordinal,superseded,lifecycle_assessed)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ATTEMPT_KEY,
                SESSION_KEY,
                3,
                1,
                1,
                "complete",
                10,
                end_ordinal,
                1.0,
                3.0,
                120_000,
                1,
                1,
                0,
                120,
                0,
                "[]",
                json.dumps(ATTEMPT_SCOPE),
                10,
                end_ordinal,
                None,
                1,
            ),
        )


def _insert_observation(
    connection,
    ordinal: int,
    *,
    front_wing: int = 45,
    fuel_load: float | None = 25.5,
    next_wing: float | None = 12.5,
    status: str = "observed",
    reason: str | None = None,
) -> None:
    setup = {
        "front_wing": front_wing,
        "rear_wing": 38,
        "on_throttle_differential": 55,
        "off_throttle_differential": 30,
        "front_camber": -3.5,
        "rear_camber": -2.5,
        "front_toe": 0.1,
        "rear_toe": 0.2,
        "front_suspension": 4,
        "rear_suspension": 5,
        "front_anti_roll_bar": 6,
        "rear_anti_roll_bar": 7,
        "front_suspension_height": 8,
        "rear_suspension_height": 9,
        "brake_pressure_percent": 95,
        "brake_bias_percent": 55,
        "engine_braking_percent": 10,
        "rear_left_tyre_pressure_psi": 22.0,
        "rear_right_tyre_pressure_psi": 23.0,
        "front_left_tyre_pressure_psi": 24.0,
        "front_right_tyre_pressure_psi": 25.0,
        "ballast": 50,
        "fuel_load": fuel_load,
        "invalid_fields": [],
    }
    connection.execute(
        """INSERT INTO player_car_setup_observations(
                   run_id,session_uid,frame_ordinal,frame_identifier,
                   overall_frame_identifier,packet_format,association_epoch,
                   association_scope_assessable,player_car_index,session_time_s,
                   status,reason,setup_json,next_front_wing_value,source_packet_count)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            RUN_ID,
            "42",
            ordinal,
            ordinal & 0xFFFF_FFFF,
            ordinal,
            2025,
            0,
            1,
            3,
            ordinal / 60,
            status,
            reason,
            json.dumps(setup) if status == "observed" else None,
            next_wing if status == "observed" else None,
            1,
        ),
    )


def test_attempt_context_uses_last_pre_start_snapshot_and_separates_requested_wing(tmp_path):
    database_path = tmp_path / "setups.sqlite3"
    _seed(database_path)
    with Database(database_path) as db, db.connection:
        _insert_observation(db.connection, 9, front_wing=45, fuel_load=25.5, next_wing=12.5)
        _insert_observation(db.connection, 20, front_wing=50, fuel_load=30.0, next_wing=13.5)
        _insert_observation(db.connection, 25, front_wing=50, fuel_load=30.0, next_wing=13.5)
        report = load_attempt_player_car_setup_context(db.connection, ATTEMPT_KEY)

    assert report is not None
    assert report["status"] == "observed_changed"
    assert report["continuity_claim"] is False
    at_start = report["at_start"]
    assert at_start["status"] == "reported"
    assert at_start["setup"]["front_wing"] == 45
    assert at_start["setup"]["fuel_load"] == 25.5
    assert at_start["next_front_wing_value"] == 12.5
    assert at_start["source"]["age_frames"] == 1
    assert report["observed_change_count"] == 1
    assert report["observations"][0]["setup"]["front_wing"] == 50
    assert "next_front_wing_value" not in report["at_start"]["setup"]


def test_first_mid_attempt_setup_is_not_backfilled_to_attempt_start(tmp_path):
    database_path = tmp_path / "setups.sqlite3"
    _seed(database_path)
    with Database(database_path) as db, db.connection:
        _insert_observation(db.connection, 20, front_wing=50)
        report = load_attempt_player_car_setup_context(db.connection, ATTEMPT_KEY)

    assert report is not None
    assert report["status"] == "unknown"
    assert report["at_start"]["status"] == "unknown"
    assert report["at_start"]["reason"] == "setup_not_reported_before_attempt_start"
    assert report["observations"][0]["setup"]["front_wing"] == 50


@pytest.mark.parametrize("digits", (401, 5_000))
def test_unrepresentable_json_setup_float_is_unavailable_not_a_query_error(
    tmp_path, digits: int
):
    database_path = tmp_path / "invalid-setup-number.sqlite3"
    _seed(database_path)
    with Database(database_path) as db, db.connection:
        _insert_observation(db.connection, 9)
        row = db.connection.execute(
            "SELECT setup_json FROM player_car_setup_observations WHERE frame_ordinal=9"
        ).fetchone()
        setup_json = json.loads(row["setup_json"])
        setup_json["front_camber"] = 0
        encoded = json.dumps(setup_json).replace(
            '"front_camber": 0',
            '"front_camber": ' + ("9" * digits),
        )
        db.connection.execute(
            "UPDATE player_car_setup_observations SET setup_json=? WHERE frame_ordinal=9",
            (encoded,),
        )
        report = load_attempt_player_car_setup_context(db.connection, ATTEMPT_KEY)

    assert report is not None
    assert report["status"] == "incomplete"
    assert report["at_start"]["status"] == "unknown"
    assert report["at_start"]["reason"] == "setup_payload_unavailable"


@pytest.mark.parametrize(
    ("event_count", "truncated"),
    (
        (MAX_PLAYER_CAR_SETUP_ATTEMPT_OBSERVATIONS, False),
        (MAX_PLAYER_CAR_SETUP_ATTEMPT_OBSERVATIONS + 1, True),
    ),
)
def test_attempt_setup_context_is_bounded_and_truncation_is_visible(
    tmp_path, event_count: int, truncated: bool
):
    database_path = tmp_path / "setups.sqlite3"
    _seed(database_path, end_ordinal=300)
    with Database(database_path) as db, db.connection:
        for ordinal in range(11, 11 + event_count):
            _insert_observation(db.connection, ordinal, front_wing=ordinal % 100)
        report = load_attempt_player_car_setup_context(db.connection, ATTEMPT_KEY)

    assert report is not None
    assert (report["status"] == "incomplete") is truncated
    assert ("setup_observation_history_truncated" in report.get("reasons", [])) is truncated
    assert report["observation_count"] == MAX_PLAYER_CAR_SETUP_ATTEMPT_OBSERVATIONS
    assert len(report["observations"]) <= 16
    assert report["observations_omitted_count"] > 0


class _RecordingConnection:
    def __init__(self) -> None:
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def executemany(self, _sql, rows) -> None:
        self.rows.extend(rows)


def test_setup_observation_writer_bounds_rows_and_emits_truncation_fence(monkeypatch):
    monkeypatch.setattr(
        importer_module, "MAX_STORED_PLAYER_CAR_SETUP_OBSERVATIONS", 2
    )
    monkeypatch.setattr(
        importer_module, "MAX_STORED_PLAYER_CAR_SETUP_TRUNCATION_FENCES", 1
    )
    connection = _RecordingConnection()
    writer = _PlayerCarSetupObservationWriter(connection, RUN_ID)
    observations = tuple(
        PlayerCarSetupObservation(
            session_uid=session_uid,
            frame_ordinal=1,
            frame_identifier=1,
            overall_frame_identifier=1,
            packet_format=2025,
            association_epoch=0,
            association_scope_assessable=True,
            player_car_index=3,
            session_time_s=1.0,
            status="unavailable",
            reason="car_setup_packet_unavailable",
            setup=None,
            next_front_wing_value=None,
            source_packet_count=0,
        )
        for session_uid in (11, 12, 13, 14, 14)
    )

    writer.consume(observations)
    writer.flush()

    assert writer.stored_count == 2
    assert len(writer.truncated_session_uids) == 1
    assert writer.truncation_marker_overflowed is True
    assert len(connection.rows) == 3
    assert [row[10] for row in connection.rows].count("truncated") == 1


def test_schema_11_migrates_player_car_setup_observation_storage(tmp_path):
    database_path = tmp_path / "migration.sqlite3"
    with Database(database_path) as db, db.connection:
        db.connection.execute("DROP TABLE player_car_setup_observations")
        db.connection.execute("UPDATE schema_info SET version = 11 WHERE singleton = 1")

    with Database(database_path) as db:
        version = db.connection.execute(
            "SELECT version FROM schema_info WHERE singleton = 1"
        ).fetchone()[0]
        table = db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='player_car_setup_observations'"
        ).fetchone()

    assert version == 12
    assert table is not None
