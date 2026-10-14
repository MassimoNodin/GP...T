from copy import deepcopy
from dataclasses import replace
import queue
import threading

import httpx
import pytest

from f1_engineer.api.track_region_routes import _regions
from f1_engineer.radio.client import RadioClient
from f1_engineer.radio.command import _poll
from f1_engineer.radio.tasks import parse_task_request
from f1_engineer.radio.track_geometry import RadioTrackGeometry, geometry_binding, resolve_named_region
from f1_engineer.tracks.registry import load_track_model_catalog
from tests.test_radio_opponent_controller import connected, collect, publish, reports
from tests.test_radio_opponent_captures import activate, drive
from tests.test_reviewed_track_models import _model, _write
from tests.test_standalone_radio import token_file


@pytest.fixture
def reviewed(tmp_path):
    model = _model(label="Turn 1")
    model = replace(model, track_length_m=100.0, corners=(replace(
        model.corners[0], start_distance_m=10.0, end_distance_m=50.0,
        braking_search_window_m=(12.0, 25.0), throttle_pickup_window_m=(30.0, 45.0)),))
    _write(tmp_path / "reviewed", model)
    entry = load_track_model_catalog(reviewed_root=tmp_path / "reviewed").resolve_entry(model.model_id, 1)
    return _regions(entry)


def setup_named(reviewed):
    manager, client, request, controller, radio, value = connected()
    controller.handle_request("cancel all tracking", now=10)
    value["context"]["value"]["track_id"] = 0
    result = {"status": "available", "data": {"session_id": value["session_id"], "models": [deepcopy(reviewed)]}}
    catalog = {"kind": "track_geometry", "binding": geometry_binding(value), "result": result}
    controller.feed(value, now=10, region_captures=[catalog])
    return manager, client, controller, radio, value, catalog


@pytest.mark.parametrize("question,opponent,compare,laps", [
    ("track my braking and minimum speed at Turn 1 for next lap", None, False, 1),
    ("compare my braking and minimum speed at turn-1 with Leclerc for the next 3 laps", "leclerc", False, 3),
    ("compare my braking and minimum speed at turn 1 with my previous observed region for next 2 laps", None, True, 2),
])
def test_named_request_forms(question, opponent, compare, laps):
    action = parse_task_request(question)
    assert action.region_bounds_m is None and action.region_name in {"turn 1", "turn-1"}
    assert action.region_opponent == opponent and action.region_compare is compare and action.laps == laps
    assert parse_task_request("update task 2 " + question).task_id == 2


@pytest.mark.parametrize("question", [
    "track my braking and minimum speed at turn 1 with Leclerc for next lap",
    "compare my braking and minimum speed at turn 1 for next lap",
    "compare my braking and minimum speed at turn 1 with me for next lap",
    "track my braking and minimum speed at turn 1 for next 21 laps",
])
def test_bad_named_requests_do_not_drop_qualifiers(question):
    with pytest.raises(ValueError):
        parse_task_request(question)


def test_real_reviewed_catalog_resolves_only_explicit_aliases(reviewed):
    manager, client, controller, radio, value, catalog = setup_named(reviewed)
    window, source = resolve_named_region(catalog["result"], catalog["binding"], "TURN-1")
    assert window.view() == {"start_m": 10.0, "end_m": 50.0, "track_length_m": 100.0}
    assert "revision 1" in source
    for name in ("first corner", "1", "my worst corner", "turn 10", ""):
        with pytest.raises(ValueError, match="unambiguous reviewed"):
            resolve_named_region(catalog["result"], catalog["binding"], name)


@pytest.mark.parametrize("fault", [
    "draft", "approval", "scope", "fingerprint", "track", "format", "length", "session",
    "coaching", "ranking", "checksum", "model_count", "corner_count", "duplicate_model", "duplicate_corner",
    "window", "missing_region_id", "non_dict_region", "invalid_binding",
])
def test_invalid_or_ambiguous_geometry_fails_closed(reviewed, fault):
    manager, client, controller, radio, value, catalog = setup_named(reviewed)
    result, binding = catalog["result"], catalog["binding"]
    model = result["data"]["models"][0]
    if fault == "draft":
        model["origin"] = "local_draft"
    elif fault == "approval":
        model["approved_for_candidate_ranking"] = False
    elif fault == "scope":
        model["review"]["scope"] = "anything"
    elif fault == "fingerprint":
        model["review"]["model_fingerprint_version"] = "other"
    elif fault == "track":
        model["track_id"] = 1
    elif fault == "format":
        model["packet_format"] = 2026
    elif fault == "length":
        model["track_length_m"] = 101
    elif fault == "session":
        result["data"]["session_id"] = "f" * 32
    elif fault in {"coaching", "ranking"}:
        model[fault + "_eligible"] = True
    elif fault == "checksum":
        model["model_content_sha256"] = "invalid"
    elif fault == "model_count":
        result["data"]["models"] *= 17
    elif fault == "corner_count":
        model["region_count"] = 2
    elif fault == "duplicate_model":
        result["data"]["models"].append(deepcopy(model))
    elif fault == "duplicate_corner":
        model["regions"].append(deepcopy(model["regions"][0]))
        model["region_count"] = 2
    elif fault == "window":
        model["regions"][0]["end_distance_m"] = 500
    elif fault == "missing_region_id":
        del model["regions"][0]["id"]
    elif fault == "non_dict_region":
        model["regions"].append(None)
        model["region_count"] = 2
    else:
        binding = "[1,2,3,4,5]"
    with pytest.raises(ValueError):
        resolve_named_region(result, binding, "turn 1")


def test_named_pair_collects_and_reports_without_coaching_claims(reviewed):
    manager, client, controller, radio, value, catalog = setup_named(reviewed)
    controller.handle_request("compare my braking and minimum speed at turn 1 with Leclerc for next lap", now=10)
    request, = controller.opponent_capture_requests()
    assert request["body"]["window_m"] == [10.0, 50.0]
    assert "not independently verified" in controller.conversation.context()[-1]["response"]
    manager.replace([request])
    owner = activate(manager, client, value)
    manager.poll(value)
    collect(client, controller, value, owner)
    drive(client, controller, value, owner, player_number=5, number=4)
    publish(manager, client, controller, value)
    report, = reports(controller)
    assert report["evidence"] is not None
    assert "diagnostic" in report["text"].lower()


def test_named_player_task_reuses_fixed_bounds_and_failed_update_preserves_task(reviewed):
    manager, client, controller, radio, value, catalog = setup_named(reviewed)
    controller.handle_request("track my braking and minimum speed at turn 1 for next 3 laps", now=10)
    request, = controller.region_capture_requests()
    assert request["body"]["window_m"] == [10.0, 50.0] and request["body"]["laps"] == 3
    before = controller.tasks.views()
    controller.handle_request("update task 1 track my braking and minimum speed at unknown turn for next lap", now=10)
    assert controller.tasks.views() == before
    assert "Choose explicit" in controller.conversation.context()[-1]["response"]


@pytest.mark.parametrize("fault", ["scope", "context", "duplicate_result", "stale", "missing"])
def test_catalog_cannot_cross_current_scope_or_freshness(reviewed, fault):
    manager, client, controller, radio, value, catalog = setup_named(reviewed)
    results = [catalog]
    if fault == "scope":
        value["scope"]["epoch"] += 1
    elif fault == "context":
        value["context"]["value"]["track_id"] = 1
    elif fault == "duplicate_result":
        results *= 2
    elif fault == "stale":
        value["age_s"] = 3
    else:
        results = []
    controller.feed(value, now=10, region_captures=results)
    controller.handle_request("track my braking and minimum speed at turn 1 for next lap", now=10)
    assert controller.tasks.views() == []


def test_named_task_keeps_geometry_binding_on_extension_and_ends_on_context_change(reviewed):
    manager, client, controller, radio, value, catalog = setup_named(reviewed)
    controller.handle_request("track my braking and minimum speed at turn 1 for next 3 laps", now=10)
    task, = controller.tasks.active()
    assert task.region_geometry_binding == catalog["binding"]
    controller.handle_request(f"extend task {task.task_id} by 2 laps", now=10)
    updated, = controller.tasks.active()
    assert updated.version == task.version + 1 and updated.region_geometry_binding == task.region_geometry_binding
    value["context"]["value"]["track_id"] = 1
    controller.feed(value, now=10, region_captures=[catalog])
    assert controller.tasks.views() == [] and controller.region_capture_requests() == []
    assert any(event["event"] == "radio_named_region_ended" for event in controller.notify.__self__)


def test_numeric_update_removes_named_geometry_authority(reviewed):
    manager, client, controller, radio, value, catalog = setup_named(reviewed)
    controller.handle_request("track my braking and minimum speed at turn 1 for next 3 laps", now=10)
    task, = controller.tasks.active()
    controller.handle_request(f"update task {task.task_id} track my braking and minimum speed from 10 to 50 metres for next 3 laps", now=10)
    updated, = controller.tasks.active()
    assert updated.region_geometry_binding is None
    value["context"]["value"]["track_id"] = 1
    controller.feed(value, now=10, region_captures=[catalog])
    assert controller.tasks.active() == (updated,)


def test_worker_caches_scoped_catalog_and_retries_unavailable_result(reviewed):
    manager, client, controller, radio, value, catalog = setup_named(reviewed)
    calls, clock = [], [0.0]
    class Client:
        def track_models(self, session_id):
            calls.append(session_id)
            return deepcopy(catalog["result"])
    geometry = RadioTrackGeometry(Client(), clock=lambda: clock[0])
    first = geometry.poll(value)
    first[0]["result"]["data"]["models"] = []
    assert geometry.poll(value)[0]["result"]["data"]["models"]
    assert len(calls) == 1
    value["scope"]["epoch"] += 1
    geometry.poll(value)
    assert len(calls) == 2
    catalog["result"] = {"status": "unavailable", "reason": "temporary"}
    value["scope"]["epoch"] += 1
    geometry.poll(value)
    clock[0] = 29
    geometry.poll(value)
    assert len(calls) == 3
    clock[0] = 30
    geometry.poll(value)
    assert len(calls) == 4
    value["age_s"] = 3
    assert geometry.poll(value) == [] and geometry.result is None


def test_catalog_lookup_runs_on_poll_worker_thread(reviewed):
    manager, client, controller, radio, value, catalog = setup_named(reviewed)
    stop, inbox, threads = threading.Event(), queue.Queue(maxsize=1), []
    class Client:
        def fetch(self):
            stop.set()
            return value
        def track_models(self, session_id):
            threads.append(threading.get_ident())
            return catalog["result"]
        def close(self):
            threads.append(threading.get_ident())
    worker_client = Client()
    worker = threading.Thread(target=_poll, args=(worker_client, inbox, stop, 0.1, None, None,
                                                 RadioTrackGeometry(worker_client)))
    worker.start()
    worker.join(2)
    assert not worker.is_alive()
    assert inbox.get_nowait()[2][0]["kind"] == "track_geometry"
    assert len(threads) == 2 and all(identifier != threading.get_ident() for identifier in threads)


def test_catalog_client_uses_authenticated_bounded_get(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"data": {"session_id": "a" * 32, "models": []}})
    client = RadioClient("http://localhost", token_file(tmp_path), transport=httpx.MockTransport(respond))
    try:
        assert client.track_models("a" * 32)["status"] == "available"
        assert calls[0].url.path == "/api/v2/session-evidence/sessions/" + "a" * 32 + "/track-models"
        assert calls[0].headers["Authorization"] == "Bearer " + "a" * 32
        for session_id in (None, "../elsewhere", "A" * 32, "a" * 33):
            assert client.track_models(session_id)["reason"] == "track_model_request_invalid"
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("code,body", [(302, b"{}"), (401, b"{}"), (404, b"{}"),
                                       (200, b"x" * 65_537), (200, b"[]"), (200, b'{"data":[]}')],
                         ids=["redirect", "auth", "missing", "oversized", "array", "invalid_data"])
def test_catalog_client_rejects_redirects_errors_and_invalid_payloads(tmp_path, code, body):
    client = RadioClient("http://localhost", token_file(tmp_path),
                         transport=httpx.MockTransport(lambda request: httpx.Response(code, content=body)))
    try:
        assert client.track_models("a" * 32)["status"] == "unavailable"
    finally:
        client.close()
