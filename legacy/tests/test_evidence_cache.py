from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from time import monotonic, sleep

import pytest

from f1_engineer.analysis import evidence_cache
from f1_engineer.analysis.evidence_cache import EvidenceCache, measurement_cache_key
from f1_engineer.processing.evidence import EvidenceUnavailable, encode


def wait_for_misses(cache, count):
    deadline = monotonic() + 5
    while cache.status()["misses"] < count:
        if monotonic() >= deadline:
            pytest.fail("cache callers did not arrive")
        sleep(0.001)


def test_defaults_hits_and_nested_isolation():
    cache = EvidenceCache()
    original = {"nested": {"values": [1, {"text": "秘密"}]}}
    calls = []

    def compute():
        calls.append(True)
        return original

    first = cache.get_or_compute("secret-key", compute)
    expected = deepcopy(first)
    original["nested"]["values"].append(2)
    first["nested"]["values"][1]["text"] = "changed"
    second = cache.get_or_compute("secret-key", compute)
    assert second == expected
    assert second is not first
    second["nested"]["values"].clear()
    assert cache.get_or_compute("secret-key", compute) == expected
    assert len(calls) == 1
    status = cache.status()
    assert status == {"entries": 1, "bytes": len(encode(expected).encode("utf-8")),
                      "hits": 2, "misses": 1, "inflight": 0}
    status["entries"] = 999
    assert cache.status()["entries"] == 1
    assert cache._max_entries == 32
    assert cache._max_bytes == 16 * 1024 * 1024


@pytest.mark.parametrize("name", ["max_entries", "max_bytes"])
@pytest.mark.parametrize("value", [0, -1, None, True, False, 1.5, "2"])
def test_invalid_budgets(name, value):
    with pytest.raises(ValueError, match="positive integer"):
        EvidenceCache(**{name: value})


@pytest.mark.parametrize("name, ceiling", [("max_entries", 32), ("max_bytes", 16 * 1024 * 1024)])
@pytest.mark.parametrize("increment", [1, 100_000_000])
def test_budget_cap_escalation_is_rejected(name, ceiling, increment):
    with pytest.raises(ValueError, match=f"^{name} must be <= {ceiling}$"):
        EvidenceCache(**{name: ceiling + increment})


@pytest.mark.parametrize("entries, byte_budget", [(1, 2), (31, 16 * 1024 * 1024 - 1),
                                                (32, 16 * 1024 * 1024)])
def test_budgets_at_or_below_caps_are_accepted(entries, byte_budget):
    cache = EvidenceCache(max_entries=entries, max_bytes=byte_budget)
    assert cache.get_or_compute("empty", lambda: {}) == {}
    assert cache.status()["entries"] == 1
    assert cache.status()["bytes"] == 2


def test_entry_lru_eviction():
    cache = EvidenceCache(max_entries=2)
    for key in ("first", "second", "first", "third"):
        assert cache.get_or_compute(key, lambda: {"value": key}) == {"value": key}
    assert cache.status()["entries"] == 2
    assert cache.get_or_compute("first", lambda: pytest.fail("recent entry evicted")) == {"value": "first"}
    assert cache.get_or_compute("second", lambda: {"recomputed": True}) == {"recomputed": True}


def test_byte_eviction_exact_cap_and_oversize_rejection():
    value = {"value": "é"}
    size = len(encode(value).encode("utf-8"))
    cache = EvidenceCache(max_bytes=size)
    cache.get_or_compute("first", lambda: value)
    cache.get_or_compute("second", lambda: value)
    assert cache.status()["entries"] == 1
    assert cache.status()["bytes"] == size
    for _ in range(2):
        with pytest.raises(EvidenceUnavailable, match="^measurement_cache_entry_over_budget$"):
            cache.get_or_compute("huge", lambda: {"value": "x" * size})
    assert cache.status() == {"entries": 1, "bytes": size, "hits": 0, "misses": 4, "inflight": 0}


def test_concurrent_duplicates_coalesce_and_return_isolated_results():
    cache = EvidenceCache()
    entered, release = Event(), Event()
    calls = []

    def compute():
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return {"nested": [1]}

    with ThreadPoolExecutor(max_workers=6) as executor:
        owner = executor.submit(cache.get_or_compute, "same", compute)
        assert entered.wait(5)
        duplicates = [executor.submit(cache.get_or_compute, "same", compute) for _ in range(5)]
        try:
            wait_for_misses(cache, 6)
            assert cache.status()["inflight"] == 1
        finally:
            release.set()
        results = [future.result(timeout=5) for future in [owner, *duplicates]]
    assert len(calls) == 1
    assert all(result == {"nested": [1]} for result in results)
    results[0]["nested"].append(2)
    assert all(result == {"nested": [1]} for result in results[1:])
    assert cache.get_or_compute("same", compute) == {"nested": [1]}


@pytest.mark.parametrize("failure", [ValueError("failed"), EvidenceUnavailable("unavailable"),
                                     KeyboardInterrupt("cancelled"), SystemExit("stopped")])
def test_failure_and_base_exception_wake_duplicates_and_allow_retry(failure):
    cache = EvidenceCache()
    entered, release = Event(), Event()

    def compute():
        entered.set()
        assert release.wait(5)
        raise failure

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(cache.get_or_compute, "same", compute)
        assert entered.wait(5)
        duplicate = executor.submit(cache.get_or_compute, "same", compute)
        try:
            wait_for_misses(cache, 2)
        finally:
            release.set()
        for future in (owner, duplicate):
            with pytest.raises(type(failure), match=str(failure)):
                future.result(timeout=5)
    assert cache.status()["inflight"] == cache.status()["entries"] == 0
    assert cache.get_or_compute("same", lambda: {"retry": True}) == {"retry": True}


def test_two_flight_limit_and_bounded_duplicate_wait(monkeypatch):
    cache = EvidenceCache()
    entered = [Event(), Event()]
    release = Event()

    def compute(index):
        entered[index].set()
        assert release.wait(5)
        return {"index": index}

    assert evidence_cache._WAIT_TIMEOUT_SECONDS == 30.0
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(cache.get_or_compute, str(index), lambda index=index: compute(index))
                   for index in range(2)]
        try:
            assert all(event.wait(5) for event in entered)
            assert cache.status()["inflight"] == 2
            with pytest.raises(EvidenceUnavailable, match="^measurement_cache_busy$"):
                cache.get_or_compute("third", lambda: pytest.fail("rejected compute ran"))
            monkeypatch.setattr(evidence_cache, "_WAIT_TIMEOUT_SECONDS", 0.01)
            with pytest.raises(EvidenceUnavailable, match="^measurement_cache_wait_timeout$"):
                cache.get_or_compute("0", lambda: pytest.fail("duplicate compute ran"))
            assert cache.status()["inflight"] == 2
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
    assert cache.status()["inflight"] == 0


def test_clear_during_compute_keeps_flight_bounded_and_prevents_repopulation():
    cache = EvidenceCache()
    cache.get_or_compute("resident", lambda: {"resident": True})
    entered, release = Event(), Event()

    def compute():
        entered.set()
        assert release.wait(5)
        return {"old": [True]}

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(cache.get_or_compute, "active", compute)
        assert entered.wait(5)
        cache.clear()
        assert cache.status() == {"entries": 0, "bytes": 0, "hits": 0, "misses": 0, "inflight": 1}
        duplicate = executor.submit(cache.get_or_compute, "active", compute)
        try:
            wait_for_misses(cache, 1)
        finally:
            release.set()
        first, second = owner.result(timeout=5), duplicate.result(timeout=5)
    first["old"].clear()
    assert second == {"old": [True]}
    assert cache.status()["entries"] == cache.status()["bytes"] == cache.status()["inflight"] == 0
    assert cache.get_or_compute("active", lambda: {"new": True}) == {"new": True}
    cache.clear()
    cache.clear()
    assert cache.status() == {"entries": 0, "bytes": 0, "hits": 0, "misses": 0, "inflight": 0}


@pytest.mark.parametrize("value", [[], {"invalid": float("nan")}, {"invalid": object()}])
def test_invalid_results_are_not_cached(value):
    cache = EvidenceCache()
    with pytest.raises((TypeError, ValueError)):
        cache.get_or_compute("invalid", lambda: value)
    assert cache.status()["entries"] == cache.status()["bytes"] == cache.status()["inflight"] == 0
    assert cache.get_or_compute("invalid", lambda: {}) == {}


def attempt(identifier):
    return {"id": identifier, "session": "session", "driver": "driver",
            "manifest": {"chunks": ["digest"], "epoch": 1},
            "payload": {"lap_time_ms": 90_000, "context_segments": []},
            "source_provenance": {"generation": "generation", "version": "source-v1"}}


def test_key_is_canonical_ordered_and_version_isolated():
    target, reference = attempt("target"), attempt("reference")
    original = deepcopy(target)

    def key(target=target, reference=reference, policy="policy", version="v1"):
        return measurement_cache_key(target, reference, policy=policy, analysis_version=version)

    baseline = key()
    assert len(baseline) == 64
    assert target == original
    reordered = dict(reversed(list(target.items())))
    reordered["manifest"] = {"epoch": 1, "chunks": ["digest"]}
    reordered["payload"] = {"context_segments": [], "lap_time_ms": 90_000}
    assert key(target=reordered) == baseline
    assert key(target=reference, reference=target) != baseline
    assert key(policy="different") != baseline
    assert key(version="v2") != baseline
    for field in ("id", "session", "driver", "manifest", "payload", "source_provenance"):
        changed = deepcopy(target)
        changed[field] = {"changed": True} if isinstance(changed[field], dict) else "changed"
        assert key(target=changed) != baseline
        assert key(reference=changed) != baseline
    changed = deepcopy(target)
    changed.update(readiness={"state": "changed"}, candidates=["new"])
    assert key(target=changed) == baseline
    cache = EvidenceCache()
    assert cache.get_or_compute(baseline, lambda: {"version": 1}) == {"version": 1}
    assert cache.get_or_compute(key(version="v2"), lambda: {"version": 2}) == {"version": 2}


@pytest.mark.parametrize("field", ["source", "provenance", "generation", "evidence_generation"])
def test_optional_source_provenance_is_part_of_key(field):
    target, reference = attempt("target"), attempt("reference")
    baseline = measurement_cache_key(target, reference, policy="policy", analysis_version="v1")
    target[field] = {"generation": "other"}
    assert measurement_cache_key(target, reference, policy="policy", analysis_version="v1") != baseline


@pytest.mark.parametrize("side", ["target", "reference"])
@pytest.mark.parametrize("field", ["version", "source_generation", "journal_hash", "derived_revision"])
def test_materialization_provenance_changes_isolate_cached_results(side, field):
    target, reference = attempt("target"), attempt("reference")
    selected = target if side == "target" else reference
    selected["materialization"] = {
        "version": "historical-v1", "source_generation": "generation-1",
        "journal_hash": "journal-digest-1", "derived_revision": "derived-1",
    }
    baseline = measurement_cache_key(target, reference, policy="policy", analysis_version="v1")
    cache = EvidenceCache()
    cache.get_or_compute(baseline, lambda: {"measurement": "original"})
    selected["materialization"][field] = "changed"
    changed = measurement_cache_key(target, reference, policy="policy", analysis_version="v1")
    assert changed != baseline
    assert cache.get_or_compute(changed, lambda: {"measurement": "changed"}) == {"measurement": "changed"}
    assert cache.status()["entries"] == 2
    del selected["materialization"]
    assert measurement_cache_key(target, reference, policy="policy", analysis_version="v1") not in (baseline, changed)


@pytest.mark.parametrize("side", ["target", "reference"])
@pytest.mark.parametrize("field, value", [("lap_time_ms", 91_000),
                                         ("context_segments", [{"context": {"track_id": 2}}])])
def test_payload_changes_isolate_identical_attempt_and_manifest(side, field, value):
    target, reference = attempt("target"), attempt("reference")
    baseline = measurement_cache_key(target, reference, policy="policy", analysis_version="v1")
    selected = target if side == "target" else reference
    original = deepcopy(selected)
    selected["payload"][field] = value
    assert selected["id"] == original["id"]
    assert selected["manifest"] == original["manifest"]
    changed = measurement_cache_key(target, reference, policy="policy", analysis_version="v1")
    assert changed != baseline
    cache = EvidenceCache()
    assert cache.get_or_compute(baseline, lambda: {"measurement": "original"}) == {"measurement": "original"}
    assert cache.get_or_compute(changed, lambda: {"measurement": "changed"}) == {"measurement": "changed"}


def test_byte_budget_evicts_multiple_entries_and_keeps_recent_one():
    small = {"value": "x"}
    size = len(encode(small).encode("utf-8"))
    cache = EvidenceCache(max_bytes=3 * size)
    for key in ("first", "second", "third"):
        cache.get_or_compute(key, lambda: small)
    cache.get_or_compute("first", lambda: pytest.fail("unexpected miss"))
    larger = {"value": "x" * (size + 1)}
    cache.get_or_compute("large", lambda: larger)
    assert cache.status()["entries"] == 2
    assert cache.status()["bytes"] == size + len(encode(larger).encode("utf-8"))
    assert cache.get_or_compute("first", lambda: pytest.fail("recent entry evicted")) == small


def test_repeated_clear_cannot_bypass_active_flight_limit():
    cache = EvidenceCache()
    entered = [Event(), Event()]
    release = Event()

    def compute(index):
        entered[index].set()
        assert release.wait(5)
        return {"index": index}

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(cache.get_or_compute, str(index), lambda index=index: compute(index))
                   for index in range(2)]
        try:
            assert all(event.wait(5) for event in entered)
            for _ in range(3):
                cache.clear()
                assert cache.status()["inflight"] == 2
                with pytest.raises(EvidenceUnavailable, match="^measurement_cache_busy$"):
                    cache.get_or_compute("third", lambda: pytest.fail("busy compute ran"))
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
    assert cache.status()["entries"] == cache.status()["inflight"] == 0


def test_oversized_failure_wakes_waiters_and_leaves_no_entry():
    cache = EvidenceCache(max_bytes=2)
    entered, release = Event(), Event()

    def compute():
        entered.set()
        assert release.wait(5)
        return {"too_large": True}

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(cache.get_or_compute, "large", compute)
        assert entered.wait(5)
        duplicate = executor.submit(cache.get_or_compute, "large", compute)
        try:
            wait_for_misses(cache, 2)
        finally:
            release.set()
        for future in (owner, duplicate):
            with pytest.raises(EvidenceUnavailable, match="^measurement_cache_entry_over_budget$"):
                future.result(timeout=5)
    assert cache.status()["entries"] == cache.status()["bytes"] == cache.status()["inflight"] == 0
    assert cache.get_or_compute("large", lambda: {}) == {}


def test_copy_cancellation_cleans_flight():
    class CancelCopy:
        def __deepcopy__(self, memo):
            raise KeyboardInterrupt("copy cancelled")

    cache = EvidenceCache()
    with pytest.raises(KeyboardInterrupt, match="copy cancelled"):
        cache.get_or_compute("cancel", lambda: {"value": CancelCopy()})
    assert cache.status()["entries"] == cache.status()["inflight"] == 0
    assert cache.get_or_compute("cancel", lambda: {}) == {}
