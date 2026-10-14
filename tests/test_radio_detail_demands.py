from copy import deepcopy
from dataclasses import asdict
import json

import httpx
import pytest

from f1_engineer.processing.detail_demand import DetailCommand, DetailDemandQueue
from f1_engineer.radio.client import RadioClient
from f1_engineer.radio.detail_demands import RadioDetailDemands
from tests.test_standalone_radio import token_file


def descriptor(task_id=1, version=1):
    return {"task_id": task_id, "version": version, "session_id": "a" * 32,
            "scope": {"session_uid": 100, "packet_format": 2026, "epoch": 3},
            "opponent": {"car_index": 1, "tenure_ordinal": 2,
                         "participant_identity_fingerprint": "e" * 64}}


def target():
    request = descriptor()
    return {**request["opponent"], "epoch": 3, "packet_format": 2026,
            "is_player": False, "binding_id": "b" * 64}


class DemandClient:
    def __init__(self):
        self.clock = [0.0]
        self.queue = DetailDemandQueue(clock=lambda: self.clock[0])
        self.rows = [target()]
        self.calls = []
        self.override = {}
        self.hook = lambda method: None
        self.sequence = 0

    def detail_targets(self, session_id):
        self.calls.append(("targets", session_id, None))
        self.hook("targets")
        return self.override.get("targets", {"status": "available", "data": deepcopy(self.rows)})

    def detail_task(self, method, task_id, body=None):
        self.calls.append((method, task_id, deepcopy(body)))
        self.hook(method)
        if method in self.override:
            return deepcopy(self.override[method])
        if method == "POST":
            code, data = self.queue.enqueue(DetailCommand("post", task_id, body["session_id"], body["binding_id"]))
        else:
            task = self.queue.get(task_id)
            if task is None:
                return {"status": "unavailable", "status_code": 404}
            if method == "GET":
                code, data = 200, asdict(task)
            else:
                code, data = self.queue.enqueue(DetailCommand("release", task_id, task.session_id, task.binding_id))
        return ({"status": "available", "data": data} if code in (200, 202)
                else {"status": "unavailable", "status_code": code})

    def apply(self, **options):
        while command := self.queue.pop():
            self.sequence += 1
            self.queue.apply(command, effective_sequence=self.sequence, frame_ordinal=10,
                             target_current=True, **options)


def ready():
    client = DemandClient()
    events = []
    broker = RadioDetailDemands(client, clock=lambda: client.clock[0], notify=events.append)
    broker.replace([descriptor()])
    return client, broker, events


def active():
    client, broker, events = ready()
    broker.poll()
    client.apply()
    assert broker.poll()[0]["state"] == "active"
    return client, broker, events


def count(client, method):
    return sum(call[0] == method for call in client.calls)


def test_queued_admission_is_not_active_and_stable_id_requires_owner_application():
    client, broker, events = ready()
    first = broker.poll()[0]
    assert first["state"] == "waiting"
    assert first["activation"] is None
    for unused in range(5):
        assert broker.poll()[0]["detail_task_id"] == first["detail_task_id"]
    assert count(client, "POST") == 1
    client.apply()
    result = broker.poll()[0]
    assert result["state"] == "active"
    assert result["binding_id"] == "b" * 64
    assert result["activation"] == (1, 10)
    assert [event["event"] for event in events] == ["radio_detail_admission", "radio_detail_activated"]
    assert all(event["latency_s"] >= 0 for event in events)


@pytest.mark.parametrize("change", ["slot", "tenure", "fingerprint", "epoch", "format", "player", "bool", "duplicate", "binding"])
def test_targets_require_unique_exact_nonplayer_identity(change):
    client, broker, events = ready()
    edits = {"slot": ("car_index", 2), "tenure": ("tenure_ordinal", 3),
             "fingerprint": ("participant_identity_fingerprint", "f" * 64),
             "epoch": ("epoch", 4), "format": ("packet_format", 2025),
             "player": ("is_player", True), "bool": ("car_index", True),
             "binding": ("binding_id", "../unsafe")}
    if change == "duplicate":
        client.rows.append(deepcopy(client.rows[0]))
    else:
        key, value = edits[change]
        client.rows[0][key] = value
    assert broker.poll()[0]["state"] == "unavailable"
    assert count(client, "POST") == 0
    client.rows = [target()]
    assert broker.poll()[0]["state"] == "unavailable"
    assert count(client, "POST") == 0


def test_temporary_target_failure_pauses_without_releasing_or_rebinding():
    client, broker, events = active()
    client.override["targets"] = {"status": "unavailable"}
    assert broker.poll()[0]["reason"] == "detail_targets_unverified"
    assert count(client, "DELETE") == 0
    del client.override["targets"]
    assert broker.poll()[0]["state"] == "active"
    assert count(client, "POST") == 1


def test_identity_replacement_releases_old_owner_and_never_rebinds():
    client, broker, events = active()
    client.rows[0]["binding_id"] = "c" * 64
    assert broker.poll()[0]["reason"] == "detail_identity_unavailable"
    client.apply()
    client.rows = [target()]
    assert broker.poll()[0]["state"] == "unavailable"
    assert count(client, "POST") == 1
    assert count(client, "DELETE") == 1


def test_renewal_preserves_activation_and_waits_for_application():
    client, broker, events = active()
    client.clock[0] = 120
    assert broker.poll()[0]["reason"] == "detail_renewal_pending"
    client.clock[0] = 250
    for unused in range(8):
        assert broker.poll()[0]["state"] == "waiting"
    assert count(client, "POST") == 2
    client.apply()
    assert broker.poll()[0]["activation"] == (1, 10)
    assert broker.poll()[0]["state"] == "active"


def test_ambiguous_renewal_does_not_repeat_an_unverified_command():
    client, broker, events = active()
    client.override["POST"] = {"status": "unavailable"}
    client.clock[0] = 120
    broker.poll()
    client.clock[0] = 260
    assert broker.poll()[0]["state"] == "waiting"
    assert count(client, "POST") == 2


@pytest.mark.parametrize("method", ["POST", "targets", "GET"])
def test_cancellation_during_io_never_exposes_ready_owner(method):
    client, broker, events = ready()
    client.hook = lambda current: broker.replace([]) if current == method else None
    assert broker.poll() == []
    if method != "targets":
        assert count(client, "DELETE") == 1
        client.apply()
        assert broker.close()["cleanup_verified"]
    else:
        assert count(client, "POST") == 0


def test_queued_delete_is_pending_not_verified_and_does_not_flood_commands():
    client, broker, events = active()
    result = broker.close()
    assert not result["cleanup_verified"]
    assert len(result["pending_detail_task_ids"]) == 1
    for unused in range(20):
        broker.poll()
    assert count(client, "DELETE") == 1
    client.apply()
    assert broker.close() == {"cleanup_verified": True, "pending_detail_task_ids": []}


def test_unverified_cleanup_remains_bounded_and_does_not_delete_other_owners():
    client, broker, events = ready()
    broker.replace([descriptor(index) for index in range(1, 5)])
    broker.poll()
    client.apply()
    foreign = DetailCommand("post", "dashboard_owner", "a" * 32, "b" * 64)
    client.queue.enqueue(foreign)
    client.override["DELETE"] = {"status": "unavailable"}
    broker.replace([descriptor(5)])
    result = broker.poll()[0]
    assert result["reason"] == "detail_owner_budget_busy"
    assert len(broker._owned) == 4
    assert all(call[1].startswith("radio_") for call in client.calls if call[0] == "DELETE")
    for unused in range(10):
        broker.poll()
    assert count(client, "DELETE") == 4


@pytest.mark.parametrize("change", ["version", "session", "scope"])
def test_supersession_releases_fixed_old_binding_and_allocates_new_owner(change):
    client, broker, events = active()
    old = broker.poll()[0]["detail_task_id"]
    request = descriptor()
    if change == "version":
        request["version"] = 2
    elif change == "session":
        request["session_id"] = "c" * 32
    else:
        request["scope"]["epoch"] = 4
        client.rows[0]["epoch"] = 4
    broker.replace([request])
    result = broker.poll()[0]
    assert result["detail_task_id"] != old
    assert count(client, "DELETE") == 1
    client.apply()
    broker.poll()
    assert len(broker._owned) == 1


def test_terminal_owner_never_auto_revives():
    client, broker, events = ready()
    first = broker.poll()[0]
    client.apply(is_player=True)
    assert broker.poll()[0]["reason"] == "detail_rejected"
    for unused in range(5):
        broker.poll()
    assert count(client, "POST") == 1
    assert broker.close()["cleanup_verified"]
    assert count(client, "DELETE") == 0


def test_descriptors_are_copied_and_returned_state_cannot_mutate_binding():
    client, broker, events = ready()
    request = descriptor()
    broker.replace([request])
    request["opponent"]["car_index"] = 2
    result = broker.poll()[0]
    result["opponent"]["car_index"] = 3
    assert broker.poll()[0]["opponent"]["car_index"] == 1


@pytest.mark.parametrize("change", ["task_id", "session_id", "binding_id", "activation", "frame", "state"])
def test_unverified_owner_snapshots_never_report_active(change):
    client, broker, events = active()
    owner_id = broker.poll()[0]["detail_task_id"]
    data = asdict(client.queue.get(owner_id))
    edits = {"task_id": ("task_id", "foreign"), "session_id": ("session_id", "c" * 32),
             "binding_id": ("binding_id", "c" * 64), "activation": ("activation_ordinal", True),
             "frame": ("activation_frame_ordinal", True), "state": ("state", [])}
    key, value = edits[change]
    data[key] = value
    client.override["GET"] = {"status": "available", "data": data}
    assert broker.poll()[0]["reason"] == "detail_status_unverified"
    assert not broker.close()["cleanup_verified"]


def test_owner_reactivation_cannot_silently_change_capture_activation():
    client, broker, events = active()
    owner_id = broker.poll()[0]["detail_task_id"]
    data = asdict(client.queue.get(owner_id))
    data["activation_ordinal"] += 1
    client.override["GET"] = {"status": "available", "data": data}
    assert broker.poll()[0]["reason"] == "detail_activation_changed"
    assert count(client, "DELETE") == 1


def test_expired_owner_is_terminal_and_proves_cleanup():
    client, broker, events = active()
    client.clock[0] = 301
    client.queue.expire(effective_sequence=20)
    assert broker.poll()[0]["reason"] == "detail_expired"
    assert count(client, "POST") == 1
    assert broker.close()["cleanup_verified"]


def test_ambiguous_initial_post_does_not_flood_or_claim_verified_cleanup():
    client, broker, events = ready()
    client.override["POST"] = {"status": "unavailable"}
    for unused in range(10):
        assert broker.poll()[0]["state"] == "unavailable"
    assert count(client, "POST") == 1
    assert not broker.close()["cleanup_verified"]
    assert count(client, "DELETE") == 1


@pytest.mark.parametrize("code", [401, 403, 404, 409, 422])
def test_definitive_admission_rejection_is_terminal_without_cleanup_claims(code):
    client, broker, events = ready()
    client.override["POST"] = {"status": "unavailable", "status_code": code}
    assert broker.poll()[0]["reason"] == "detail_admission_rejected"
    broker.poll()
    assert count(client, "POST") == 1
    assert broker.close()["cleanup_verified"]
    assert count(client, "DELETE") == 0


@pytest.mark.parametrize("requests", [None, {}, [descriptor()] * 2,
                                     [descriptor(index) for index in range(1, 6)],
                                     [{**descriptor(), "version": True}],
                                     [{**descriptor(), "session_id": "unsafe"}]])
def test_invalid_descriptors_do_not_replace_existing_desired(requests):
    client, broker, events = ready()
    with pytest.raises(ValueError, match="detail_descriptors_invalid"):
        broker.replace(requests)
    assert broker.poll()[0]["task_id"] == 1


@pytest.mark.parametrize("method", ["targets", "POST", "GET", "DELETE"])
def test_transport_routes_authentication_and_exact_body(tmp_path, method):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200 if request.method == "GET" else 202,
                              json={"data": [target()] if method == "targets" else {"state": "queued"}})
    client = RadioClient("http://localhost", token_file(tmp_path), transport=httpx.MockTransport(respond))
    body = {"task_id": "radio_owner", "session_id": "a" * 32, "binding_id": "b" * 64}
    try:
        result = (client.detail_targets("a" * 32) if method == "targets"
                  else client.detail_task(method, "radio_owner", body if method == "POST" else None))
        assert result["status"] == "available"
        assert calls[0].headers["Authorization"] == "Bearer " + "a" * 32
        if method == "POST":
            assert calls[0].url.path == "/api/v2/session-evidence/detail-tasks"
            assert json.loads(calls[0].content) == body
        elif method == "targets":
            assert calls[0].url.params["session_id"] == "a" * 32
        else:
            assert calls[0].url.path.endswith("/radio_owner")
    finally:
        client.close()


@pytest.mark.parametrize("code,body", [(403, b"secret"), (302, b"secret"), (200, b"invalid"),
                                      (200, b"x" * 65_537), (200, b'{"data":[]}'),
                                      (200, b'{"data":null}')],
                         ids=["forbidden", "redirect", "json", "oversized", "list", "null"])
def test_transport_fails_closed_without_leaking_bodies(tmp_path, code, body):
    client = RadioClient("http://localhost", token_file(tmp_path),
                         transport=httpx.MockTransport(lambda request: httpx.Response(code, content=body)))
    try:
        result = client.detail_task("GET", "radio_owner")
        assert result["status"] == "unavailable"
        assert "secret" not in json.dumps(result)
        if code != 200:
            assert result["status_code"] == code
    finally:
        client.close()


@pytest.mark.parametrize("data", [[target()] * 25, {}, [None], None])
def test_transport_rejects_unbounded_or_malformed_target_lists(tmp_path, data):
    client = RadioClient("http://localhost", token_file(tmp_path),
                         transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": data})))
    try:
        assert client.detail_targets("a" * 32)["status"] == "unavailable"
    finally:
        client.close()


@pytest.mark.parametrize("method,task_id,body", [("PATCH", "owner", None), ("GET", "../owner", None),
                                               ("GET", "owner", {}), ("POST", "owner", {}),
                                               ("POST", "owner", {"task_id": "different", "session_id": "a" * 32,
                                                                  "binding_id": "b" * 64})])
def test_transport_invalid_commands_never_reach_network(tmp_path, method, task_id, body):
    client = RadioClient("http://localhost", token_file(tmp_path),
                         transport=httpx.MockTransport(lambda request: pytest.fail("unexpected network request")))
    try:
        assert client.detail_task(method, task_id, body)["reason"] == "detail_request_invalid"
        assert client.detail_targets("unsafe")["reason"] == "detail_request_invalid"
    finally:
        client.close()
