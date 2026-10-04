from __future__ import annotations

import hashlib
from dataclasses import replace
import json
from pathlib import Path

import pytest

from f1_engineer.tracks import registry


MODEL_SOURCE = (
    Path(__file__).parents[1]
    / "f1_engineer"
    / "tracks"
    / "data"
    / "melbourne_f1_25_tt_draft_v1.json"
)


def _write_local_model(directory: Path, *, model_id: str, filename: str) -> Path:
    value = json.loads(MODEL_SOURCE.read_text(encoding="utf-8"))
    value["model_id"] = model_id
    value["track_id"] = 12
    value["track_name"] = "Shanghai"
    value["track_length_m"] = 5451
    path = directory / filename
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_candidate_ranking_needs_content_bound_registry_approval(monkeypatch) -> None:
    draft = next(iter(registry.TRACK_MODEL_REGISTRY.values()))
    validated = replace(draft, validation_status="validated")
    key = (validated.model_id, validated.revision)

    monkeypatch.setattr(registry, "TRACK_MODEL_REGISTRY", {key: validated})
    unapproved = registry.corner_candidate_ranking_approval(validated)
    assert unapproved["registered"] is True
    assert unapproved["approved_for_candidate_ranking"] is False
    assert unapproved["reason"] == "track_model_not_approved_for_candidate_ranking"

    monkeypatch.setattr(
        registry,
        "CORNER_CANDIDATE_RANKING_APPROVALS",
        {
            key: {
                "model_content_sha256": registry._model_fingerprint(validated),
                "approval_provenance": {"review_record": "synthetic-test-review"},
            }
        },
    )
    approved = registry.corner_candidate_ranking_approval(validated)
    assert approved["approved_for_candidate_ranking"] is True
    assert approved["model_content_sha256"] == registry._model_fingerprint(validated)

    altered = replace(validated, provenance="changed after review")
    altered_assessment = registry.corner_candidate_ranking_approval(altered)
    assert altered_assessment["registered"] is False
    assert altered_assessment["approved_for_candidate_ranking"] is False


def test_local_catalog_models_are_immutable_drafts_and_not_rankable(tmp_path) -> None:
    source = _write_local_model(
        tmp_path, model_id="local-shanghai-v1", filename="shanghai.json"
    )
    catalog = registry.load_track_model_catalog(tmp_path)
    entry = catalog.resolve_entry("local-shanghai-v1", 1)
    metadata = entry.metadata()

    assert entry.origin == "local_draft"
    assert metadata["origin"] == "local_draft"
    expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    assert metadata["content_sha256"] == expected_sha256
    assert metadata["source_filename"] == source.name
    assert entry.model.validation_status == "draft"
    assert ("local-shanghai-v1", 1) not in registry.TRACK_MODEL_REGISTRY
    with pytest.raises(ValueError, match="unknown_track_model_revision"):
        registry.resolve_track_model("local-shanghai-v1", 1)

    original_metadata = entry.metadata()
    _write_local_model(tmp_path, model_id="local-replacement", filename=source.name)
    assert entry.metadata() == original_metadata


def test_local_catalog_rejects_json_symlinks_that_escape_root(tmp_path) -> None:
    model_root = tmp_path / "models"
    model_root.mkdir()
    outside = tmp_path / "outside.json"
    _write_local_model(tmp_path, model_id="local-outside", filename=outside.name)
    link = model_root / "outside.json"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlinks are not available: {exc}")

    with pytest.raises(ValueError, match="local_track_model_path_outside_root"):
        registry.load_track_model_catalog(model_root)


def test_local_catalog_rejects_packaged_id_shadow_and_validated_models(tmp_path) -> None:
    packaged_id = next(iter(registry.TRACK_MODEL_REGISTRY))[0]
    _write_local_model(tmp_path, model_id=packaged_id, filename="shadow.json")
    with pytest.raises(ValueError, match="shadows_packaged_model"):
        registry.load_track_model_catalog(tmp_path)

    path = tmp_path / "shadow.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["model_id"] = "local-validated"
    value["validation_status"] = "validated"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="must_remain_draft"):
        registry.load_track_model_catalog(tmp_path)


def test_local_catalog_rejects_duplicate_identity_and_excess_model_count(tmp_path) -> None:
    _write_local_model(tmp_path, model_id="local-shanghai-v1", filename="a.json")
    _write_local_model(tmp_path, model_id="local-shanghai-v1", filename="b.json")
    with pytest.raises(ValueError, match="duplicate_track_model_id_and_revision"):
        registry.load_track_model_catalog(tmp_path)

    for path in tmp_path.glob("*.json"):
        path.unlink()
    for index in range(registry.MAX_LOCAL_TRACK_MODELS + 1):
        (tmp_path / f"model-{index}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="local_track_model_count_limit_exceeded"):
        registry.load_track_model_catalog(tmp_path)
