from __future__ import annotations

from copy import deepcopy
import sys
from types import ModuleType

import pytest

from f1_engineer.analysis import session_measurement as adapter
from f1_engineer.analysis.session_comparison import measure_completed_laps
from f1_engineer.processing.evidence import EvidenceUnavailable, digest, encode


def _attempt(identifier):
    return {
        "id": identifier, "session": "session-1", "generation": "generation-1",
        "role": "player", "driver": "driver-1", "rows": 61,
        "readiness": {"state": "published", "sequence": 1},
        "manifest": {
            "format": 2026, "epoch": 1, "driver": "driver-1", "qualifications": [],
            "chunks": [f"{identifier}-chunk"],
        },
        "payload": {
            "disposition": "completed",
            "lap_time_ms": 3300 if identifier == "target" else 3000,
            "game_valid": True, "start_observed": True,
            "pit_encountered": False, "car_index": 0, "sample_count": 61,
            "context_segments": [{"from_frame_identifier": 0, "context": {
                "packet_format": 2026, "session_type": "short_practice",
                "game_mode": "driver_career_25", "rule_set": "practice_qualifying",
                "track_id": 5, "track_name": "Austria", "track_length_m": 4318,
                "formula_id": 0, "equal_car_performance_id": 0,
                "steering_assist_id": 0, "braking_assist_id": 0, "gearbox_assist_id": 1,
            }}],
        },
    }


def _rows(identifier):
    target = identifier == "target"
    return [
        {
            "frame_identifier": index, "lap_distance_m": index * 5,
            "current_lap_time_ms": index * (60 if target else 50),
            "session_time_s": 10 + index * (0.06 if target else 0.05),
            "speed_mps": 42 if target else 40, "throttle": 0.5,
            "brake": 0.4 if (15 if target else 12) <= index < 25 else 0,
            "steering": 0.0, "gear": 4, "drs_active": False,
        }
        for index in range(61)
    ]


class _Provider:
    def __init__(self):
        self.metadata = {identifier: _attempt(identifier)
                         for identifier in ("target", "reference")}
        self.rows = {identifier: _rows(identifier) for identifier in self.metadata}
        self.evidence_calls = []
        self.attempt_calls = []

    def attempt(self, attempt_id, *, session):
        self.attempt_calls.append((attempt_id, session))
        attempt = self.metadata[attempt_id]
        if attempt["session"] != session:
            raise EvidenceUnavailable("attempt_not_in_session")
        return deepcopy(attempt)

    def attempts(self, session, *, limit=100, after=""):
        return [deepcopy(attempt) for identifier, attempt in sorted(self.metadata.items())
                if attempt["session"] == session and identifier > after][:limit]

    def evidence(self, attempt_id, *, session):
        self.evidence_calls.append((attempt_id, session))
        return self.attempt(attempt_id, session=session), deepcopy(self.rows[attempt_id])


class _Cache:
    def __init__(self):
        self.values = {}
        self.calls = []

    def get_or_compute(self, key, compute):
        self.calls.append(key)
        if key not in self.values:
            self.values[key] = compute()
        return self.values[key]


@pytest.fixture
def siblings(monkeypatch):
    selection = {
        "status": "available", "reason": None, "policy": "session-reference-v1",
        "session": "session-1", "target_id": "target", "reference_id": "reference",
        "candidate_set_hash": "candidates-1",
    }
    calls = []
    keys = []

    def select(provider, session, target_id, *, reference_id):
        calls.append((provider, session, target_id, reference_id))
        return deepcopy(selection)

    def cache_key(target, reference, *, analysis_version, policy):
        parameters = {"target": target, "reference": reference,
                      "analysis_version": analysis_version, "policy": policy}
        keys.append(deepcopy(parameters))
        return digest(encode(parameters))

    reference_module = ModuleType("f1_engineer.analysis.session_reference")
    reference_module.select_session_reference = select
    cache_module = ModuleType("f1_engineer.analysis.evidence_cache")
    cache_module.measurement_cache_key = cache_key
    monkeypatch.setitem(sys.modules, reference_module.__name__, reference_module)
    monkeypatch.setitem(sys.modules, cache_module.__name__, cache_module)
    return selection, calls, keys


def _analyze(provider, **kwargs):
    return adapter.analyze_session_pair(
        provider, "session-1", "target", "reference", **kwargs
    )


def test_fixed_trace_reuses_measured_report_with_provenance(siblings):
    provider = _Provider()
    expected = measure_completed_laps(provider, "session-1", "target", "reference")
    report = _analyze(provider)
    assert report == _analyze(provider)
    for name, value in expected.items():
        assert report[name] == value
    assert report["distance_m"] == list(range(0, 301, 5))
    assert report["channels"]["speed_mps"]["values"] == [2.0] * 61
    assert report["delta_time"]["observed_range_change_s"] == pytest.approx(0.6)
    assert report["braking_zones"][0]["target_minus_reference_m"] == 15
    assert report["target"]["manifest_hash"] == digest(encode(provider.metadata["target"]["manifest"]))
    assert report["reference"]["revision"] == "reference"
    assert report["reference_selection"] == siblings[0]
    assert siblings[1][0] == (provider, "session-1", "target", "reference")


def test_missing_channels_are_uncovered_not_invented(siblings):
    provider = _Provider()
    for row in provider.rows["target"]:
        for channel in ("speed_mps", "brake", "throttle", "steering"):
            row.pop(channel)
    report = _analyze(provider)
    for channel in report["channels"].values():
        assert channel["coverage"] == 0
        assert channel["values"] == [None] * 61
        assert channel["mask"] == [False] * 61
    assert all(not region["supported"] for region in report["braking_zones"])
    assert all(region["target_minus_reference_m"] is None
               for region in report["braking_zones"])


def test_distance_gap_remains_excluded(siblings):
    provider = _Provider()
    provider.rows["target"] = [row for row in provider.rows["target"]
                               if not 100 <= row["lap_distance_m"] <= 180]
    report = _analyze(provider)
    assert report["delta_time"]["coverage"] < 1
    assert report["excluded_spans"][0]
    for index, distance in enumerate(report["distance_m"]):
        if 100 <= distance <= 180:
            assert report["delta_time"]["values_s"][index] is None
            assert report["delta_time"]["mask"][index] is False


def test_unknown_track_uses_only_numeric_regions(siblings):
    provider = _Provider()
    for attempt in provider.metadata.values():
        attempt["payload"]["context_segments"][0]["context"]["track_id"] = -1
    report = _analyze(provider)
    assert "track_geometry_not_fully_established" in report["qualifications"]
    assert "numeric_distance_regions_not_named_corners" in report["qualifications"]
    assert report["braking_zones"]
    for region in report["braking_zones"]:
        assert all(isinstance(value, (float, int)) for value in region["region_m"])
        assert "label" not in region and "corner_name" not in region


@pytest.mark.parametrize("reason", [
    "reference_not_eligible", "attempt_not_in_session", "game_validity_unknown",
    "context_incompatible", "trace_not_selected", "analysis_read_budget_exceeded",
])
def test_reference_rejection_precedes_measurement_and_cache(siblings, reason):
    selection, _, _ = siblings
    selection.update(status="unavailable", reason=reason)
    provider = _Provider()
    cache = _Cache()
    with pytest.raises(EvidenceUnavailable, match=reason):
        _analyze(provider, cache=cache)
    assert provider.evidence_calls == []
    assert provider.attempt_calls == []
    assert cache.calls == []


def test_cache_parity_fresh_selection_and_mutation_isolation(siblings):
    selection, calls, keys = siblings
    provider = _Provider()
    cache = _Cache()
    uncached = _analyze(provider)
    cached = _analyze(provider, cache=cache)
    assert cached == uncached
    evidence_calls = len(provider.evidence_calls)
    cached["distance_m"].clear()
    cached["target"]["readiness"]["state"] = "modified"
    cached["reference_selection"]["candidate_set_hash"] = "modified"
    selection["candidate_set_hash"] = "candidates-2"
    hit = _analyze(provider, cache=cache)
    assert len(provider.evidence_calls) == evidence_calls
    assert hit["reference_selection"]["candidate_set_hash"] == "candidates-2"
    assert hit["distance_m"] == uncached["distance_m"]
    assert hit["target"] == uncached["target"]
    assert len(calls) == 3
    assert cache.calls[0] == cache.calls[1]
    assert keys[0]["target"] == provider.metadata["target"]
    assert keys[0]["reference"] == provider.metadata["reference"]
    assert adapter.ADAPTER_VERSION in keys[0]["analysis_version"]
    assert keys[0]["policy"] == "observed_session_distance"
    assert all("reference_selection" not in value for value in cache.values.values())


def test_cache_hit_still_rejects_now_ineligible_reference(siblings):
    selection, calls, _ = siblings
    provider = _Provider()
    cache = _Cache()
    _analyze(provider, cache=cache)
    selection.update(status="unavailable", reason="reference_quarantined")
    with pytest.raises(EvidenceUnavailable, match="reference_quarantined"):
        _analyze(provider, cache=cache)
    assert len(calls) == 2
    assert len(cache.calls) == 1
    assert len(provider.evidence_calls) == 2


@pytest.mark.parametrize("side,field", [
    ("target", "generation"), ("reference", "generation"),
    ("target", "manifest"), ("reference", "manifest"),
])
def test_cache_key_uses_current_attempt_metadata(siblings, side, field):
    provider = _Provider()
    cache = _Cache()
    _analyze(provider, cache=cache)
    if field == "generation":
        provider.metadata[side][field] = "generation-2"
    else:
        provider.metadata[side][field]["chunks"] = ["replacement-chunk"]
    _analyze(provider, cache=cache)
    assert cache.calls[0] != cache.calls[1]
    assert len(provider.evidence_calls) == 4


def test_policy_forwarded_and_versioned_in_cache_key(siblings, monkeypatch):
    calls = []

    def measure(provider, session, target, reference, *, policy):
        calls.append(policy)
        return {"policy": policy}

    monkeypatch.setattr(adapter, "measure_completed_laps", measure)
    provider = _Provider()
    cache = _Cache()
    _analyze(provider, cache=cache)
    _analyze(provider, cache=cache, policy="practice_qualifying")
    assert calls == ["observed_session_distance", "practice_qualifying"]
    assert cache.calls[0] != cache.calls[1]


@pytest.mark.parametrize("failure", ["policy", "distance", "readiness"])
def test_measurement_failures_propagate_without_caching(siblings, failure):
    provider = _Provider()
    cache = _Cache()
    policy = "observed_session_distance"
    if failure == "policy":
        policy = "unsupported"
    elif failure == "distance":
        for row in provider.rows["target"]:
            row.pop("lap_distance_m")
    else:
        provider.metadata["target"]["readiness"]["state"] = "quarantined"
    with pytest.raises(EvidenceUnavailable):
        _analyze(provider, cache=cache, policy=policy)
    assert cache.values == {}


def test_report_is_never_measured_coaching_or_ranking(siblings):
    report = _analyze(_Provider())
    assert report["schema_version"] == 1
    assert report["analysis_version"] == adapter.ANALYSIS_VERSION
    assert report["diagnostic_only"] is True
    assert report["coaching_eligible"] is False
    assert report["ranking_eligible"] is False
    assert "measurement_not_reference_ranking" in report["qualifications"]
    assert "later_braking_is_not_automatically_better" in report["qualifications"]
    assert not {"advice", "guaranteed_gain_s", "ranked_candidates", "corner_loss_candidates",
                "corner_comparison_brief", "setup_recommendations"} & report.keys()


@pytest.mark.parametrize("policy", ["observed_session_distance", "practice_qualifying"])
def test_real_selector_and_cache_parity(policy):
    cache_module = pytest.importorskip("f1_engineer.analysis.evidence_cache")
    pytest.importorskip("f1_engineer.analysis.session_reference")
    provider = _Provider()
    cache = cache_module.EvidenceCache()
    uncached = _analyze(provider, policy=policy)
    cached = _analyze(provider, policy=policy, cache=cache)
    assert cached == uncached
    evidence_calls = len(provider.evidence_calls)
    hit = _analyze(provider, policy=policy, cache=cache)
    assert hit == uncached
    assert len(provider.evidence_calls) == evidence_calls
    assert hit["reference_selection"]["reference"] == "reference"
    assert hit["reference_selection"]["policy"] == "session-reference-v1"
    assert cache.status()["hits"] == 1
    assert cache.status()["misses"] == 1
    provider.metadata["reference"]["readiness"]["state"] = "quarantined"
    with pytest.raises(EvidenceUnavailable, match="explicit_reference_invalid"):
        _analyze(provider, policy=policy, cache=cache)
    assert cache.status()["hits"] == 1
    assert len(provider.evidence_calls) == evidence_calls


@pytest.mark.parametrize("failure", ["validity", "missing_rows", "gap", "driver", "track"])
def test_real_selector_rejects_insufficient_reference_evidence(failure):
    pytest.importorskip("f1_engineer.analysis.session_reference")
    provider = _Provider()
    reference = provider.metadata["reference"]
    if failure == "validity":
        reference["payload"]["game_valid"] = None
    elif failure == "missing_rows":
        reference["rows"] = 60
    elif failure == "gap":
        reference["manifest"]["qualifications"] = ["acquisition_gap"]
    elif failure == "driver":
        reference["driver"] = reference["manifest"]["driver"] = "driver-2"
    else:
        reference["payload"]["context_segments"][0]["context"]["track_id"] = -1
    with pytest.raises(EvidenceUnavailable, match="explicit_reference_invalid"):
        _analyze(provider)
    assert provider.evidence_calls == []


def test_real_cache_immutable_key_and_fresh_selection():
    cache_module = pytest.importorskip("f1_engineer.analysis.evidence_cache")
    pytest.importorskip("f1_engineer.analysis.session_reference")
    provider = _Provider()
    cache = cache_module.EvidenceCache()
    first = _analyze(provider, cache=cache)
    evidence_calls = len(provider.evidence_calls)
    provider.metadata["reference"]["readiness"]["sequence"] = 2
    second = _analyze(provider, cache=cache)
    assert len(provider.evidence_calls) == evidence_calls
    assert second["reference_selection"]["candidate_set_hash"] != first["reference_selection"]["candidate_set_hash"]
    assert cache.status()["hits"] == 1
    provider.metadata["reference"]["manifest"]["chunks"] = ["replacement-chunk"]
    third = _analyze(provider, cache=cache)
    assert len(provider.evidence_calls) == evidence_calls + 2
    assert third["reference"]["manifest_hash"] != first["reference"]["manifest_hash"]
    assert cache.status()["misses"] == 2
