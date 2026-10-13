from copy import deepcopy
from dataclasses import replace

import pytest

from f1_engineer.analysis.resampling import TraceSample
from f1_engineer.radio.questions import QuestionPlan
from f1_engineer.radio.regions import RegionTracker, RegionWindow
from f1_engineer.radio.tasks import LapReportRegistry, parse_task_request
from tests.test_radio_questions import live


REQUEST = "track my braking and minimum speed from 10 to 50 metres for next lap"


def ready():
    controller, radio, events, value = live()
    value.update(frame_identifier=1, frame_ordinal=1, session_time_s=100.0)
    value["context"] = {"status": "available", "value": {"session_uid": 99, "packet_format": 2025,
                                                               "track_length_m": 100.0}}
    player = value["drivers"][0]
    player["lap"].update(current_lap_invalid_id=0, pit_status_id=0, result_status_id=2,
                          lap_distance_m=0.0, current_lap_time_ms=0)
    player["tyre"] = {"status": "available", "value": {"actual_compound": 16}}
    controller.feed(value, now=10)
    controller.handle_request(REQUEST, now=10)
    radio.cancel()
    return controller, radio, events, value


def push(controller, value, *, lap=4, distance=0.0, clock=0, speed=180, brake=0.0,
         step=0.05, mutate=None):
    player = value["drivers"][0]
    previous_number = player["lap"]["current_lap_number"]
    value["frame_identifier"] += 1
    value["frame_ordinal"] += 1
    value["session_time_s"] += step
    value["received_monotonic_ns"] += int(step * 1e9)
    player["lap"].update(current_lap_number=lap, lap_distance_m=distance, current_lap_time_ms=clock)
    value["events"] = []
    if lap == previous_number + 1:
        player["lap"]["last_lap_time_ms"] = 1000
        value["event_cursor"] += 1
        value["events"] = [{"id": value["event_cursor"], "kind": "lap_completed",
            "scope": deepcopy(value["scope"]), "driver": deepcopy(player["identity"]),
            "qualification": "observed_lap_transition", "lap_number": previous_number,
            "lap_time_ms": 1000, "previous_observed_invalid": 0,
            "received_monotonic_ns": value["received_monotonic_ns"]}]
    player["observation"] = {"status": "available", "identity": deepcopy(player["identity"]),
        "value": {"lap_number": lap, "lap_distance_m": distance, "current_lap_time_ms": clock,
                  "speed_kph": speed, "brake": brake},
        "source": {**value["scope"], "frame_identifier": value["frame_identifier"],
            "frame_ordinal": value["frame_ordinal"], "session_time_s": value["session_time_s"],
            "lap_received_monotonic_ns": value["received_monotonic_ns"],
            "telemetry_received_monotonic_ns": value["received_monotonic_ns"], "telemetry_captured_at_ns": 1}}
    if mutate is not None:
        mutate(value)
    controller.feed(value, now=10 + value["frame_ordinal"] * 0.05)


def collect(controller, radio, value, *, step=0.05, mutate=None):
    for index in range(13):
        push(controller, value, distance=float(index * 5), clock=index * 50, speed=180 - index,
             brake=0.5 if 4 <= index <= 8 else 0.0, step=step,
             mutate=mutate if index == 6 else None)
    radio.cancel()


def complete(controller, radio, value):
    push(controller, value, lap=5)
    return next(event for event in reversed(controller_events(controller)) if event["event"] == "radio_region_report")


def controller_events(controller):
    return controller.notify.__self__


@pytest.mark.parametrize("request_text,laps,bounds", [
    (REQUEST, 1, (10, 50)),
    (REQUEST.replace("metres", "meters").replace("next lap", "the next 3 laps"), 3, (10, 50)),
    (REQUEST.replace("10 to 50", "10.5 to 50.25"), 1, (10.5, 50.25)),
])
def test_numeric_region_intents_are_bounded(request_text, laps, bounds):
    action = parse_task_request(request_text)
    assert action.laps == laps and action.region_bounds_m == bounds


def test_next_lap_schedule_excludes_current_partial_lap_and_reports_once():
    controller, radio, events, value = ready()
    task = controller.tasks.active()[0]
    assert (task.first_lap, task.until_lap) == (4, 4)
    assert not controller.regions.records
    collect(controller, radio, value)
    report = complete(controller, radio, value)
    assert "Sampled minimum 171.0" in report["text"]
    assert "between 15 and 20 metres" in report["text"]
    assert "0.400 seconds" in report["text"]
    assert report["evidence"]["actual_compound"] == 16
    assert not report["evidence"]["measurement"]["coaching_eligible"]
    assert not controller.tasks.active()
    assert len(radio.pending) == 1
    controller.feed(value, now=11)
    assert sum(event["event"] == "radio_region_report" for event in events) == 1


def test_sparse_polling_reports_partial_minimum_but_no_onset_or_region_time():
    controller, radio, events, value = ready()
    collect(controller, radio, value, step=0.25)
    report = complete(controller, radio, value)
    assert "partial coverage" in report["text"]
    assert "Brake onset unavailable" in report["text"]
    assert "Region time unavailable" in report["text"]


@pytest.mark.parametrize("field,value", [("current_lap_invalid_id", 1), ("pit_status_id", 1),
                                          ("result_status_id", 3), ("current_lap_invalid_id", None)])
def test_invalid_or_pit_observations_poison_the_lap(field, value):
    controller, radio, events, snapshot = ready()
    collect(controller, radio, snapshot,
            mutate=lambda data: data["drivers"][0]["lap"].update({field: value}))
    report = complete(controller, radio, snapshot)
    assert report["evidence"] is None and "unavailable" in report["text"]


@pytest.mark.parametrize("section,field,value", [
    ("source", "epoch", 9), ("source", "frame_identifier", True),
    ("source", "telemetry_received_monotonic_ns", 0),
    ("value", "lap_number", 9), ("value", "speed_kph", True), ("value", "brake", float("nan")),
])
def test_bad_alignment_and_channels_cannot_be_filled_across(section, field, value):
    controller, radio, events, snapshot = ready()
    collect(controller, radio, snapshot,
            mutate=lambda data: data["drivers"][0]["observation"][section].update({field: value}))
    assert complete(controller, radio, snapshot)["evidence"] is None


@pytest.mark.parametrize("kind", ["compound", "length", "context_scope", "distance", "clock"])
def test_scope_conditions_and_regressions_cannot_produce_results(kind):
    controller, radio, events, value = ready()
    def corrupt(data):
        player = data["drivers"][0]
        if kind == "compound":
            player["tyre"]["value"]["actual_compound"] = 17
        elif kind == "length":
            data["context"]["value"]["track_length_m"] = 99
        elif kind == "context_scope":
            data["context"]["value"]["session_uid"] = 98
        else:
            field = "lap_distance_m" if kind == "distance" else "current_lap_time_ms"
            player["lap"][field] = player["observation"]["value"][field] = 0
    collect(controller, radio, value, mutate=corrupt)
    assert complete(controller, radio, value)["evidence"] is None


def test_cancellation_removes_owned_samples_and_queued_speech():
    controller, radio, events, value = ready()
    collect(controller, radio, value)
    assert controller.regions.records
    controller.handle_request("cancel task 1", now=10.7)
    assert not controller.regions.records
    radio.cancel()
    push(controller, value, lap=5)
    assert not any(event["event"] == "radio_region_report" for event in events)


def test_updates_restart_on_next_lap_without_old_samples():
    controller, radio, events, value = ready()
    collect(controller, radio, value)
    controller.handle_request("update task 1 " + REQUEST, now=10.7)
    task = controller.tasks.active()[0]
    assert task.version == 2 and task.first_lap == 5
    assert not controller.regions.records


@pytest.mark.parametrize("request_text", [
    REQUEST.replace("10 to 50", "50 to 10"), REQUEST.replace("10 to 50", "10 to 101"),
    REQUEST.replace("next lap", "next 21 laps"), REQUEST.replace("my braking", "Leclerc braking"),
    REQUEST + " and quit",
])
def test_unsupported_bounds_duration_and_opponent_requests_create_no_task(request_text):
    controller, radio, events, value = ready()
    controller.handle_request("cancel all tracking", now=10)
    controller.handle_request(request_text, now=10)
    assert not controller.tasks.active()


def test_unknown_track_length_prevents_installation():
    controller, radio, events, value = live()
    controller.handle_request(REQUEST, now=10)
    assert not controller.tasks.active() and "track length" in radio.pending[-1].text


@pytest.mark.parametrize("reason", ["scope", "history", "unavailable", "stale"])
def test_lifecycle_failures_clear_tracking_and_samples(reason):
    controller, radio, events, value = ready()
    collect(controller, radio, value)
    if reason == "scope":
        value["scope"]["epoch"] += 1
    elif reason == "history":
        value["event_cursor"] = value["event_floor"] = 10
    elif reason == "unavailable":
        value["status"] = "unavailable"
    else:
        value["age_s"] = 3
    controller.feed(value, now=11)
    assert not controller.tasks.active() and not controller.regions.records


def test_sample_memory_is_bounded_and_overflow_abstains():
    registry = LapReportRegistry()
    task = registry.install(QuestionPlan(("self",), ("regional_observations",)), (None,),
                            cursor=0, lap=4, laps=1, region_window=RegionWindow(10, 50, 100))
    tracker = RegionTracker()
    player = {"lap": {"current_lap_number": 4, "current_lap_invalid_id": 0,
                       "pit_status_id": 0, "result_status_id": 2},
              "tyre": {"status": "available", "value": {"actual_compound": 16}}}
    sample = TraceSample(0, 25, 0, 50, None, 0, None, None, None, session_time_s=0)
    for index in range(4097):
        tracker.observe((task,), player, replace(sample, frame_identifier=index,
                        time_s=index * 0.001, session_time_s=index * 0.001), track_length_m=100)
    record = next(iter(tracker.records.values()))
    assert record["invalid"] and not record["samples"]


def test_task_listing_describes_window_and_future_schedule():
    controller, radio, events, value = ready()
    controller.handle_request("list tasks", now=10)
    assert "numeric region 10 to 50 metres" in radio.pending[-1].text
    assert "through lap 4" in radio.pending[-1].text


def test_unknown_completion_validity_cannot_publish_measurements():
    controller, radio, events, value = ready()
    collect(controller, radio, value)
    push(controller, value, lap=5,
         mutate=lambda data: data["events"][0].update(previous_observed_invalid=None))
    report = next(event for event in reversed(events) if event["event"] == "radio_region_report")
    assert report["evidence"] is None


def test_conflicting_duplicate_telemetry_poisoned_instead_of_double_counted():
    controller, radio, events, value = ready()
    for index in range(7):
        push(controller, value, distance=float(index * 5), clock=index * 50)
    value["drivers"][0]["observation"]["value"]["brake"] = 1.0
    controller.feed(value, now=10.5)
    for index in range(7, 13):
        push(controller, value, distance=float(index * 5), clock=index * 50)
    assert complete(controller, radio, value)["evidence"] is None


def test_identical_duplicate_does_not_create_an_extra_observation():
    controller, radio, events, value = ready()
    push(controller, value, distance=10.0, clock=100)
    count = len(next(iter(controller.regions.records.values()))["samples"])
    controller.feed(value, now=10.2)
    assert len(next(iter(controller.regions.records.values()))["samples"]) == count


@pytest.mark.parametrize("mode", ["quiet", "listening"])
def test_suppressed_speech_still_consumes_finite_tracking_without_backlog(mode):
    controller, radio, events, value = ready()
    collect(controller, radio, value)
    if mode == "quiet":
        radio.quiet()
    else:
        radio.suspended = True
    complete(controller, radio, value)
    assert not controller.tasks.active() and not controller.regions.records
    assert not radio.pending


def test_old_report_expires_against_lap_completion_not_early_region_samples():
    controller, radio, events, value = ready()
    collect(controller, radio, value)
    report = complete(controller, radio, value)
    assert report["binding"] == radio.scope
    assert report["player"] == value["drivers"][0]["identity"]
    assert report["evidence"]["first_frame_identifier"] < report["evidence"]["last_frame_identifier"]
    assert radio.pending[-1].owner == "lap_reports"
    radio.tick(now=20)
    assert not radio.pending and radio.active is None


def test_frame_serial_wrap_is_supported_without_reordering():
    controller, radio, events, value = ready()
    value["frame_identifier"] = 2**32 - 5
    for index in range(13):
        if value["frame_identifier"] == 2**32 - 1:
            value["frame_identifier"] = -1
        push(controller, value, distance=float(index * 5), clock=index * 50)
    assert complete(controller, radio, value)["evidence"] is not None


def test_frame_rewind_poisoned_without_changing_lap_number():
    controller, radio, events, value = ready()
    def rewind(data):
        data["frame_identifier"] = data["drivers"][0]["observation"]["source"]["frame_identifier"] = 1
    collect(controller, radio, value, mutate=rewind)
    assert complete(controller, radio, value)["evidence"] is None


@pytest.mark.parametrize("distance", [None, -1.0, True])
def test_missing_or_invalid_distance_inside_collection_cannot_conceal_a_gap(distance):
    controller, radio, events, value = ready()
    def corrupt(data):
        player = data["drivers"][0]
        player["lap"]["lap_distance_m"] = player["observation"]["value"]["lap_distance_m"] = distance
    collect(controller, radio, value, mutate=corrupt)
    assert complete(controller, radio, value)["evidence"] is None


def test_missing_lap_event_expires_and_releases_owned_buffers():
    controller, radio, events, value = ready()
    collect(controller, radio, value)
    push(controller, value, lap=5, mutate=lambda data: data.update(events=[]))
    assert not controller.tasks.active() and not controller.regions.records
    assert not any(event["event"] == "radio_region_report" for event in events)


def test_region_and_regular_lap_reports_share_one_owned_utterance():
    controller, radio, events, value = ready()
    controller.handle_request("report at end of each lap my last lap", now=10)
    radio.cancel()
    collect(controller, radio, value)
    complete(controller, radio, value)
    assert len(radio.pending) == 1
    assert "Sampled minimum" in radio.pending[-1].text and "Task 2: You: lap 4" in radio.pending[-1].text


@pytest.mark.parametrize("targets,metrics,window,laps,lap", [
    (("leader",), ("regional_observations",), RegionWindow(10, 50, 100), 1, 4),
    (("self",), ("regional_observations", "last_lap"), RegionWindow(10, 50, 100), 1, 4),
    (("self",), ("regional_observations",), None, 1, 4),
    (("self",), ("regional_observations",), RegionWindow(10, 50, 100), None, 4),
    (("self",), ("last_lap",), RegionWindow(10, 50, 100), 1, 4),
    (("self",), ("regional_observations",), RegionWindow(10, 50, 100), 2, 255),
])
def test_registry_rejects_incompatible_or_unbounded_regional_tasks(targets, metrics, window, laps, lap):
    with pytest.raises(ValueError):
        LapReportRegistry().install(QuestionPlan(targets, metrics), (None,), cursor=0,
                                    lap=lap, laps=laps, region_window=window)
