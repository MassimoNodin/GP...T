from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from f1_engineer.analysis import engineer_query
from f1_engineer.analysis.engineer_query import query_engineer_evidence
from f1_engineer.storage.database import Database
from f1_engineer.storage.query import load_attempt_engineer_summary_metadata


def _seed_attempt_database(
    database_path,
    *,
    attempt_key: str = "partial-attempt",
    game_mode: str = "unknown",
    disposition: str = "partial",
    lap_time_ms: int | None = None,
    game_valid: bool | None = None,
) -> None:
    capture_sha = "a" * 64
    context = {
        "packet_format": 2025,
        "session_type": "unknown",
        "game_mode": game_mode,
        "track_name": "Shanghai",
        "track_id": 17,
    }
    completion = {
        "status": "incomplete",
        "queue_dropped": 0,
        "unpersisted_on_shutdown": 0,
        "socket_errors": 0,
    }
    metrics = {
        "summary": {
            "packet_count": 100,
            "malformed_packet_count": 0,
            "lap_data_errors": 0,
            "car_telemetry_errors": 0,
        },
        "capture_quality": {
            "import_late_packets_ignored": 0,
            "import_frame_overflow_packets_dropped": 0,
            "lifecycle_events_dropped": 0,
            "lifecycle_evidence_truncated_session_count": 0,
            "lifecycle_reconciliation_truncated_session_count": 0,
        },
    }
    with Database(database_path) as db:
        with db.connection:
            db.connection.execute(
                """INSERT INTO captures(capture_sha256,source_path,byte_size,complete,
                          completion_json,metadata_json) VALUES (?,?,?,?,?,?)""",
                (capture_sha, "capture.f1ecap", 1024, 0, json.dumps(completion), "{}"),
            )
            db.connection.execute(
                """INSERT INTO processing_runs(run_id,capture_sha256,pipeline_version,
                          config_json,status,metrics_json) VALUES (?,?,?,?,?,?)""",
                ("b" * 64, capture_sha, "test-pipeline", "{}", "complete", json.dumps(metrics)),
            )
            db.connection.execute(
                """INSERT INTO sessions(session_key,run_id,session_uid,packet_format,context_json)
                     VALUES (?,?,?,?,?)""",
                ("b" * 64 + ":42", "b" * 64, "42", 2025, json.dumps(context)),
            )
            db.connection.execute(
                """INSERT INTO lap_attempts(attempt_key,session_key,car_index,attempt_number,
                          lap_number,disposition,start_frame_identifier,end_frame_identifier,
                          start_session_time_s,end_session_time_s,lap_time_ms,game_valid,
                          start_observed,pit_encountered,sample_count,reference_eligible,
                          exclusion_reasons_json,attempt_json,superseded,lifecycle_assessed)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    attempt_key,
                    "b" * 64 + ":42",
                    0,
                    3,
                    3,
                    disposition,
                    100,
                    None,
                    10.0,
                    None,
                    lap_time_ms,
                    None if game_valid is None else int(game_valid),
                    1,
                    0,
                    12,
                    0,
                    json.dumps([]),
                    "{}",
                    None,
                    0,
                ),
            )
            db.connection.execute(
                """INSERT INTO lap_context_segments(attempt_key,ordinal,from_frame_identifier,
                          context_json) VALUES (?,?,?,?)""",
                (attempt_key, 0, 100, json.dumps(context)),
            )
            db.connection.execute(
                """INSERT INTO attempt_timing_evidence(attempt_key,status,evidence_json)
                     VALUES (?,?,?)""",
                (
                    attempt_key,
                    "unavailable",
                    json.dumps({"status": "unavailable", "reasons": ["timing_not_matched"]}),
                ),
            )
            db.connection.execute(
                """INSERT INTO telemetry_files(attempt_key,relative_path,schema_version,
                          row_count,sha256,quality_json,ready) VALUES (?,?,?,?,?,?,?)""",
                (
                    attempt_key,
                    "traces/does-not-exist.parquet",
                    4,
                    12,
                    "c" * 64,
                    "{}",
                    1,
                ),
            )


def _supported_release() -> dict[str, object]:
    target = [547.9824, 549.1797]
    reference = [558.8594, 560.7285]
    return {
        "status": "supported",
        "schema_version": 1,
        "analysis_version": "brake-threshold-release-v1",
        "target_end_bracket_m": target,
        "reference_end_bracket_m": reference,
        "target_minus_reference_end_bracket_m": [
            target[0] - reference[1],
            target[1] - reference[0],
        ],
        "left_censored": {"target": True, "reference": False},
        "right_censored": {"target": False, "reference": False},
        "unit": "m",
    }


def _paired_report() -> dict[str, object]:
    facts = [
        {"kind": f"fact_{index}", "text": f"Measured fact {index}.", "source_fields": [f"supported_differences.fact_{index}.value"]}
        for index in range(5)
    ]
    omissions = [
        {"kind": "exit_speed", "reason_code": "coverage_incomplete", "text": "exit speed omitted: coverage incomplete."}
    ]
    return {
        "analysis_version": "paired-distance-region-observations-v1",
        "region_analysis_version": "distance-region-analysis-v1",
        "attempts": {
            side: {
                "attempt_key": f"{side}-attempt",
                "run_id": "d" * 64,
                "trace_sha256": ("a" if side == "target" else "b") * 64,
                "trace_schema_version": 4,
                "capture": {"sha256": ("c" if side == "target" else "d") * 64, "complete": False},
            }
            for side in ("target", "reference")
        },
        "model": {
            "model_id": "melbourne-draft",
            "revision": 2,
            "content_sha256": "e" * 64,
            "origin": "local_draft",
        },
        "warnings": {
            "target": [
                {"code": "capture_incomplete", "text": "The source capture footer is incomplete."},
                {"code": "lifecycle_unassessed", "text": "Lifecycle evidence is unassessed."},
                *[
                    {"code": f"additional_warning_{index}", "text": f"Additional qualification {index}."}
                    for index in range(9)
                ],
            ],
            "reference": [
                {"code": "capture_incomplete", "text": "The source capture footer is incomplete."},
                {"code": "lifecycle_unassessed", "text": "Lifecycle evidence is unassessed."},
            ],
        },
        "regions": [
            {
                "identifier": "r1",
                "debrief": {"facts": [], "omissions": []},
                "supported_differences": {},
            },
            {
                "identifier": "r2",
                "debrief": {"facts": facts, "omissions": omissions},
                "supported_differences": {
                    "brake_10_percent_release": _supported_release(),
                },
            },
        ],
    }


def _catalog(model: object | None = None):
    metadata = {
        "model_id": "melbourne-draft",
        "revision": 2,
        "origin": "local_draft",
        "content_sha256": "e" * 64,
    }
    entry = SimpleNamespace(model=model or object(), metadata=lambda: metadata)
    return SimpleNamespace(resolve_entry=lambda model_id, revision: entry if (model_id, revision) == ("melbourne-draft", 2) else (_ for _ in ()).throw(ValueError("unknown_track_model_revision")))


def test_attempt_summary_is_sqlite_only_and_preserves_unknown_timing(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "attempt.sqlite3"
    _seed_attempt_database(database_path, game_mode="unknown", disposition="partial")

    def no_trace(*_args, **_kwargs):
        raise AssertionError("attempt summary must not read Parquet")

    monkeypatch.setattr("f1_engineer.storage.query.read_trace", no_trace)
    report = query_engineer_evidence(
        database_path,
        {"intent": "attempt_summary", "target_attempt_key": "partial-attempt"},
    )

    assert report["status"] == "partial"
    assert report["provenance"]["verification_scope"] == "metadata_only"
    assert report["provenance"]["trace_metadata"]["checksum_verified"] is False
    assert any("incomplete" in warning["code"] for warning in report["warnings"])
    assert any(warning["code"] == "lifecycle_unassessed" for warning in report["warnings"])
    assert any(warning["code"] == "session_history_timing_unavailable" for warning in report["warnings"])
    assert not any(fact["kind"] == "session_history_timing" for fact in report["facts"])
    assert not any(fact["kind"] == "recorded_lap_time" for fact in report["facts"])
    assert any("unknown" in fact["text"] for fact in report["facts"])


def test_attempt_summary_supports_race_and_keeps_capture_footer_warning(tmp_path) -> None:
    database_path = tmp_path / "race.sqlite3"
    _seed_attempt_database(
        database_path,
        attempt_key="race-lap",
        game_mode="race",
        disposition="completed",
        lap_time_ms=91_234,
        game_valid=False,
    )
    metadata = load_attempt_engineer_summary_metadata(database_path, "race-lap")
    assert metadata is not None
    assert metadata["context"]["game_mode"] == "race"
    assert metadata["trace_metadata"]["checksum_verified"] is False

    report = query_engineer_evidence(
        database_path,
        {"intent": "attempt_summary", "target_attempt_key": "race-lap"},
    )
    assert report["selected"]["target_attempt_key"] == "race-lap"
    assert report["provenance"]["verification_scope"] == "metadata_only"
    assert any(fact["kind"] == "recorded_lap_time" for fact in report["facts"])
    assert {warning["code"] for warning in report["warnings"]} >= {
        "capture_incomplete",
        "game_invalid",
        "lifecycle_unassessed",
    }


def test_attempt_summary_shows_latest_snapshot_when_context_changed(tmp_path) -> None:
    database_path = tmp_path / "changed-context.sqlite3"
    _seed_attempt_database(database_path, attempt_key="context-shift")
    latest_context = {
        "packet_format": 2025,
        "session_type": "practice_1",
        "game_mode": "career",
        "track_name": "Shanghai",
        "track_id": 17,
    }
    with Database(database_path) as db:
        with db.connection:
            db.connection.execute(
                "UPDATE sessions SET context_json=? WHERE session_uid='42'",
                (json.dumps(latest_context),),
            )
            db.connection.execute(
                "INSERT INTO lap_context_segments(attempt_key,ordinal,from_frame_identifier,context_json) VALUES (?,?,?,?)",
                ("context-shift", 1, 200, json.dumps(latest_context)),
            )

    report = query_engineer_evidence(
        database_path,
        {"intent": "attempt_summary", "target_attempt_key": "context-shift"},
    )

    context_fact = next(fact for fact in report["facts"] if fact["kind"] == "session_context")
    assert "context changed" in context_fact["text"]
    assert "initial: unknown, unknown, Shanghai" in context_fact["text"]
    assert "Latest session snapshot: practice 1, career, Shanghai" in context_fact["text"]
    assert "sessions.context_json" in context_fact["source_fields"]
    assert any(warning["code"] == "context_changed" for warning in report["warnings"])


def test_attempt_summary_qualifies_mixed_known_and_unknown_counters(tmp_path) -> None:
    database_path = tmp_path / "mixed-counters.sqlite3"
    _seed_attempt_database(database_path, attempt_key="mixed-counters")
    completion = {
        "status": "incomplete",
        "queue_dropped": 2,
        "unpersisted_on_shutdown": -1,
        "socket_errors": 0,
    }
    metrics = {
        "summary": {"malformed_packet_count": 0, "lap_data_errors": 0},
        "capture_quality": {
            "import_late_packets_ignored": 3,
            "import_frame_overflow_packets_dropped": None,
        },
    }
    with Database(database_path) as db:
        with db.connection:
            db.connection.execute(
                "UPDATE captures SET completion_json=?",
                (json.dumps(completion),),
            )
            db.connection.execute(
                "UPDATE processing_runs SET metrics_json=?",
                (json.dumps(metrics),),
            )

    report = query_engineer_evidence(
        database_path,
        {"intent": "attempt_summary", "target_attempt_key": "mixed-counters"},
    )

    warnings = {warning["code"]: warning["text"] for warning in report["warnings"]}
    assert "recording_loss_reported" in warnings
    assert "replay_frame_exclusions_reported" in warnings
    assert "unknown" in warnings["recording_loss_reported"]
    assert "unknown" in warnings["replay_frame_exclusions_reported"]
    assert "recording_loss_counters_unknown" not in warnings
    assert "replay_frame_counters_unknown" not in warnings


def test_request_parser_rejects_unknown_fields_invalid_policy_and_unbounded_ids() -> None:
    with pytest.raises(ValueError, match="unknown_engineer_query_request_field"):
        engineer_query.parse_engineer_query_request(
            {"intent": "attempt_summary", "target_attempt_key": "lap", "trace_path": "x.parquet"}
        )
    with pytest.raises(ValueError, match="unsupported_comparison_policy"):
        engineer_query.parse_engineer_query_request(
            {
                "intent": "region_comparison",
                "target_attempt_key": "target",
                "reference_attempt_key": "reference",
                "comparison_policy": "race",
                "track_model_id": "model",
                "track_model_revision": 1,
                "region_identifier": "r1",
            }
        )
    with pytest.raises(ValueError, match="invalid_target_attempt_key"):
        engineer_query.parse_engineer_query_request(
            {"intent": "attempt_summary", "target_attempt_key": "x" * 257}
        )
    with pytest.raises(ValueError, match="positive_integer"):
        engineer_query.parse_engineer_query_request(
            {
                "intent": "region_comparison",
                "target_attempt_key": "target",
                "reference_attempt_key": "reference",
                "comparison_policy": "time_trial",
                "track_model_id": "model",
                "track_model_revision": True,
                "region_identifier": "r1",
            }
        )


def test_region_query_uses_one_paired_report_and_preserves_facts_brackets_and_censoring(monkeypatch) -> None:
    calls = []
    report = _paired_report()

    def compare(*args, **kwargs):
        calls.append((args, kwargs))
        return report

    monkeypatch.setattr(engineer_query, "compare_attempt_regions", compare)
    result = query_engineer_evidence(
        "state.sqlite3",
        {
            "intent": "region_comparison",
            "target_attempt_key": "target-attempt",
            "reference_attempt_key": "reference-attempt",
            "comparison_policy": "time_trial",
            "track_model_id": "melbourne-draft",
            "track_model_revision": 2,
            "region_identifier": "r2",
        },
        track_model_catalog=_catalog(),
    )

    assert len(calls) == 1
    assert result["status"] == "partial"
    assert [fact["kind"] for fact in result["facts"]] == [
        "fact_0", "fact_1", "fact_2", "fact_3", "fact_4", "brake_10_percent_release"
    ]
    release_fact = result["facts"][-1]
    assert "[547.982, 549.180]m" in release_fact["text"]
    assert "[-12.747, -9.679]m" in release_fact["text"]
    assert "positive=further" in release_fact["text"]
    assert "Left-censored: target" in release_fact["text"]
    assert result["provenance"]["capture"]["target_sha256"] == "c" * 64
    assert result["provenance"]["capture"]["reference_sha256"] == "d" * 64
    assert result["provenance"]["verification_scope"] == "checksummed_trace_analysis"
    assert result["provenance"]["model"]["content_sha256"] == "e" * 64
    assert len(result["warnings"]) <= 8
    capture_warning = next(warning for warning in result["warnings"] if warning["code"] == "capture_incomplete")
    assert capture_warning["sides"] == ["target", "reference"]
    assert result["omitted_warning_count"] > 0


def test_region_query_preserves_both_censored_sides_within_text_limit(monkeypatch) -> None:
    report = _paired_report()
    release = report["regions"][1]["supported_differences"]["brake_10_percent_release"]
    release["left_censored"] = {"target": True, "reference": True}
    monkeypatch.setattr(engineer_query, "compare_attempt_regions", lambda *_args, **_kwargs: report)

    result = query_engineer_evidence(
        "state.sqlite3",
        {
            "intent": "region_comparison",
            "target_attempt_key": "target-attempt",
            "reference_attempt_key": "reference-attempt",
            "comparison_policy": "time_trial",
            "track_model_id": "melbourne-draft",
            "track_model_revision": 2,
            "region_identifier": "r2",
        },
        track_model_catalog=_catalog(),
    )

    release_fact = next(fact for fact in result["facts"] if fact["kind"] == "brake_10_percent_release")
    assert len(release_fact["text"]) <= engineer_query.ENGINEER_QUERY_MAX_TEXT_LENGTH
    assert "[547.982, 549.180]m" in release_fact["text"]
    assert "[558.859, 560.729]m" in release_fact["text"]
    assert "[-12.747, -9.679]m" in release_fact["text"]
    assert "Left-censored: target, ref." in release_fact["text"]


def test_region_query_does_not_fall_back_to_another_region_or_policy(monkeypatch) -> None:
    monkeypatch.setattr(engineer_query, "compare_attempt_regions", lambda *_args, **_kwargs: _paired_report())
    missing = query_engineer_evidence(
        "state.sqlite3",
        {
            "intent": "region_comparison",
            "target_attempt_key": "target-attempt",
            "reference_attempt_key": "reference-attempt",
            "comparison_policy": "practice_qualifying",
            "track_model_id": "melbourne-draft",
            "track_model_revision": 2,
            "region_identifier": "not-configured",
        },
        track_model_catalog=_catalog(),
    )
    assert missing["status"] == "unavailable"
    assert missing["reason_codes"] == ["region_identifier_unavailable"]
    assert missing["facts"] == []

    from f1_engineer.analysis.paired_region_service import PairedRegionReportUnavailable

    monkeypatch.setattr(
        engineer_query,
        "compare_attempt_regions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PairedRegionReportUnavailable("unsupported_race_mode")),
    )
    race = query_engineer_evidence(
        "state.sqlite3",
        {
            "intent": "region_comparison",
            "target_attempt_key": "target-attempt",
            "reference_attempt_key": "reference-attempt",
            "comparison_policy": "time_trial",
            "track_model_id": "melbourne-draft",
            "track_model_revision": 2,
            "region_identifier": "r2",
        },
        track_model_catalog=_catalog(),
    )
    assert race["status"] == "unavailable"
    assert race["reason_codes"] == ["unsupported_race_mode"]


def test_cli_and_api_use_same_structured_report(monkeypatch, tmp_path, capsys) -> None:
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    import f1_engineer.api.app as api_module
    import f1_engineer.cli as cli
    from f1_engineer.api.app import create_app

    report = {
        "schema_version": 1,
        "analysis_version": "engineer-query-v1",
        "artifact_kind": "engineer_query",
        "intent": "attempt_summary",
        "status": "partial",
        "reason_codes": [],
        "selected": {"target_attempt_key": "attempt"},
        "facts": [],
        "omitted_fact_count": 0,
        "warnings": [],
        "omitted_warning_count": 0,
        "provenance": {"verification_scope": "metadata_only"},
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
    }
    monkeypatch.setattr(api_module, "query_engineer_evidence", lambda *_args, **_kwargs: report)

    async def post(path, body):
        transport = httpx.ASGITransport(app=create_app(tmp_path / "api.sqlite3"))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json=body)

    response = asyncio.run(
        post("/api/v1/engineer/query", {"intent": "attempt_summary", "target_attempt_key": "attempt"})
    )
    assert response.status_code == 200
    assert response.json()["data"] == report

    invalid = asyncio.run(
        post(
            "/api/v1/engineer/query",
            {"intent": "attempt_summary", "target_attempt_key": "attempt", "trace_path": "x"},
        )
    )
    assert invalid.status_code == 422

    monkeypatch.setattr(cli, "query_engineer_evidence", lambda *_args, **_kwargs: report)
    args = cli.build_parser().parse_args(["engineer", "attempt-summary", "attempt"])
    assert cli._engineer_attempt_summary(args) == 0
    assert json.loads(capsys.readouterr().out) == report
