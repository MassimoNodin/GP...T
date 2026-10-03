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


def test_compare_cli_rejects_an_incomplete_window(monkeypatch) -> None:
    monkeypatch.setattr(cli, "compare_attempts", lambda *_args, **_kwargs: {})
    args = cli.build_parser().parse_args(
        ["compare", "target", "reference", "--window-start-m", "500"]
    )

    with pytest.raises(ValueError, match="bounds_must_be_selected_together"):
        cli._compare(args)


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
