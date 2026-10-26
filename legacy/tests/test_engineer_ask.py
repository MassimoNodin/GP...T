from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from f1_engineer.analysis.engineer_ask import parse_router_output
from f1_engineer.analysis.engineer_ask_service import (
    _lap_debrief_evidence_projection,
    _lap_debrief_is_selectable,
)
from f1_engineer.analysis.ollama_runtime import (
    ModelPin,
    OllamaRuntime,
    OllamaUnavailable,
    _is_supported_ollama_version,
)


def test_router_output_rejects_deep_json_and_unpaired_unicode() -> None:
    deeply_nested = '{"route":' + ("[" * 1_000) + "0" + ("]" * 1_000) + ',"focus":"overview"}'
    assert len(deeply_nested.encode("utf-8")) < 2_048
    with pytest.raises(ValueError):
        parse_router_output(deeply_nested, allowed_routes=("attempt_summary",))

    with pytest.raises(ValueError, match="router_output_malformed"):
        parse_router_output(
            '{"route":"attempt_summary","focus":"overview"}\ud800',
            allowed_routes=("attempt_summary",),
        )


def test_debrief_is_time_trial_only_and_rejects_local_draft() -> None:
    model = SimpleNamespace(corners=[SimpleNamespace(identifier="T1")])
    catalog = SimpleNamespace(
        resolve_entry=lambda _model_id, _revision: SimpleNamespace(
            origin="reviewed", model=model
        )
    )
    selection = {
        "comparison_policy": "time_trial",
        "track_model_id": "melbourne",
        "track_model_revision": 1,
        "region_identifier": "T1",
    }
    assert _lap_debrief_is_selectable(selection, catalog) is True
    assert _lap_debrief_is_selectable(
        {**selection, "comparison_policy": "practice_qualifying"}, catalog
    ) is False
    assert _lap_debrief_is_selectable(
        {**selection, "region_identifier": "missing"}, catalog
    ) is False

    catalog.resolve_entry = lambda _model_id, _revision: SimpleNamespace(
        origin="local_draft", model=model
    )
    assert _lap_debrief_is_selectable(selection, catalog) is False


def test_debrief_projection_keeps_exact_model_fingerprint_fields() -> None:
    model = {
        "model_id": "melbourne",
        "revision": 1,
        "validation_status": "validated",
        "registered": True,
        "approved_for_candidate_ranking": True,
        "origin": "reviewed",
        "content_sha256": "a" * 64,
        "model_content_sha256": "b" * 64,
        "ignored": "not projected",
    }
    comparison = {
        "comparison_policy": "time_trial",
        "target": {"attempt_key": "target", "trace_sha256": "c" * 64},
        "reference": {"attempt_key": "reference", "trace_sha256": "d" * 64},
        "track": {"track_length_m": 5_000},
        "comparison_brief": {"schema_version": 1, "facts": [], "ignored": True},
        "corner_loss_candidates": {
            "schema_version": 1,
            "ranked_candidates": [],
            "source": {"model": model},
        },
        "corner_comparison_brief": {"schema_version": 1, "regions": []},
        "corner_analysis": {"diagnostic_only": True, "source": {}, "model": {}},
    }

    evidence = _lap_debrief_evidence_projection(comparison)

    assert evidence is not None
    projected_model = evidence["corner_loss_candidates"]["source"]["model"]
    assert projected_model["origin"] == "reviewed"
    assert projected_model["content_sha256"] == "a" * 64
    assert projected_model["model_content_sha256"] == "b" * 64
    assert "ignored" not in projected_model


def test_runtime_rejects_model_pin_changed_after_readiness(monkeypatch, tmp_path) -> None:
    runtime = OllamaRuntime(tmp_path / "pin.json")
    verified_digest = "sha256:" + "a" * 64
    changed_pin = ModelPin(
        model="qwen3:4b",
        digest="sha256:" + "b" * 64,
        profile="gp-dot-t-private-v1",
        endpoint="http://127.0.0.1:11435",
        no_cloud_requested=True,
    )
    monkeypatch.setattr(
        runtime,
        "status",
        lambda: _ready_status(verified_digest),
    )
    monkeypatch.setattr(runtime, "_load_pin", lambda: changed_pin)

    with pytest.raises(OllamaUnavailable, match="model_pin_changed_during_request"):
        asyncio.run(
            runtime.route_question(
                "What does the lap show?",
                selection_kind="attempt",
                allowed_routes=("attempt_summary",),
            )
        )


def test_runtime_version_floor_covers_thinking_control() -> None:
    assert _is_supported_ollama_version("0.9.0") is True
    assert _is_supported_ollama_version("0.12.4") is True
    assert _is_supported_ollama_version("0.5.7") is False
    assert _is_supported_ollama_version("0.9.0-rc1") is False
    assert _is_supported_ollama_version("9" * 5_000 + ".0.0") is False


def test_runtime_reports_unsupported_version_before_loading_model_catalog(monkeypatch, tmp_path) -> None:
    runtime = OllamaRuntime(tmp_path / "pin.json")
    pin = ModelPin(
        model="qwen3:4b",
        digest="sha256:" + "a" * 64,
        profile="gp-dot-t-private-v1",
        endpoint="http://127.0.0.1:11435",
        no_cloud_requested=True,
    )
    monkeypatch.setattr(runtime, "_load_pin", lambda: pin)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(runtime, "_client", lambda **_kwargs: FakeClient())
    paths: list[str] = []

    async def get_json(_client, path, _maximum, **_kwargs):
        paths.append(path)
        return {"version": "0.5.7"}

    monkeypatch.setattr(runtime, "_get_json", get_json)
    status = asyncio.run(runtime.status())

    assert status["reason"] == "runtime_version_unsupported"
    assert status["runtime_version"] == "0.5.7"
    assert paths == ["/api/version"]


def test_runtime_bounds_oversized_version_in_status(monkeypatch, tmp_path) -> None:
    runtime = OllamaRuntime(tmp_path / "pin.json")
    pin = ModelPin(
        model="qwen3:4b",
        digest="sha256:" + "a" * 64,
        profile="gp-dot-t-private-v1",
        endpoint="http://127.0.0.1:11435",
        no_cloud_requested=True,
    )
    monkeypatch.setattr(runtime, "_load_pin", lambda: pin)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(runtime, "_client", lambda **_kwargs: FakeClient())

    async def get_json(_client, _path, _maximum, **_kwargs):
        return {"version": "9" * 5_000 + ".0.0"}

    monkeypatch.setattr(runtime, "_get_json", get_json)
    status = asyncio.run(runtime.status())

    assert status["reason"] == "runtime_version_unsupported"
    assert status["runtime_version"] is None


def test_runtime_pin_records_no_cloud_as_requested_configuration(tmp_path) -> None:
    pin_path = tmp_path / "pin.json"
    pin_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile": "gp-dot-t-private-v1",
                "endpoint": "http://127.0.0.1:11435",
                "no_cloud_requested": True,
                "model": "qwen3:4b",
                "digest": "sha256:" + "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    runtime = OllamaRuntime(pin_path)

    assert runtime._load_pin() == ModelPin(
        model="qwen3:4b",
        digest="sha256:" + "a" * 64,
        profile="gp-dot-t-private-v1",
        endpoint="http://127.0.0.1:11435",
        no_cloud_requested=True,
    )


async def _ready_status(digest: str) -> dict[str, object]:
    return {"status": "ready", "model_name": "qwen3:4b", "model_digest": digest}
