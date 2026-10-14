from copy import deepcopy
import queue
import threading

import pytest

from f1_engineer.radio.command import _poll
from f1_engineer.radio.tasks import parse_task_request
from tests.test_radio_opponent_captures import ready, activate, drive


REQUEST = "compare my braking and minimum speed from 10 to 50 metres with Leclerc for next lap"


def connected(*, laps=1):
    manager, client, request, controller, radio, value = ready()
    controller.handle_request("cancel all tracking", now=10)
    controller._backend_regions = True
    for row, name in zip(value["drivers"], ("Player One", "Charles Leclerc")):
        row["participant"] = {"status": "available", "identity": deepcopy(row["identity"]),
                              "value": {"name": name, "driver_id": row["car_index"], "team_id": 2, "race_number": 16}}
    controller.feed(value, now=10)
    client.clock[0] = 10
    controller.handle_request(REQUEST.replace("next lap", f"next {laps} laps"), now=10)
    request, = controller.opponent_capture_requests()
    manager.replace([request])
    radio.cancel()
    return manager, client, request, controller, radio, value


def armed(*, laps=1):
    manager, client, request, controller, radio, value = connected(laps=laps)
    owner_id = activate(manager, client, value)
    assert manager.poll(value)[0]["state"] == "collecting"
    return manager, client, request, controller, radio, value, owner_id


def publish(manager, client, controller, value):
    now = 10 + value["frame_ordinal"] * 0.05
    client.clock[0] = now
    results = manager.poll(value)
    controller.feed(value, now=now, region_captures=results)
    return results, now


def collect(client, controller, value, owner_id, *, player_lap=4, opponent_lap=3):
    for index in range(13):
        drive(client, controller, value, owner_id, index=index, player_number=player_lap, number=opponent_lap)


def reports(controller):
    return [event for event in controller.notify.__self__ if event["event"] == "radio_opponent_region_report"]


@pytest.mark.parametrize("text,opponent,bounds,laps,task_id", [
    (REQUEST, "leclerc", (10, 50), 1, None),
    ("compare my braking and minimum speed from 10.25 to 50.50 meters with Charles Leclerc for the next 3 laps",
     "charles leclerc", (10.25, 50.5), 3, None),
    ("track Leclerc's braking points and minimum speed from 10 to 50 metres in comparison to me for next lap",
     "leclerc", (10, 50), 1, None),
    ("keep track of O’Ward’s braking and minimum speed from 10 to 50 meters compared to me for the next 20 laps",
     "o'ward", (10, 50), 20, None),
    ("update task 2 " + REQUEST, "leclerc", (10, 50), 1, 2),
])
def test_explicit_paired_request_forms(text, opponent, bounds, laps, task_id):
    action = parse_task_request(text)
    assert action.region_opponent == opponent and action.region_bounds_m == bounds
    assert action.laps == laps and action.task_id == task_id
    assert action.kind == ("update" if task_id else "create")


@pytest.mark.parametrize("text", [
    "keep track of braking points and minimum speed for Leclerc in comparison to me for the next lap",
    "track Leclerc's braking points and minimum speed compared to me for next lap",
])
def test_unknown_corner_geometry_requires_clarification(text):
    with pytest.raises(ValueError, match="explicit distance window"):
        parse_task_request(text)


@pytest.mark.parametrize("opponent", ["me", "myself", "my car", "leclerc and norris", "p2", "<leclerc>"])
def test_multiple_or_non_named_opponents_do_not_install(opponent):
    manager, client, request, controller, radio, value = connected()
    controller.handle_request("cancel all tracking", now=10)
    controller.handle_request(REQUEST.replace("Leclerc", opponent), now=10)
    assert controller.opponent_capture_requests() == []


def test_controller_installs_fixed_independent_windows_and_no_player_only_duplicate():
    manager, client, request, controller, radio, value = connected(laps=3)
    assert request["body"]["first_lap"] == 4 and request["opponent_first_lap"] == 3
    assert request["body"]["laps"] == 3 and request["body"]["window_m"] == [10, 50]
    assert request["body"]["player"] == value["drivers"][0]["identity"]
    assert request["opponent"] == value["drivers"][1]["identity"]
    assert controller.region_capture_requests() == []
    assert "admission pending" in controller.conversation.context()[-1]["response"]
    request["opponent"]["tenure_ordinal"] = 9
    assert controller.opponent_capture_requests()[0]["opponent"]["tenure_ordinal"] == 1


@pytest.mark.parametrize("fault", ["backend", "name", "lap", "self", "geometry"])
def test_creation_fails_closed_when_required_facts_are_missing(fault):
    manager, client, request, controller, radio, value = connected()
    controller.handle_request("cancel all tracking", now=10)
    text = REQUEST
    if fault == "backend":
        controller._backend_regions = False
    elif fault == "name":
        value["drivers"][1]["participant"]["status"] = "unavailable"
    elif fault == "lap":
        value["drivers"][1]["lap"]["current_lap_number"] = None
    elif fault == "self":
        text = REQUEST.replace("Leclerc", "Player One")
    else:
        value["context"]["status"] = "unavailable"
    controller.feed(value, now=10)
    controller.handle_request(text, now=10)
    assert controller.opponent_capture_requests() == []


@pytest.mark.parametrize("first", ["player", "opponent"])
def test_both_independent_crossing_orders_report_once_with_real_evidence(first):
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    options = {"player_number": 5} if first == "player" else {"number": 4}
    drive(client, controller, value, owner_id, index=12, **options)
    publish(manager, client, controller, value)
    assert reports(controller) == [] and controller.opponent_capture_requests()
    drive(client, controller, value, owner_id, player_number=5, number=4)
    results, now = publish(manager, client, controller, value)
    report, = reports(controller)
    assert report["evidence"] is not None and "Your lap 4 versus Charles Leclerc's lap 3" in report["text"]
    assert "171.0 versus 151.0" in report["text"] and "not coaching" in report["text"]
    assert controller.tasks.views()[0]["state"] == "completed"
    assert controller.opponent_capture_requests() == []
    assert client.queue.get(owner_id).state == "active"
    controller.feed(value, now=now, region_captures=results)
    assert len(reports(controller)) == 1
    turn = controller.conversation.context()[-1]
    assert turn["kind"] == "task_result" and turn["tasks"][0]["state"] == "completed"
    assert turn["response"] == report["text"]
    manager.replace(controller.opponent_capture_requests())
    assert manager.poll(value) == []
    client.apply()
    assert manager.close()["cleanup_verified"]


def test_longer_tracking_consumes_sequential_pairs_without_player_expiration():
    manager, client, request, controller, radio, value, owner_id = armed(laps=2)
    for player_lap, opponent_lap in ((4, 3), (5, 4)):
        collect(client, controller, value, owner_id, player_lap=player_lap, opponent_lap=opponent_lap)
        drive(client, controller, value, owner_id, player_number=player_lap + 1, number=opponent_lap + 1)
        results, now = publish(manager, client, controller, value)
        controller.feed(value, now=now, region_captures=results)
        assert len(reports(controller)) == player_lap - 3
    assert [report["lap_number"] for report in reports(controller)] == [4, 5]
    assert controller.tasks.views()[0]["state"] == "completed"


@pytest.mark.parametrize("control", ["cancel", "cancel_all", "update", "quiet", "stale", "scope", "discontinuity"])
def test_control_changes_do_not_replay_or_revive_pair_reports(control):
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    now = 10 + value["frame_ordinal"] * 0.05
    client.clock[0] = now
    results = manager.poll(value)
    if control == "quiet":
        radio.quiet()
    elif control == "cancel":
        controller.handle_request(f"cancel task {request['task_id']}", now=now)
    elif control == "cancel_all":
        controller.handle_request("cancel all tracking", now=now)
    elif control == "update":
        controller.handle_request(f"update task {request['task_id']} " + REQUEST, now=now)
    elif control == "stale":
        value["age_s"] = 3
    elif control == "scope":
        value["scope"]["epoch"] += 1
    else:
        value["event_cursor"] += 1
        value["events"] = [{"id": value["event_cursor"], "kind": "driver_discontinuity",
                            "scope": deepcopy(value["scope"]), "driver": deepcopy(value["drivers"][0]["identity"])}]
    controller.feed(value, now=now, region_captures=results)
    if control == "quiet":
        assert len(reports(controller)) == 1 and controller.tasks.views()[0]["state"] == "completed"
        radio.muted = False
        controller.feed(value, now=now, region_captures=results)
    else:
        assert reports(controller) == []
    assert not any(message.owner == "opponent_regions" for message in radio.pending)


@pytest.mark.parametrize("control", ["cancel", "cancel_all", "scope", "stale", "opponent_discontinuity"])
def test_queued_and_active_pair_audio_is_cancelled_when_authority_ends(control):
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    results, now = publish(manager, client, controller, value)
    radio.pending = [message for message in radio.pending if message.owner == "opponent_regions"]
    radio.tick(now=now)
    assert radio.active is not None and radio.active.owner == "opponent_regions"
    if control == "cancel":
        controller.handle_request(f"cancel task {request['task_id']}", now=now)
    elif control == "cancel_all":
        controller.handle_request("cancel all tracking", now=now)
    elif control == "scope":
        value["scope"]["epoch"] += 1
    elif control == "stale":
        value["age_s"] = 3
    else:
        value["event_cursor"] += 1
        value["events"] = [{"id": value["event_cursor"], "kind": "driver_discontinuity",
                            "scope": deepcopy(value["scope"]), "driver": deepcopy(value["drivers"][1]["identity"])}]
    controller.feed(value, now=now, region_captures=results)
    assert radio.active is None or radio.active.owner != "opponent_regions"


def test_missed_admission_ends_once_without_shifting_lap_windows():
    manager, client, request, controller, radio, value = connected()
    drive(client, controller, value, "not-active", player_number=4)
    results, now = publish(manager, client, controller, value)
    report, = reports(controller)
    assert report["evidence"] is None and "unavailable" in report["text"]
    assert controller.opponent_capture_requests() == []
    controller.feed(value, now=now, region_captures=results)
    assert len(reports(controller)) == 1


def test_replaced_opponent_is_not_rebound_to_same_name():
    manager, client, request, controller, radio, value, owner_id = armed()
    value["drivers"][1]["identity"]["tenure_ordinal"] += 1
    value["drivers"][1]["participant"]["identity"] = deepcopy(value["drivers"][1]["identity"])
    results, now = publish(manager, client, controller, value)
    assert len(reports(controller)) == 1 and reports(controller)[0]["evidence"] is None
    assert controller.opponent_capture_requests() == []


@pytest.mark.parametrize("fault", ["version", "request", "duplicate", "stale", "future", "malformed"])
def test_untrusted_or_old_pair_views_cannot_publish_measurements(fault):
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    now = 10 + value["frame_ordinal"] * 0.05
    client.clock[0] = now
    results = manager.poll(value)
    if fault == "version":
        results[0]["version"] += 1
    elif fault == "request":
        results[0]["body"]["window_m"] = [20, 50]
    elif fault == "duplicate":
        results.append(deepcopy(results[0]))
    elif fault == "stale":
        results[0]["frame_age_s"] = 3
    elif fault == "future":
        results[0]["received_at"] = now + 1
    else:
        results[0]["legs"] = [None, {}]
    controller.feed(value, now=now, region_captures=results)
    assert reports(controller) == []
    assert controller.tasks.views()[0]["reported_through_lap"] == 3


def test_incomplete_numeric_evidence_reports_unavailable_and_consumes_finished_pair():
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    now = 10 + value["frame_ordinal"] * 0.05
    client.clock[0] = now
    results = manager.poll(value)
    results[0]["legs"][1]["response"]["data"]["results"][0]["evidence"] = None
    controller.feed(value, now=now, region_captures=results)
    report, = reports(controller)
    assert report["evidence"] is None and "unavailable" in report["text"]
    assert controller.tasks.views()[0]["state"] == "completed"


def test_opponent_discontinuity_fences_an_unconsumed_complete_pair():
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    now = 10 + value["frame_ordinal"] * 0.05
    client.clock[0] = now
    results = manager.poll(value)
    value["event_cursor"] += 1
    value["events"] = [{"id": value["event_cursor"], "kind": "driver_discontinuity",
                        "scope": deepcopy(value["scope"]), "driver": deepcopy(value["drivers"][1]["identity"])}]
    controller.feed(value, now=now, region_captures=results)
    report, = reports(controller)
    assert report["evidence"] is None and "discontinuity" in report["text"]
    assert controller.opponent_capture_requests() == []
    controller.feed(value, now=now, region_captures=results)
    assert len(reports(controller)) == 1


def test_completed_report_audio_cannot_continue_after_same_name_replacement():
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    results, now = publish(manager, client, controller, value)
    radio.pending = [message for message in radio.pending if message.owner == "opponent_regions"]
    radio.tick(now=now)
    assert radio.active is not None and radio.active.owner == "opponent_regions"
    value["drivers"][1]["identity"]["tenure_ordinal"] += 1
    value["drivers"][1]["participant"]["identity"] = deepcopy(value["drivers"][1]["identity"])
    controller.feed(value, now=now, region_captures=results)
    assert radio.active is None and not any(message.owner == "opponent_regions" for message in radio.pending)
    assert len(reports(controller)) == 1


@pytest.mark.parametrize("control", ["cancel", "update"])
def test_inflight_pair_read_is_discarded_when_the_controller_changes_request(control):
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    now = 10 + value["frame_ordinal"] * 0.05
    client.clock[0] = now
    def interrupt(method):
        if method != "region_GET":
            return
        client.hook = lambda method: None
        question = (f"cancel task {request['task_id']}" if control == "cancel"
                    else f"update task {request['task_id']} " + REQUEST)
        controller.handle_request(question, now=now)
        manager.replace(controller.opponent_capture_requests())
    client.hook = interrupt
    results = manager.poll(value)
    assert results == []
    controller.feed(value, now=now, region_captures=results)
    assert reports(controller) == []
    assert any(method == "region_DELETE" for method, identifier, body in client.calls)


def test_dispatch_deadline_includes_elapsed_delivery_time_but_not_old_completion_age():
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    now = 10 + value["frame_ordinal"] * 0.05
    client.clock[0] = now
    results = manager.poll(value)
    results[0]["legs"][0]["response"]["data"]["results"][0]["completion_age_s"] = 40
    controller.feed(value, now=now + 0.75, region_captures=results)
    report, = reports(controller)
    assert report["evidence"] is not None and report["observation_age_s"] == pytest.approx(0.75)
    message, = [message for message in radio.pending if message.owner == "opponent_regions"]
    assert message.dispatch_before == pytest.approx(now + 3)


def test_pair_latency_reports_interval_uncertainty_and_links_scheduler_queue_wait():
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    now = 10 + value["frame_ordinal"] * 0.05
    client.clock[0] = now
    results = manager.poll(value)
    results[0]["request_age_s"] = 0.7
    for leg, completion_age, elapsed, request_age in zip(results[0]["legs"], (40, 1), (0.3, 0.1), (0.4, 0.2)):
        leg["received_at"] = now - elapsed
        leg["request_age_s"] = request_age
        leg["response"]["data"]["results"][0]["completion_age_s"] = completion_age
    controller.feed(value, now=now + 0.5, region_captures=results)
    report, = reports(controller)
    assert report["latency"]["player_completion_to_queue_interval_s"] == pytest.approx([40.8, 41.2])
    assert report["latency"]["opponent_completion_to_queue_interval_s"] == pytest.approx([1.6, 1.8])
    assert report["latency"]["paired_ready_to_queue_interval_s"] == pytest.approx([1.6, 1.8])
    assert report["latency"]["pair_poll_s"] == pytest.approx(0.7)
    assert report["observation_age_s"] == pytest.approx(1.2)
    radio.pending = [message for message in radio.pending if message.owner == "opponent_regions"]
    radio.tick(now=now + 0.7)
    started = next(event for event in reversed(controller.notify.__self__) if event["event"] == "radio_started")
    assert started["key"] == report["message_key"] and started["queue_wait_s"] == pytest.approx(0.2)


@pytest.mark.parametrize("ages", [(0, 12), (12, 0)])
def test_pair_ready_latency_uses_the_later_independent_completion(ages):
    manager, client, request, controller, radio, value, owner_id = armed()
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    now = 10 + value["frame_ordinal"] * 0.05
    client.clock[0] = now
    results = manager.poll(value)
    for leg, age in zip(results[0]["legs"], ages):
        leg["response"]["data"]["results"][0]["completion_age_s"] = age
    controller.feed(value, now=now + 0.2, region_captures=results)
    report, = reports(controller)
    assert report["latency"]["paired_ready_to_queue_interval_s"] == pytest.approx([0.2, 0.2])
    assert report["evidence"] is not None


def test_unavailable_report_has_no_fabricated_completion_latency():
    manager, client, request, controller, radio, value = connected()
    drive(client, controller, value, "not-active", player_number=4)
    publish(manager, client, controller, value)
    report, = reports(controller)
    assert report["latency"] is None and report["message_key"]


@pytest.mark.parametrize("question", ["track that for another two laps", "extend task {task_id} by 2 laps"])
def test_pair_extension_uses_captured_reference_and_confirms_discarded_partial_data(question):
    manager, client, request, controller, radio, value = connected(laps=2)
    context = controller.capture_task_context()
    controller.handle_request(question.format(task_id=request["task_id"]), now=10, task_context=context)
    updated, = controller.opponent_capture_requests()
    assert updated["task_id"] == request["task_id"] and updated["version"] == request["version"] + 1
    assert updated["body"]["first_lap"] == 4 and updated["opponent_first_lap"] == 3
    assert updated["body"]["laps"] == 4
    assert updated["opponent"] == request["opponent"] and updated["body"]["window_m"] == request["body"]["window_m"]
    text = controller.conversation.context()[-1]["response"]
    assert "admission pending" in text and "old pairs are discarded" in text
    controller.handle_request("extend task {task_id} by 2 laps".format(task_id=request["task_id"]),
                              now=10, task_context=context)
    assert controller.opponent_capture_requests() == [updated]
    assert "changed or ended" in controller.conversation.context()[-1]["response"]


@pytest.mark.parametrize("fault", ["replacement", "missing_lap", "backwards", "stale_context", "completed"])
def test_pair_followup_extension_fails_without_mutating_original_authority(fault):
    manager, client, request, controller, radio, value = connected(laps=2)
    context = controller.capture_task_context()
    if fault == "replacement":
        value["drivers"][1]["identity"]["tenure_ordinal"] += 1
    elif fault == "missing_lap":
        value["drivers"][1]["lap"]["current_lap_number"] = None
    elif fault == "backwards":
        value["drivers"][1]["lap"]["current_lap_number"] = 1
    elif fault == "stale_context":
        controller.handle_request(f"update task {request['task_id']} " + REQUEST, now=10)
    else:
        controller.tasks.end_region_pair(request["task_id"], request["version"], "capture_failed")
    controller.feed(value, now=10)
    before = controller.tasks.views()
    controller.handle_request("track that for another two laps", now=10, task_context=context)
    assert controller.tasks.views() == before


def test_extension_discards_inflight_old_result_then_collects_fresh_independent_pairs():
    manager, client, request, controller, radio, value, owner_id = armed(laps=2)
    collect(client, controller, value, owner_id)
    drive(client, controller, value, owner_id, player_number=5, number=4)
    now = 10 + value["frame_ordinal"] * 0.05
    client.clock[0] = now
    controller.feed(value, now=now)
    def interrupt(method):
        if method != "region_GET":
            return
        client.hook = lambda method: None
        controller.handle_request(f"extend task {request['task_id']} by 2 laps", now=now)
        manager.replace(controller.opponent_capture_requests())
    client.hook = interrupt
    assert manager.poll(value) == []
    assert reports(controller) == []
    updated, = controller.opponent_capture_requests()
    assert updated["body"]["first_lap"] == 6 and updated["opponent_first_lap"] == 5
    assert updated["body"]["laps"] == 2
    assert any(method == "region_DELETE" for method, identifier, body in client.calls)
    new_owner = activate(manager, client, value)
    assert new_owner != owner_id and client.queue.get(owner_id).state == "released"
    assert manager.poll(value)[0]["state"] == "collecting"
    collect(client, controller, value, new_owner, player_lap=6, opponent_lap=5)
    drive(client, controller, value, new_owner, player_number=7, number=6)
    results, now = publish(manager, client, controller, value)
    report, = reports(controller)
    assert report["version"] == updated["version"] and report["lap_number"] == 6
    assert report["evidence"] is not None and "Your lap 6 versus Charles Leclerc's lap 5" in report["text"]
    assert controller.tasks.active()[0].reported_through_lap == 6


def test_worker_polls_both_managers_and_closes_all_on_its_own_thread():
    stop = threading.Event()
    inbox = queue.Queue(maxsize=1)
    calls = []
    value = {"status": "available"}
    class Client:
        def fetch(self):
            stop.set()
            return value
        def close(self):
            calls.append(("client_close", threading.get_ident()))
    class Captures:
        def poll(self):
            calls.append(("player_poll", threading.get_ident()))
            return [{"player": True}]
        def close(self):
            calls.append(("player_close", threading.get_ident()))
    class Opponents:
        def poll(self, snapshot):
            assert snapshot is value
            calls.append(("opponent_poll", threading.get_ident()))
            return [{"opponent": True}]
        def close(self):
            calls.append(("opponent_close", threading.get_ident()))
    worker = threading.Thread(target=_poll, args=(Client(), inbox, stop, 0.1, Captures(), Opponents()))
    worker.start()
    worker.join(2)
    assert not worker.is_alive()
    snapshot, started, results = inbox.get_nowait()
    assert snapshot is value and results == [{"player": True}, {"opponent": True}]
    assert [name for name, identity in calls] == ["player_poll", "opponent_poll", "player_close", "opponent_close", "client_close"]
    assert all(identity != threading.get_ident() for name, identity in calls)


@pytest.mark.parametrize("fault", ["fetch", "player_close", "opponent_close"])
def test_worker_cleanup_still_closes_remaining_owners_when_one_stage_raises(fault):
    calls = []
    stop = threading.Event()
    class Client:
        def fetch(self):
            stop.set()
            if fault == "fetch":
                raise RuntimeError(fault)
            return {}
        def close(self):
            calls.append("client_close")
    class Captures:
        def __init__(self, name):
            self.name = name
        def poll(self, *arguments):
            return []
        def close(self):
            calls.append(self.name)
            if fault == self.name:
                raise RuntimeError(fault)
    with pytest.raises(RuntimeError, match=fault):
        _poll(Client(), queue.Queue(maxsize=1), stop, 0.1, Captures("player_close"), Captures("opponent_close"))
    assert calls == ["player_close", "opponent_close", "client_close"]
