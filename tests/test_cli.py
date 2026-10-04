from __future__ import annotations

import json

import pytest

from f1_engineer import cli
from f1_engineer.analysis.comparison_window import DistanceWindow


def test_compare_cli_forwards_an_explicit_distance_window(monkeypatch, capsys) -> None:
    calls: dict[str, object] = {}

    def compare(*_args, **kwargs):
        calls.update(kwargs)
        return {
            "comparison_window": {"window_m": {"start_m": 500.0, "end_m": 1200.0}},
            "comparison_brief": {
                "analysis_version": "comparison-brief-v1",
                "text": "Target was 1.000 s slower.",
            },
            "corner_comparison_brief": {
                "analysis_version": "corner-comparison-brief-v1",
                "status": "abstained",
            },
            "distance_window_brief": {
                "analysis_version": "distance-window-brief-v1",
                "status": "available",
                "coaching_eligible": False,
            },
        }

    monkeypatch.setattr(cli, "compare_attempts", compare)
    args = cli.build_parser().parse_args(
        [
            "compare",
            "target",
            "reference",
            "--window-start-m",
            "500",
            "--window-end-m",
            "1200",
        ]
    )

    assert cli._compare(args) == 0
    assert calls["distance_window"] == DistanceWindow(500.0, 1200.0)
    output = json.loads(capsys.readouterr().out)
    assert output["comparison_window"]["window_m"] == {
        "start_m": 500.0,
        "end_m": 1200.0,
    }
    assert output["comparison_brief"]["analysis_version"] == "comparison-brief-v1"
    assert output["corner_comparison_brief"]["analysis_version"] == (
        "corner-comparison-brief-v1"
    )
    assert output["distance_window_brief"]["analysis_version"] == (
        "distance-window-brief-v1"
    )


def test_compare_cli_rejects_an_incomplete_window(monkeypatch) -> None:
    monkeypatch.setattr(cli, "compare_attempts", lambda *_args, **_kwargs: {})
    args = cli.build_parser().parse_args(
        ["compare", "target", "reference", "--window-start-m", "500"]
    )

    with pytest.raises(ValueError, match="bounds_must_be_selected_together"):
        cli._compare(args)


def test_car_observation_cli_commands_forward_bounded_selection(
    monkeypatch, capsys
) -> None:
    calls = {}
    monkeypatch.setattr(
        cli,
        "list_car_observation_inventory",
        lambda database, run_id, session_uid, *, limit, offset: calls.update(
            inventory=(database, run_id, session_uid, limit, offset)
        )
        or {"status": "available", "slots": {"total": 0, "items": []}},
    )
    monkeypatch.setattr(
        cli,
        "load_car_observation_preview",
        lambda database, run_id, session_uid, car_index, *, limit, offset: calls.update(
            preview=(database, run_id, session_uid, car_index, limit, offset)
        )
        or {"status": "available", "observations": {"total": 0, "items": []}},
    )
    cars_args = cli.build_parser().parse_args(
        [
            "cars",
            "--database",
            "state.sqlite3",
            "--run-id",
            "r" * 64,
            "--session-uid",
            "18446744073709550001",
            "--limit",
            "24",
        ]
    )
    preview_args = cli.build_parser().parse_args(
        [
            "car-observations",
            "--database",
            "state.sqlite3",
            "--run-id",
            "r" * 64,
            "--session-uid",
            "18446744073709550001",
            "--car-index",
            "23",
            "--limit",
            "120",
            "--offset",
            "40",
        ]
    )

    assert cli._cars(cars_args) == 0
    inventory_output = json.loads(capsys.readouterr().out)
    assert inventory_output["slots"]["total"] == 0
    assert cli._car_observations(preview_args) == 0
    preview_output = json.loads(capsys.readouterr().out)
    assert preview_output["observations"]["total"] == 0
    assert calls == {
        "inventory": (
            "state.sqlite3",
            "r" * 64,
            "18446744073709550001",
            24,
            0,
        ),
        "preview": (
            "state.sqlite3",
            "r" * 64,
            "18446744073709550001",
            23,
            120,
            40,
        ),
    }


def test_observation_set_cli_forwards_explicit_attempts_and_window(
    monkeypatch, capsys
) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "build_observation_set",
        lambda *args, **kwargs: calls.update(args=args, kwargs=kwargs)
        or {"artifact_kind": "selected_window_observation_set"},
    )
    args = cli.build_parser().parse_args(
        [
            "observation-set",
            "attempt-2",
            "attempt-1",
            "--comparison-policy",
            "practice_qualifying",
            "--window-start-m",
            "500",
            "--window-end-m",
            "1200",
        ]
    )

    assert cli._observation_set(args) == 0
    assert calls["args"][1] == ["attempt-2", "attempt-1"]
    assert calls["args"][2] == DistanceWindow(500.0, 1200.0)
    assert calls["kwargs"] == {"policy": "practice_qualifying"}
    assert json.loads(capsys.readouterr().out)["artifact_kind"] == (
        "selected_window_observation_set"
    )


def test_compare_regions_cli_forwards_explicit_pair_and_catalog_model(monkeypatch, capsys) -> None:
    calls: dict[str, object] = {}
    model = object()
    metadata = {
        "model_id": "local-test-model",
        "revision": 2,
        "origin": "local_draft",
        "content_sha256": "a" * 64,
    }
    monkeypatch.setattr(
        cli,
        "load_track_model_catalog",
        lambda root: calls.update(root=root)
        or type("Catalog", (), {
            "resolve_entry": lambda _self, model_id, revision: type(
                "Entry", (), {"model": model, "metadata": lambda _self: metadata}
            )()
        })(),
    )
    document = {
        "artifact_kind": "paired_distance_region_observations",
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
        "regions": [
            {
                "supported_differences": {
                    "brake_10_percent_release": {
                        "status": "supported",
                        "analysis_version": "brake-threshold-release-v1",
                        "target_end_bracket_m": [10.0, 11.0],
                        "reference_end_bracket_m": [12.0, 13.0],
                        "target_minus_reference_end_bracket_m": [-3.0, -1.0],
                    }
                },
                "debrief": {
                    "schema_version": 1,
                    "analysis_version": "diagnostic-region-debrief-v1",
                    "facts": [{"kind": "minimum_speed", "text": "A measured fact."}],
                    "omissions": [],
                }
            }
        ],
    }
    monkeypatch.setattr(
        cli,
        "compare_attempt_regions",
        lambda *args, **kwargs: calls.update(args=args, kwargs=kwargs) or document,
    )
    args = cli.build_parser().parse_args(
        [
            "compare-regions",
            "target",
            "reference",
            "--comparison-policy",
            "practice_qualifying",
            "--database",
            "state.sqlite3",
            "--track-model-id",
            "local-test-model",
            "--track-model-revision",
            "2",
            "--track-models-root",
            "models",
        ]
    )

    assert cli._compare_regions(args) == 0
    assert calls["args"] == ("state.sqlite3", "target", "reference", model)
    assert calls["kwargs"] == {
        "model_metadata": metadata,
        "policy": cli.ComparisonPolicy.PRACTICE_QUALIFYING,
    }
    assert calls["root"] == "models"
    assert json.loads(capsys.readouterr().out) == document


def test_compare_trajectories_cli_exports_versioned_json(monkeypatch, capsys, tmp_path) -> None:
    output_path = tmp_path / "paired-paths.json"
    calls: dict[str, object] = {}
    document = {
        "analysis_version": "trajectory-comparison-preview-v1",
        "artifact_kind": "observed_trajectory_comparison_preview",
        "diagnostic_only": True,
        "limits_applied": {"target_points": 2, "reference_points": 2},
    }
    monkeypatch.setattr(
        cli,
        "compare_observed_trajectories",
        lambda *args, **kwargs: calls.update(args=args, kwargs=kwargs) or document,
    )
    args = cli.build_parser().parse_args(
        [
            "compare-trajectories",
            "target",
            "reference",
            "--comparison-policy",
            "practice_qualifying",
            "--database",
            "state.sqlite3",
            "--position-probe-m",
            "1250.5",
            "--output",
            str(output_path),
        ]
    )

    assert cli._compare_trajectories(args) == 0
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved == document
    assert calls["args"] == ("state.sqlite3", "target", "reference")
    assert calls["kwargs"]["policy"] is cli.ComparisonPolicy.PRACTICE_QUALIFYING
    assert calls["kwargs"]["position_probe_m"] == 1250.5
    summary = json.loads(capsys.readouterr().out)
    assert summary["artifact_kind"] == "observed_trajectory_comparison_preview"
    assert summary["limits_applied"]["target_points"] == 2
