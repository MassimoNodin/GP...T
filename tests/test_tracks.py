from __future__ import annotations

import json
from pathlib import Path

import pytest

from f1_engineer.tracks.loader import load_track_model
from f1_engineer.tracks.model import CornerDefinition, TrackModel


MELBOURNE_DRAFT = (
    Path(__file__).parents[1]
    / "f1_engineer"
    / "tracks"
    / "data"
    / "melbourne_f1_25_tt_draft_v1.json"
)


def test_melbourne_capture_regions_are_versioned_and_explicitly_draft() -> None:
    model = load_track_model(MELBOURNE_DRAFT)

    assert model.packet_format == 2025
    assert model.track_id == 0
    assert model.track_length_m == 5276
    assert model.validation_status == "draft"
    assert len(model.corners) == 6
    assert all(corner.identifier.startswith("melbourne-braking-region-") for corner in model.corners)
    assert all(corner.nominal_apex_m is None for corner in model.corners)
    assert all(corner.direction is None for corner in model.corners)


@pytest.mark.parametrize(
    "corners, message",
    [
        (
            (
                CornerDefinition("a", "A", 0, 20),
                CornerDefinition("b", "B", 10, 30),
            ),
            "overlapping",
        ),
        (
            (
                CornerDefinition("a", "A", 20, 30),
                CornerDefinition("b", "B", 0, 10),
            ),
            "ordered",
        ),
        (
            (CornerDefinition("a", "A", 0, 20, nominal_apex_m=20),),
            "nominal apex",
        ),
        (
            (
                CornerDefinition(
                    "a", "A", 0, 20, braking_search_window_m=(-1, 10)
                ),
            ),
            "braking search bounds",
        ),
    ],
)
def test_track_model_rejects_ambiguous_or_invalid_region_bounds(corners, message) -> None:
    with pytest.raises(ValueError, match=message):
        TrackModel(
            model_id="test-track",
            revision=1,
            packet_format=2025,
            track_id=0,
            track_name="Test",
            layout_id="default",
            track_length_m=100,
            distance_origin_m=0,
            provenance="test",
            validation_status="draft",
            corners=corners,
        )


def test_track_model_loader_rejects_overlapping_regions_without_shared_complex(tmp_path) -> None:
    value = json.loads(MELBOURNE_DRAFT.read_text(encoding="utf-8"))
    value["corners"] = [
        {"identifier": "a", "label": "A", "start_distance_m": 0, "end_distance_m": 100},
        {"identifier": "b", "label": "B", "start_distance_m": 90, "end_distance_m": 150},
    ]
    path = tmp_path / "overlap.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="overlapping"):
        load_track_model(path)


def test_track_model_allows_explicit_overlapping_complex_members() -> None:
    model = TrackModel(
        model_id="complex-test",
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Test",
        layout_id="default",
        track_length_m=100,
        distance_origin_m=0,
        provenance="test",
        validation_status="draft",
        corners=(
            CornerDefinition("a", "A", 0, 20, complex_id="T3-T4"),
            CornerDefinition("b", "B", 10, 30, complex_id="T3-T4"),
        ),
    )

    assert tuple(corner.identifier for corner in model.corners) == ("a", "b")
