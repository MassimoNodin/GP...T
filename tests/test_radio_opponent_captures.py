from copy import deepcopy

import pytest

from f1_engineer.radio.opponent_captures import RadioOpponentCaptures
from tests.test_radio_detail_demands import DemandClient
from tests.test_opponent_region_capture import ready_opponent, tick, OPPONENT_LEASE
from tests.test_region_capture import LEASE


class PairClient(DemandClient):
    def __init__(self, store, value):
        super().__init__()
        self.store, self.value = store, value
        self.rows = [{**value["drivers"][1]["identity"], "epoch": value["scope"]["epoch"],
                      "packet_format": value["scope"]["packet_format"], "is_player": False,
                      "binding_id": "b" * 64}]
        self.region_override = lambda method, body: None

    def apply(self):
        while command := self.queue.pop():
            self.sequence += 1
            self.queue.apply(command, effective_sequence=self.sequence,
                             frame_ordinal=self.value["frame_ordinal"], target_current=True)

    def region_capture(self, method, lease_id, body=None):
        self.calls.append(("region_" + method, lease_id, deepcopy(body)))
        self.hook("region_" + method)
        override = self.region_override(method, body)
        if override is not None:
            return override
        if method == "POST":
            code, data = self.store.arm(lease_id, body, self.value)
        else:
            data = self.store.get(lease_id, self.value) if method == "GET" else self.store.release(lease_id)
            code = 200 if data is not None else 404
        return ({"status": "available", "data": data} if code in (200, 202)
                else {"status": "unavailable", "status_code": code})


def ready():
    store, opponent_body, clock, controller, radio, value = ready_opponent()
    store.release(LEASE)
    store.release(OPPONENT_LEASE)
    body = {key: deepcopy(opponent_body[key]) for key in
            ("generation", "session_id", "scope", "player", "laps", "window_m")}
    body["first_lap"] = 4
    request = {"task_id": 1, "version": 1, "body": body,
               "opponent": deepcopy(opponent_body["opponent"]), "opponent_first_lap": 3}
    client = PairClient(store, value)
    manager = RadioOpponentCaptures(client, clock=lambda: client.clock[0],
                                   clock_ns=lambda: value["received_monotonic_ns"])
    manager.replace([request])
    return manager, client, request, controller, radio, value


def activate(manager, client, value):
    first = manager.poll(value)[0]
    assert first["state"] == "waiting"
    client.apply()
    owner_id = first["owner"]["detail_task_id"]
    value["drivers"][1]["observation"]["detail_task_ids"] = [owner_id]
    client.store.observe(value)
    return owner_id


def armed():
    manager, client, request, controller, radio, value = ready()
    owner_id = activate(manager, client, value)
    assert manager.poll(value)[0]["state"] == "collecting"
    return manager, client, request, controller, radio, value, owner_id


def drive(client, controller, value, owner_id, **options):
    tick(client.store, controller, value, mutate=lambda snapshot:
         snapshot["drivers"][1]["observation"].update(detail_task_ids=[owner_id]), **options)


def test_both_leases_wait_for_authoritative_owner_and_processed_frame_metadata():
    manager, client, request, controller, radio, value = ready()
    first = manager.poll(value)[0]
    assert not any(call[0] == "region_POST" for call in client.calls)
    client.apply()
    assert manager.poll(value)[0]["state"] == "waiting"
    assert not any(call[0] == "region_POST" for call in client.calls)
    owner_id = first["owner"]["detail_task_id"]
    value["drivers"][1]["observation"]["detail_task_ids"] = [owner_id]
    result = manager.poll(value)[0]
    assert result["state"] == "collecting"
    player, opponent = result["legs"]
    assert player["body"]["first_lap"] == 4
    assert opponent["body"]["first_lap"] == 3
    assert opponent["body"]["detail_task_id"] == owner_id
    assert player["lease_id"] != opponent["lease_id"]


@pytest.mark.parametrize("first_driver", ["player", "opponent"])
def test_pair_retains_owner_until_both_independent_crossings_are_consumed(first_driver):
    manager, client, request, controller, radio, value, owner_id = armed()
    for index in range(13):
        drive(client, controller, value, owner_id, index=index)
    if first_driver == "player":
        drive(client, controller, value, owner_id, index=12, player_number=5)
    else:
        drive(client, controller, value, owner_id, index=12, number=4)
    result = manager.poll(value)[0]
    assert result["state"] == "collecting"
    assert sorted(leg["response"]["data"]["state"] for leg in result["legs"]) == ["active", "complete"]
    assert client.queue.get(owner_id).state == "active"
    drive(client, controller, value, owner_id, player_number=5, number=4)
    result = manager.poll(value)[0]
    assert result["state"] == "complete"
    player, opponent = result["legs"]
    assert player["response"]["data"]["results"][0]["lap_number"] == 4
    assert opponent["response"]["data"]["results"][0]["lap_number"] == 3
    assert client.queue.get(owner_id).state == "active"
    manager.replace([])
    assert manager.poll(value) == []
    assert all(client.store.get(leg["lease_id"], value)["state"] == "released" for leg in result["legs"])
    client.apply()
    assert manager.close()["cleanup_verified"]


@pytest.mark.parametrize("driver", [0, 1])
def test_late_activation_never_shifts_requested_next_lap(driver):
    manager, client, request, controller, radio, value = ready()
    activate(manager, client, value)
    value["drivers"][driver]["lap"]["current_lap_number"] += 1
    result = manager.poll(value)[0]
    assert result["reason"] == "opponent_requested_lap_missed"
    assert result["state"] == "unavailable"
    assert not any(call[0] == "region_POST" for call in client.calls)
    value["drivers"][driver]["lap"]["current_lap_number"] -= 1
    assert manager.poll(value)[0]["state"] == "unavailable"


def test_region_budget_rejection_releases_admitted_leg_and_never_returns_half_pair():
    manager, client, request, controller, radio, value = ready()
    owner_id = activate(manager, client, value)
    client.region_override = lambda method, body: (
        {"status": "unavailable", "status_code": 409}
        if method == "POST" and "opponent" in body else None)
    result = manager.poll(value)[0]
    assert result["state"] == "unavailable"
    assert result["legs"] == []
    player_post = next(call for call in client.calls if call[0] == "region_POST" and "opponent" not in call[2])
    assert client.store.get(player_post[1], value)["state"] == "released"
    client.apply()
    assert client.queue.get(owner_id).state == "released"


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(session_id="f" * 32),
    lambda value: value.update(generation="f" * 32),
    lambda value: value["scope"].update(epoch=3),
    lambda value: value["drivers"][0]["identity"].update(tenure_ordinal=2),
    lambda value: value["drivers"][1]["identity"].update(tenure_ordinal=2),
    lambda value: value["drivers"].append(deepcopy(value["drivers"][1])),
    lambda value: value.update(player_car_index=True),
])
def test_scope_or_identity_changes_fence_and_release_both_legs(mutation):
    manager, client, request, controller, radio, value, owner_id = armed()
    leases = manager.poll(value)[0]["legs"]
    mutation(value)
    result = manager.poll(value)[0]
    assert result["state"] == "unavailable" and not result["legs"]
    assert all(client.store.get(leg["lease_id"], value)["state"] == "released" for leg in leases)


def test_stale_frame_waits_without_admission_and_does_not_invent_later_laps():
    manager, client, request, controller, radio, value = ready()
    manager.clock_ns = lambda: value["received_monotonic_ns"] + 3_000_000_000
    assert manager.poll(value)[0]["reason"] == "opponent_frame_unverified"
    assert not any(call[0] == "region_POST" for call in client.calls)
    manager.clock_ns = lambda: value["received_monotonic_ns"]
    assert manager.poll(value)[0]["state"] == "waiting"


def test_owner_metadata_older_than_activation_frame_does_not_arm_regions():
    manager, client, request, controller, radio, value = ready()
    activate(manager, client, value)
    value["frame_ordinal"] = 0
    assert manager.poll(value)[0]["state"] == "waiting"
    assert not any(call[0] == "region_POST" for call in client.calls)


def test_cancel_during_capture_admission_releases_pair_without_returning_results():
    manager, client, request, controller, radio, value = ready()
    owner_id = activate(manager, client, value)
    client.hook = lambda method: manager.replace([]) if method == "region_POST" else None
    assert manager.poll(value) == []
    posts = [call for call in client.calls if call[0] == "region_POST"]
    assert all(client.store.get(call[1], value)["state"] == "released" for call in posts)
    client.apply()
    assert client.queue.get(owner_id).state == "released"


def test_transport_loss_pauses_pair_readiness_without_discarding_collection():
    manager, client, request, controller, radio, value, owner_id = armed()
    client.override["targets"] = {"status": "unavailable"}
    result = manager.poll(value)[0]
    assert result["state"] == "waiting"
    assert len(result["legs"]) == 2
    assert all(leg["response"]["data"]["state"] == "active" for leg in result["legs"])
    assert not any(call[0] == "region_DELETE" for call in client.calls)


@pytest.mark.parametrize("edit", [
    lambda request: request.update(opponent_first_lap=True),
    lambda request: request["body"].update(laps=21),
    lambda request: request["body"].update(window_m=[0, float("nan")]),
    lambda request: request.update(opponent=deepcopy(request["body"]["player"])),
    lambda request: request["body"].update(extra=True),
    lambda request: request["opponent"].update(tenure_ordinal=0),
])
def test_invalid_descriptors_are_refused_without_network_io(edit):
    manager, client, request, controller, radio, value = ready()
    edit(request)
    with pytest.raises(ValueError, match="opponent_capture_descriptors_invalid"):
        manager.replace([request])
    assert not client.calls


def test_desired_pair_is_copied_and_shutdown_reports_queued_owner_cleanup():
    manager, client, request, controller, radio, value = ready()
    request["body"]["first_lap"] = 8
    owner_id = activate(manager, client, value)
    result = manager.poll(value)[0]
    assert result["body"]["first_lap"] == 4
    cleanup = manager.close()
    assert cleanup["captures"]["cleanup_verified"]
    assert not cleanup["cleanup_verified"]
    assert cleanup["demands"]["pending_detail_task_ids"] == [owner_id]
    client.apply()
    assert manager.close()["cleanup_verified"]


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(lease_id="f" * 32),
    lambda data: data.update(first_lap=True),
    lambda data: data["window"].update(start_m=11),
    lambda data: data["window"].update(track_length_m=101),
    lambda data: data.update(state="complete", results=[]),
    lambda data: data.update(results=[{"lap_number": True}]),
])
def test_mismatched_or_incomplete_capture_payload_never_returns_completed_pair(mutation):
    manager, client, request, controller, radio, value, owner_id = armed()
    original = client.region_capture
    def respond(method, lease_id, body=None):
        response = original(method, lease_id, body)
        if method == "GET" and response.get("status") == "available":
            mutation(response["data"])
        return response
    client.region_capture = respond
    result = manager.poll(value)[0]
    assert result["state"] == "unavailable" and result["legs"] == []


def test_same_version_cannot_mutate_fixed_lap_window():
    manager, client, request, controller, radio, value = ready()
    request["body"]["first_lap"] += 1
    with pytest.raises(ValueError, match="opponent_capture_identity_conflict"):
        manager.replace([request])
    request["version"] += 1
    manager.replace([request])


@pytest.mark.parametrize("requests", [None, {}, []])
def test_pair_request_list_contract(requests):
    manager, client, request, controller, radio, value = ready()
    if requests == []:
        manager.replace(requests)
        assert manager.poll(value) == []
    else:
        with pytest.raises(ValueError, match="opponent_capture_descriptors_invalid"):
            manager.replace(requests)
