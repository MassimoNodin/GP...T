from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from f1_engineer.api import app as app_module
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore
from tests.test_detail_evidence import evidence_packets
from tests.test_radio_regions import push
from tests.test_region_capture import capture, LEASE, TOKEN


OPPONENT_LEASE = "b" * 32
OWNER = "radio-opponent-owner"


def ready_opponent():
    store, player_body, clock, controller, radio, value = capture()
    rival = deepcopy(value["drivers"][0])
    rival.update(car_index=1, identity={"car_index": 1, "tenure_ordinal": 1,
                                       "participant_identity_fingerprint": "e" * 64})
    rival["lap"].update(current_lap_number=2, car_position=2)
    rival["observation"] = {"status": "unavailable", "identity": deepcopy(rival["identity"]),
                            "detail_task_ids": [OWNER], "value": None, "source": None}
    value["drivers"] = [value["drivers"][0], rival]
    store.observe(value)
    body = {**deepcopy(player_body), "first_lap": 3, "opponent": deepcopy(rival["identity"]), "detail_task_id": OWNER}
    assert store.arm(OPPONENT_LEASE, body, value)[0] == 202
    return store, body, clock, controller, radio, value


def tick(store, controller, value, *, number=3, player_number=4, index=0, mutate=None):
    push(controller, value, lap=player_number, distance=float(index * 5), clock=index * 50,
         speed=180 - index, brake=0.5 if 4 <= index <= 8 else 0.0)
    rival = value["drivers"][1]
    previous = rival["lap"]["current_lap_number"]
    rival["lap"].update(current_lap_number=number, lap_distance_m=float(index * 5),
                         current_lap_time_ms=index * 50 + 200)
    rival["observation"] = {"status": "available", "identity": deepcopy(rival["identity"]),
        "detail_task_ids": [OWNER], "source": deepcopy(value["drivers"][0]["observation"]["source"]),
        "value": {"lap_number": number, "lap_distance_m": float(index * 5),
                  "current_lap_time_ms": index * 50 + 200, "speed_kph": 160 - index,
                  "brake": 0.7 if 5 <= index <= 9 else 0.0}}
    if number == previous + 1:
        rival["lap"]["last_lap_time_ms"] = 2000
        value["event_cursor"] += 1
        value["events"].append({"id": value["event_cursor"], "kind": "lap_completed",
            "scope": deepcopy(value["scope"]), "driver": deepcopy(rival["identity"]),
            "qualification": "observed_lap_transition", "lap_number": previous,
            "lap_time_ms": 2000, "previous_observed_invalid": 0,
            "received_monotonic_ns": value["received_monotonic_ns"]})
    if mutate is not None:
        mutate(value)
    store.observe(value)


def collect(store, controller, value):
    for index in range(13):
        tick(store, controller, value, index=index)


def test_opponent_completion_survives_later_player_event_and_source_mutation():
    store, body, clock, controller, radio, value = ready_opponent()
    collect(store, controller, value)
    tick(store, controller, value, number=4)
    earlier = store.get(OPPONENT_LEASE, value)["results"][0]
    original = deepcopy(earlier["completion"])
    earlier["completion"]["driver"]["tenure_ordinal"] = 99
    value["events"][-1]["lap_time_ms"] = 9999
    tick(store, controller, value, number=4, player_number=5)
    result = store.get(OPPONENT_LEASE, value)["results"][0]
    assert result["completion"] == original
    assert result["completion"]["driver"] == body["opponent"]
    assert result["completion"]["driver"] != value["events"][-1]["driver"]


def test_opponent_region_uses_its_own_next_lap_and_finish_crossing():
    store, body, clock, controller, radio, value = ready_opponent()
    collect(store, controller, value)
    tick(store, controller, value, index=12, player_number=5)
    player = store.get(LEASE, value)
    rival = store.get(OPPONENT_LEASE, value)
    assert player["state"] == "complete" and "171.0" in player["results"][0]["text"]
    assert rival["state"] == "active" and not rival["results"]
    tick(store, controller, value, number=4, player_number=5)
    rival = store.get(OPPONENT_LEASE, value)
    assert rival["state"] == "complete" and rival["opponent"] == body["opponent"]
    assert rival["detail_task_id"] == OWNER and rival["first_lap"] == 3
    result, = rival["results"]
    assert result["lap_number"] == 3 and "151.0" in result["text"]
    assert result["completion"] == value["events"][-1]
    assert result["completion"]["driver"] == body["opponent"]
    assert result["completion"]["lap_number"] == 3
    assert result["completion"]["lap_time_ms"] == 2000
    assert "between 20 and 25" in result["text"]
    assert result["evidence"]["measurement"]["elapsed"]["elapsed_s"] == pytest.approx(0.4)
    assert not store._leases[OPPONENT_LEASE]["tracker"].records


def test_opponent_clock_cannot_be_substituted_by_player_clock():
    store, body, clock, controller, radio, value = ready_opponent()
    collect(store, controller, value)
    tick(store, controller, value, number=4,
         mutate=lambda snapshot: snapshot["events"][-1].update(lap_time_ms=1000))
    result = store.get(OPPONENT_LEASE, value)["results"][0]
    assert result["evidence"] is None and "completion mismatch" in result["text"]


def test_opponent_can_complete_before_player_without_consuming_players_window():
    store, body, clock, controller, radio, value = ready_opponent()
    collect(store, controller, value)
    tick(store, controller, value, number=4, index=12)
    rival = store.get(OPPONENT_LEASE, value)
    assert rival["state"] == "complete" and "151.0" in rival["results"][0]["text"]
    assert store.get(LEASE, value)["state"] == "active"
    tick(store, controller, value, number=4, player_number=5)
    player = store.get(LEASE, value)
    assert player["state"] == "complete" and "171.0" in player["results"][0]["text"]


def test_capture_requires_opponents_next_lap_not_players_next_lap():
    store, body, clock, controller, radio, value = ready_opponent()
    changed = {**body, "first_lap": 4}
    assert store.arm("c" * 32, changed, value)[1]["reason"] == "region_capture_requires_next_lap"


@pytest.mark.parametrize("mutation", [
    lambda row: row["observation"].update(detail_task_ids=[]),
    lambda row: row["observation"].update(detail_task_ids=["someone-else"]),
    lambda row: row["identity"].update(tenure_ordinal=2),
    lambda row: row["observation"]["identity"].update(tenure_ordinal=2),
    lambda row: row.update(car_index=True),
    lambda row: row["identity"].update(car_index=True),
    lambda row: row["observation"]["identity"].update(car_index=True),
    lambda row: row["observation"].update(detail_task_ids=[OWNER, OWNER]),
])
def test_owner_or_identity_change_revokes_only_opponent_capture(mutation):
    store, body, clock, controller, radio, value = ready_opponent()
    tick(store, controller, value, index=2)
    mutation(value["drivers"][1])
    result = store.get(OPPONENT_LEASE, value)
    assert result["state"] == "unavailable" and result["results"] == []
    assert not store._leases[OPPONENT_LEASE]["tracker"].records
    assert store.get(LEASE, value)["state"] == "active"


@pytest.mark.parametrize("field,broken", [("car_index", True), ("tenure_ordinal", True),
                                         ("participant_identity_fingerprint", "not-a-binding")])
def test_malformed_requested_opponent_identity_is_rejected(field, broken):
    store, body, clock, controller, radio, value = ready_opponent()
    body["opponent"][field] = broken
    assert store.arm("c" * 32, body, value)[0] == 409
    assert "c" * 32 not in store._leases


def test_player_cannot_be_disguised_as_opponent():
    store, body, clock, controller, radio, value = ready_opponent()
    body["opponent"] = deepcopy(body["player"])
    assert store.arm("c" * 32, body, value)[0] == 409


def test_duplicate_opponent_slot_is_not_admitted():
    store, body, clock, controller, radio, value = ready_opponent()
    value["drivers"].append(deepcopy(value["drivers"][1]))
    assert store.arm("c" * 32, body, value)[0] == 409


def test_renewal_preserves_independent_opponent_samples():
    store, body, clock, controller, radio, value = ready_opponent()
    tick(store, controller, value, index=2)
    before = deepcopy(store._leases[OPPONENT_LEASE]["tracker"].records)
    clock[0] = 29
    assert store.arm(OPPONENT_LEASE, body, value)[0] == 202
    assert store._leases[OPPONENT_LEASE]["tracker"].records == before


@pytest.mark.parametrize("end", ["release", "expire", "invalidate"])
def test_opponent_capture_end_clears_only_owned_samples(end):
    store, body, clock, controller, radio, value = ready_opponent()
    tick(store, controller, value, index=2)
    assert store._leases[OPPONENT_LEASE]["tracker"].records
    if end == "release":
        store.release(OPPONENT_LEASE)
    elif end == "expire":
        clock[0] = 30
        store.get(OPPONENT_LEASE, value)
    else:
        store.invalidate("test-reset")
    assert not store._leases[OPPONENT_LEASE]["tracker"].records
    assert not store.get(OPPONENT_LEASE, value)["results"]


@pytest.mark.parametrize("target", ["player", "opponent"])
def test_player_or_opponent_discontinuity_invalidates_opponent_capture(target):
    store, body, clock, controller, radio, value = ready_opponent()
    def discontinuity(snapshot):
        snapshot["event_cursor"] += 1
        snapshot["events"].append({"id": snapshot["event_cursor"], "kind": "driver_discontinuity",
            "scope": deepcopy(snapshot["scope"]), "driver": deepcopy(body[target])})
    tick(store, controller, value, index=2, mutate=discontinuity)
    assert store.get(OPPONENT_LEASE, value)["reason"] == "region_driver_discontinuity"


def test_all_region_leases_share_one_active_budget():
    store, body, clock, controller, radio, value = ready_opponent()
    assert store.arm("c" * 32, body, value)[0] == 202
    assert store.arm("d" * 32, body, value)[1]["reason"] == "region_capture_budget_busy"


@pytest.mark.parametrize("mutation", [
    lambda row: row["lap"].update(pit_status_id=1),
    lambda row: row["tyre"]["value"].update(actual_compound=18),
    lambda row: row["observation"].update(value=None),
    lambda row: row["observation"]["value"].update(lap_number=4),
    lambda row: row["observation"]["source"].update(telemetry_received_monotonic_ns=0),
])
def test_interrupted_or_unusable_opponent_window_cannot_produce_comparison_evidence(mutation):
    store, body, clock, controller, radio, value = ready_opponent()
    for index in range(13):
        tick(store, controller, value, index=index,
             mutate=(lambda snapshot: mutation(snapshot["drivers"][1])) if index == 6 else None)
    tick(store, controller, value, number=4)
    result = store.get(OPPONENT_LEASE, value)["results"][0]
    assert result["evidence"] is None and "unavailable" in result["text"].lower()


def test_duplicate_completion_does_not_duplicate_opponent_result():
    store, body, clock, controller, radio, value = ready_opponent()
    collect(store, controller, value)
    tick(store, controller, value, number=4)
    store.observe(value)
    assert len(store.get(OPPONENT_LEASE, value)["results"]) == 1


def test_owner_loss_during_backend_frame_clears_opponent_samples():
    store, body, clock, controller, radio, value = ready_opponent()
    tick(store, controller, value, index=2)
    tick(store, controller, value, index=3,
         mutate=lambda snapshot: snapshot["drivers"][1]["observation"].update(detail_task_ids=[]))
    assert store.get(OPPONENT_LEASE, value)["state"] == "unavailable"
    assert not store._leases[OPPONENT_LEASE]["tracker"].records


def test_coordinator_publishes_exact_verified_target_identity(tmp_path):
    store = EvidenceStore(tmp_path / "capture-targets.sqlite3")
    coordinator = SessionCoordinator(store, "capture-targets", detail_profile="demand_v1")
    try:
        for packet in evidence_packets(frames=8):
            coordinator.ingest(packet)
        session = store.sessions()[0]["id"]
        bindings = coordinator._current_detail_bindings()
        targets = coordinator.detail_demand_queue.targets(session)
        assert targets
        for target in targets:
            tenure = bindings[target["binding_id"]]
            assert target["tenure_ordinal"] == tenure.tenure_ordinal
            assert target["participant_identity_fingerprint"] == tenure.participant_identity_fingerprint
    finally:
        coordinator.close()


def opponent_api(tmp_path, monkeypatch):
    store, body, clock, controller, radio, value = ready_opponent()
    owner = SimpleNamespace(task_id=OWNER, state="active", session_id=body["session_id"], binding_id="f" * 64)
    targets = [{"binding_id": owner.binding_id, "car_index": 1, "epoch": body["scope"]["epoch"],
                "tenure_ordinal": body["opponent"]["tenure_ordinal"],
                "participant_identity_fingerprint": body["opponent"]["participant_identity_fingerprint"],
                "packet_format": body["scope"]["packet_format"], "is_player": False}]
    class Runtime:
        region_captures = store
        detail_profile = "demand_v1"
        def engineer_snapshot(self):
            return deepcopy(value)
        def detail_task(self, identifier):
            return owner if identifier == OWNER else None
        def detail_targets(self, session):
            return targets if session == body["session_id"] else None
    runtime = Runtime()
    monkeypatch.setattr(app_module, "LiveSessionRuntime", lambda *args, **kwargs: runtime)
    app = app_module.create_app(tmp_path / "archive.sqlite3", control_token=TOKEN, automatic_acquisition=True)
    return TestClient(app), store, body, value, runtime, owner, targets


def test_authenticated_api_admits_reads_and_releases_owned_opponent_region(tmp_path, monkeypatch):
    client, store, body, value, runtime, owner, targets = opponent_api(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    url = f"/api/v2/engineer/region-captures/{OPPONENT_LEASE}"
    assert client.post(url, headers=headers, json=body).status_code == 202
    result = client.get(url, headers=headers)
    assert result.status_code == 200 and result.json()["data"]["detail_task_id"] == OWNER
    assert result.headers["cache-control"] == "no-store"
    assert client.delete(url, headers=headers).json()["data"]["state"] == "released"


@pytest.mark.parametrize("state", ["queued", "released", "expired", "rejected"])
def test_api_refuses_nonactive_owner_and_revokes_even_if_frame_metadata_is_old(tmp_path, monkeypatch, state):
    client, store, body, value, runtime, owner, targets = opponent_api(tmp_path, monkeypatch)
    owner.state = state
    headers = {"Authorization": f"Bearer {TOKEN}"}
    url = f"/api/v2/engineer/region-captures/{OPPONENT_LEASE}"
    assert client.post(url, headers=headers, json=body).status_code == 409
    result = client.get(url, headers=headers).json()["data"]
    assert result["state"] == "unavailable" and result["reason"] == "region_opponent_owner_unavailable"


@pytest.mark.parametrize("mutation", [
    lambda runtime, owner, targets: setattr(runtime, "detail_profile", "full"),
    lambda runtime, owner, targets: setattr(owner, "session_id", "different-session"),
    lambda runtime, owner, targets: targets[0].update(car_index=2),
    lambda runtime, owner, targets: targets[0].update(epoch=999),
    lambda runtime, owner, targets: targets[0].update(tenure_ordinal=2),
    lambda runtime, owner, targets: targets[0].update(participant_identity_fingerprint="replacement"),
    lambda runtime, owner, targets: targets[0].update(is_player=True),
])
def test_api_checks_profile_session_and_current_binding(tmp_path, monkeypatch, mutation):
    client, store, body, value, runtime, owner, targets = opponent_api(tmp_path, monkeypatch)
    mutation(runtime, owner, targets)
    response = client.post(f"/api/v2/engineer/region-captures/{'c' * 32}", json=body,
                           headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 409 and "c" * 32 not in store._leases
