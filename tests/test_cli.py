from __future__ import annotations

import json

import pytest

from f1_engineer import cli
from f1_engineer.analysis.comparison_window import DistanceWindow


def test_compare_cli_forwards_an_explicit_distance_window(monkeypatch, capsys) -> None:
    calls: dict[str, object] = {}

    def compare(*_args, **kwargs):
        calls.update(kwargs)
        return {"comparison_window": {"window_m": {"start_m": 500.0, "end_m": 1200.0}}}

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
    assert json.loads(capsys.readouterr().out)["comparison_window"]["window_m"] == {
        "start_m": 500.0,
        "end_m": 1200.0,
    }


def test_compare_cli_rejects_an_incomplete_window(monkeypatch) -> None:
    monkeypatch.setattr(cli, "compare_attempts", lambda *_args, **_kwargs: {})
    args = cli.build_parser().parse_args(
        ["compare", "target", "reference", "--window-start-m", "500"]
    )

    with pytest.raises(ValueError, match="bounds_must_be_selected_together"):
        cli._compare(args)
