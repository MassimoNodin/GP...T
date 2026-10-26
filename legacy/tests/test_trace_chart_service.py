from __future__ import annotations

from dataclasses import replace
import json
from argparse import Namespace
from pathlib import Path

import pytest

from f1_engineer.analysis import trace_chart_service
from f1_engineer.analysis.trace_chart_service import (
    TraceChartUnavailable,
    build_attempt_trace_chart_preview,
)
from f1_engineer import cli
from f1_engineer.storage.query import AttemptTraceReadLimitError, StoredAttemptTrace


def _sample(
    frame: int,
    time_s: float,
    *,
    speed_mps: float | None = 20.0,
    throttle: float | None = 0.25,
    brake: float | None = 0.5,
    steering: float | None = -0.2,
) -> dict[str, object]:
    return {
        "frame_identifier": frame,
        "session_time_s": time_s,
        "lap_distance_m": float(frame % 1000),
        "speed_mps": speed_mps,
        "throttle": throttle,
        "brake": brake,
        "steering": steering,
        "current_lap_time_ms": frame * 10,
        "gear": 3,
        "drs_active": False,
    }


def _attempt(
    samples: tuple[dict[str, object], ...],
    *,
    context: dict[str, object] | None = None,
    schema_version: int = 3,
    disposition: str = "completed",
    game_valid: bool | None = True,
) -> StoredAttemptTrace:
    return StoredAttemptTrace(
        attempt_key="run:42:0:1",
        run_id="run",
        session_uid="14237356543050158953",
        car_index=0,
        disposition=disposition,
        lap_time_ms=80_000 if disposition == "completed" else None,
        game_valid=game_valid,
        reference_eligible=False,
        exclusion_reasons=("game_marked_invalid",) if game_valid is False else (),
        trace_sha256="a" * 64,
        trace_schema_version=schema_version,
        quality={"sample_count": len(samples)},
        context_segments=((1, context),),
        samples=samples,
        attempt_number=1,
        start_observed=True,
        pit_encountered=False,
    )


@pytest.mark.parametrize(
    ("game_mode", "session_type", "schema_version", "disposition", "game_valid"),
    [
        ("time_trial", "time_trial", 1, "completed", True),
        ("driver_career_25", "practice_1", 2, "partial", None),
        ("driver_career_25", "qualifying_1", 3, "completed", False),
        ("race", "race", 3, "abandoned", None),
        (None, None, 3, "partial", None),
    ],
)
def test_trace_preview_is_available_across_modes_statuses_and_schemas(
    game_mode, session_type, schema_version, disposition, game_valid
) -> None:
    context = (
        {"game_mode": game_mode, "session_type": session_type, "track_name": "Shanghai"}
        if game_mode is not None
        else None
    )
    attempt = _attempt(
        (_sample(10, 12.0),),
        context=context,
        schema_version=schema_version,
        disposition=disposition,
        game_valid=game_valid,
    )

    report = build_attempt_trace_chart_preview(attempt)

    assert report["diagnostic_only"] is True
    assert report["status"] == "observed"
    assert report["source"]["trace_checksum_verified"] is True
    assert report["source"]["disposition"] == disposition
    assert report["source"]["game_valid"] is game_valid
    assert report["source"]["trace_schema_version"] == schema_version
    assert report["context"]["game_modes"] == ([game_mode] if game_mode else ["unknown"])
    assert report["context"]["session_types"] == ([session_type] if session_type else ["unknown"])
    assert report["channels"]["speed"]["segments"][0]["points"][0]["value"] == pytest.approx(72.0)
    assert report["channels"]["throttle"]["segments"][0]["points"][0]["value"] == pytest.approx(25.0)
    assert report["channels"]["steering"]["segments"][0]["points"][0]["value"] == pytest.approx(-0.2)


def test_trace_preview_aggregate_context_labels_keep_unknown_intervals() -> None:
    attempt = replace(
        _attempt((_sample(1, 1.0), _sample(2, 1.016))),
        context_segments=(
            (1, {"game_mode": "driver_career_25", "session_type": "practice_1", "track_name": "Shanghai"}),
            (2, None),
        ),
    )

    report = build_attempt_trace_chart_preview(attempt)

    assert report["context"]["game_modes"] == ["driver_career_25", "unknown"]
    assert report["context"]["session_types"] == ["practice_1", "unknown"]
    assert report["context"]["track_names"] == ["Shanghai", "unknown"]


def test_missing_channel_samples_break_only_that_channel() -> None:
    samples = (
        _sample(1, 1.0),
        _sample(2, 1.016, throttle=None),
        _sample(3, 1.033),
    )

    report = build_attempt_trace_chart_preview(_attempt(samples))

    assert report["channels"]["speed"]["source_run_count"] == 1
    throttle = report["channels"]["throttle"]
    assert throttle["source_sample_count"] == 3
    assert throttle["observed_sample_count"] == 2
    assert throttle["missing_value_sample_count"] == 1
    assert throttle["source_run_count"] == 2
    assert throttle["segments"][1]["break_before_reasons"] == ["missing_channel_value"]
    assert [point["frame_identifier"] for segment in throttle["segments"] for point in segment["points"]] == [1, 3]


def test_frame_gap_and_session_time_reset_keep_chronology_in_separate_runs() -> None:
    gap_samples = (
        _sample(1, 1.0),
        _sample(2, 1.016),
        _sample(5, 1.5),
        _sample(6, 1.516),
    )
    gap_report = build_attempt_trace_chart_preview(_attempt(gap_samples))
    speed = gap_report["channels"]["speed"]
    assert speed["source_run_count"] == 2
    assert speed["segments"][1]["break_before_reasons"] == ["frame_gap", "session_time_gap"]

    reset_samples = (
        _sample(1, 12.0),
        _sample(2, 12.016),
        _sample(3, 0.5),
    )
    reset_report = build_attempt_trace_chart_preview(_attempt(reset_samples))
    reset_speed = reset_report["channels"]["speed"]
    assert reset_speed["source_run_count"] == 2
    assert reset_speed["segments"][1]["break_before_reasons"] == ["session_time_regression"]
    assert [point["session_time_s"] for point in reset_speed["segments"][1]["points"]] == [0.5]


def test_uint32_frame_wrap_does_not_split_a_continuous_trace() -> None:
    samples = tuple(
        _sample(frame, 1.0 + index / 60)
        for index, frame in enumerate((0xFFFFFFFE, 0xFFFFFFFF, 0, 1))
    )

    report = build_attempt_trace_chart_preview(_attempt(samples))
    speed = report["channels"]["speed"]

    assert speed["source_run_count"] == 1
    assert [point["frame_identifier"] for point in speed["segments"][0]["points"]] == [
        0xFFFFFFFE,
        0xFFFFFFFF,
        0,
        1,
    ]


def test_trace_preview_is_deterministically_thinned_and_run_capped() -> None:
    long_run = tuple(_sample(frame, 2.0 + frame / 60) for frame in range(20))
    first = build_attempt_trace_chart_preview(_attempt(long_run), point_limit=4)
    second = build_attempt_trace_chart_preview(_attempt(long_run), point_limit=4)
    speed = first["channels"]["speed"]

    assert first == second
    assert speed["observed_sample_count"] == 20
    assert speed["rendered_point_count"] == 4
    assert speed["omitted_point_count"] == 16
    assert [point["frame_identifier"] for point in speed["segments"][0]["points"]] == [0, 6, 13, 19]
    assert speed["segments"][0]["start_anchor"]["frame_identifier"] == 0
    assert speed["segments"][0]["end_anchor"]["frame_identifier"] == 19

    separated = tuple(
        _sample(frame, 3.0 + frame / 60, throttle=0.5 if frame % 2 == 0 else None)
        for frame in range(5)
    )
    capped = build_attempt_trace_chart_preview(_attempt(separated), run_limit=2)
    throttle = capped["channels"]["throttle"]
    assert throttle["source_run_count"] == 3
    assert throttle["rendered_run_count"] == 2
    assert throttle["omitted_run_count"] == 1
    assert throttle["observed_sample_count"] == 3
    assert throttle["rendered_point_count"] == 2
    assert throttle["omitted_point_count"] == 1


def test_trace_preview_load_uses_shared_read_limits_and_reports_limit_reasons(
    monkeypatch, tmp_path
) -> None:
    attempt = _attempt((_sample(1, 1.0),))
    calls: dict[str, object] = {}

    def load(_database, _attempt_key, **kwargs):
        calls.update(kwargs)
        return attempt

    monkeypatch.setattr(trace_chart_service, "load_attempt_trace", load)
    report = trace_chart_service.load_attempt_trace_chart_preview(
        tmp_path / "analysis.sqlite3", attempt.attempt_key
    )
    assert report is not None
    assert report["artifact_kind"] == "single_attempt_player_trace_preview"
    assert calls["columns"] == trace_chart_service.ANALYSIS_TRACE_COLUMNS
    assert calls["max_trace_rows"] == 100_000
    assert calls["max_trace_bytes"] == 64 * 1024 * 1024
    assert calls["max_context_segments"] == 1_024
    assert calls["max_context_bytes"] == 4 * 1024 * 1024

    def limit(_database, _attempt_key, **_kwargs):
        raise AttemptTraceReadLimitError("rows")

    monkeypatch.setattr(trace_chart_service, "load_attempt_trace", limit)
    with pytest.raises(TraceChartUnavailable) as error:
        trace_chart_service.load_attempt_trace_chart_preview(
            tmp_path / "analysis.sqlite3", attempt.attempt_key
        )
    assert error.value.reason_code == "trace_chart_source_rows_limit_exceeded"


def test_no_chartable_samples_is_explicit() -> None:
    samples = (_sample(1, 1.0, speed_mps=None, throttle=None, brake=None, steering=None),)

    report = build_attempt_trace_chart_preview(_attempt(samples))

    assert report["status"] == "no_chartable_samples"
    assert all(channel["segments"] == [] for channel in report["channels"].values())


def test_source_metadata_is_preserved_without_claiming_attempt_comparability() -> None:
    attempt = replace(
        _attempt((_sample(1, 1.0),), game_valid=False),
        exclusion_reasons=("game_marked_invalid", "manual_reset"),
        reference_eligible=False,
    )

    report = build_attempt_trace_chart_preview(attempt)

    assert report["source"]["trace_sha256"] == "a" * 64
    assert report["source"]["exclusion_reasons"] == ["game_marked_invalid", "manual_reset"]
    assert report["source"]["reference_eligible"] is False
    assert report["continuity_policy"]["coordinate"].startswith("stored session_time_s")


def test_trace_cli_exports_the_versioned_preview_atomically(
    monkeypatch, tmp_path, capsys
) -> None:
    report = build_attempt_trace_chart_preview(_attempt((_sample(1, 1.0),)))
    output = tmp_path / "nested" / "attempt-traces.json"
    monkeypatch.setattr(
        cli,
        "load_attempt_trace_chart_preview",
        lambda _database, _attempt_key: report,
    )

    code = cli._traces(
        Namespace(
            database="analysis.sqlite3",
            attempt_key="run:42:0:1",
            output=str(output),
            overwrite=False,
        )
    )

    assert code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["artifact_kind"] == "single_attempt_player_trace_preview"
    summary = json.loads(capsys.readouterr().out)
    assert summary["channels"]["speed"]["observed_sample_count"] == 1


def test_trace_cli_does_not_truncate_a_predictable_temporary_path(
    monkeypatch, tmp_path, capsys
) -> None:
    report = build_attempt_trace_chart_preview(_attempt((_sample(1, 1.0),)))
    output = tmp_path / "attempt-traces.json"
    unrelated_temporary = tmp_path / "attempt-traces.json.tmp"
    unrelated_temporary.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(cli, "load_attempt_trace_chart_preview", lambda _db, _key: report)

    code = cli._traces(
        Namespace(database="analysis.sqlite3", attempt_key="run:42:0:1", output=str(output), overwrite=False)
    )

    assert code == 0
    assert output.exists()
    assert unrelated_temporary.read_text(encoding="utf-8") == "keep me"
    capsys.readouterr()


def test_trace_cli_does_not_overwrite_a_destination_created_after_precheck(
    monkeypatch, tmp_path, capsys
) -> None:
    report = build_attempt_trace_chart_preview(_attempt((_sample(1, 1.0),)))
    output = tmp_path / "attempt-traces.json"
    original_link = cli.os.link
    monkeypatch.setattr(cli, "load_attempt_trace_chart_preview", lambda _db, _key: report)

    def create_racing_destination(source, destination) -> None:
        Path(destination).write_text("external file", encoding="utf-8")
        original_link(source, destination)

    monkeypatch.setattr(cli.os, "link", create_racing_destination)
    code = cli._traces(
        Namespace(database="analysis.sqlite3", attempt_key="run:42:0:1", output=str(output), overwrite=False)
    )

    assert code == 2
    assert output.read_text(encoding="utf-8") == "external file"
    assert "use --overwrite" in capsys.readouterr().err
