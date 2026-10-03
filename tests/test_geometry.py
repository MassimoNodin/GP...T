from __future__ import annotations

import json
from pathlib import Path

import pytest

from f1_engineer.cli import _geometry_validate, build_parser
from f1_engineer.tracks.geometry import (
    MAX_GEOMETRY_ANCHORS,
    MAX_GEOMETRY_MODEL_BYTES,
    GeometryAnchor,
    GeometryModel,
    GeometrySegment,
    GeometryValidationEvidence,
    project_sample,
)
from f1_engineer.tracks.geometry_loader import load_geometry_model


_SIGN = "positive_is_normal_(-tangent_z,+tangent_x)_in_world_xz"


def _evidence(status: str = "unreviewed") -> GeometryValidationEvidence:
    if status == "validated":
        return GeometryValidationEvidence(
            status="validated",
            reviewer="test reviewer",
            method="independent review",
            evidence_reference="test record",
        )
    return GeometryValidationEvidence(
        status="unreviewed", reviewer=None, method=None, evidence_reference=None
    )


def _model(
    segments: tuple[GeometrySegment, ...] | None = None,
    *,
    origin: float = 0,
    role: str = "observed_reference_path",
    geometry_validation: GeometryValidationEvidence | None = None,
    calibration_validation: GeometryValidationEvidence | None = None,
    cyclic_seam_policy: str = "unsupported",
    lap_length: float = 100,
) -> GeometryModel:
    if segments is None:
        start_distance = origin
        end_distance = origin + lap_length
        segments = (
            GeometrySegment(
                "whole-lap",
                (
                    GeometryAnchor(start_distance, start_distance, 1, 0),
                    GeometryAnchor(end_distance, end_distance, 1, 0),
                ),
            ),
        )
    return GeometryModel(
        model_id="test-geometry",
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Test",
        layout_id="test-layout",
        lap_length_m=lap_length,
        game_distance_origin_m=origin,
        coordinate_frame="world_xyz",
        coordinate_units="m",
        lateral_sign_convention=_SIGN,
        cyclic_seam_policy=cyclic_seam_policy,
        role=role,
        provenance="synthetic geometry fixture",
        geometry_validation=geometry_validation or _evidence(),
        calibration_validation=calibration_validation or _evidence(),
        segments=segments,
    )


def _project(
    model: GeometryModel,
    *,
    distance: float | None,
    x: float | None,
    y: float | None = 1,
    z: float | None = 0,
    packet_format: int = 2025,
    track_id: int = 0,
    layout_id: str = "test-layout",
) -> dict[str, object]:
    return project_sample(
        model,
        packet_format=packet_format,
        track_id=track_id,
        layout_id=layout_id,
        game_distance_m=distance,
        world_x_m=x,
        world_y_m=y,
        world_z_m=z,
    )


def test_straight_projection_reports_known_lateral_longitudinal_and_vertical_residuals() -> None:
    projected = _project(_model(), distance=10, x=12, y=5, z=3)

    assert projected["status"] == "supported"
    assert projected["geometry_evidence_status"] == "diagnostic_observed_reference_path"
    assert projected["lap_reference_eligibility_evaluated"] is False
    assert projected["longitudinal_residual_m"] == pytest.approx(2)
    assert projected["lateral_residual_m"] == pytest.approx(3)
    assert projected["vertical_residual_m"] == pytest.approx(4)
    assert projected["reference_position_m"] == {"x": 10, "y": 1, "z": 0}


def test_curved_path_uses_the_local_tangent_and_documented_lateral_sign() -> None:
    curved = _model(
        (
            GeometrySegment(
                "curve",
                (
                    GeometryAnchor(0, 0, 0, 0),
                    GeometryAnchor(50, 50, 0, 0),
                    GeometryAnchor(100, 50, 0, 50),
                ),
            ),
        )
    )

    before_turn = _project(curved, distance=25, x=25, y=0, z=2)
    after_turn = _project(curved, distance=75, x=48, y=0, z=25)

    assert before_turn["lateral_residual_m"] == pytest.approx(2)
    assert before_turn["longitudinal_residual_m"] == pytest.approx(0)
    assert after_turn["lateral_residual_m"] == pytest.approx(2)
    assert after_turn["longitudinal_residual_m"] == pytest.approx(0)


def test_closed_path_supports_both_ends_without_interpolating_across_the_lap_seam() -> None:
    closed = _model(
        (
            GeometrySegment(
                "closed",
                (
                    GeometryAnchor(0, 0, 0, 0),
                    GeometryAnchor(25, 10, 0, 0),
                    GeometryAnchor(50, 20, 0, 10),
                    GeometryAnchor(75, 10, 0, 20),
                    GeometryAnchor(90, -10, 0, 0),
                    GeometryAnchor(100, 0, 0, 0),
                ),
            ),
        ),
        cyclic_seam_policy="closed",
    )

    at_start = _project(closed, distance=0, x=0, y=0, z=0)
    near_finish = _project(closed, distance=99, x=-1, y=0, z=0)
    lap_length_wrap = _project(closed, distance=100, x=0, y=0, z=0)

    assert at_start["status"] == "supported"
    assert near_finish["status"] == "supported"
    assert lap_length_wrap["status"] == "supported"
    assert lap_length_wrap["geometry_distance_m"] == 100


def test_game_distance_origin_shifts_lookup_distance_without_aliasing_the_lap_end() -> None:
    rotated = _model(origin=10)

    projected = _project(rotated, distance=5, x=15)
    lap_end = _project(rotated, distance=90, x=100)
    unsupported_alias = _project(rotated, distance=100, x=110)

    assert projected["geometry_distance_m"] == pytest.approx(15)
    assert projected["reference_position_m"]["x"] == pytest.approx(15)
    assert projected["longitudinal_residual_m"] == pytest.approx(0)
    assert lap_end["geometry_distance_m"] == 100
    assert lap_end["status"] == "supported"
    assert unsupported_alias["reason"] == "unsupported_lap_seam"


def test_gaps_seams_and_extrapolation_return_explicit_unavailable_reasons() -> None:
    segmented = _model(
        (
            GeometrySegment(
                "first",
                (GeometryAnchor(0, 0, 0, 0), GeometryAnchor(10, 10, 0, 0)),
            ),
            GeometrySegment(
                "second",
                (GeometryAnchor(20, 20, 0, 0), GeometryAnchor(30, 30, 0, 0)),
            ),
            GeometrySegment(
                "third",
                (GeometryAnchor(30, 30, 0, 0), GeometryAnchor(40, 40, 0, 0)),
            ),
        ),
        lap_length=50,
    )

    gap = _project(segmented, distance=15, x=15)
    seam = _project(segmented, distance=30, x=30)
    extrapolated = _project(segmented, distance=45, x=45)

    assert gap["reason"] == "unsupported_distance"
    assert seam["reason"] == "unsupported_segment_seam"
    assert extrapolated["reason"] == "unsupported_distance"


def test_unsupported_lap_seam_never_aliases_distance_length_to_zero() -> None:
    open_path = _model()
    end_only = _model(
        (
            GeometrySegment(
                "end-only",
                (GeometryAnchor(90, 90, 0, 0), GeometryAnchor(100, 100, 0, 0)),
            ),
        )
    )

    start = _project(open_path, distance=0, x=0)
    end = _project(open_path, distance=100, x=100)
    near_end = _project(end_only, distance=99, x=99)
    ambiguous_end = _project(end_only, distance=100, x=100)

    assert start["reason"] == "unsupported_lap_seam"
    assert end["reason"] == "unsupported_lap_seam"
    assert near_end["status"] == "supported"
    assert ambiguous_end["reason"] == "unsupported_lap_seam"


def test_wrong_format_track_or_layout_is_unavailable() -> None:
    model = _model()

    assert _project(model, distance=1, x=1, packet_format=2026)["reason"] == (
        "packet_format_mismatch"
    )
    assert _project(model, distance=1, x=1, track_id=1)["reason"] == "track_id_mismatch"
    assert _project(model, distance=1, x=1, layout_id="other")["reason"] == (
        "layout_mismatch"
    )


@pytest.mark.parametrize(
    ("distance", "x", "y", "z", "reason"),
    [
        (float("nan"), 0, 0, 0, "non_finite_sample"),
        (101, 0, 0, 0, "game_distance_out_of_range"),
        (1, float("inf"), 0, 0, "non_finite_sample"),
    ],
)
def test_non_finite_or_out_of_range_sample_is_unavailable(
    distance: float, x: float, y: float, z: float, reason: str
) -> None:
    projected = _project(_model(), distance=distance, x=x, y=y, z=z)

    assert projected["status"] == "unavailable"
    assert projected["reason"] == reason


@pytest.mark.parametrize(
    ("distance", "x", "y", "z", "reason"),
    [
        (None, 0, 0, 0, "game_distance_unavailable"),
        (1, None, 0, 0, "position_unavailable"),
        (1, 0, None, 0, "position_unavailable"),
        (1, 0, 0, None, "position_unavailable"),
    ],
)
def test_missing_legacy_or_motion_values_are_explicitly_unavailable(
    distance: float | None,
    x: float | None,
    y: float | None,
    z: float | None,
    reason: str,
) -> None:
    projected = _project(_model(), distance=distance, x=x, y=y, z=z)

    assert projected["status"] == "unavailable"
    assert projected["reason"] == reason


def test_degenerate_horizontal_tangent_is_unavailable() -> None:
    vertical = _model(
        (
            GeometrySegment(
                "vertical-only",
                (GeometryAnchor(0, 1, 0, 1), GeometryAnchor(100, 1, 3, 1)),
            ),
        )
    )

    projected = _project(vertical, distance=50, x=1, y=1, z=1)

    assert projected["status"] == "unavailable"
    assert projected["reason"] == "degenerate_horizontal_tangent"


@pytest.mark.parametrize(
    ("anchors", "x", "reason"),
    [
        (
            (GeometryAnchor(0, -1e308, 0, 0), GeometryAnchor(100, 1e308, 0, 0)),
            0,
            "non_finite_geometry",
        ),
        (
            (GeometryAnchor(0, -1e308, 0, 0), GeometryAnchor(100, -1e308, 0, 100)),
            1e308,
            "non_finite_projection",
        ),
    ],
)
def test_finite_extreme_values_cannot_produce_non_finite_supported_results(
    anchors: tuple[GeometryAnchor, GeometryAnchor], x: float, reason: str
) -> None:
    model = _model((GeometrySegment("extreme", anchors),))

    projected = _project(model, distance=50, x=x, z=50)

    assert projected["status"] == "unavailable"
    assert projected["reason"] == reason


def test_centreline_evidence_requires_geometry_and_calibration_review() -> None:
    model = _model(
        role="reviewed_centreline",
        geometry_validation=_evidence("validated"),
        calibration_validation=_evidence("validated"),
    )

    projected = _project(model, distance=25, x=25)

    assert projected["geometry_evidence_status"] == "reviewed_centreline"
    assert projected["lap_reference_eligibility_evaluated"] is False


@pytest.mark.parametrize(
    ("geometry_status", "calibration_status", "expected"),
    [
        ("unreviewed", "validated", "geometry_unreviewed"),
        ("validated", "unreviewed", "distance_calibration_unreviewed"),
    ],
)
def test_geometry_and_distance_calibration_validation_are_separate_gates(
    geometry_status: str, calibration_status: str, expected: str
) -> None:
    model = _model(
        role="reviewed_centreline",
        geometry_validation=_evidence(geometry_status),
        calibration_validation=_evidence(calibration_status),
    )

    projected = _project(model, distance=25, x=25)

    assert projected["geometry_evidence_status"] == expected
    assert projected["lap_reference_eligibility_evaluated"] is False


def test_observed_path_remains_diagnostic_even_if_validation_fields_claim_review() -> None:
    model = _model(
        role="observed_reference_path",
        geometry_validation=_evidence("validated"),
        calibration_validation=_evidence("validated"),
    )

    projected = _project(model, distance=25, x=25)

    assert projected["geometry_evidence_status"] == "diagnostic_observed_reference_path"
    assert projected["lap_reference_eligibility_evaluated"] is False


def test_geometry_segments_reject_duplicate_or_out_of_order_anchor_distances() -> None:
    for distances in ((0, 0), (10, 5)):
        with pytest.raises(ValueError, match="strictly increasing"):
            GeometrySegment(
                "bad",
                tuple(
                    GeometryAnchor(distance, float(index), 0, 0)
                    for index, distance in enumerate(distances)
                ),
            )


def test_geometry_model_rejects_overlapping_or_out_of_range_segments() -> None:
    first = GeometrySegment(
        "first",
        (GeometryAnchor(0, 0, 0, 0), GeometryAnchor(20, 20, 0, 0)),
    )
    overlapping = GeometrySegment(
        "overlap",
        (GeometryAnchor(10, 10, 0, 0), GeometryAnchor(30, 30, 0, 0)),
    )
    with pytest.raises(ValueError, match="non-overlapping"):
        _model((first, overlapping))

    outside = GeometrySegment(
        "outside",
        (GeometryAnchor(90, 90, 0, 0), GeometryAnchor(110, 110, 0, 0)),
    )
    with pytest.raises(ValueError, match="origin-shifted lap range"):
        _model((outside,))


def test_geometry_anchor_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        GeometryAnchor(0, float("nan"), 0, 0)


def test_model_resource_limits_reject_excess_segments_and_anchors() -> None:
    too_many_segments = tuple(
        GeometrySegment(
            f"segment-{index}",
            (
                GeometryAnchor(index * 2, index * 2, 0, 0),
                GeometryAnchor(index * 2 + 1, index * 2 + 1, 0, 0),
            ),
        )
        for index in range(257)
    )
    with pytest.raises(ValueError, match="1 to 256"):
        _model(too_many_segments, lap_length=600)

    anchors = tuple(
        GeometryAnchor(index * 0.004, index * 0.004, 0, 0)
        for index in range(MAX_GEOMETRY_ANCHORS + 1)
    )
    with pytest.raises(ValueError, match="20000 anchor"):
        _model((GeometrySegment("large", anchors),), lap_length=100)


def _document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "model_id": "synthetic-model",
        "revision": 1,
        "packet_format": 2025,
        "track_id": 0,
        "track_name": "Synthetic track",
        "layout_id": "synthetic-default",
        "lap_length_m": 100,
        "game_distance_origin_m": 0,
        "coordinate_frame": "world_xyz",
        "coordinate_units": "m",
        "lateral_sign_convention": _SIGN,
        "cyclic_seam_policy": "unsupported",
        "role": "observed_reference_path",
        "provenance": "synthetic fixture only",
        "geometry_validation": {"status": "unreviewed"},
        "calibration_validation": {"status": "unreviewed"},
        "segments": [
            {
                "identifier": "one",
                "anchors": [
                    {"distance_m": 0, "x_m": 0, "y_m": 0, "z_m": 0},
                    {"distance_m": 100, "x_m": 100, "y_m": 0, "z_m": 0},
                ],
            }
        ],
    }


def test_geometry_loader_and_cli_report_structure_without_claiming_physical_validation(
    tmp_path: Path, capsys
) -> None:
    model_path = tmp_path / "geometry.json"
    model_path.write_text(json.dumps(_document()), encoding="utf-8")

    model = load_geometry_model(model_path)
    assert model.artifact_sha256 is not None
    assert len(model.artifact_sha256) == 64

    args = build_parser().parse_args(["geometry-validate", str(model_path)])
    assert args.handler is _geometry_validate
    assert _geometry_validate(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "artifact_structure_valid"
    assert report["verification_scope"] == "schema_and_internal_consistency_only"
    assert report["physical_geometry_verified_by_command"] is False
    assert report["geometry_validation"]["status"] == "unreviewed"


def test_geometry_loader_rejects_bad_schema_ambiguous_segments_and_file_over_limit(
    tmp_path: Path,
) -> None:
    document = _document()
    document["schema_version"] = 2
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported geometry model schema"):
        load_geometry_model(path)

    document = _document()
    document["segments"] = [
        {
            "identifier": "ambiguous",
            "anchors": [
                {"distance_m": 0, "x_m": 0, "y_m": 0, "z_m": 0},
                {"distance_m": 0, "x_m": 1, "y_m": 0, "z_m": 0},
            ],
        }
    ]
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="strictly increasing"):
        load_geometry_model(path)

    path.write_bytes(b" " * (MAX_GEOMETRY_MODEL_BYTES + 1))
    with pytest.raises(ValueError, match="8 MiB"):
        load_geometry_model(path)


def test_geometry_loader_counts_raw_anchors_before_constructing_them(
    tmp_path: Path, monkeypatch
) -> None:
    document = _document()
    assert isinstance(document["segments"], list)
    document["segments"][0]["anchors"] = [
        {"distance_m": index, "x_m": index, "y_m": 0, "z_m": 0}
        for index in range(MAX_GEOMETRY_ANCHORS + 1)
    ]
    path = tmp_path / "too-many-anchors.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    def should_not_construct(_value):
        pytest.fail("anchor parsing must not start before the total count is bounded")

    monkeypatch.setattr("f1_engineer.tracks.geometry_loader._anchor", should_not_construct)
    with pytest.raises(ValueError, match="20000 anchor"):
        load_geometry_model(path)


def test_geometry_loader_normalizes_float_conversion_overflow(tmp_path: Path) -> None:
    document = _document()
    document["lap_length_m"] = 10**1000
    path = tmp_path / "numeric-overflow.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="finite numeric range"):
        load_geometry_model(path)
