from __future__ import annotations

import asyncio
import json

import pytest

from f1_engineer.analysis import quality as quality_module
from f1_engineer.api import app as api_module
from f1_engineer.api.app import create_app
from f1_engineer.storage.query import StoredAttemptTrace, StoredReferenceInventory


def _attempt(
    *,
    schema_version: int = 2,
    disposition: str = "partial",
    game_valid: bool | None = None,
    mode: str | None = "race",
    frames: tuple[int, ...] = (1, 3, 4, 5),
    distances: tuple[float | None, ...] = (10.0, 20.0, 30.0, 40.0),
    session_times: tuple[float, ...] | None = None,
) -> StoredAttemptTrace:
    samples = tuple(
        {
            "frame_identifier": frame,
            "session_time_s": session_times[index] if session_times is not None else index * 0.1,
            "lap_distance_m": distance,
            "current_lap_time_ms": index * 100,
            "speed_mps": 20.0 + index,
            "throttle": 0.5,
            "brake": 0.0,
            "steering": 0.1,
            "gear": 3,
            "drs_active": False,
            "car_telemetry_available": True,
            "motion_available": True if schema_version > 1 else None,
            "world_position_x_m": float(index) if schema_version > 1 else None,
            "world_position_y_m": 0.0 if schema_version > 1 else None,
            "world_position_z_m": 0.0 if schema_version > 1 else None,
            "world_velocity_x_mps": 20.0 if schema_version > 1 else None,
            "world_velocity_y_mps": 0.0 if schema_version > 1 else None,
            "world_velocity_z_mps": 0.0 if schema_version > 1 else None,
        }
        for index, (frame, distance) in enumerate(zip(frames, distances))
    )
    context = {
        "track_name": "Melbourne",
        "track_length_m": 100.0,
        "session_type": mode,
        "game_mode": mode,
    }
    return StoredAttemptTrace(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="42",
        car_index=0,
        disposition=disposition,
        lap_time_ms=None,
        game_valid=game_valid,
        reference_eligible=False,
        exclusion_reasons=("lap_not_completed",) if disposition != "completed" else (),
        trace_sha256="a" * 64,
        trace_schema_version=schema_version,
        quality={"sample_count": len(samples), "largest_frame_gap": 1},
        context_segments=((frames[0], context),),
        samples=samples,
        attempt_number=1,
        start_observed=True,
        pit_encountered=False,
    )


def _inventory(
    attempt: StoredAttemptTrace, completion: object = None
) -> StoredReferenceInventory:
    return StoredReferenceInventory(
        target_attempt_key=attempt.attempt_key,
        run_id=attempt.run_id,
        session_uid=attempt.session_uid,
        car_index=attempt.car_index,
        target_attempt_number=attempt.attempt_number,
        capture_complete=False,
        capture_completion=completion,
        processing_quality={
            "import_late_packets_ignored": 0,
            "import_frame_overflow_packets_dropped": 0,
            "missing_car_telemetry_lap_sample_count": 0,
        },
        attempts=(),
    )


def _report(
    monkeypatch,
    attempt: StoredAttemptTrace,
    *,
    completion: object = None,
) -> dict[str, object]:
    monkeypatch.setattr(
        quality_module,
        "load_attempt_trace",
        lambda *_args, **_kwargs: attempt,
    )
    monkeypatch.setattr(
        quality_module,
        "load_reference_inventory",
        lambda *_args, **_kwargs: _inventory(attempt, completion),
    )
    result = quality_module.inspect_attempt_quality("unused.sqlite3", attempt.attempt_key)
    assert result is not None
    return result


def test_quality_is_reference_independent_for_invalid_partial_race_attempt(
    monkeypatch,
) -> None:
    report = _report(monkeypatch, _attempt(game_valid=False))

    assert report["attempt"]["disposition"] == "partial"
    assert report["attempt"]["game_valid"] is False
    assert report["attempt"]["reference_eligible"] is False
    assert report["context"]["game_modes"] == ["race"]
    assert report["identity"]["trace_checksum_verified"] is True
    assert report["continuity"]["frame_gaps"]["count"] == 1
    assert report["distance_support"]["channels"]["speed_mps"]["observed_range_coverage"] == 1.0
    assert report["distance_support"]["channels"]["speed_mps"]["full_track_coverage"] == pytest.approx(31 / 101)
    assert report["evidence"]["recording"]["counters"]["queue_dropped"] is None


def test_quality_keeps_unknown_track_length_and_schema_v1_motion_unavailable(
    monkeypatch,
) -> None:
    attempt = _attempt(schema_version=1, mode=None)
    attempt = StoredAttemptTrace(
        **{
            **{name: getattr(attempt, name) for name in attempt.__dataclass_fields__},
            "context_segments": ((1, {"game_mode": None, "session_type": None}),),
        }
    )
    report = _report(monkeypatch, attempt)

    assert report["context"]["game_modes"] == []
    assert report["channels"]["motion_available"]["status"] == "unavailable_in_trace_schema"
    assert report["channels"]["motion_available"]["missing_samples"] is None
    assert report["observed_status"]["status"] == "unavailable_in_trace_schema"
    assert report["observed_status"]["matched_sample_count"] is None
    assert report["distance_support"]["track_length_m"] is None
    assert report["distance_support"]["channels"]["speed_mps"]["full_track_coverage"] is None


def test_quality_reports_frame_order_and_distance_regressions_without_resampling(
    monkeypatch,
) -> None:
    report = _report(
        monkeypatch,
        _attempt(
            frames=(1, 3, 2, 4),
            distances=(10.0, 20.0, 15.0, 25.0),
        ),
    )

    assert report["continuity"]["frame_gaps"]["count"] == 2
    assert report["continuity"]["frame_gaps"]["order_discontinuity_count"] == 1
    assert report["continuity"]["lap_distance"]["regression_count"] == 1
    assert report["distance_support"]["status"] == "unavailable"
    assert report["distance_support"]["reason"] == "trace frame identifiers are not in chronological order"


def test_quality_uses_float32_tolerance_at_session_time_gap_boundary(monkeypatch) -> None:
    report = _report(
        monkeypatch,
        _attempt(
            session_times=(10.0, 10.100000381469727, 10.2, 10.3),
        ),
    )

    assert report["continuity"]["session_time"]["gap_count"] == 0
    assert report["continuity"]["session_time"]["regression_count"] == 0


def test_quality_sanitizes_malformed_footer_evidence(monkeypatch) -> None:
    report = _report(
        monkeypatch,
        _attempt(),
        completion={
            "status": 42,
            "queue_dropped": float("nan"),
            "unpersisted_on_shutdown": -1,
            "late_packets_ignored": 0,
            "frame_overflow_packets_dropped": 2,
        },
    )

    recording = report["evidence"]["recording"]
    observer = report["evidence"]["recording_observer"]
    assert recording["footer_status"] is None
    assert recording["footer_status_evidence"] == "invalid"
    assert recording["counter_evidence"]["queue_dropped"] == "invalid"
    assert recording["counters"]["queue_dropped"] is None
    assert recording["counter_evidence"]["unpersisted_on_shutdown"] == "invalid"
    assert observer["available"] is True
    assert observer["counters"]["late_packets_ignored"] == 0
    json.dumps(report, allow_nan=False)


def test_quality_handles_non_object_footer_as_invalid_shape(monkeypatch) -> None:
    report = _report(monkeypatch, _attempt(), completion=["not a footer object"])

    recording = report["evidence"]["recording"]
    assert recording["footer_available"] is False
    assert recording["footer_evidence_status"] == "invalid_shape"


def test_quality_reports_exact_frame_status_counts_changes_and_compound_labels(
    monkeypatch,
) -> None:
    attempt = _attempt(schema_version=3, mode="race")
    status_samples = []
    for index, sample in enumerate(attempt.samples):
        status_samples.append({
            **sample,
            "car_status_available": True,
            "car_status_unavailable_reason": None,
            "validation_flags": [],
            "traction_control": 1 if index < 2 else 2,
            "anti_lock_brakes": False,
            "fuel_mix": 2,
            "front_brake_bias_percent": 54,
            "pit_limiter_active": False,
            "fuel_in_tank_reported": 12.0 - index,
            "fuel_capacity_reported": 100.0,
            "fuel_remaining_laps": -1.0,
            "actual_tyre_compound": 20,
            "visual_tyre_compound": 17,
            "tyre_age_laps": 3,
            "drs_allowed": True,
            "drs_activation_distance_m": 0,
            "vehicle_fia_flag": -1,
            "network_paused": False,
        })
    attempt = StoredAttemptTrace(
        **{
            **{name: getattr(attempt, name) for name in attempt.__dataclass_fields__},
            "samples": tuple(status_samples),
            "context_segments": ((1, {"formula_id": 0, "game_mode": "race"}),),
        }
    )
    report = _report(monkeypatch, attempt)
    status = report["observed_status"]

    assert report["attempt"]["reference_eligible"] is False
    assert status["matched_sample_count"] == 4
    assert status["missing_join_sample_count"] == 0
    assert status["fields"]["fuel_remaining_laps"]["valid_count"] == 4
    assert status["fields"]["fuel_remaining_laps"]["invalid_count"] == 0
    assert status["first_last_observed"]["fuel_in_tank_reported"]["first"]["frame_identifier"] == 1
    assert status["discrete_changes"] == [{
        "field": "traction_control",
        "from_frame_identifier": 3,
        "to_frame_identifier": 4,
        "from_value": 1,
        "to_value": 2,
    }]
    assert status["distinct_compounds"]["actual"] == [
        {"raw_id": 20, "formula_id": 0, "label": "C1"}
    ]
    assert status["fuel_quantity_unit_note"] == "reported quantity; unit unspecified"


def test_quality_resolves_compounds_across_uint32_frame_wrap(monkeypatch) -> None:
    attempt = _attempt(
        schema_version=3,
        mode="race",
        frames=(0xFFFFFFFE, 0xFFFFFFFF, 0, 1),
        distances=(10.0, 20.0, 30.0, 40.0),
    )
    samples = tuple({
        **sample,
        "car_status_available": True,
        "car_status_unavailable_reason": None,
        "validation_flags": [],
        "actual_tyre_compound": 20,
        "visual_tyre_compound": 17,
    } for sample in attempt.samples)
    attempt = StoredAttemptTrace(
        **{
            **{name: getattr(attempt, name) for name in attempt.__dataclass_fields__},
            "samples": samples,
            "context_segments": (
                (0xFFFFFFFE, {"formula_id": 0}),
                (0, {"formula_id": 2}),
            ),
        }
    )

    status = _report(monkeypatch, attempt)["observed_status"]

    assert status["distinct_compounds"]["actual"] == [
        {"raw_id": 20, "formula_id": 0, "label": "C1"},
        {"raw_id": 20, "formula_id": 2, "label": None},
    ]
    assert status["distinct_compounds"]["visual"] == [
        {"raw_id": 17, "formula_id": 0, "label": "medium"},
        {"raw_id": 17, "formula_id": 2, "label": None},
    ]


def test_quality_caps_discrete_changes_in_chronological_order(monkeypatch) -> None:
    attempt = _attempt(schema_version=3, frames=(1, 2, 3, 4))
    fields = (
        "traction_control",
        "anti_lock_brakes",
        "fuel_mix",
        "front_brake_bias_percent",
        "pit_limiter_active",
        "actual_tyre_compound",
        "visual_tyre_compound",
        "tyre_age_laps",
        "drs_allowed",
        "drs_activation_distance_m",
        "vehicle_fia_flag",
        "network_paused",
    )
    low = (0, False, 0, 50, False, 20, 17, 1, False, 0, 0, False)
    high = (2, True, 3, 51, True, 21, 18, 2, True, 50, 1, True)
    samples = tuple({
        **sample,
        "car_status_available": True,
        "validation_flags": [],
        **dict(zip(fields, low if index % 2 == 0 else high)),
    } for index, sample in enumerate(attempt.samples))
    attempt = StoredAttemptTrace(
        **{
            **{name: getattr(attempt, name) for name in attempt.__dataclass_fields__},
            "samples": samples,
        }
    )

    status = _report(monkeypatch, attempt)["observed_status"]
    changes = status["discrete_changes"]

    assert len(changes) == 20
    assert status["discrete_changes_truncated"] is True
    assert [change["to_frame_identifier"] for change in changes[:12]] == [2] * 12
    assert [change["to_frame_identifier"] for change in changes[12:]] == [3] * 8


def test_quality_api_returns_versioned_report_without_reference_selection(
    monkeypatch, tmp_path
) -> None:
    report = {"report_version": 1, "identity": {"session_uid": 42}}
    monkeypatch.setattr(api_module, "inspect_attempt_quality", lambda *_args: report)
    app = create_app(tmp_path / "unused.sqlite3")

    async def request():
        import httpx

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/v1/attempts/run:42:0:1/quality")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json()["data"]["report_version"] == 1
    assert response.json()["data"]["identity"]["session_uid"] == "42"
