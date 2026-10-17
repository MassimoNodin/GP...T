from copy import deepcopy
import queue
import threading

import httpx
import pytest

from f1_engineer.radio.client import RadioClient
from f1_engineer.radio.controller import RadioController
from f1_engineer.radio.command import _poll
from f1_engineer.radio.region_captures import RadioRegionCaptures
from f1_engineer.processing.region_capture import RegionCaptureStore
from tests.test_radio_regions import ready, push, REQUEST
from tests.test_standalone_radio import token_file


class CaptureClient:
    def __init__(self, store, value):
        self.store = store
        self.value = value
        self.calls = []

    def region_capture(self, method, lease_id, body=None):
        self.calls.append((method, lease_id, deepcopy(body)))
        if method == "POST":
            code, data = self.store.arm(lease_id, body, self.value)
        else:
            data = (self.store.get(lease_id, self.value) if method == "GET"
                    else self.store.release(lease_id))
            code = 200 if data is not None else 404
        return ({"status": "available", "data": data} if code in (200, 202)
                else {"status": "unavailable", "status_code": code, "reason": "test_rejection"})


def backend_ready():
    original, radio, events, value = ready()
    controller = RadioController(radio, notify=events.append, backend_regions=True)
    controller.feed(value, now=10)
    controller.handle_request(REQUEST, now=10)
    radio.cancel()
    clock = [10.0]
    store = RegionCaptureStore(clock=lambda: clock[0], clock_ns=lambda: value["received_monotonic_ns"])
    store.observe(value)
    client = CaptureClient(store, value)
    captures = RadioRegionCaptures(client, clock=lambda: clock[0], notify=events.append)
    captures.replace(controller.region_capture_requests())
    captures.poll()
    return controller, radio, events, value, clock, store, client, captures


def finish_backend(controller, value, store):
    original, radio, events, unused = ready()
    for index in range(13):
        push(original, value, distance=float(index * 5), clock=index * 50,
             speed=180 - index, brake=0.5 if 4 <= index <= 8 else 0.0)
        store.observe(value)
        if index == 0:
            controller.feed(value, now=10)
    push(original, value, lap=5)
    store.observe(value)


def test_backend_capture_speaks_complete_measurement_without_radio_intermediate_polls():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    finish_backend(controller, value, store)
    results = captures.poll()
    controller.feed(value, now=10, region_captures=results)
    report = next(event for event in events if event["event"] == "radio_region_report")
    assert "Sampled minimum 171.0" in report["text"]
    assert "between 15 and 20" in report["text"]
    assert "0.400 seconds" in report["text"]
    assert not controller.regions.records
    assert not controller.region_capture_requests()
    controller.feed(value, now=10, region_captures=results)
    assert sum(event["event"] == "radio_region_report" for event in events) == 1
    captures.replace(controller.region_capture_requests())
    captures.poll()
    assert not store._tracker.records
    assert store.get(results[0]["lease_id"], value)["state"] == "released"


@pytest.mark.parametrize("change", ["version", "lease", "request", "binding", "window", "lap", "event",
                                   "duplicate", "age", "transport", "queue", "clock", "authority", "oversized",
                                   "surrogate", "boolean", "expired"])
def test_wrong_version_binding_or_stale_results_never_become_measurement_speech(change):
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    finish_backend(controller, value, store)
    results = captures.poll()
    item = results[0]
    data = item["response"]["data"]
    result = data["results"][0]
    now = 10
    if change == "version":
        item["version"] += 1
    elif change == "lease":
        item["lease_id"] = "b" * 32
    elif change == "request":
        item["body"]["first_lap"] += 1
    elif change == "binding":
        data["binding"]["session_id"] = "b" * 32
    elif change == "window":
        data["window"]["end_m"] += 1
    elif change == "lap":
        result["lap_number"] += 1
    elif change == "event":
        result["event_id"] += 1
    elif change == "duplicate":
        data["results"].append(deepcopy(result))
    elif change == "age":
        result["completion_age_s"] = 3
    elif change == "transport":
        item["request_age_s"] = 3
    elif change == "queue":
        now = 13
    elif change == "clock":
        item["received_at"] = 11
    elif change == "authority":
        result["evidence"]["measurement"]["coaching_eligible"] = True
    elif change == "oversized":
        result["text"] = "x" * 901
    elif change == "surrogate":
        result["text"] = chr(0xD800)
    elif change == "boolean":
        item["task_id"] = True
    elif change == "expired":
        data["state"] = "expired"
    controller.feed(value, now=now, region_captures=results)
    report = next(event for event in events if event["event"] == "radio_region_report")
    assert report["evidence"] is None
    assert "unavailable" in report["text"]
    assert not any(event["event"] == "radio_region_capture_consumed" for event in events)


def test_backend_mode_does_not_silently_fall_back_to_polling():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    finish_backend(controller, value, store)
    controller.feed(value, now=10)
    report = next(event for event in events if event["event"] == "radio_region_report")
    assert report["evidence"] is None
    assert "unavailable" in report["text"]


def test_renewal_keeps_immutable_request_and_cancel_releases_capture():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    request = controller.region_capture_requests()[0]
    captures.poll()
    assert len([call for call in client.calls if call[0] == "POST"]) == 1
    clock[0] = 21
    captures.poll()
    posts = [call for call in client.calls if call[0] == "POST"]
    assert len(posts) == 2 and posts[0] == posts[1]
    controller.handle_request("cancel task 1", now=10)
    captures.replace(controller.region_capture_requests())
    captures.poll()
    assert store.get(request["lease_id"], value)["state"] == "released"


def test_updated_task_releases_old_capture_and_arms_a_distinct_lease():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    old = controller.region_capture_requests()[0]
    controller.handle_request("update task 1 " + REQUEST.replace("10 to 50", "15 to 45"), now=10)
    new = controller.region_capture_requests()[0]
    assert new["version"] == 2 and new["lease_id"] != old["lease_id"]
    captures.replace(controller.region_capture_requests())
    captures.poll()
    assert store.get(old["lease_id"], value)["state"] == "released"
    assert store.get(new["lease_id"], value)["state"] == "active"
    captures.close()
    assert store.get(new["lease_id"], value)["state"] == "released"


def test_rejected_admission_does_not_rearm_on_a_later_lap():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    captures.close()
    captures.replace(controller.region_capture_requests())
    captures.poll()
    calls = len([call for call in client.calls if call[0] == "POST"])
    clock[0] = 50
    captures.poll()
    assert len([call for call in client.calls if call[0] == "POST"]) == calls


def test_cancel_during_inflight_admission_releases_lease_without_publishing_result():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    captures.close()
    controller.handle_request("update task 1 " + REQUEST, now=10)
    request = controller.region_capture_requests()[0]
    captures.replace([request])
    original = client.region_capture
    def cancel(method, lease_id, body=None):
        result = original(method, lease_id, body)
        if method == "POST":
            captures.replace([])
        return result
    client.region_capture = cancel
    assert captures.poll() == []
    assert store.get(request["lease_id"], value)["state"] == "released"


@pytest.mark.parametrize("method,code", [("POST", 202), ("GET", 200), ("DELETE", 200)])
def test_capture_http_client_authentication_and_success_shape(tmp_path, method, code):
    def respond(request):
        assert request.headers["Authorization"].startswith("Bearer ")
        assert request.method == method
        assert request.url.path.endswith("a" * 32)
        return httpx.Response(code, json={"data": {"state": "active"}})
    client = RadioClient("http://localhost", token_file(tmp_path), transport=httpx.MockTransport(respond))
    try:
        assert client.region_capture(method, "a" * 32, {})["status"] == "available"
    finally:
        client.close()


@pytest.mark.parametrize("response", [httpx.Response(403, json={}), httpx.Response(409, json={}),
                                    httpx.Response(200, json=[]), httpx.Response(200, json={"data": []}),
                                    httpx.Response(200, content=b"x" * 1_048_577)])
def test_capture_http_client_fails_closed_on_bad_responses(tmp_path, response):
    client = RadioClient("http://localhost", token_file(tmp_path),
                         transport=httpx.MockTransport(lambda request: response))
    try:
        assert client.region_capture("GET", "a" * 32)["status"] == "unavailable"
    finally:
        client.close()


def test_transient_admission_failure_retries_same_id_without_losing_samples():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    captures.close()
    controller.handle_request("update task 1 " + REQUEST, now=10)
    captures.replace(controller.region_capture_requests())
    original = client.region_capture
    attempts = []
    def respond(method, lease_id, body=None):
        if method == "POST":
            attempts.append((lease_id, deepcopy(body)))
            result = original(method, lease_id, body)
            if len(attempts) == 1:
                return {"status": "unavailable", "reason": "response_lost_after_admission"}
            return result
        return original(method, lease_id, body)
    client.region_capture = respond
    assert captures.poll()[0]["response"]["status"] == "unavailable"
    assert captures.poll()[0]["response"]["status"] == "available"
    assert attempts[0] == attempts[1]


def test_stale_runtime_ends_requests_and_releases_owned_capture():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    lease_id = controller.region_capture_requests()[0]["lease_id"]
    controller.check_freshness(now=13)
    captures.replace(controller.region_capture_requests())
    captures.poll()
    assert store.get(lease_id, value)["state"] == "released"


def test_failed_release_remains_owned_and_retries_with_backoff():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    lease_id = controller.region_capture_requests()[0]["lease_id"]
    original = client.region_capture
    attempts = []
    def respond(method, identifier, body=None):
        if method == "DELETE":
            attempts.append(identifier)
            if len(attempts) == 1:
                return {"status": "unavailable", "reason": "network_unavailable"}
        return original(method, identifier, body)
    client.region_capture = respond
    result = captures.close()
    assert result == {"cleanup_verified": False, "pending_lease_ids": [lease_id]}
    assert store.get(lease_id, value)["state"] == "active"
    for unused in range(10):
        captures.poll()
    assert attempts == [lease_id]
    clock[0] += 10
    captures.poll()
    assert attempts == [lease_id, lease_id]
    assert captures.close()["cleanup_verified"]
    assert store.get(lease_id, value)["state"] == "released"


@pytest.mark.parametrize("data", [None, {}, {"state": "released"},
                                  {"lease_id": "f" * 32, "state": "released"},
                                  {"lease_id": None, "state": "active"}])
def test_success_transport_is_not_proof_of_matching_capture_cleanup(data):
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    lease_id = controller.region_capture_requests()[0]["lease_id"]
    original = client.region_capture
    client.region_capture = lambda method, identifier, body=None: (
        {"status": "available", "data": deepcopy(data)} if method == "DELETE"
        else original(method, identifier, body))
    assert not captures.close()["cleanup_verified"]
    assert lease_id in captures._owned
    assert events[-1]["event"] == "radio_region_capture_cleanup"
    assert events[-1]["cleanup_verified"] is False


def test_pending_cleanup_cannot_be_rearmed_by_readding_same_capture():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    request = controller.region_capture_requests()[0]
    original = client.region_capture
    client.region_capture = lambda method, identifier, body=None: (
        {"status": "unavailable"} if method == "DELETE" else original(method, identifier, body))
    assert not captures.close()["cleanup_verified"]
    captures.replace([request])
    posts = len([call for call in client.calls if call[0] == "POST"])
    assert captures.poll()[0]["response"]["reason"] == "region_capture_release_pending"
    assert len([call for call in client.calls if call[0] == "POST"]) == posts
    client.region_capture = original
    clock[0] += 10
    assert captures.poll()[0]["response"]["reason"] == "region_capture_released"
    assert len([call for call in client.calls if call[0] == "POST"]) == posts
    assert captures.close()["cleanup_verified"]


def test_lost_delete_acknowledgement_is_not_immediate_cleanup_evidence():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    lease_id = controller.region_capture_requests()[0]["lease_id"]
    original = client.region_capture
    def respond(method, identifier, body=None):
        result = original(method, identifier, body)
        return {"status": "unavailable"} if method == "DELETE" else result
    client.region_capture = respond
    assert not captures.close()["cleanup_verified"]
    assert store.get(lease_id, value)["state"] == "released"
    client.region_capture = original
    clock[0] += 10
    assert captures.close()["cleanup_verified"]


def test_pending_capture_cleanup_uses_shared_bounded_owner_budget():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    original = client.region_capture
    requests = []
    for index in range(3):
        request = deepcopy(controller.region_capture_requests()[0])
        request["lease_id"] = str(index) * 32
        request["task_id"] = index + 1
        requests.append(request)
    captures.close()
    captures.replace(requests)
    captures.poll()
    assert len(captures._owned) == 3
    client.region_capture = lambda method, identifier, body=None: (
        {"status": "unavailable"} if method == "DELETE" else original(method, identifier, body))
    fresh = deepcopy(requests[0])
    fresh["lease_id"] = "d" * 32
    captures.replace([fresh])
    result = captures.poll()[0]
    assert result["response"]["reason"] == "region_capture_owner_budget_busy"
    assert len(captures._owned) == 3
    assert not any(call[1] == fresh["lease_id"] for call in client.calls)


def test_quiet_mode_consumes_completed_capture_without_backlog():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    radio.quiet()
    finish_backend(controller, value, store)
    controller.feed(value, now=10, region_captures=captures.poll())
    assert not controller.tasks.active() and not radio.pending
    assert any(event["event"] == "radio_region_report" for event in events)


def test_long_uptime_clock_is_not_confused_with_bounded_observation_age():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    finish_backend(controller, value, store)
    results = captures.poll()
    results[0]["received_at"] = 100_000
    controller.feed(value, now=100_000, region_captures=results)
    assert any(event["event"] == "radio_region_capture_consumed" for event in events)


def test_worker_owns_network_calls_and_releases_before_closing_client():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    finished = threading.Event()
    stop = threading.Event()
    inbox = queue.Queue(maxsize=1)
    calls = []
    original = client.region_capture
    def request(method, lease_id, body=None):
        calls.append((method, threading.get_ident()))
        return original(method, lease_id, body)
    def fetch():
        stop.set()
        return value
    client.region_capture = request
    client.fetch = fetch
    client.close = lambda: (calls.append(("CLOSE", threading.get_ident())), finished.set())
    worker = threading.Thread(target=_poll, args=(client, inbox, stop, 0.1, captures))
    worker.start()
    worker.join(2)
    assert not worker.is_alive() and finished.is_set()
    assert calls[-2][0] == "DELETE" and calls[-1][0] == "CLOSE"
    assert all(identity != threading.get_ident() for method, identity in calls)
    assert len(inbox.get_nowait()) == 3


@pytest.mark.parametrize("lease_id", [None, True, "../capture"])
def test_invalid_lease_ids_do_not_send_network_requests(tmp_path, lease_id):
    client = RadioClient("http://localhost", token_file(tmp_path),
                         transport=httpx.MockTransport(lambda request: pytest.fail("unexpected network request")))
    try:
        assert client.region_capture("GET", lease_id)["status"] == "unavailable"
    finally:
        client.close()


def test_lost_read_response_can_recover_without_rearming_or_fallback():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    finish_backend(controller, value, store)
    original = client.region_capture
    reads = []
    def respond(method, lease_id, body=None):
        if method == "GET":
            reads.append(lease_id)
            if len(reads) == 1:
                return {"status": "unavailable", "reason": "response_lost"}
        return original(method, lease_id, body)
    client.region_capture = respond
    assert captures.poll()[0]["response"]["status"] == "unavailable"
    results = captures.poll()
    controller.feed(value, now=10, region_captures=results)
    assert any(event["event"] == "radio_region_capture_consumed" for event in events)
    assert len([call for call in client.calls if call[0] == "POST"]) == 1


def test_failed_release_is_reported_and_backend_ttl_clears_retained_data():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    lease_id = controller.region_capture_requests()[0]["lease_id"]
    original = client.region_capture
    client.region_capture = lambda method, lease, body=None: (
        {"status": "unavailable", "reason": "network_unavailable"} if method == "DELETE"
        else original(method, lease, body))
    captures.close()
    assert events[-1]["cleanup_verified"] is False
    assert next(event for event in reversed(events)
                if event["event"] == "radio_region_capture_released")["status"] == "unavailable"
    clock[0] = 41
    assert store.get(lease_id, value)["state"] == "expired"
    assert not store._tracker.records
