from __future__ import annotations

from dataclasses import replace

from f1_engineer.tracks import registry


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
