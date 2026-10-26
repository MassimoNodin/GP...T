from __future__ import annotations

from copy import deepcopy

import pytest

from f1_engineer.analysis.session_reference import select_session_reference
from f1_engineer.processing.evidence import EvidenceUnavailable


def _attempt(identifier: str, time: int = 70_000, *, time_trial: bool = False) -> dict:
    context = {
        "packet_format": 2026, "session_type": "short_practice",
        "game_mode": "driver_career_25", "rule_set": "practice_qualifying",
        "track_id": 5, "track_name": "Austria", "track_length_m": 4318,
        "formula_id": 0, "equal_car_performance_id": 0,
        "steering_assist_id": 0, "braking_assist_id": 0, "gearbox_assist_id": 1,
    }
    if time_trial:
        context.update(session_type="time_trial", game_mode="time_trial", rule_set="time_trial",
                       weather_id=0, weather_name="clear", track_temperature_c=30, air_temperature_c=20)
    return {
        "id": identifier, "session": "session-1", "role": "player", "driver": "driver-1",
        "rows": 20, "readiness": {"state": "published", "reason": "completed_observed_lap"},
        "manifest": {"format": 2026, "epoch": 1, "driver": "driver-1", "qualifications": []},
        "payload": {
            "disposition": "completed", "lap_time_ms": time, "game_valid": True,
            "start_observed": True, "pit_encountered": False, "car_index": 0, "sample_count": 20,
            "context_segments": [{"from_frame_identifier": 1, "context": context}],
        },
    }


class FakeProvider:
    def __init__(self, *attempts: dict):
        self.data = {attempt["id"]: deepcopy(attempt) for attempt in attempts}
        self.lookups = []
        self.pages = []

    def attempt(self, identifier, *, session=None):
        self.lookups.append((identifier, session))
        if identifier not in self.data or self.data[identifier]["session"] != session:
            raise EvidenceUnavailable("attempt_not_in_session")
        return deepcopy(self.data[identifier])

    def attempts(self, session, *, limit=100, after=""):
        self.pages.append((session, limit, after))
        return [deepcopy(self.data[key]) for key in sorted(self.data)
                if key > after and self.data[key]["session"] == session][:limit]

    def evidence(self, identifier, *, session=None):
        pytest.fail("reference selection must not read traces")


def _select(provider, **kwargs):
    result = select_session_reference(provider, "session-1", "target", **kwargs)
    assert set(result) == {
        "schema_version", "policy", "status", "reason", "target", "reference",
        "candidate_set_hash", "exclusions", "diagnostic_only", "coaching_eligible", "ranking_eligible",
    }
    assert result["schema_version"] == 1
    assert result["policy"] == "session-reference-v1"
    assert result["diagnostic_only"] is True
    assert result["coaching_eligible"] is False
    assert result["ranking_eligible"] is False
    assert len(result["candidate_set_hash"]) == 64
    return result


def _update(attempt, path, value):
    current = attempt
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value


def test_fastest_then_revision_and_self_exclusion():
    provider = FakeProvider(_attempt("target", 1), _attempt("z-fast", 60_000),
                            _attempt("a-fast", 60_000), _attempt("slow", 90_000))
    result = _select(provider)
    assert result["status"] == "available"
    assert result["reason"] is None
    assert result["reference"] == "a-fast"
    assert result["exclusions"] == [{"id": "target", "reasons": ["target_attempt"]}]
    assert provider.lookups == [("target", "session-1")]


INVALID_CASES = [
    (("payload", "disposition"), "partial", "not_completed"),
    (("payload", "game_valid"), False, "game_validity_required"),
    (("payload", "game_valid"), None, "game_validity_required"),
    (("payload", "start_observed"), False, "lap_start_unobserved"),
    (("payload", "start_observed"), None, "lap_start_unobserved"),
    (("payload", "pit_encountered"), True, "pit_encountered_or_unknown"),
    (("payload", "pit_encountered"), None, "pit_encountered_or_unknown"),
    (("payload", "lap_time_ms"), 0, "positive_lap_time_required"),
    (("payload", "lap_time_ms"), -1, "positive_lap_time_required"),
    (("payload", "lap_time_ms"), True, "positive_lap_time_required"),
    (("payload", "lap_time_ms"), 1.5, "positive_lap_time_required"),
    (("payload", "lap_time_ms"), None, "positive_lap_time_required"),
    (("payload", "lap_time_ms"), "60000", "positive_lap_time_required"),
    (("readiness", "state"), "deferred", "evidence_not_published"),
    (("readiness", "state"), "quarantined", "evidence_not_published"),
    (("readiness",), None, "evidence_not_published"),
    (("manifest", "qualifications"), ["acquisition_gap"], "acquisition_gap"),
    (("manifest", "qualifications"), ["missing_observation_rows"], "missing_observation_rows"),
    (("manifest", "qualifications"), ["session_context_unknown_or_truncated"], "session_context_unknown_or_truncated"),
    (("manifest", "qualifications"), ["unfamiliar_quality_flag"], "unfamiliar_quality_flag"),
    (("manifest", "qualifications"), None, "evidence_qualifications_unknown"),
    (("rows",), 19, "missing_observation_rows"),
    (("rows",), 0, "observation_rows_unavailable"),
    (("rows",), True, "observation_rows_unavailable"),
    (("payload", "sample_count"), None, "observation_rows_unavailable"),
    (("driver",), None, "verified_driver_unavailable"),
    (("binding_verified",), False, "driver_binding_unverified"),
    (("manifest", "driver"), "other", "verified_driver_unavailable"),
    (("payload", "car_index"), None, "car_identity_unavailable"),
    (("manifest", "epoch"), None, "session_identity_unavailable"),
    (("manifest", "format"), True, "session_identity_unavailable"),
    (("role",), "opponent", "not_player_evidence"),
    (("payload", "context_segments"), [], "unknown_context"),
    (("payload", "context_segments"), [{"context": None}], "unknown_context"),
    (("payload",), None, "invalid_attempt_metadata"),
    (("manifest",), None, "invalid_attempt_metadata"),
]


@pytest.mark.parametrize("path,value,reason", INVALID_CASES)
def test_invalid_candidates_are_excluded(path, value, reason):
    bad = _attempt("bad", 1)
    _update(bad, path, value)
    result = _select(FakeProvider(_attempt("target"), bad, _attempt("good")))
    assert result["reference"] == "good"
    assert reason in next(item["reasons"] for item in result["exclusions"] if item["id"] == "bad")


@pytest.mark.parametrize("path,value,reason", INVALID_CASES)
def test_invalid_target_fails_before_scanning(path, value, reason):
    target = _attempt("target")
    _update(target, path, value)
    provider = FakeProvider(target, _attempt("good"))
    result = _select(provider)
    assert result["reason"] == "target_invalid"
    assert reason in result["exclusions"][0]["reasons"]
    assert not provider.pages


@pytest.mark.parametrize("path,value,reason", INVALID_CASES)
def test_invalid_explicit_reference_is_never_replaced(path, value, reason):
    reference = _attempt("reference")
    _update(reference, path, value)
    provider = FakeProvider(_attempt("target"), reference, _attempt("replacement", 1))
    result = _select(provider, reference_id="reference")
    assert result["reason"] == "explicit_reference_invalid"
    assert result["reference"] is None
    assert reason in result["exclusions"][0]["reasons"]
    assert not provider.pages


@pytest.mark.parametrize("path,value,reason", [
    (("driver",), "driver-2", "incompatible_driver"),
    (("payload", "car_index"), 1, "incompatible_car"),
    (("manifest", "epoch"), 2, "incompatible_session_identity"),
    (("manifest", "format"), 2025, "incompatible_session_identity"),
])
def test_identity_mismatch(path, value, reason):
    candidate = _attempt("bad", 1)
    _update(candidate, path, value)
    result = _select(FakeProvider(_attempt("target"), candidate))
    assert result["reason"] == "no_eligible_reference"
    assert reason in result["exclusions"][0]["reasons"]


@pytest.mark.parametrize("field,value", [
    ("track_id", -1), ("track_id", None), ("track_id", 7),
    ("track_length_m", 0), ("track_length_m", True), ("track_length_m", float("nan")),
    ("track_length_m", float("inf")), ("track_name", ""), ("track_name", None),
    ("session_type", "race"), ("session_type", None), ("game_mode", "unknown"),
    ("rule_set", "race"), ("packet_format", 2025), ("formula_id", None),
    ("steering_assist_id", True), ("gearbox_assist_id", 2),
])
def test_unknown_or_incompatible_context(field, value):
    candidate = _attempt("bad", 1)
    candidate["payload"]["context_segments"][0]["context"][field] = value
    result = _select(FakeProvider(_attempt("target"), candidate))
    assert result["reason"] == "no_eligible_reference"


def test_changed_context_is_rejected():
    candidate = _attempt("bad")
    second = deepcopy(candidate["payload"]["context_segments"][0])
    second["context"]["track_id"] = 7
    candidate["payload"]["context_segments"].append(second)
    result = _select(FakeProvider(_attempt("target"), candidate))
    assert result["reason"] == "no_eligible_reference"


def test_explicit_reference_is_not_replaced_or_ranked_against_others():
    bad = _attempt("bad")
    bad["payload"]["game_valid"] = False
    provider = FakeProvider(_attempt("target"), bad, _attempt("good", 1), _attempt("slow", 90_000))
    assert _select(provider, reference_id="bad")["reason"] == "explicit_reference_invalid"
    assert _select(provider, reference_id="slow")["reference"] == "slow"
    assert _select(provider, reference_id="missing")["reason"] == "explicit_reference_invalid"
    assert _select(provider, reference_id="target")["reason"] == "explicit_reference_invalid"
    assert not provider.pages


def test_session_scoping_and_missing_target():
    foreign = _attempt("foreign")
    foreign["session"] = "session-2"
    provider = FakeProvider(_attempt("target"), foreign)
    assert _select(provider, reference_id="foreign")["reason"] == "explicit_reference_invalid"
    assert _select(FakeProvider())["reason"] == "target_unavailable:attempt_not_in_session"
    assert _select(provider)["reason"] == "no_eligible_reference"


@pytest.mark.parametrize("count", [99, 100, 199, 200, 511, 512, 513, 700])
def test_pagination_and_fail_closed_budget(count):
    provider = FakeProvider(_attempt("target"), *[_attempt(f"c-{index:04}", 60_000 + index)
                                               for index in range(count)])
    result = _select(provider)
    if count > 512:
        assert result["reason"] == "candidate_scan_budget_exceeded"
        assert result["reference"] is None
        assert len(provider.pages) == 6
    else:
        assert result["reference"] == "c-0000"
    assert all(limit == 100 and session == "session-1" for session, limit, _ in provider.pages)
    assert len(provider.pages) <= 6


def test_hash_is_reproducible_and_includes_excluded_eligibility_metadata():
    attempts = [_attempt("target"), _attempt("fast", 60_000), _attempt("bad")]
    attempts[2]["payload"]["game_valid"] = False
    original = _select(FakeProvider(*attempts))
    assert original == _select(FakeProvider(*reversed(attempts)))
    attempts[2]["payload"]["lap_time_ms"] = 65_000
    updated = _select(FakeProvider(*attempts))
    assert updated["reference"] == original["reference"]
    assert updated["candidate_set_hash"] != original["candidate_set_hash"]
    attempts[2]["payload"]["game_valid"] = True
    assert _select(FakeProvider(*attempts))["candidate_set_hash"] != updated["candidate_set_hash"]


@pytest.mark.parametrize("verified", [True, "legacy"])
def test_verified_and_legacy_provider_bindings_are_accepted(verified):
    target, reference = _attempt("target"), _attempt("reference")
    if verified is True:
        target["binding_verified"] = reference["binding_verified"] = True
    result = _select(FakeProvider(target, reference), reference_id="reference")
    assert result["status"] == "available"
    assert result["reference"] == "reference"


@pytest.mark.parametrize("identifier", ["target", "reference"])
def test_binding_verification_changes_candidate_set_hash(identifier):
    provider = FakeProvider(_attempt("target"), _attempt("reference"))
    original = _select(provider)
    provider.data[identifier]["binding_verified"] = True
    verified = _select(provider)
    assert verified["reference"] == original["reference"]
    assert verified["candidate_set_hash"] != original["candidate_set_hash"]
    provider.data[identifier]["binding_verified"] = False
    unverified = _select(provider)
    assert unverified["status"] == "unavailable"
    assert unverified["candidate_set_hash"] != verified["candidate_set_hash"]


def test_binding_marker_affects_hash_even_for_already_excluded_candidate():
    bad = _attempt("bad")
    bad["payload"]["game_valid"] = False
    provider = FakeProvider(_attempt("target"), _attempt("reference"), bad)
    original = _select(provider)
    provider.data["bad"]["binding_verified"] = False
    updated = _select(provider)
    assert updated["reference"] == original["reference"]
    assert updated["candidate_set_hash"] != original["candidate_set_hash"]


def test_strict_time_trial_reuses_condition_aware_policy():
    target, reference = _attempt("target", time_trial=True), _attempt("reference", time_trial=True)
    assert _select(FakeProvider(target, reference))["reference"] == "reference"
    context = reference["payload"]["context_segments"][0]["context"]
    context["weather_id"] = None
    result = _select(FakeProvider(target, reference))
    assert result["reason"] == "no_eligible_reference"
    assert "unknown_conditions" in result["exclusions"][0]["reasons"]
    context["weather_id"] = 1
    assert _select(FakeProvider(target, reference))["reason"] == "no_eligible_reference"


@pytest.mark.parametrize("field,value", [
    ("weather_id", True), ("weather_name", ""), ("weather_name", None),
    ("track_temperature_c", True), ("track_temperature_c", float("nan")),
    ("air_temperature_c", float("inf")), ("air_temperature_c", "20"),
])
def test_time_trial_unknown_conditions_fail_closed(field, value):
    target = _attempt("target", time_trial=True)
    reference = _attempt("reference", time_trial=True)
    reference["payload"]["context_segments"][0]["context"][field] = value
    result = _select(FakeProvider(target, reference))
    assert result["reason"] == "no_eligible_reference"
    assert "unknown_conditions" in result["exclusions"][0]["reasons"]


def test_inputs_are_not_mutated_and_added_candidates_change_hash():
    provider = FakeProvider(_attempt("target"), _attempt("reference"))
    before = deepcopy(provider.data)
    first = _select(provider)
    assert provider.data == before
    provider.data["extra"] = _attempt("extra", 90_000)
    second = _select(provider)
    assert first["reference"] == second["reference"]
    assert first["candidate_set_hash"] != second["candidate_set_hash"]


def test_wrong_explicit_revision_is_explained_without_replacement():
    provider = FakeProvider(_attempt("target"))
    original = provider.attempt
    provider.attempt = lambda identifier, **kwargs: (
        original(identifier, **kwargs) if identifier == "target" else _attempt("wrong")
    )
    result = _select(provider, reference_id="reference")
    assert result["reason"] == "explicit_reference_invalid"
    assert result["exclusions"] == [{"id": "reference", "reasons": ["attempt_identity_mismatch"]}]


@pytest.mark.parametrize("page", [None, [_attempt("duplicate"), _attempt("duplicate")],
                                  [_attempt("z"), _attempt("a")], [None],
                                  [_attempt(f"c-{index:04}") for index in range(101)]])
def test_malformed_pages_fail_closed(page):
    provider = FakeProvider(_attempt("target"))
    provider.attempts = lambda session, **kwargs: deepcopy(page)
    assert _select(provider)["reason"] == "invalid_candidate_page"


def test_scan_failure_is_not_partial_success():
    provider = FakeProvider(_attempt("target"), *[_attempt(f"c-{index:04}") for index in range(100)])
    original = provider.attempts

    def pages(session, **kwargs):
        if kwargs["after"]:
            raise EvidenceUnavailable("source_unavailable")
        return original(session, **kwargs)

    provider.attempts = pages
    assert _select(provider)["reason"] == "candidate_scan_unavailable"


def test_provider_returning_wrong_session_or_revision_fails_closed():
    provider = FakeProvider(_attempt("target"), _attempt("reference"))
    original = provider.attempt

    def lookup(identifier, *, session=None):
        result = original(identifier, session=session)
        result["session"] = "other-occurrence"
        return result

    provider.attempt = lookup
    assert _select(provider)["reason"] == "target_invalid"
    provider.attempt = lambda identifier, **kwargs: _attempt("wrong")
    assert _select(provider)["reason"] == "target_invalid"
