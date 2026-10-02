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
