from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from f1_engineer.analysis.source_limits import (
    ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
    ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
)
from f1_engineer.tracks import draft_authoring
from f1_engineer.tracks.loader import load_track_model


def _context(**overrides: object) -> dict[str, object]:
    return {
        "packet_format": 2025,
        "track_id": 0,
        "track_name": "Melbourne",
        "track_length_m": 1000,
        "session_type": "time_trial",
        "game_mode": "time_trial",
        "rule_set": "time_trial",
        "formula_id": 0,
        "equal_car_performance_id": 0,
        "steering_assist_id": 0,
        "braking_assist_id": 0,
        "gearbox_assist_id": 1,
        **overrides,
    }


def _attempt(
    *,
    context_segments: tuple[tuple[int, dict[str, object] | None], ...] | None = None,
    disposition: str = "completed",
) -> SimpleNamespace:
    context_segments = context_segments or ((1, _context()),)
    return SimpleNamespace(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="42",
        car_index=0,
        attempt_number=1,
        disposition=disposition,
        lap_time_ms=80_000 if disposition == "completed" else None,
        game_valid=False,
        reference_eligible=False,
        start_observed=True,
        pit_encountered=False,
        source_sample_count=3,
        trace_sha256="a" * 64,
        trace_schema_version=4,
        superseded=False,
        lifecycle_assessed=True,
        context_segments=context_segments,
    )


def _metadata(context: dict[str, object] | None = None) -> dict[str, object]:
    context = context or _context()
    return {
        "scope": {
            "run_id": "run",
            "session_uid": "42",
            "car_index": 0,
            "packet_format": context["packet_format"],
        },
        "attempt": {
            "attempt_key": "run:42:0:1",
            "attempt_number": 1,
            "disposition": "completed",
            "lap_time_ms": 80_000,
            "game_valid": False,
            "start_observed": True,
            "pit_encountered": False,
            "superseded": False,
            "lifecycle_assessed": True,
            "reference_eligible": False,
        },
        "context_segment_count": 1,
        "context": dict(context),
        "trace_metadata": {
            "ready": True,
            "row_count": 3,
            "sha256": "a" * 64,
            "schema_version": 4,
        },
        "capture": {
            "sha256": "b" * 64,
            "complete": False,
            "completion": {
                "status": "incomplete",
                "queue_dropped": 2,
                "unpersisted_on_shutdown": 0,
                "socket_errors": 0,
            },
        },
        "processing": {
            "metrics": {
                "capture_quality": {
                    "import_late_packets_ignored": 1,
                    "import_frame_overflow_packets_dropped": 0,
                }
            }
        },
        "metadata_limits": {},
    }


def _patch_source(monkeypatch, attempt=None, metadata=None) -> None:
    attempt = attempt or _attempt()
    metadata = metadata or _metadata()
    monkeypatch.setattr(
        draft_authoring,
        "load_attempt_draft_authoring_snapshot",
        lambda *_args, **kwargs: (
            (attempt, metadata)
            if kwargs
            == {
                "max_context_segments": ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
                "max_context_bytes": ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
            }
            else (None, None)
        ),
    )


def _build(regions=None):
    return draft_authoring.build_draft_track_model(
        "unused.sqlite3",
        "run:42:0:1",
        model_id="my-track-draft",
        revision=2,
        layout_id="caller-layout",
        regions=regions
        if regions is not None
        else [
            {
                "identifier": "window-1",
                "label": "Draft Window 1",
                "start_distance_m": 100,
                "end_distance_m": 200,
                "braking_search_window_m": [110, 130],
                "turn_in_search_window_m": [140, 150],
                "throttle_pickup_window_m": [170, 190],
            }
        ],
    )


def test_draft_builder_exports_loader_compatible_diagnostic_model(monkeypatch, tmp_path) -> None:
    _patch_source(monkeypatch)

    result = _build()
    output = tmp_path / "draft.json"
    output.write_text(json.dumps(result["model"]), encoding="utf-8")
    model = load_track_model(output)

    assert model.validation_status == "draft"
    assert model.distance_origin_m == 0
    assert model.layout_id == "caller-layout"
    assert model.corners[0].braking_search_window_m == (110, 130)
    assert result["source"]["scope_matches_attempt"] is True
    assert result["source"]["context_matches_attempt"] is True
    assert result["source"]["source_trace_checksum_verified"] is False
    assert result["catalog_installation"] == "not_performed"
    assert result["model"]["provenance"].find("have not been verified") >= 0
    assert {warning["code"] for warning in result["warnings"]} >= {
        "game_invalid",
        "capture_incomplete",
        "recording_loss_reported",
        "replay_frame_exclusions_reported",
    }


@pytest.mark.parametrize(
    "regions, reason",
    [
        ([{"identifier": "a", "label": "A", "start_distance_m": 10, "end_distance_m": 10}], "invalid analysis bounds"),
        ([{"identifier": "a", "label": "A", "start_distance_m": 10, "end_distance_m": 30}, {"identifier": "a", "label": "B", "start_distance_m": 40, "end_distance_m": 50}], "duplicate corner identifier"),
        ([{"identifier": "a", "label": "A", "start_distance_m": 10, "end_distance_m": 40}, {"identifier": "b", "label": "B", "start_distance_m": 30, "end_distance_m": 50}], "overlapping corner regions"),
        ([{"identifier": "a", "label": "A", "start_distance_m": 10, "end_distance_m": 40, "braking_search_window_m": [5, 15]}], "invalid braking search bounds"),
        ([{"identifier": "a", "label": "A", "start_distance_m": True, "end_distance_m": 40}], "must_be_numeric"),
        ([{"identifier": "a", "label": "A", "start_distance_m": 10, "end_distance_m": float("nan")}], "must_be_finite"),
        ([{"identifier": "a", "label": "A", "start_distance_m": 10, "end_distance_m": 10**400}], "must_be_finite"),
    ],
)
def test_draft_builder_rejects_invalid_or_ambiguous_regions(
    monkeypatch, regions, reason
) -> None:
    _patch_source(monkeypatch)

    with pytest.raises(ValueError, match=reason):
        _build(regions)


def test_draft_builder_accepts_64_regions_and_rejects_65(monkeypatch) -> None:
    _patch_source(monkeypatch)
    regions = [
        {
            "identifier": f"window-{index}",
            "label": f"Window {index}",
            "start_distance_m": index * 10,
            "end_distance_m": index * 10 + 5,
        }
        for index in range(64)
    ]

    result = _build(regions)
    assert len(result["model"]["corners"]) == 64
    with pytest.raises(ValueError, match="region_count_limit_exceeded"):
        _build(regions + [
            {
                "identifier": "window-64",
                "label": "Window 64",
                "start_distance_m": 640,
                "end_distance_m": 645,
            }
        ])


@pytest.mark.parametrize(
    "context, reason",
    [
        (_context(session_type="race", game_mode="race", rule_set="race"), "unsupported_mode"),
        (_context(session_type="unknown", game_mode="unknown", rule_set="unknown"), "unknown_mode"),
    ],
)
def test_draft_builder_rejects_race_and_unknown_modes(monkeypatch, context, reason) -> None:
    _patch_source(monkeypatch, _attempt(context_segments=((1, context),)), _metadata(context))

    with pytest.raises(ValueError, match=reason):
        _build()


def test_draft_builder_accepts_practice_and_qualifying_modes(monkeypatch) -> None:
    context = _context(
        session_type="practice_1",
        game_mode="grand_prix_23",
        rule_set="practice_qualifying",
    )
    _patch_source(monkeypatch, _attempt(context_segments=((1, context),)), _metadata(context))

    result = _build()

    assert result["source"]["context_mode"] == "practice_qualifying"
    assert any(
        warning["code"] == "practice_qualifying_conditions_uncontrolled"
        for warning in result["warnings"]
    )


def test_draft_builder_rejects_changing_or_mismatched_source_context(monkeypatch) -> None:
    first = _context()
    changed = _context(track_id=1)
    _patch_source(
        monkeypatch,
        _attempt(context_segments=((1, first), (2, changed))),
        _metadata(first),
    )
    with pytest.raises(ValueError, match="context_changed"):
        _build()

    _patch_source(monkeypatch, _attempt(), _metadata(_context(track_id=1)))
    with pytest.raises(ValueError, match="metadata_changed_during_assessment"):
        _build()


@pytest.mark.parametrize(
    "section, field, value",
    [
        ("attempt", "disposition", "abandoned"),
        ("trace_metadata", "sha256", "c" * 64),
        ("trace_metadata", "row_count", 4),
        (None, "context_segment_count", 2),
    ],
)
def test_draft_builder_rejects_changed_attempt_trace_or_context_metadata(
    monkeypatch, section, field, value
) -> None:
    metadata = _metadata()
    target = metadata if section is None else metadata[section]
    target[field] = value
    _patch_source(monkeypatch, _attempt(), metadata)

    with pytest.raises(ValueError, match="metadata_changed_during_assessment"):
        _build()


def test_draft_builder_bounds_the_raw_request_before_parsing_regions(monkeypatch) -> None:
    _patch_source(monkeypatch)
    too_large = [{"identifier": "a", "label": "A", "start_distance_m": 0, "end_distance_m": 1, "unused": "x" * (draft_authoring.MAX_DRAFT_MODEL_REQUEST_BYTES)}]

    with pytest.raises(ValueError, match="draft_model_request_size_limit_exceeded"):
        _build(too_large)
