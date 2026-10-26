from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from f1_engineer.analysis import trajectory_projection
from f1_engineer.analysis.trajectory_projection import (
    TrajectoryProjectionUnavailable,
    project_attempt_trajectory,
)
from f1_engineer.cli import build_parser
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.storage.importer import import_capture, list_laps
from f1_engineer.storage.query import AttemptTraceReadLimitError, StoredAttemptTrace
from tests.test_season_pack_2026 import (
    _MOTION_RECORD,
    _SESSION_UID,
    _lap_body,
    _motion_body,
    _packet,
    _session_body,
)


_LATERAL_SIGN = "positive_is_normal_(-tangent_z,+tangent_x)_in_world_xz"
_CONTEXT = {
    "packet_format": 2026,
    "track_id": 42,
    "track_name": "Synthetic",
    "track_length_m": 5_279,
}


def _geometry_document(
    *,
    segments: list[dict[str, object]] | None = None,
    origin: float = 5.0,
    lap_length: float = 5_279.0,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "model_id": "synthetic-reference",
        "revision": 3,
        "packet_format": 2026,
        "track_id": 42,
        "track_name": "Synthetic",
        "layout_id": "synthetic-layout",
        "lap_length_m": lap_length,
        "game_distance_origin_m": origin,
        "coordinate_frame": "world_xyz",
        "coordinate_units": "m",
        "lateral_sign_convention": _LATERAL_SIGN,
        "cyclic_seam_policy": "unsupported",
        "role": "observed_reference_path",
        "provenance": "synthetic test artifact",
        "geometry_validation": {
            "status": "unreviewed",
            "reviewer": None,
            "method": None,
            "evidence_reference": None,
        },
        "calibration_validation": {
            "status": "unreviewed",
            "reviewer": None,
            "method": None,
            "evidence_reference": None,
        },
        "segments": segments
        or [
            {
                "identifier": "straight",
                "anchors": [
                    {"distance_m": origin, "x_m": origin, "y_m": 0.0, "z_m": 0.0},
                    {
                        "distance_m": origin + lap_length,
                        "x_m": origin + lap_length,
                        "y_m": 0.0,
                        "z_m": 0.0,
                    },
                ],
            }
        ],
    }


def _write_geometry(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _sample(
    frame: int,
    distance: float,
    *,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    session_time: float | None = None,
    lap_time_ms: int | None = None,
    motion_available: bool = True,
) -> dict[str, object]:
    return {
        "frame_identifier": frame,
        "session_time_s": session_time if session_time is not None else frame / 60.0,
        "lap_distance_m": distance,
        "current_lap_time_ms": lap_time_ms if lap_time_ms is not None else frame * 16,
        "motion_available": motion_available,
        "world_position_x_m": x if x is not None else distance + 5.5,
        "world_position_y_m": y if y is not None else 1.25,
        "world_position_z_m": z if z is not None else 0.75,
    }


def _stored_attempt(
    samples: tuple[dict[str, object], ...],
    *,
    contexts: tuple[tuple[int, dict[str, object] | None], ...] | None = None,
    schema_version: int = 3,
) -> StoredAttemptTrace:
    return StoredAttemptTrace(
        attempt_key="run:42:23:1",
        run_id="run",
        session_uid=str(_SESSION_UID),
        car_index=23,
        disposition="completed",
        lap_time_ms=80_000,
        game_valid=False,
        reference_eligible=False,
        exclusion_reasons=("game_marked_invalid",),
        trace_sha256="a" * 64,
        trace_schema_version=schema_version,
        quality={},
        context_segments=contexts or ((1, _CONTEXT),),
        samples=samples,
        source_sample_count=len(samples),
    )


def _service_with_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    attempt: StoredAttemptTrace,
    document: dict[str, object] | None = None,
) -> dict[str, object]:
    geometry = _write_geometry(
        tmp_path / "geometry.json", document or _geometry_document()
    )
    monkeypatch.setattr(
        trajectory_projection,
        "load_attempt_trace",
        lambda *_args, **_kwargs: attempt,
    )
    return project_attempt_trajectory(
        tmp_path / "state.sqlite3",
        attempt.attempt_key,
        geometry,
        declared_layout_id="synthetic-layout",
    )


def _motion_at_distance(distance_m: float, *, origin_m: float, frame: int, sequence: int):
    body = bytearray(_motion_body())
    position_offset = 23 * _MOTION_RECORD.size
    struct.pack_into(
        "<3f",
        body,
        position_offset,
        distance_m + origin_m + 0.5,
        1.25,
        0.75,
    )
    return _packet(0, bytes(body), frame=frame, sequence=sequence)


def _persisted_attempt(tmp_path: Path) -> tuple[Path, str]:
    capture_path = tmp_path / "projection.f1ecap"
    database_path = tmp_path / "state.sqlite3"
    packets = [_packet(1, _session_body(), frame=1, sequence=1)]
    packets.append(
        _packet(
            2,
            _lap_body(distance_m=-1.0, driver_status=0),
            frame=10,
            sequence=10,
        )
    )
    for packet_index, (frame, distance) in enumerate(
        zip(range(11, 15), (10.0, 15.0, 20.0, 40.0)), start=11
    ):
        current_time = int(distance * 15)
        packets.append(
            _packet(
                2,
                _lap_body(distance_m=distance, current_time_ms=current_time),
                frame=frame,
                sequence=packet_index * 2,
            )
        )
        packets.append(
            _motion_at_distance(
                distance,
                origin_m=5.0,
                frame=frame,
                sequence=packet_index * 2 + 1,
            )
        )
    packets.append(
        _packet(
            2,
            _lap_body(
                lap_number=2,
                distance_m=1.0,
                current_time_ms=100,
                last_lap_time_ms=80_000,
            ),
            frame=15,
            sequence=200,
        )
    )
    with CaptureWriter(capture_path, {"fixture": "trajectory-projection"}) as writer:
        for packet in packets:
            writer.write(packet)
    result = import_capture(capture_path, database_path)
    assert result.status == "complete"
    attempt = next(
        row
        for row in list_laps(database_path, run_id=result.run_id)
        if row["disposition"] == "completed"
    )
    return database_path, attempt["attempt_key"]


def test_persisted_attempt_projection_reports_known_residuals_and_layout_provenance(
    tmp_path: Path,
) -> None:
    database_path, attempt_key = _persisted_attempt(tmp_path)
    geometry = _write_geometry(tmp_path / "geometry.json", _geometry_document())

    result = project_attempt_trajectory(
        database_path,
        attempt_key,
        geometry,
        declared_layout_id="synthetic-layout",
    )

    assert result["status"] == "projected"
    assert result["diagnostic_only"] is True
    assert result["coaching_eligible"] is False
    assert result["physical_verification_established"] is False
    assert result["layout_assertion"] == {
        "layout_id": "synthetic-layout",
        "source": "caller_declared",
        "matches_geometry_artifact": True,
        "session_context_contains_layout_id": False,
    }
    assert result["source"]["trace_sha256"]
    assert result["geometry"]["artifact_sha256"]
    coverage = result["coverage"]
    assert coverage["projected_supported_sample_count"] == 4
    assert coverage["source_sample_count"] == 4
    assert result["work"]["estimated_segment_anchor_lookup_operations"] <= 4_000_000
    first = result["runs"][0]["points"][0]
    assert first["source_anchor"]["frame_identifier"] == 11
    assert first["source_anchor"]["lap_distance_m"] == pytest.approx(10.0)
    assert first["source_anchor"]["world_position_m"] == {
        "x": pytest.approx(15.5),
        "y": pytest.approx(1.25),
        "z": pytest.approx(0.75),
    }
    assert first["geometry_distance_m"] == pytest.approx(15.0)
    assert first["longitudinal_residual_m"] == pytest.approx(0.5)
    assert first["lateral_residual_m"] == pytest.approx(0.75)
    assert first["vertical_residual_m"] == pytest.approx(1.25)


def test_persisted_projection_splits_geometry_gaps_and_cli_publishes_atomically(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database_path, attempt_key = _persisted_attempt(tmp_path)
    gap_geometry = _geometry_document(
        segments=[
            {
                "identifier": "before-gap",
                "anchors": [
                    {"distance_m": 5.0, "x_m": 5.0, "y_m": 0.0, "z_m": 0.0},
                    {"distance_m": 15.0, "x_m": 15.0, "y_m": 0.0, "z_m": 0.0},
                ],
            },
            {
                "identifier": "after-gap",
                "anchors": [
                    {"distance_m": 25.0, "x_m": 25.0, "y_m": 0.0, "z_m": 0.0},
                    {"distance_m": 5_284.0, "x_m": 5_284.0, "y_m": 0.0, "z_m": 0.0},
                ],
            },
        ]
    )
    geometry = _write_geometry(tmp_path / "gap-geometry.json", gap_geometry)
    output = tmp_path / "projection.json"
    args = build_parser().parse_args(
        [
            "project-trajectory",
            attempt_key,
            "--database",
            str(database_path),
            "--geometry",
            str(geometry),
            "--layout-id",
            "synthetic-layout",
            "--output",
            str(output),
        ]
    )

    assert args.handler(args) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "projected"
    assert result["coverage"]["projected_supported_sample_count"] == 3
    assert result["coverage"]["projection_gap_count"] == 1
    assert result["coverage"]["support_reason_counts"] == {"unsupported_distance": 1}
    assert [run["source_sample_count"] for run in result["runs"]] == [1, 2]
    assert result["runs"][1]["break_before_reasons"] == [
        "unsupported_geometry_span"
    ]
    assert result["geometry_gaps"]["examples"][0]["start_anchor"][
        "frame_identifier"
    ] == 12
    assert not list(tmp_path.glob(".projection.json.*.tmp"))


def test_curved_geometry_projection_reports_signed_local_residuals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tangent = (0.6, 0.8)
    normal = (-0.8, 0.6)
    sample = _sample(
        50,
        25.0,
        x=15.0 + tangent[0] * 2.0 + normal[0] * 3.0,
        y=4.0,
        z=20.0 + tangent[1] * 2.0 + normal[1] * 3.0,
    )
    attempt = _stored_attempt((sample,))
    curve = _geometry_document(
        origin=0.0,
        lap_length=100.0,
        segments=[
            {
                "identifier": "curve",
                "anchors": [
                    {"distance_m": 0.0, "x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
                    {"distance_m": 50.0, "x_m": 30.0, "y_m": 0.0, "z_m": 40.0},
                    {"distance_m": 100.0, "x_m": 70.0, "y_m": 0.0, "z_m": 60.0},
                ],
            }
        ],
    )
    curve["track_length_m"] = 100.0
    curve["game_distance_origin_m"] = 0.0
    context = {
        **_CONTEXT,
        "track_length_m": 100,
    }
    attempt = _stored_attempt((sample,), contexts=((1, context),))

    result = _service_with_attempt(monkeypatch, tmp_path, attempt, curve)

    point = result["runs"][0]["points"][0]
    assert point["longitudinal_residual_m"] == pytest.approx(2.0)
    assert point["lateral_residual_m"] == pytest.approx(3.0)
    assert point["vertical_residual_m"] == pytest.approx(4.0)


def test_projection_abstains_when_samples_skip_a_degenerate_anchor_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    degenerate_geometry = _geometry_document(
        origin=0.0,
        lap_length=100.0,
        segments=[
            {
                "identifier": "has-degenerate-bracket",
                "anchors": [
                    {"distance_m": 0.0, "x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
                    {"distance_m": 5.0, "x_m": 5.0, "y_m": 0.0, "z_m": 0.0},
                    {"distance_m": 10.0, "x_m": 5.0, "y_m": 0.0, "z_m": 0.0},
                    {"distance_m": 20.0, "x_m": 15.0, "y_m": 0.0, "z_m": 0.0},
                ],
            }
        ],
    )
    context = {**_CONTEXT, "track_length_m": 100}
    attempt = _stored_attempt(
        (
            _sample(10, 4.0, x=4.5, y=1.0, z=0.5),
            _sample(11, 11.0, x=11.5, y=1.0, z=0.5),
        ),
        contexts=((1, context),),
    )

    result = _service_with_attempt(
        monkeypatch, tmp_path, attempt, degenerate_geometry
    )

    assert result["status"] == "unavailable"
    assert result["reasons"] == ["geometry_anchor_interval_unusable"]
    assert result["runs"] == []
    assert result["geometry_support_preflight"][
        "unsupported_anchor_interval_count"
    ] == 1
    assert result["geometry_support_preflight"]["unsupported_intervals"] == [
        {
            "segment_id": "has-degenerate-bracket",
            "start_distance_m": 5.0,
            "end_distance_m": 10.0,
            "reason": "degenerate_horizontal_tangent",
        }
    ]


@pytest.mark.parametrize(
    ("distances", "segments"),
    [
        (
            (10.0, 20.0),
            [
                {
                    "identifier": "before-gap",
                    "anchors": [
                        {"distance_m": 5.0, "x_m": 5.0, "y_m": 0.0, "z_m": 0.0},
                        {"distance_m": 15.0, "x_m": 15.0, "y_m": 0.0, "z_m": 0.0},
                    ],
                },
                {
                    "identifier": "after-gap",
                    "anchors": [
                        {"distance_m": 25.0, "x_m": 25.0, "y_m": 0.0, "z_m": 0.0},
                        {"distance_m": 5_284.0, "x_m": 5_284.0, "y_m": 0.0, "z_m": 0.0},
                    ],
                },
            ],
        ),
        (
            (9.0, 11.0),
            [
                {
                    "identifier": "before-touching-seam",
                    "anchors": [
                        {"distance_m": 5.0, "x_m": 5.0, "y_m": 0.0, "z_m": 0.0},
                        {"distance_m": 15.0, "x_m": 15.0, "y_m": 0.0, "z_m": 0.0},
                    ],
                },
                {
                    "identifier": "after-touching-seam",
                    "anchors": [
                        {"distance_m": 15.0, "x_m": 15.0, "y_m": 0.0, "z_m": 0.0},
                        {"distance_m": 5_284.0, "x_m": 5_284.0, "y_m": 0.0, "z_m": 0.0},
                    ],
                },
            ],
        ),
    ],
    ids=("unsampled-gap", "unsampled-touching-seam"),
)
def test_projection_splits_when_supported_samples_cross_geometry_segments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    distances: tuple[float, float],
    segments: list[dict[str, object]],
) -> None:
    attempt = _stored_attempt(
        (
            _sample(10, distances[0]),
            _sample(11, distances[1]),
        )
    )
    model = _geometry_document(segments=segments)

    result = _service_with_attempt(monkeypatch, tmp_path, attempt, model)

    assert result["coverage"]["projected_supported_sample_count"] == 2
    assert result["coverage"]["geometry_segment_transition_count"] == 1
    assert len(result["runs"]) == 2
    assert result["runs"][1]["break_before_reasons"] == [
        "geometry_segment_boundary"
    ]
    assert result["geometry_boundaries"]["examples"][0]["reason"] == (
        "geometry_segment_transition_between_source_samples"
    )
    assert result["coverage"]["projected_unsupported_sample_count"] == 0


def test_projection_thins_after_full_source_analysis_and_preserves_frame_wrap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    samples = tuple(
        _sample(
            (0xFFFFFFFF + index) & 0xFFFFFFFF,
            float(index + 1),
            x=float(index + 1) + 5.5,
            session_time=10.0 + index / 60.0,
            lap_time_ms=100 + index * 16,
        )
        for index in range(2_101)
    )
    context = {**_CONTEXT, "track_length_m": 5_279}
    attempt = _stored_attempt(samples, contexts=((1, context),))

    result = _service_with_attempt(monkeypatch, tmp_path, attempt)

    assert result["coverage"]["projected_supported_sample_count"] == 2_101
    assert result["coverage"]["rendered_point_count"] == 2_000
    points = result["runs"][0]["points"]
    assert points[0]["source_anchor"]["frame_identifier"] == 0xFFFFFFFF
    assert points[-1]["source_anchor"]["frame_identifier"] == (0xFFFFFFFF + 2_100) & 0xFFFFFFFF
    assert result["source_continuity"]["policy"]["frame_delta"] == (
        "exactly one, with uint32 wrap allowed"
    )


def test_projection_rejects_changing_context_bad_layout_and_excess_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sample = _sample(10, 10.0)
    changed_context = _stored_attempt(
        (sample,),
        contexts=(
            (1, _CONTEXT),
            (2, {**_CONTEXT, "track_id": 43}),
        ),
    )
    changed = _service_with_attempt(monkeypatch, tmp_path, changed_context)
    assert changed["status"] == "unavailable"
    assert changed["reasons"] == ["session_track_identity_changed"]

    geometry = _write_geometry(tmp_path / "wrong-layout.json", _geometry_document())
    with pytest.raises(TrajectoryProjectionUnavailable, match="layout_assertion_mismatch"):
        project_attempt_trajectory(
            tmp_path / "state.sqlite3",
            "run:42:23:1",
            geometry,
            declared_layout_id="wrong-layout",
        )

    monkeypatch.setattr(trajectory_projection, "TRAJECTORY_PROJECTION_WORK_LIMIT", 2)
    over_budget = _service_with_attempt(
        monkeypatch, tmp_path, _stored_attempt((sample,))
    )
    assert over_budget["status"] == "unavailable"
    assert over_budget["reasons"] == ["projection_work_limit_exceeded"]


def test_projection_handles_legacy_missing_motion_and_source_read_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    legacy_sample = _sample(10, 10.0, motion_available=False)
    legacy = _service_with_attempt(
        monkeypatch,
        tmp_path,
        _stored_attempt((legacy_sample,), schema_version=1),
    )
    assert legacy["status"] == "unavailable"
    assert legacy["coverage"]["source_sample_count"] == 1
    assert legacy["coverage"]["projected_sample_coverage"] == 0.0

    def oversized(*_args, **_kwargs):
        raise AttemptTraceReadLimitError("rows")

    monkeypatch.setattr(trajectory_projection, "load_attempt_trace", oversized)
    geometry = _write_geometry(tmp_path / "geometry-limits.json", _geometry_document())
    with pytest.raises(
        TrajectoryProjectionUnavailable,
        match="trajectory_projection_source_rows_limit_exceeded",
    ):
        project_attempt_trajectory(
            tmp_path / "state.sqlite3",
            "run:42:23:1",
            geometry,
            declared_layout_id="synthetic-layout",
        )
