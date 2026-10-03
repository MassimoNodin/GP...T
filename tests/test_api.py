from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

import f1_engineer.api.app as api_module
from f1_engineer.api.app import create_app


def _get(app, path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, params=params)

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

    def resolve(model_id: str, revision: int):
        calls["identity"] = (model_id, revision)
        return model

    def load(_database, attempt_key: str, selected_model):
        calls["attempt_key"] = attempt_key
        calls["model"] = selected_model
        return report

    monkeypatch.setattr(api_module, "resolve_track_model", resolve)
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
    }


def test_attempt_regions_api_requires_registered_model_identity_and_reports_policy(
    monkeypatch, tmp_path
) -> None:
    def unsupported(*_args, **_kwargs):
        raise api_module.RegionReportUnavailable("unsupported_mode")

    monkeypatch.setattr(api_module, "resolve_track_model", lambda *_args: object())
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
    monkeypatch.setattr(api_module, "resolve_track_model", lambda *_args: object())

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


def test_compare_api_resolves_explicit_model_id_and_revision(monkeypatch, tmp_path) -> None:
    resolved_model = object()
    calls: dict[str, object] = {}

    def resolve(model_id: str, revision: int):
        calls["resolved"] = (model_id, revision)
        return resolved_model

    def compare(*_args, track_model=None, **_kwargs):
        calls["model"] = track_model
        return {"corner_analysis": {"diagnostic_only": True, "regions": []}}

    monkeypatch.setattr(api_module, "resolve_track_model", resolve)
    monkeypatch.setattr(api_module, "compare_attempts", compare)
    response = _get(
        create_app(tmp_path / "unused.sqlite3"),
        "/api/v1/compare/laps",
        params={
            "target_attempt_key": "run:42:0:2",
            "reference_attempt_key": "run:42:0:1",
            "track_model_id": "melbourne-f1-25-time-trial-draft-v1",
            "track_model_revision": "1",
        },
    )

    assert response.status_code == 200
    assert calls["resolved"] == ("melbourne-f1-25-time-trial-draft-v1", 1)
    assert calls["model"] is resolved_model
    assert response.json()["data"]["corner_analysis"]["diagnostic_only"] is True


def test_compare_api_rejects_unknown_or_incomplete_track_model_identity(
    monkeypatch, tmp_path
) -> None:
    def unknown(_model_id: str, _revision: int):
        raise ValueError("unknown_track_model_revision")

    monkeypatch.setattr(api_module, "resolve_track_model", unknown)
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
        params={**common, "track_model_id": "melbourne-f1-25-time-trial-draft-v1"},
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
