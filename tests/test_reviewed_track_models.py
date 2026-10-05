from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from f1_engineer.tracks.fingerprint import track_model_fingerprint
from f1_engineer.tracks.model import CornerDefinition, TrackModel
from f1_engineer.tracks.registry import (
    corner_candidate_ranking_approval,
    load_track_model_catalog,
)
from f1_engineer.tracks.reviewed_bundle import (
    MAX_REVIEWED_TRACK_MODEL_DIRECTORY_ENTRIES,
    MAX_REVIEWED_TRACK_MODELS,
    MAX_TRACK_MODEL_JSON_BYTES,
    validate_reviewed_track_model_bundle,
)


def _model(
    model_id: str = "reviewed-test-model", *, status: str = "validated", label: str = "Turn"
) -> TrackModel:
    return TrackModel(
        model_id=model_id,
        revision=1,
        packet_format=2025,
        track_id=0,
        track_name="Test Circuit",
        layout_id="operator-declared-layout",
        track_length_m=1000.0,
        distance_origin_m=0.0,
        provenance="Synthetic test fixture only",
        validation_status=status,  # type: ignore[arg-type]
        corners=(
            CornerDefinition(
                identifier="turn-1",
                label=label,
                start_distance_m=100.0,
                end_distance_m=250.0,
                braking_search_window_m=(110.0, 170.0),
                throttle_pickup_window_m=(180.0, 230.0),
            ),
        ),
    )


def _bundle(model: TrackModel) -> dict[str, object]:
    return {
        "schema_version": 1,
        "bundle_version": "reviewed-distance-regions-v1",
        "model": {"schema_version": 1, **asdict(model)},
        "review": {
            "review_id": "review-001",
            "reviewer": "test-reviewer",
            "reviewed_at_utc": "2026-10-05T00:00:00Z",
            "scope": "distance_region_measurements",
            "model_fingerprint_version": "track-model-canonical-v1",
            "model_content_sha256": track_model_fingerprint(model),
            "evidence": [{"reference": "fixture-not-real", "sha256": None}],
            "notes": "Test-only operator assertion; no physical verification.",
        },
    }


def _write(root: Path, model: TrackModel, filename: str = "test.reviewed.json") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    path.write_text(json.dumps(_bundle(model), separators=(",", ":")), encoding="utf-8")
    return path


def test_reviewed_bundle_is_admitted_and_hashes_model_and_source_separately(tmp_path) -> None:
    root = tmp_path / "reviewed"
    path = _write(root, _model())
    bundle_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = load_track_model_catalog(reviewed_root=root)
    entry = catalog.resolve_entry("reviewed-test-model", 1)

    assert entry.origin == "reviewed"
    assert entry.model_content_sha256 == track_model_fingerprint(entry.model)
    assert entry.content_sha256 == bundle_sha256
    assert entry.metadata()["bundle_content_sha256"] == bundle_sha256
    assert entry.metadata()["source_kind"] == "review_bundle"
    assert entry.metadata()["approved_for_candidate_ranking"] is True
    assert corner_candidate_ranking_approval(entry.model, catalog)[
        "approved_for_candidate_ranking"
    ] is True
    validation = validate_reviewed_track_model_bundle(path)
    assert validation["valid"] is True
    assert validation["physical_geometry_verified"] is False


def test_reviewed_catalog_is_a_startup_snapshot_and_roots_are_isolated(tmp_path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_model = _model("first-model")
    second_model = _model("second-model")
    first_path = _write(first_root, first_model)
    _write(second_root, second_model)
    first_catalog = load_track_model_catalog(reviewed_root=first_root)
    second_catalog = load_track_model_catalog(reviewed_root=second_root)
    first_path.write_text("{}", encoding="utf-8")

    assert first_catalog.resolve_entry("first-model", 1).model == first_model
    assert corner_candidate_ranking_approval(first_model, second_catalog)["registered"] is False
    assert corner_candidate_ranking_approval(first_model, first_catalog)[
        "approved_for_candidate_ranking"
    ] is True


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda bundle: bundle["model"].update(track_name="Changed Circuit"), "reviewed_track_model_fingerprint_mismatch"),
        (lambda bundle: bundle["review"].update(scope="physical_geometry"), "reviewed_track_model_review_scope_unsupported"),
        (lambda bundle: bundle["review"].update(unexpected=True), "reviewed_track_model_review_fields_invalid"),
        (lambda bundle: bundle["review"].update(reviewed_at_utc="yesterday"), "reviewed_track_model_review_timestamp_invalid"),
    ],
)
def test_stale_or_out_of_contract_bundle_is_rejected(tmp_path, mutate, reason) -> None:
    bundle = _bundle(_model())
    mutate(bundle)
    path = tmp_path / "invalid.reviewed.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(ValueError, match=reason):
        validate_reviewed_track_model_bundle(path)


def test_duplicate_json_keys_unknown_bundle_fields_and_invalid_unicode_are_rejected(tmp_path) -> None:
    valid = json.dumps(_bundle(_model()), separators=(",", ":"))
    duplicate = valid.replace('{"schema_version":1,', '{"schema_version":1,"schema_version":1,', 1)
    path = tmp_path / "invalid.reviewed.json"
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed_track_model_duplicate_json_key"):
        validate_reviewed_track_model_bundle(path)

    bundle = _bundle(_model())
    bundle["unrecognized"] = True
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed_track_model_bundle_fields_invalid"):
        validate_reviewed_track_model_bundle(path)

    malformed_unicode = valid.replace("test-reviewer", "\\ud800")
    path.write_text(malformed_unicode, encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed_track_model_malformed_unicode"):
        validate_reviewed_track_model_bundle(path)


@pytest.mark.parametrize("model_id,accepted", [("m" * 256, True), ("m" * 257, False), ("🟠" * 128, True), ("🟠" * 129, False)])
def test_reviewed_model_identity_strings_match_browser_length(
    tmp_path, model_id, accepted
) -> None:
    model = _model(model_id)
    model_path = _write(tmp_path / f"model-{len(model_id)}-{accepted}", model)
    if accepted:
        assert validate_reviewed_track_model_bundle(model_path)["valid"] is True
    else:
        with pytest.raises(
            ValueError, match="reviewed_track_model_identity_string_limit_exceeded"
        ):
            validate_reviewed_track_model_bundle(model_path)

    region_name = "r" * (256 if accepted else 257)
    corner = replace(model.corners[0], identifier=region_name)
    region_model = replace(model, model_id="region-model", corners=(corner,))
    region_path = _write(tmp_path / f"region-{len(region_name)}-{accepted}", region_model)
    if accepted:
        assert validate_reviewed_track_model_bundle(region_path)["valid"] is True
    else:
        with pytest.raises(
            ValueError, match="reviewed_track_model_identity_string_limit_exceeded"
        ):
            validate_reviewed_track_model_bundle(region_path)


@pytest.mark.parametrize(
    "revision,accepted",
    [(10**15, True), (9_007_199_254_740_991, True), (9_007_199_254_740_992, False)],
)
def test_reviewed_model_revision_stays_javascript_safe(tmp_path, revision, accepted) -> None:
    model = replace(_model(), revision=revision)
    path = _write(tmp_path / f"revision-{revision}", model)
    if accepted:
        assert validate_reviewed_track_model_bundle(path)["valid"] is True
    else:
        with pytest.raises(ValueError, match="reviewed_track_model_revision_limit_exceeded"):
            validate_reviewed_track_model_bundle(path)


@pytest.mark.parametrize(
    "field,accepted_reason",
    [("reviewer", "reviewed_track_model_reviewer_invalid"),
     ("reference", "reviewed_track_model_reference_invalid")],
)
def test_review_provenance_strings_match_browser_length(
    tmp_path, field, accepted_reason
) -> None:
    for units, accepted in ((256, True), (258, False)):
        bundle = _bundle(_model())
        value = "🟠" * (units // 2)
        if field == "reviewer":
            bundle["review"]["reviewer"] = value
        else:
            bundle["review"]["evidence"][0]["reference"] = value
        path = tmp_path / f"{field}-{units}.reviewed.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        if accepted:
            assert validate_reviewed_track_model_bundle(path)["valid"] is True
        else:
            with pytest.raises(ValueError, match=accepted_reason):
                validate_reviewed_track_model_bundle(path)


def test_local_drafts_and_cross_origin_duplicate_identities_are_not_approved(tmp_path) -> None:
    draft_root = tmp_path / "drafts"
    reviewed_root = tmp_path / "reviewed"
    draft_root.mkdir()
    draft = _model(status="draft")
    (draft_root / "draft.json").write_text(
        json.dumps({"schema_version": 1, **asdict(draft)}), encoding="utf-8"
    )
    catalog = load_track_model_catalog(local_root=draft_root)
    assert corner_candidate_ranking_approval(draft, catalog)[
        "approved_for_candidate_ranking"
    ] is False

    _write(reviewed_root, _model())
    with pytest.raises(ValueError, match="duplicate_track_model_id_and_revision"):
        load_track_model_catalog(draft_root, reviewed_root)


def test_review_bundle_count_directory_and_file_size_limits_are_enforced(tmp_path) -> None:
    count_root = tmp_path / "too-many-bundles"
    count_root.mkdir()
    for index in range(MAX_REVIEWED_TRACK_MODELS + 1):
        (count_root / f"{index:02d}.reviewed.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed_track_model_count_limit_exceeded"):
        load_track_model_catalog(reviewed_root=count_root)

    entries_root = tmp_path / "too-many-entries"
    entries_root.mkdir()
    for index in range(MAX_REVIEWED_TRACK_MODEL_DIRECTORY_ENTRIES + 1):
        (entries_root / f"{index:03d}.txt").touch()
    with pytest.raises(ValueError, match="reviewed_track_model_directory_entry_limit_exceeded"):
        load_track_model_catalog(reviewed_root=entries_root)

    large_root = tmp_path / "too-large"
    large_path = _write(large_root, _model())
    large_path.write_bytes(b" " * (MAX_TRACK_MODEL_JSON_BYTES + 1))
    with pytest.raises(ValueError, match="reviewed_track_model_file_size_limit_exceeded"):
        load_track_model_catalog(reviewed_root=large_root)


def test_api_catalog_isolated_by_root_and_remains_a_startup_snapshot(tmp_path) -> None:
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    from f1_engineer.api.app import create_app

    first_root = tmp_path / "api-first"
    second_root = tmp_path / "api-second"
    first_path = _write(first_root, _model("api-first-model"))
    _write(second_root, _model("api-second-model"))
    first_app = create_app(tmp_path / "first.sqlite3", reviewed_track_models_root=first_root)
    second_app = create_app(tmp_path / "second.sqlite3", reviewed_track_models_root=second_root)

    async def models_for(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/track-models")
        assert response.status_code == 200
        return response.json()["data"]

    first_models = asyncio.run(models_for(first_app))
    second_models = asyncio.run(models_for(second_app))
    first_entry = next(item for item in first_models if item["model_id"] == "api-first-model")
    second_ids = {item["model_id"] for item in second_models}
    first_path.write_text("{}", encoding="utf-8")
    first_models_after_edit = asyncio.run(models_for(first_app))
    first_entry_after_edit = next(
        item for item in first_models_after_edit if item["model_id"] == "api-first-model"
    )

    assert first_entry["origin"] == "reviewed"
    assert first_entry["approved_for_candidate_ranking"] is True
    assert first_entry["review"]["review_id"] == "review-001"
    assert first_entry["model_content_sha256"] == track_model_fingerprint(_model("api-first-model"))
    assert "api-first-model" not in second_ids
    assert first_entry_after_edit["content_sha256"] == first_entry["content_sha256"]


def test_cli_validator_reports_only_bundle_and_fingerprint_scope(tmp_path, capsys) -> None:
    from f1_engineer.cli import _validate_reviewed_track_model, build_parser

    path = _write(tmp_path / "reviewed", _model())
    args = build_parser().parse_args(["validate-reviewed-track-model", str(path)])

    assert _validate_reviewed_track_model(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True
    assert output["physical_geometry_verified"] is False
    assert output["validation_scope"] == "bundle_structure_and_fingerprint_binding_only"
