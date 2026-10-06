from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

import f1_engineer.api.app as api_module
from f1_engineer.analysis.comparison_window import DistanceWindow
from f1_engineer.api.app import create_app
from f1_engineer.tracks import registry


def _get(
    app,
    path: str,
    *,
    params: dict[str, str] | list[tuple[str, str]] | None = None,
) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, params=params)

    return asyncio.run(request())


def _post(app, path: str, body: object) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json=body)

    return asyncio.run(request())


def _post_content(app, path: str, content: bytes) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, content=content)

    return asyncio.run(request())


def test_sessions_api_is_versioned_and_keeps_session_uid_as_text(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        api_module,
        "list_sessions",
        lambda _database: [
            {
                "session_key": "run:14237356543050158953",
                "run_id": "run",
                "session_uid": "14237356543050158953",
                "packet_format": 2025,
                "context": {"session_type": "time_trial", "session_uid": 14237356543050158953},
                "run_status": "complete",
                "capture_quality": None,
                "capture_sha256": "a" * 64,
                "pipeline_version": "player-traces-v8",
                "started_at_utc": "2026-10-02T00:00:00Z",
                "finished_at_utc": "2026-10-02T00:01:00Z",
                "lap_attempts": 3,
            }
        ],
    )
    response = _get(create_app(tmp_path / "unused.sqlite3"), "/api/v1/sessions")

    assert response.status_code == 200
    assert response.json()["api_version"] == "v1"
    assert response.json()["data"][0]["session_uid"] == "14237356543050158953"
    assert response.json()["data"][0]["context"]["session_uid"] == "14237356543050158953"
    assert response.json()["data"][0]["capture_sha256"] == "a" * 64


def test_lap_api_exposes_bounded_player_contexts(monkeypatch, tmp_path) -> None:
    setup_context = {
        "schema_version": 1,
        "status": "observed_unchanged",
        "continuity_claim": False,
        "scope": {"player_car_index": 0},
        "at_start": {"status": "reported", "setup": {"front_wing": 45}},
        "observations": [],
        "observation_count": 1,
        "observed_change_count": 0,
        "unknown_event_count": 0,
        "observations_omitted_count": 0,
    }
    participant_context = {"schema_version": 1, "status": "unknown"}
    monkeypatch.setattr(
        api_module,
        "list_laps",
        lambda *_args, **_kwargs: [
            {
                "attempt_key": "attempt",
                "run_id": "a" * 64,
                "session_uid": "42",
                "car_index": 0,
                "attempt_number": 1,
                "lap_number": 1,
                "disposition": "completed",
                "lap_time_ms": 90_000,
                "game_valid": True,
                "reference_eligible": False,
                "start_frame_ordinal": 10,
                "end_frame_ordinal": 20,
                "superseded": None,
                "lifecycle_assessed": False,
                "start_observed": True,
                "pit_encountered": False,
                "sample_count": 10,
                "trace_row_count": 10,
                "trace_schema_version": 4,
                "trace_sha256": "b" * 64,
                "context": None,
                "quality": {},
                "exclusion_reasons": [],
                "timing_evidence": {},
                "player_participant_context": participant_context,
                "player_car_setup_context": setup_context,
            }
        ],
    )

    response = _get(create_app(tmp_path / "unused.sqlite3"), "/api/v1/laps")

    assert response.status_code == 200
    attempt = response.json()["data"][0]
    assert attempt["player_participant_context"] == participant_context
    assert attempt["player_car_setup_context"] == setup_context


def test_session_best_api_requires_and_uses_explicit_context_anchor(
    monkeypatch, tmp_path
) -> None:
    calls = []
    monkeypatch.setattr(
        api_module,
        "assess_session_best",
        lambda database, anchor: calls.append((database, anchor))
        or SimpleNamespace(
            to_dict=lambda: {
                "status": "assessed",
                "anchor_attempt_key": anchor,
                "scope": {"session_uid": 4_294_967_296},
            }
        ),
    )
    app = create_app(tmp_path / "unused.sqlite3")

    missing_anchor = _get(app, "/api/v1/analysis/session-best")
    response = _get(
        app,
        "/api/v1/analysis/session-best",
        params={"anchor_attempt_key": "run:42:0:3"},
    )

    assert missing_anchor.status_code == 422
    assert response.status_code == 200
    assert calls == [(tmp_path / "unused.sqlite3", "run:42:0:3")]
    assert response.json()["data"]["scope"]["session_uid"] == "4294967296"


def test_attempt_timing_api_is_standalone_and_stringifies_source_session_uid(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        api_module,
        "load_attempt_timing_evidence",
        lambda _database, attempt_key: {
            "status": "matched",
            "reasons": [],
            "provenance": {
                "attempt_key": attempt_key,
                "run_id": "run-id",
                "capture_sha256": "a" * 64,
                "completion_frame_ordinal": 10,
            },
            "source": {"session_uid": 18446744073709551600},
        }
        if attempt_key == "attempt"
        else None,
    )

    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/attempts/attempt/timing",
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "matched"
    assert response.json()["data"]["source"]["session_uid"] == "18446744073709551600"
    assert response.json()["data"]["provenance"] == {
        "attempt_key": "attempt",
        "run_id": "run-id",
        "capture_sha256": "a" * 64,
        "completion_frame_ordinal": 10,
    }


def test_processing_run_api_bounds_pages_and_preserves_unsigned_session_uids(
    monkeypatch, tmp_path
) -> None:
    calls = {}
    monkeypatch.setattr(
        api_module,
        "list_processing_run_summaries",
        lambda _database, *, limit, offset, filters: calls.update(
            limit=limit, offset=offset, filters=filters.to_dict()
        )
        or {
            "items": [],
            "total": 12,
            "limit": limit,
            "offset": offset,
            "filters": filters.to_dict(),
        },
    )
    run_id = "a" * 64
    monkeypatch.setattr(
        api_module,
        "get_processing_run_detail",
        lambda _database, requested, **page: (
            {
                "summary": {"run_id": requested},
                "sessions": {
                    "items": [{"session_uid": "18446744073709550001"}],
                    "total": 1,
                    "limit": page["session_limit"],
                    "offset": page["session_offset"],
                },
                "attempts": {"items": [], "total": 0, "limit": page["attempt_limit"], "offset": page["attempt_offset"]},
            }
            if requested == run_id
            else None
        ),
    )
    app = create_app(tmp_path / "unused.sqlite3")

    page_response = _get(
        app,
        "/api/v1/processing-runs",
        params={"limit": "3", "offset": "6"},
    )
    detail_response = _get(
        app,
        f"/api/v1/processing-runs/{run_id}",
        params={
            "session_limit": "4",
            "session_offset": "8",
            "attempt_limit": "5",
            "attempt_offset": "10",
        },
    )
    invalid_page_response = _get(
        app, "/api/v1/processing-runs", params={"limit": "51"}
    )
    oversized_run_offset_response = _get(
        app,
        "/api/v1/processing-runs",
        params={"offset": str(1 << 63)},
    )
    oversized_session_offset_response = _get(
        app,
        f"/api/v1/processing-runs/{run_id}",
        params={"session_offset": str(1 << 63)},
    )
    oversized_attempt_offset_response = _get(
        app,
        f"/api/v1/processing-runs/{run_id}",
        params={"attempt_offset": str(1 << 63)},
    )

    assert page_response.status_code == 200
    assert calls == {
        "limit": 3,
        "offset": 6,
        "filters": {
            "q": None,
            "packet_format": None,
            "track_id": None,
            "session_category": None,
            "started_from": None,
            "started_through": None,
        },
    }
    assert page_response.json()["data"]["total"] == 12
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["sessions"]["items"][0]["session_uid"] == "18446744073709550001"
    assert detail_response.json()["data"]["attempts"]["offset"] == 10
    assert invalid_page_response.status_code == 422
    assert oversized_run_offset_response.status_code == 422
    assert oversized_session_offset_response.status_code == 422
    assert oversized_attempt_offset_response.status_code == 422


def test_processing_run_api_normalizes_and_rejects_archive_filters(
    monkeypatch, tmp_path
) -> None:
    calls = []

    def list_page(_database, *, limit, offset, filters):
        calls.append(filters)
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "filters": filters.to_dict(),
        }

    monkeypatch.setattr(api_module, "list_processing_run_summaries", list_page)
    app = create_app(tmp_path / "unused.sqlite3")
    normalized = _get(
        app,
        "/api/v1/processing-runs",
        params=[
            ("q", " ABC "),
            ("packet_format", "2026"),
            ("track_id", "17"),
            ("session_category", "PRACTICE"),
            ("started_from", "2026-10-01"),
            ("started_through", "2026-10-05"),
        ],
    )
    repeated = _get(
        app,
        "/api/v1/processing-runs",
        params=[("q", "abc"), ("q", "def")],
    )
    incomplete_track = _get(
        app,
        "/api/v1/processing-runs",
        params={"packet_format": "2025"},
    )
    invalid_literal_search = _get(
        app, "/api/v1/processing-runs", params={"q": "abc%"}
    )
    reversed_dates = _get(
        app,
        "/api/v1/processing-runs",
        params={"started_from": "2026-10-06", "started_through": "2026-10-05"},
    )

    assert normalized.status_code == 200
    assert normalized.json()["data"]["filters"] == {
        "q": "abc",
        "packet_format": 2026,
        "track_id": 17,
        "session_category": "practice",
        "started_from": "2026-10-01",
        "started_through": "2026-10-05",
    }
    assert len(calls) == 1
    for response, reason in (
        (repeated, "invalid_archive_filter_q"),
        (incomplete_track, "invalid_archive_filter_track_context"),
        (invalid_literal_search, "invalid_archive_filter_q"),
        (reversed_dates, "invalid_archive_filter_date_range"),
    ):
        assert response.status_code == 422
        assert response.json()["reason"] == reason


def test_processing_run_api_reports_archive_filter_budget_abstention(
    monkeypatch, tmp_path
) -> None:
    def limit_exceeded(_database, *, limit, offset, filters):
        raise api_module.ArchiveFilterLimitExceeded("archive_filter_limit_exceeded")

    monkeypatch.setattr(
        api_module, "list_processing_run_summaries", limit_exceeded
    )
    app = create_app(tmp_path / "unused.sqlite3")

    response = _get(
        app,
        "/api/v1/processing-runs",
        params={"session_category": "race"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "api_version": "v1",
        "status": "unavailable",
        "data": None,
        "reason": "archive_filter_limit_exceeded",
    }


def test_car_observation_api_exposes_bounded_inventory_and_preview(
    monkeypatch, tmp_path
) -> None:
    run_id = "c" * 64
    calls = {}
    monkeypatch.setattr(
        api_module,
        "list_car_observation_inventory",
        lambda _database, requested_run, session_uid, *, limit, offset: calls.update(
            inventory=(requested_run, session_uid, limit, offset)
        )
        or {
            "run_id": requested_run,
            "session_uid": session_uid,
            "status": "available",
            "opponent_eligibility": "not_assessed",
        },
    )
    monkeypatch.setattr(
        api_module,
        "load_car_observation_preview",
        lambda _database, requested_run, session_uid, car_index, *, limit, offset: calls.update(
            preview=(requested_run, session_uid, car_index, limit, offset)
        )
        or {
            "run_id": requested_run,
            "session_uid": session_uid,
            "car_index": car_index,
            "status": "available",
            "opponent_eligibility": "not_assessed",
        },
    )
    monkeypatch.setattr(
        api_module,
        "load_car_lap_inventory_page",
        lambda _database, requested_run, session_uid, car_index, *, limit, offset: calls.update(
            lap_inventory=(requested_run, session_uid, car_index, limit, offset)
        )
        or {
            "run_id": requested_run,
            "session_uid": session_uid,
            "car_index": car_index,
            "status": "assessed",
            "verification_scope": "admitted_participants_and_lap_data",
            "reference_eligibility": "not_assessed",
            "coaching_eligible": False,
            "attempts": {"limit": limit, "offset": offset, "total": 0, "returned": 0, "items": []},
        },
    )
    app = create_app(tmp_path / "unused.sqlite3")

    inventory = _get(
        app,
        f"/api/v1/processing-runs/{run_id}/sessions/18446744073709550001/cars",
        params={"limit": "24", "offset": "0"},
    )
    preview = _get(
        app,
        f"/api/v1/processing-runs/{run_id}/sessions/18446744073709550001/cars/23/observations",
        params={"limit": "120", "offset": "40"},
    )
    invalid_preview = _get(
        app,
        f"/api/v1/processing-runs/{run_id}/sessions/18446744073709550001/cars/24/observations",
    )
    lap_inventory = _get(
        app,
        f"/api/v1/processing-runs/{run_id}/sessions/18446744073709550001/cars/23/lap-inventory",
        params={"limit": "100", "offset": "100000"},
    )

    assert inventory.status_code == 200
    assert inventory.json()["data"]["session_uid"] == "18446744073709550001"
    assert inventory.json()["data"]["opponent_eligibility"] == "not_assessed"
    assert preview.status_code == 200
    assert preview.json()["data"]["car_index"] == 23
    assert lap_inventory.status_code == 200
    assert lap_inventory.json()["data"]["coaching_eligible"] is False
    assert calls == {
        "inventory": (run_id, "18446744073709550001", 24, 0),
        "preview": (run_id, "18446744073709550001", 23, 120, 40),
        "lap_inventory": (run_id, "18446744073709550001", 23, 100, 100000),
    }

    def preview_limit(_database, _run, _session, _car, *, limit, offset):
        raise ValueError("observation_preview_source_bytes_limit_exceeded")

    monkeypatch.setattr(api_module, "load_car_observation_preview", preview_limit)
    limited_preview = _get(
        app,
        f"/api/v1/processing-runs/{run_id}/sessions/18446744073709550001/cars/23/observations",
    )
    assert limited_preview.json() == {
        "api_version": "v1",
        "status": "unavailable",
        "reason": "observation_preview_source_bytes_limit_exceeded",
        "data": None,
    }
    invalid_lap_page = _get(
        app,
        f"/api/v1/processing-runs/{run_id}/sessions/18446744073709550001/cars/23/lap-inventory",
        params={"limit": "101", "offset": "0"},
    )
    assert invalid_lap_page.status_code == 422
    assert invalid_preview.status_code == 422


def test_recording_sources_api_preserves_latest_completed_run_id(
    monkeypatch, tmp_path
) -> None:
    run_id = "b" * 64
    source = {
        "capture_id": "c" * 32,
        "display_name": "capture.f1ecap",
        "byte_size": 123,
        "modified_at_utc": "2026-10-03T00:00:00+00:00",
        "latest_job_id": "d" * 32,
        "latest_job_status": "complete",
        "latest_job_run_id": run_id,
        "available": True,
    }
    monkeypatch.setattr(api_module, "list_recording_sources", lambda *_args: [source])

    response = _get(create_app(tmp_path / "unused.sqlite3"), "/api/v1/recording-sources")

    assert response.status_code == 200
    assert response.json()["data"][0]["latest_job_run_id"] == run_id


def test_recording_source_page_api_bounds_filters_and_preserves_explicit_selection(
    tmp_path,
):
    recordings_root = tmp_path / "recordings"
    recordings_root.mkdir()
    (recordings_root / "alpha.f1ecap").write_bytes(b"capture")
    (recordings_root / "run_%_final.f1ecap").write_bytes(b"capture")
    app = create_app(tmp_path / "archive.sqlite3", recordings_root=recordings_root)

    first = _get(app, "/api/v1/recording-sources/page", params={"limit": "1"})
    first_body = first.json()["data"]
    assert first.status_code == 200
    assert first_body["total_count"] == 2
    assert len(first_body["items"]) == 1
    assert first_body["items"][0]["display_name"] == "alpha.f1ecap"
    assert first_body["has_more"] is True

    exact_literal = _get(
        app,
        "/api/v1/recording-sources/page",
        params={"q": "%_", "selected_capture_id": "f" * 32},
    )
    literal_body = exact_literal.json()["data"]
    assert literal_body["total_count"] == 1
    assert literal_body["items"][0]["display_name"] == "run_%_final.f1ecap"
    assert literal_body["selected_capture"] is None

    selected_id = _get(app, "/api/v1/recording-sources/page").json()["data"][
        "items"
    ][1]["capture_id"]
    selected_off_page = _get(
        app,
        "/api/v1/recording-sources/page",
        params={"limit": "1", "offset": "0", "selected_capture_id": selected_id},
    ).json()["data"]
    assert selected_off_page["items"][0]["capture_id"] != selected_id
    assert selected_off_page["selected_capture"]["capture_id"] == selected_id

    repeated = _get(
        app,
        "/api/v1/recording-sources/page",
        params=[("availability", "all"), ("availability", "missing")],
    )
    assert repeated.status_code == 422
    assert repeated.json()["reason"] == "invalid_recording_catalog_repeated_parameter"


def test_compare_api_reports_unsupported_pair_without_losing_status(monkeypatch, tmp_path) -> None:
    def reject_pair(*_args, **_kwargs):
        raise ValueError("attempts have incompatible track, format, or Time Trial settings")

    monkeypatch.setattr(api_module, "compare_attempts", reject_pair)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/laps",
        params={
            "target_attempt_key": "run:42:0:1",
            "reference_attempt_key": "run:99:0:1",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["reason"] == "attempts have incompatible track, format, or Time Trial settings"


def test_trajectory_comparison_api_returns_bounded_diagnostic_result(
    monkeypatch, tmp_path
) -> None:
    calls = {}
    result = {
        "analysis_version": "trajectory-comparison-preview-v1",
        "artifact_kind": "observed_trajectory_comparison_preview",
        "diagnostic_only": True,
        "is_centreline": False,
        "paths": {"target": {"source": {"session_uid": "18446744073709551600"}}},
    }

    def compare(database, target, reference, *, policy, **options):
        calls.update(
            database=database,
            target=target,
            reference=reference,
            policy=policy,
            **options,
        )
        return result

    monkeypatch.setattr(api_module, "compare_observed_trajectories", compare)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/trajectories",
        params={
            "target_attempt_key": "run:42:0:2",
            "reference_attempt_key": "run:42:0:1",
            "comparison_policy": "practice_qualifying",
            "position_probe_m": "1250.5",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["data"]["diagnostic_only"] is True
    assert response.json()["data"]["paths"]["target"]["source"]["session_uid"] == (
        "18446744073709551600"
    )
    assert calls["target"] == "run:42:0:2"
    assert calls["reference"] == "run:42:0:1"
    assert calls["policy"] == "practice_qualifying"
    assert calls["position_probe_m"] == 1250.5


def test_trajectory_comparison_api_keeps_unavailable_reason(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        api_module,
        "compare_observed_trajectories",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            api_module.TrajectoryComparisonUnavailable("trajectory_pair_scope_mismatch")
        ),
    )
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/trajectories",
        params={
            "target_attempt_key": "run:42:0:2",
            "reference_attempt_key": "run:42:0:1",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["reason"] == "trajectory_pair_scope_mismatch"


def test_track_model_catalog_exposes_draft_revision_and_provenance(tmp_path) -> None:
    response = _get(create_app(tmp_path / "unused.sqlite3"), "/api/v1/track-models")

    assert response.status_code == 200
    model = response.json()["data"][0]
    assert model["model_id"] == "melbourne-f1-25-time-trial-draft-v1"
    assert model["revision"] == 1
    assert model["validation_status"] == "draft"
    assert model["region_count"] == 6
    assert "not been validated" in model["provenance"]
    assert "path" not in model


def test_draft_track_model_api_returns_read_only_builder_result(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def build(database, attempt_key, **kwargs):
        calls.update(database=database, attempt_key=attempt_key, **kwargs)
        return {
            "status": "draft_model_built",
            "model": {"model_id": "user-draft", "validation_status": "draft"},
            "source": {"attempt_key": attempt_key},
            "warnings": [],
            "catalog_installation": "not_performed",
        }

    monkeypatch.setattr(api_module, "build_draft_track_model", build)
    database = tmp_path / "read-only.sqlite3"
    response = _post(
        create_app(database),
        "/api/v1/track-models/draft",
        {
            "source_attempt_key": "run:42:0:1",
            "model_id": "user-draft",
            "revision": 1,
            "layout_id": "my-layout",
            "regions": [
                {
                    "identifier": "window-1",
                    "label": "Window 1",
                    "start_distance_m": 100,
                    "end_distance_m": 200,
                    "braking_search_window_m": [110, 130],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["model"]["validation_status"] == "draft"
    assert response.json()["data"]["catalog_installation"] == "not_performed"
    assert calls["attempt_key"] == "run:42:0:1"
    assert calls["layout_id"] == "my-layout"
    assert calls["regions"][0]["identifier"] == "window-1"
    assert calls["regions"][0]["braking_search_window_m"] == (110, 130)
    assert not database.exists()


def test_draft_track_model_api_documents_its_bounded_request_schema(tmp_path) -> None:
    operation = create_app(tmp_path / "unused.sqlite3").openapi()["paths"][
        "/api/v1/track-models/draft"
    ]["post"]
    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert body_schema["properties"]["regions"]["maxItems"] == 64
    region_schema = body_schema["properties"]["regions"]["items"]
    assert region_schema["additionalProperties"] is False
    assert "nominal_apex_m" not in region_schema["properties"]


def test_draft_track_model_api_rejects_unknown_request_fields() -> None:
    response = _post(
        create_app("unused.sqlite3"),
        "/api/v1/track-models/draft",
        {
            "source_attempt_key": "run:42:0:1",
            "model_id": "user-draft",
            "revision": 1,
            "layout_id": "my-layout",
            "regions": [
                {
                    "identifier": "window-1",
                    "label": "Window 1",
                    "start_distance_m": 100,
                    "end_distance_m": 200,
                    "nominal_apex_m": 150,
                }
            ],
        },
    )

    assert response.status_code == 422


def test_draft_track_model_api_rejects_oversized_body_before_json_parse(monkeypatch) -> None:
    monkeypatch.setattr(
        api_module,
        "build_draft_track_model",
        lambda *_args, **_kwargs: pytest.fail("oversized body reached the builder"),
    )
    response = _post_content(
        create_app("unused.sqlite3"),
        "/api/v1/track-models/draft",
        b" " * (api_module.MAX_DRAFT_MODEL_REQUEST_BYTES + 1),
    )

    assert response.status_code == 413
    assert response.json()["reason"] == "draft_model_request_size_limit_exceeded"


def test_attempt_trajectory_api_returns_bounded_preview_and_string_session_uid(
    monkeypatch, tmp_path
) -> None:
    preview = {
        "artifact_kind": "observed_driven_trajectory_preview",
        "diagnostic_only": True,
        "source": {"session_uid": "14237356543050158953", "game_valid": False},
        "coverage": {"position_sample_count": 10, "segment_count": 1},
        "preview": {"rendered_point_count": 4, "omitted_position_point_count": 6},
        "segments": [{"points": [{"frame_identifier": 1}, {"frame_identifier": 10}]}],
    }
    monkeypatch.setattr(
        api_module,
        "load_observed_trajectory_preview",
        lambda _database, attempt_key: preview if attempt_key == "run:42:0:1" else None,
    )

    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/attempts/run%3A42%3A0%3A1/trajectory",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["data"]["artifact_kind"] == "observed_driven_trajectory_preview"
    assert response.json()["data"]["source"]["session_uid"] == "14237356543050158953"
    assert response.json()["data"]["preview"]["rendered_point_count"] == 4


def test_attempt_trajectory_api_reports_explicit_unavailable_reason(
    monkeypatch, tmp_path
) -> None:
    def unavailable(_database, _attempt_key):
        raise api_module.TrajectoryPreviewUnavailable(
            "motion_unavailable_for_trace_schema"
        )

    monkeypatch.setattr(api_module, "load_observed_trajectory_preview", unavailable)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/attempts/legacy%3A42%3A0%3A1/trajectory",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["reason"] == "motion_unavailable_for_trace_schema"


def test_attempt_traces_api_returns_mode_agnostic_chart_preview(monkeypatch, tmp_path) -> None:
    preview = {
        "report_version": 1,
        "artifact_kind": "single_attempt_player_trace_preview",
        "diagnostic_only": True,
        "source": {"session_uid": "14237356543050158953", "game_valid": False},
        "context": {"game_modes": ["driver_career_25"], "session_types": ["practice_1"]},
        "channels": {"speed": {"observed_sample_count": 2, "segments": []}},
    }
    monkeypatch.setattr(
        api_module,
        "load_attempt_trace_chart_preview",
        lambda _database, attempt_key: preview if attempt_key == "run:42:0:1" else None,
    )

    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/attempts/run%3A42%3A0%3A1/traces",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["data"]["artifact_kind"] == "single_attempt_player_trace_preview"
    assert response.json()["data"]["source"]["session_uid"] == "14237356543050158953"
    assert response.json()["data"]["context"]["game_modes"] == ["driver_career_25"]


def test_attempt_traces_api_maps_source_limit_to_explicit_unavailable(monkeypatch, tmp_path) -> None:
    def limited(*_args, **_kwargs):
        raise api_module.TraceChartUnavailable("trace_chart_source_rows_limit_exceeded")

    monkeypatch.setattr(api_module, "load_attempt_trace_chart_preview", limited)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/attempts/legacy%3A42%3A0%3A1/traces",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["reason"] == "trace_chart_source_rows_limit_exceeded"


def test_attempt_regions_api_resolves_registered_model_and_returns_report(
    monkeypatch, tmp_path
) -> None:
    model = object()
    calls: dict[str, object] = {}
    report = {
        "schema_version": 1,
        "artifact_kind": "single_attempt_distance_region_observations",
        "diagnostic_only": True,
        "source": {"session_uid": "14237356543050158953"},
        "model": {"model_id": "melbourne-draft-v1", "revision": 1},
        "regions": [],
    }

    def resolve_entry(model_id: str, revision: int):
        calls["identity"] = (model_id, revision)
        return SimpleNamespace(
            model=model,
            metadata=lambda: {
                "model_id": model_id,
                "revision": revision,
                "origin": "local_draft",
                "content_sha256": "a" * 64,
                "source_filename": "local.json",
            },
        )

    def load(_database, attempt_key: str, selected_model, *, model_metadata):
        calls["attempt_key"] = attempt_key
        calls["model"] = selected_model
        calls["model_metadata"] = model_metadata
        return report

    monkeypatch.setattr(
        api_module,
        "load_track_model_catalog",
        lambda *_args: SimpleNamespace(resolve_entry=resolve_entry),
    )
    monkeypatch.setattr(api_module, "load_attempt_region_report", load)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/attempts/run%3A42%3A0%3A1/regions",
        params={
            "track_model_id": "melbourne-draft-v1",
            "track_model_revision": "1",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["data"]["diagnostic_only"] is True
    assert response.json()["data"]["source"]["session_uid"] == "14237356543050158953"
    assert calls == {
        "identity": ("melbourne-draft-v1", 1),
        "attempt_key": "run:42:0:1",
        "model": model,
        "model_metadata": {
            "model_id": "melbourne-draft-v1",
            "revision": 1,
            "origin": "local_draft",
            "content_sha256": "a" * 64,
            "source_filename": "local.json",
        },
    }


def test_attempt_regions_api_requires_registered_model_identity_and_reports_policy(
    monkeypatch, tmp_path
) -> None:
    def unsupported(*_args, **_kwargs):
        raise api_module.RegionReportUnavailable("unsupported_mode")

    monkeypatch.setattr(
        api_module,
        "load_track_model_catalog",
        lambda *_args: SimpleNamespace(
            resolve_entry=lambda *_identity: SimpleNamespace(
                model=object(), metadata=lambda: {}
            )
        ),
    )
    monkeypatch.setattr(api_module, "load_attempt_region_report", unsupported)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/attempts/run%3A42%3A0%3A1/regions",
        params={"track_model_id": "melbourne-draft-v1", "track_model_revision": "1"},
    )
    incomplete = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/attempts/run%3A42%3A0%3A1/regions",
        params={"track_model_id": "melbourne-draft-v1"},
    )

    assert response.json()["status"] == "unavailable"
    assert response.json()["reason"] == "unsupported_mode"
    assert incomplete.json()["status"] == "unavailable"
    assert incomplete.json()["reason"] == "track_model_id_and_revision_must_be_selected_together"


def test_attempt_regions_api_maps_trace_file_errors_to_unavailable(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        api_module,
        "load_track_model_catalog",
        lambda *_args: SimpleNamespace(
            resolve_entry=lambda *_identity: SimpleNamespace(
                model=object(), metadata=lambda: {}
            )
        ),
    )

    def missing_trace(*_args, **_kwargs):
        raise FileNotFoundError("trace disappeared during load")

    monkeypatch.setattr(api_module, "load_attempt_region_report", missing_trace)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/attempts/run%3A42%3A0%3A1/regions",
        params={"track_model_id": "melbourne-draft-v1", "track_model_revision": "1"},
    )

    assert response.json()["status"] == "unavailable"
    assert response.json()["reason"] == "attempt_trace_unavailable"


def test_paired_regions_api_resolves_catalog_model_and_preserves_diagnostic_scope(
    monkeypatch, tmp_path
) -> None:
    model = object()
    calls: dict[str, object] = {}

    def resolve_entry(model_id: str, revision: int):
        calls["identity"] = (model_id, revision)
        return SimpleNamespace(
            model=model,
            metadata=lambda: {
                "model_id": model_id,
                "revision": revision,
                "origin": "local_draft",
                "content_sha256": "d" * 64,
                "source_filename": "local-draft.json",
            },
        )

    def compare(_database, target_key, reference_key, selected_model, **kwargs):
        calls["pair"] = (target_key, reference_key)
        calls["model"] = selected_model
        calls["metadata"] = kwargs["model_metadata"]
        calls["policy"] = kwargs["policy"]
        return {
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
            "model": kwargs["model_metadata"],
            "attempts": {
                "target": {"session_uid": 18_446_744_073_709_551_600},
                "reference": {"session_uid": 18_446_744_073_709_551_600},
            },
        }

    monkeypatch.setattr(
        api_module,
        "load_track_model_catalog",
        lambda *_args: SimpleNamespace(resolve_entry=resolve_entry),
    )
    monkeypatch.setattr(api_module, "compare_attempt_regions", compare)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/regions",
        params={
            "target_attempt_key": "target:42:0:1",
            "reference_attempt_key": "reference:42:0:2",
            "comparison_policy": "practice_qualifying",
            "track_model_id": "local-model",
            "track_model_revision": "3",
        },
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["artifact_kind"] == "paired_distance_region_observations"
    assert payload["diagnostic_only"] is True
    assert payload["coaching_eligible"] is False
    assert payload["ranking_eligible"] is False
    assert payload["regions"][0]["debrief"]["analysis_version"] == (
        "diagnostic-region-debrief-v1"
    )
    assert payload["regions"][0]["debrief"]["facts"][0]["text"] == "A measured fact."
    assert payload["regions"][0]["supported_differences"][
        "brake_10_percent_release"
    ]["analysis_version"] == "brake-threshold-release-v1"
    assert payload["regions"][0]["supported_differences"][
        "brake_10_percent_release"
    ]["target_minus_reference_end_bracket_m"] == [-3.0, -1.0]
    assert payload["model"]["origin"] == "local_draft"
    assert payload["attempts"]["target"]["session_uid"] == "18446744073709551600"
    assert calls["identity"] == ("local-model", 3)
    assert calls["pair"] == ("target:42:0:1", "reference:42:0:2")
    assert calls["model"] is model
    assert calls["policy"] == "practice_qualifying"


def test_paired_regions_api_requires_model_identity_and_keeps_abstention_reason(
    monkeypatch, tmp_path
) -> None:
    def unsupported(*_args, **_kwargs):
        raise api_module.PairedRegionReportUnavailable("region_pair_must_share_session")

    monkeypatch.setattr(
        api_module,
        "load_track_model_catalog",
        lambda *_args: SimpleNamespace(
            resolve_entry=lambda *_identity: SimpleNamespace(model=object(), metadata=lambda: {})
        ),
    )
    monkeypatch.setattr(api_module, "compare_attempt_regions", unsupported)
    app = create_app(tmp_path / "unused.sqlite3")
    base = {
        "target_attempt_key": "target",
        "reference_attempt_key": "reference",
    }
    unavailable = _get(
        app,
        "/api/v1/compare/regions",
        params={
            **base,
            "track_model_id": "local-model",
            "track_model_revision": "3",
        },
    )
    incomplete = _get(
        app,
        "/api/v1/compare/regions",
        params={**base, "track_model_id": "local-model"},
    )

    assert unavailable.json()["status"] == "unavailable"
    assert unavailable.json()["reason"] == "region_pair_must_share_session"
    assert incomplete.json()["status"] == "unavailable"
    assert incomplete.json()["reason"] == "track_model_id_and_revision_must_be_selected_together"


def test_compare_api_resolves_explicit_model_id_and_revision(monkeypatch, tmp_path) -> None:
    model_id, revision = next(iter(registry.TRACK_MODEL_REGISTRY))
    resolved_model = registry.TRACK_MODEL_REGISTRY[(model_id, revision)]
    calls: dict[str, object] = {}

    def compare(*_args, track_model=None, track_model_catalog=None, **_kwargs):
        calls["model"] = track_model
        calls["catalog"] = track_model_catalog
        return {"corner_analysis": {"diagnostic_only": True, "regions": []}}

    monkeypatch.setattr(api_module, "compare_attempts", compare)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/laps",
        params={
            "target_attempt_key": "run:42:0:2",
            "reference_attempt_key": "run:42:0:1",
            "track_model_id": model_id,
            "track_model_revision": str(revision),
        },
    )

    assert response.status_code == 200
    assert calls["model"] is resolved_model
    assert calls["catalog"].resolve_entry(model_id, revision).model is resolved_model
    assert response.json()["data"]["corner_analysis"]["diagnostic_only"] is True


def test_compare_api_requires_and_forwards_the_selected_comparison_policy(
    monkeypatch, tmp_path
) -> None:
    calls: dict[str, object] = {}

    def compare(*_args, **kwargs):
        calls.update(kwargs)
        return {"comparison_policy": kwargs["policy"], "diagnostic_only": True}

    monkeypatch.setattr(api_module, "compare_attempts", compare)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/laps",
        params={
            "target_attempt_key": "run:42:7:3",
            "reference_attempt_key": "run:42:7:2",
            "comparison_policy": "practice_qualifying",
            "window_start_m": "100",
            "window_end_m": "200.5",
        },
    )

    assert response.status_code == 200
    assert calls["policy"] == "practice_qualifying"
    assert calls["distance_window"] == DistanceWindow(100.0, 200.5)
    assert response.json()["data"]["comparison_policy"] == "practice_qualifying"


def test_compare_api_returns_the_shared_structured_brief(monkeypatch, tmp_path) -> None:
    brief = {
        "schema_version": 1,
        "analysis_version": "comparison-brief-v1",
        "status": "available",
        "text": "Target was 1.000 s slower than the reference by official lap time.",
        "facts": [{"kind": "official_lap_time_difference", "unit": "s"}],
        "limitations": [],
    }
    corner_brief = {
        "schema_version": 1,
        "analysis_version": "corner-comparison-brief-v1",
        "status": "abstained",
        "coaching_eligible": False,
    }
    lap_debrief = {
        "schema_version": 1,
        "analysis_version": "lap-debrief-v1",
        "status": "partial",
        "diagnostic_only": True,
        "coaching_eligible": False,
    }
    driving_pattern_assessment = {
        "schema_version": 1,
        "analysis_version": "driving-pattern-assessment-v1",
        "status": "abstained",
        "diagnostic_only": True,
        "coaching_eligible": False,
        "action": None,
    }
    throttle_pattern_assessment = {
        "schema_version": 1,
        "analysis_version": "throttle-pattern-assessment-v1",
        "status": "abstained",
        "diagnostic_only": True,
        "coaching_eligible": False,
        "action": None,
    }
    distance_window_brief = {
        "schema_version": 1,
        "analysis_version": "distance-window-brief-v1",
        "status": "available",
        "diagnostic_only": True,
        "coaching_eligible": False,
    }
    monkeypatch.setattr(
        api_module,
        "compare_attempts",
        lambda *_args, **_kwargs: {
            "comparison_brief": brief,
            "corner_comparison_brief": corner_brief,
            "lap_debrief": lap_debrief,
            "driving_pattern_assessment": driving_pattern_assessment,
            "throttle_pattern_assessment": throttle_pattern_assessment,
            "distance_window_brief": distance_window_brief,
        },
    )

    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/laps",
        params={
            "target_attempt_key": "target",
            "reference_attempt_key": "reference",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["comparison_brief"] == brief
    assert response.json()["data"]["corner_comparison_brief"] == corner_brief
    assert response.json()["data"]["lap_debrief"] == lap_debrief
    assert response.json()["data"]["driving_pattern_assessment"] == driving_pattern_assessment
    assert response.json()["data"]["throttle_pattern_assessment"] == throttle_pattern_assessment
    assert response.json()["data"]["distance_window_brief"] == distance_window_brief


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"window_start_m": "100"}, "bounds_must_be_selected_together"),
        ({"window_end_m": "200"}, "bounds_must_be_selected_together"),
        ({"window_start_m": "nan", "window_end_m": "200"}, "invalid_bounds"),
    ],
)
def test_compare_api_rejects_incomplete_or_invalid_window_bounds(
    params, reason, tmp_path
) -> None:
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/laps",
        params={
            "target_attempt_key": "run:42:7:3",
            "reference_attempt_key": "run:42:7:2",
            **params,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert reason in response.json()["reason"]


def test_compare_api_rejects_unknown_comparison_policy(tmp_path) -> None:
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/laps",
        params={
            "target_attempt_key": "run:42:7:3",
            "reference_attempt_key": "run:42:7:2",
            "comparison_policy": "race",
        },
    )

    assert response.status_code == 422


def test_compare_api_reports_missing_trace_as_unavailable(monkeypatch, tmp_path) -> None:
    def compare(*_args, **_kwargs):
        raise FileNotFoundError("trace file disappeared")

    monkeypatch.setattr(api_module, "compare_attempts", compare)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/laps",
        params={
            "target_attempt_key": "run:42:7:3",
            "reference_attempt_key": "run:42:7:2",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["reason"] == "attempt_trace_unavailable"


def test_observation_set_api_accepts_repeated_attempt_keys_and_window(
    monkeypatch, tmp_path
) -> None:
    calls: dict[str, object] = {}
    document = {
        "artifact_kind": "selected_window_observation_set",
        "diagnostic_only": True,
        "session_uid": 18446744073709551615,
        "onset_repeatability": {
            "analysis_version": "selected-window-onset-spread-v1",
            "consistency_claim": False,
        },
    }

    def build(database, attempt_keys, window, **kwargs):
        calls.update(
            database=database,
            attempt_keys=attempt_keys,
            window=window,
            kwargs=kwargs,
        )
        return document

    monkeypatch.setattr(api_module, "build_observation_set", build)
    app = create_app(tmp_path / "unused.sqlite3")
    response = _get(
        app,
        "/api/v1/analysis/observation-set",
        params=[
            ("attempt_key", "attempt-1"),
            ("attempt_key", "attempt-2"),
            ("comparison_policy", "practice_qualifying"),
            ("window_start_m", "500"),
            ("window_end_m", "1200"),
        ],
    )

    assert response.status_code == 200
    assert response.json()["data"]["session_uid"] == "18446744073709551615"
    assert response.json()["data"]["onset_repeatability"] == document["onset_repeatability"]
    assert calls["attempt_keys"] == ["attempt-1", "attempt-2"]
    assert calls["window"] == DistanceWindow(500.0, 1200.0)
    assert calls["kwargs"] == {"policy": "practice_qualifying"}


def test_observation_set_api_caps_repeated_attempt_keys(tmp_path) -> None:
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/analysis/observation-set",
        params=[
            *(("attempt_key", f"attempt-{index}") for index in range(9)),
            ("window_start_m", "1"),
            ("window_end_m", "2"),
        ],
    )

    assert response.status_code == 422


def test_compare_api_rejects_unknown_or_incomplete_track_model_identity(
    monkeypatch, tmp_path
) -> None:
    app = create_app(tmp_path / "unused.sqlite3")
    common = {
        "target_attempt_key": "run:42:0:2",
        "reference_attempt_key": "run:42:0:1",
    }
    unknown_response = _get(
        app,
        "/api/v1/compare/laps",
        params={
            **common,
            "track_model_id": "unknown",
            "track_model_revision": "9",
        },
    )
    incomplete_response = _get(
        app,
        "/api/v1/compare/laps",
        params={**common, "track_model_id": next(iter(registry.TRACK_MODEL_REGISTRY))[0]},
    )

    assert unknown_response.json()["status"] == "unavailable"
    assert unknown_response.json()["reason"] == "unknown_track_model_revision"
    assert incomplete_response.json()["status"] == "unavailable"
    assert (
        incomplete_response.json()["reason"]
        == "track_model_id_and_revision_must_be_selected_together"
    )


def test_sessions_api_returns_503_without_creating_a_database(tmp_path) -> None:
    database_path = tmp_path / "not-created.sqlite3"
    response = _get(create_app(database_path), "/api/v1/sessions")

    assert response.status_code == 503
    assert response.json()["reason"] == "configured_database_unavailable"
    assert not database_path.exists()


def test_storage_usage_api_measures_only_configured_locations(tmp_path) -> None:
    database = tmp_path / "managed" / "engineer.sqlite3"
    recordings = tmp_path / "managed-recordings"
    database.parent.mkdir()
    database.write_bytes(b"database")
    recordings.mkdir()
    (recordings / "session.f1ecap").write_bytes(b"capture-data")
    app = create_app(database, recordings_root=recordings)

    response = _get(
        app,
        "/api/v1/storage/usage",
        params={"database_path": str(tmp_path / "attacker.sqlite3"), "root": str(tmp_path)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["api_version"] == "v1"
    assert body["status"] == "ok"
    assert body["data"]["scopes"]["database"]["logical_bytes"] == len(b"database")
    assert body["data"]["scopes"]["finalized_captures"]["logical_bytes"] == len(b"capture-data")
    assert str(tmp_path) not in response.text


def test_telemetry_service_reports_bounded_effective_configuration(tmp_path) -> None:
    app = create_app(
        tmp_path / "telemetry.sqlite3",
        recordings_root=tmp_path / "captures",
        recording_host="127.0.0.1",
        recording_port=20888,
        recording_queue_size=4096,
    )

    response = _get(app, "/api/v1/telemetry/service")

    assert response.status_code == 200
    assert len(response.content) <= 4096
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["api_version"] == "v1"
    assert body["status"] == "ok"
    assert body["data"]["udp_bind_host"] == "127.0.0.1"
    assert body["data"]["udp_port"] == 20888
    assert body["data"]["receive_queue_size"] == 4096
    assert body["data"]["configuration_available"] is True
    assert body["data"]["controller_ready"] is False
    assert body["data"]["operation_reservation"] == "unavailable"
    assert body["data"]["unavailable_reason"] == "recording_controller_unavailable"
    assert body["data"]["observed_at_utc"].endswith("Z")


def test_telemetry_service_rejects_query_parameters_and_unsupported_configuration(
    tmp_path,
) -> None:
    query_response = _get(
        create_app(tmp_path / "query.sqlite3"),
        "/api/v1/telemetry/service",
        params={"host": "127.0.0.1"},
    )
    assert query_response.status_code == 422
    assert (
        query_response.json()["reason"]
        == "telemetry_service_query_parameters_unsupported"
    )

    unsupported = _get(
        create_app(
            tmp_path / "unsupported.sqlite3",
            recording_queue_size=1_000_001,
        ),
        "/api/v1/telemetry/service",
    )
    assert unsupported.status_code == 200
    data = unsupported.json()["data"]
    assert data["configuration_available"] is False
    assert data["udp_bind_host"] is None
    assert data["udp_port"] is None
    assert data["receive_queue_size"] is None
    assert data["unavailable_reason"] == "telemetry_configuration_unsupported"


def test_telemetry_service_reports_reservations_without_changing_them(tmp_path) -> None:
    app = create_app(tmp_path / "reservations.sqlite3")

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                controller = app.state.import_controller
                for operation in ("recording", "import", "replay", "upload"):
                    assert controller.reserve_operation(operation)
                    first = await client.get("/api/v1/telemetry/service")
                    second = await client.get("/api/v1/telemetry/service")
                    assert first.status_code == second.status_code == 200
                    assert first.json()["data"]["controller_ready"] is True
                    assert first.json()["data"]["operation_reservation"] == operation
                    assert second.json()["data"]["operation_reservation"] == operation
                    assert controller.current_operation_reservation == operation
                    controller.release_operation(operation)
                idle = await client.get("/api/v1/telemetry/service")
                assert idle.json()["data"]["operation_reservation"] == "idle"

    asyncio.run(exercise())


def test_engineer_ask_rejects_second_active_request_as_busy(monkeypatch, tmp_path) -> None:
    app = create_app(
        tmp_path / "engineer-ask.sqlite3",
        recordings_root=tmp_path / "captures",
        control_token="test-engineer-control-token",
        recording_host="127.0.0.1",
        recording_port=20889,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_answer(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return {
            "schema_version": 1,
            "analysis_version": "engineer-ask-v1",
            "status": "unavailable",
            "route": None,
            "focus": None,
            "message": "Selected evidence is unavailable.",
            "selection": {"intent": "attempt_summary", "target_attempt_key": "run:1:0:2"},
            "report": None,
            "debrief": None,
            "debrief_evidence": None,
            "model": None,
            "diagnostic_only": True,
            "coaching_eligible": False,
            "ranking_eligible": False,
        }

    monkeypatch.setattr(api_module, "answer_engineer_question", hold_answer)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {
                "Authorization": "Bearer test-engineer-control-token",
                "Content-Type": "application/json",
            }
            body = {
                "question": "What does the lap show?",
                "selection": {
                    "intent": "attempt_summary",
                    "target_attempt_key": "run:1:0:2",
                },
            }
            first = asyncio.create_task(
                client.post("/api/v1/engineer/ask", headers=headers, json=body)
            )
            await asyncio.wait_for(entered.wait(), timeout=2)
            second = await client.post(
                "/api/v1/engineer/ask", headers=headers, json=body
            )
            assert second.status_code == 409
            assert second.json()["reason"] == "engineer_ask_busy"
            release.set()
            first_response = await first
            assert first_response.status_code == 200
            assert first_response.json()["data"]["status"] == "unavailable"

    asyncio.run(exercise())
