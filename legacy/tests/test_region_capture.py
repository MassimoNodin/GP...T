from copy import deepcopy
import threading

import pytest
from fastapi.testclient import TestClient

from f1_engineer.api import app as app_module
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.engineer_facts import LiveEngineerFacts
from f1_engineer.processing.evidence import EvidenceStore
from f1_engineer.processing.region_capture import RegionCaptureStore
from tests.test_engineer_facts import observe, source
from tests.test_radio_regions import ready, push
from tests.test_session_evidence import admitted_packets


LEASE = "a" * 32
TOKEN = "capture-test-token-" + "a" * 32


def capture():
    controller, radio, events, value = ready()
    clock = [0.0]
    store = RegionCaptureStore(clock=lambda: clock[0], clock_ns=lambda: value["received_monotonic_ns"])
    store.observe(value)
    body = {key: deepcopy(value[key]) for key in ("generation", "session_id", "scope")}
    body.update(player=deepcopy(value["drivers"][0]["identity"]), first_lap=4, laps=1, window_m=[10, 50])
    assert store.arm(LEASE, body, value)[0] == 202
    return store, body, clock, controller, radio, value


def feed_window(store, controller, value, *, mutate=None):
    for index in range(13):
        push(controller, value, distance=float(index * 5), clock=index * 50,
             speed=180 - index, brake=0.5 if 4 <= index <= 8 else 0.0,
             mutate=mutate if index == 6 else None)
        store.observe(value)
    push(controller, value, lap=5)
    store.observe(value)


def test_capture_completion_facts_are_copied_and_bounded_to_original_fields():
    store, body, clock, controller, radio, value = capture()
    feed_window(store, controller, value)
    result = store.get(LEASE, value)["results"][0]
    original = deepcopy(result["completion"])
    assert set(original) == {"id", "kind", "scope", "driver", "qualification", "lap_number",
                             "lap_time_ms", "previous_observed_invalid", "received_monotonic_ns"}
    result["completion"]["driver"]["tenure_ordinal"] = 99
    value["events"][0]["lap_time_ms"] = 9999
    value["events"][0]["extra"] = "not retained"
    assert store.get(LEASE, value)["results"][0]["completion"] == original


def test_invalid_completion_retains_invalidity_without_fabricating_measured_evidence():
    store, body, clock, controller, radio, value = capture()
    for index in range(13):
        push(controller, value, distance=float(index * 5), clock=index * 50,
             speed=180 - index, brake=0.5 if 4 <= index <= 8 else 0.0)
        store.observe(value)
    push(controller, value, lap=5)
    value["events"][0].update(previous_observed_invalid=1, extra="not retained")
    store.observe(value)
    result = store.get(LEASE, value)["results"][0]
    assert result["evidence"] is None
    assert result["completion"]["previous_observed_invalid"] == 1
    assert "extra" not in result["completion"]


def test_backend_frames_complete_region_without_intermediate_http_polls():
    store, body, clock, controller, radio, value = capture()
    feed_window(store, controller, value)
    result = store.get(LEASE, value)
    assert result["state"] == "complete"
    assert len(result["results"]) == 1
    completion = result["results"][0]["completion"]
    assert completion == value["events"][0]
    assert completion["driver"] == body["player"]
    assert completion["lap_number"] == 4
    assert completion["lap_time_ms"] == 1000
    result = result["results"][0]
    assert "171.0" in result["text"] and "between 15 and 20" in result["text"]
    assert result["evidence"]["measurement"]["elapsed"]["elapsed_s"] == pytest.approx(0.4)
    assert result["completion_age_s"] == 0
    assert not store._tracker.records
    store.observe(value)
    assert len(store.get(LEASE, value)["results"]) == 1


def test_renewal_is_idempotent_and_preserves_observations():
    store, body, clock, controller, radio, value = capture()
    push(controller, value, distance=10.0, clock=100)
    store.observe(value)
    records = deepcopy(store._tracker.records)
    clock[0] = 29
    code, renewed = store.arm(LEASE, body, value)
    assert code == 202 and renewed["expires_in_s"] == 30
    assert store._tracker.records == records
    clock[0] = 31
    assert store.get(LEASE, value)["state"] == "active"


def test_expiry_and_release_clear_owned_data():
    store, body, clock, controller, radio, value = capture()
    push(controller, value, distance=10.0, clock=100)
    store.observe(value)
    assert store._tracker.records
    clock[0] = 30
    assert store.get(LEASE, value)["state"] == "expired"
    assert not store._tracker.records
    assert store.arm(LEASE, body, value)[0] == 409
    assert store.release(LEASE)["state"] == "released"
    assert store.release(LEASE)["state"] == "released"


def test_completed_results_expire_without_client_renewal():
    store, body, clock, controller, radio, value = capture()
    feed_window(store, controller, value)
    clock[0] = 30
    result = store.get(LEASE, value)
    assert result["state"] == "expired" and result["results"] == []


def test_capture_budget_and_identity_conflicts_are_bounded():
    store, body, clock, controller, radio, value = capture()
    assert store.arm("b" * 32, body, value)[0] == 202
    assert store.arm("c" * 32, body, value)[0] == 202
    assert store.arm("d" * 32, body, value)[1]["reason"] == "region_capture_budget_busy"
    changed = deepcopy(body)
    changed["window_m"] = [20, 50]
    assert store.arm(LEASE, changed, value)[1]["reason"] == "region_capture_identity_conflict"


@pytest.mark.parametrize("key,broken", [("first_lap", True), ("first_lap", 0), ("laps", None),
    ("laps", True), ("laps", 21), ("window_m", [10]), ("window_m", "10,50")])
def test_invalid_requests_are_rejected(key, broken):
    store, body, clock, controller, radio, value = capture()
    body[key] = broken
    assert store.arm("b" * 32, body, value)[0] == 422


@pytest.mark.parametrize("bounds", [[50, 10], [-1, 50], [10, 101], [True, 50], [float("nan"), 50]])
def test_invalid_numeric_windows_are_rejected(bounds):
    store, body, clock, controller, radio, value = capture()
    body["window_m"] = bounds
    assert store.arm("b" * 32, body, value)[0] == 422


@pytest.mark.parametrize("section,key", [("scope", "epoch"), ("scope", "session_uid"),
                                       ("player", "car_index"), ("player", "tenure_ordinal")])
def test_boolean_binding_metadata_is_not_integer_evidence(section, key):
    store, body, clock, controller, radio, value = capture()
    body[section][key] = True
    assert store.arm("b" * 32, body, value)[0] == 422


def test_new_capture_requires_the_actual_next_lap():
    store, body, clock, controller, radio, value = capture()
    body["first_lap"] = 3
    assert store.arm("b" * 32, body, value)[1]["reason"] == "region_capture_requires_next_lap"


@pytest.mark.parametrize("section,field,broken", [
    ("source", "epoch", 9), ("source", "frame_ordinal", True),
    ("source", "telemetry_received_monotonic_ns", 0), ("value", "brake", float("nan")),
    ("value", "speed_kph", True), ("value", "lap_distance_m", None),
])
def test_bad_aligned_observations_do_not_fill_regional_gaps(section, field, broken):
    store, body, clock, controller, radio, value = capture()
    feed_window(store, controller, value, mutate=lambda data:
                data["drivers"][0]["observation"][section].update({field: broken}))
    result = store.get(LEASE, value)["results"][0]
    assert result["evidence"] is None


@pytest.mark.parametrize("kind", ["scope", "tenure", "context", "unavailable"])
def test_changed_bindings_or_runtime_clear_existing_results(kind):
    store, body, clock, controller, radio, value = capture()
    feed_window(store, controller, value)
    assert store.get(LEASE, value)["results"]
    if kind == "scope":
        value["scope"]["epoch"] += 1
    elif kind == "tenure":
        value["drivers"][0]["identity"]["tenure_ordinal"] += 1
    elif kind == "context":
        value["context"]["value"] = None
    else:
        value["status"] = "unavailable"
    if kind == "context":
        store.observe(value)
    result = store.get(LEASE, value)
    assert result["state"] == "unavailable" and not result["results"]


def test_history_gap_terminates_capture_without_backfilling_events():
    store, body, clock, controller, radio, value = capture()
    value["event_floor"] = value["event_cursor"] = 10
    store.observe(value)
    assert store.get(LEASE, value)["state"] == "unavailable"
    assert not store._tracker.records


def test_result_views_are_detached_from_owned_memory():
    store, body, clock, controller, radio, value = capture()
    feed_window(store, controller, value)
    result = store.get(LEASE, value)
    result["results"].clear()
    result["binding"]["scope"]["epoch"] = 999
    assert store.get(LEASE, value)["results"]
    assert store.get(LEASE, value)["binding"]["scope"]["epoch"] == 2


def test_publication_failure_clears_capture_and_never_exposes_partial_result(tmp_path):
    store, body, clock, controller, radio, value = capture()
    coordinator = SessionCoordinator(EvidenceStore(tmp_path / "evidence.sqlite3"), "test", region_captures=store)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with coordinator._transaction():
                feed_window(store, controller, value)
                assert store._leases[LEASE]["results"]
                raise RuntimeError("rollback")
        result = store.get(LEASE, value)
        assert result["state"] == "unavailable" and not result["results"]
        assert not store._tracker.records
    finally:
        coordinator.close()


def test_reader_waits_for_owner_transaction_before_seeing_results():
    store, body, clock, controller, radio, value = capture()
    started, finished = threading.Event(), threading.Event()
    output = []
    def read():
        started.set()
        output.append(store.get(LEASE, value))
        finished.set()
    with store.owner_transaction():
        worker = threading.Thread(target=read)
        worker.start()
        assert started.wait(1)
        assert not finished.wait(0.05)
        feed_window(store, controller, value)
    worker.join(2)
    assert not worker.is_alive() and output[0]["state"] == "complete"


def test_facts_observer_sees_every_frame_and_invalidation():
    observed, invalidated = [], []
    facts = LiveEngineerFacts(on_observe=lambda current, events, cursor, floor:
                             observed.append(current["frame_ordinal"]), on_invalidate=invalidated.append)
    observe(facts, source())
    observe(facts, source(ordinal=6))
    observe(facts, source(ordinal=7))
    assert observed == [5, 6, 7]
    facts.invalidate("gap")
    assert invalidated == ["gap"]


def api_client(tmp_path, monkeypatch):
    store, body, clock, controller, radio, value = capture()
    class Runtime:
        region_captures = store
        detail_profile = "full"
        def engineer_snapshot(self):
            return deepcopy(value)
    monkeypatch.setattr(app_module, "LiveSessionRuntime", lambda *args, **kwargs: Runtime())
    app = app_module.create_app(tmp_path / "archive.sqlite3", control_token=TOKEN, automatic_acquisition=True)
    return TestClient(app), store, body, value


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_capture_endpoints_require_auth(tmp_path, monkeypatch, method):
    client, store, body, value = api_client(tmp_path, monkeypatch)
    response = client.request(method, f"/api/v2/engineer/region-captures/{LEASE}")
    assert response.status_code == 403


def test_api_renew_read_release_are_authenticated_and_no_store(tmp_path, monkeypatch):
    client, store, body, value = api_client(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    url = f"/api/v2/engineer/region-captures/{LEASE}"
    assert client.post(url, headers=headers, json=body).status_code == 202
    result = client.get(url, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert client.delete(url, headers=headers).json()["data"]["state"] == "released"


@pytest.mark.parametrize("payload", [b"not json", b"x" * 4097, b"[]", b"[" * 1500 + b"0" + b"]" * 1500],
                         ids=["invalid-json", "oversized", "non-object", "excessive-nesting"])
def test_api_rejects_invalid_or_oversized_body(tmp_path, monkeypatch, payload):
    client, store, body, value = api_client(tmp_path, monkeypatch)
    result = client.post(f"/api/v2/engineer/region-captures/{'b' * 32}", content=payload,
                         headers={"Authorization": f"Bearer {TOKEN}"})
    assert result.status_code == 422


def test_disabled_runtime_explained_after_authentication(tmp_path):
    app = app_module.create_app(tmp_path / "archive.sqlite3", control_token=TOKEN, automatic_acquisition=False)
    client = TestClient(app)
    result = client.post(f"/api/v2/engineer/region-captures/{LEASE}", json={},
                         headers={"Authorization": f"Bearer {TOKEN}"})
    assert result.status_code == 409 and result.json()["reason"] == "region_runtime_disabled"


@pytest.mark.parametrize("profile", ["full", "demand_v1"])
def test_one_publication_observes_multiple_frames_without_expanding_detail_selection(tmp_path, profile):
    class Captures(RegionCaptureStore):
        def __init__(self):
            super().__init__()
            self.observed_frames = []
        def observe(self, snapshot):
            self.observed_frames.append(snapshot["frame_identifier"])
            super().observe(snapshot)
    captures = Captures()
    counts = []
    for name, capture_store in (("captured", captures), ("baseline", None)):
        coordinator = SessionCoordinator(EvidenceStore(tmp_path / f"{name}.sqlite3"), name,
                                         detail_profile=profile, region_captures=capture_store)
        try:
            coordinator.journal_batch(list(admitted_packets(frames=12)))
            if name == "captured":
                assert not captures.observed_frames
            coordinator.publish_pending()
            counts.append((coordinator.pipeline.observation_rows_created,
                           coordinator.pipeline.observation_rows_skipped_by_policy))
        finally:
            coordinator.close()
    assert len(captures.observed_frames) > 1
    assert captures.observed_frames == sorted(set(captures.observed_frames))
    assert counts[0] == counts[1]


def test_terminal_capture_history_is_bounded():
    store, body, clock, controller, radio, value = capture()
    store.release(LEASE)
    for index in range(30):
        lease_id = f"{index:032x}"
        assert store.arm(lease_id, body, value)[0] == 202
        store.release(lease_id)
    assert len(store._leases) == 16
    assert not store._tracker.records
