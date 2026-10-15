from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from f1_engineer.radio.region_progress import RegionProgress, RegionReference, measured_reference
from f1_engineer.radio.tasks import LapReportRegistry, parse_task_request
from f1_engineer.radio.questions import QuestionPlan
from f1_engineer.radio.regions import RegionWindow
from tests.test_radio_region_captures import backend_ready, finish_backend
from tests.test_radio_regions import push, ready, collect, complete


COMPARE = "compare my braking and minimum speed from 10 to 50 metres with my previous observed region for next 3 laps"


def baseline():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    finish_backend(controller, value, store)
    controller.feed(value, now=10, region_captures=captures.poll())
    radio.cancel()
    report = next(event for event in events if event["event"] == "radio_region_report")
    return controller, radio, events, value, clock, store, client, captures, report["evidence"]


def collect_lap(controller, value, store, captures, *, lap, step_ms=45, speed_offset=10):
    original, original_radio, original_events, original_value = ready()
    for index in range(13):
        push(original, value, lap=lap, distance=float(index * 5), clock=index * step_ms,
             speed=180 - index + speed_offset, brake=0.5 if 5 <= index <= 9 else 0.0)
        store.observe(value)
        if index == 0:
            controller.feed(value, now=10, region_captures=captures.poll())
    push(original, value, lap=lap + 1)
    store.observe(value)
    controller.feed(value, now=10, region_captures=captures.poll())


def test_fixed_reference_backend_comparison_keeps_baseline_across_faster_laps():
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    controller.handle_request(COMPARE, now=10)
    task = controller.tasks.active()[0]
    assert task.region_reference.lap_number == 4 and task.first_lap == 6
    assert (task.until_lap, task.region_reference.actual_compound) == (8, 16)
    captures.replace(controller.region_capture_requests())
    captures.poll()
    radio.cancel()
    collect_lap(controller, value, store, captures, lap=6)
    report = next(event for event in reversed(events) if event["event"] == "radio_region_report")
    assert report["comparison"]["elapsed_delta_s"] == pytest.approx(-0.04)
    assert report["comparison"]["minimum_speed_delta_kph"] == pytest.approx(10)
    assert report["comparison"]["brake_shift_bracket_m"] == [0, 10]
    assert "direction unresolved" in report["text"] and "Diagnostic, not coaching" in report["text"]
    assert report["comparison"]["reference"]["lap_number"] == 4
    assert not report["comparison"]["ranking_eligible"]
    assert task.region_reference.lap_number == 4
    assert next(iter(controller.region_progress.references.values())).lap_number == 6
    radio.cancel()
    collect_lap(controller, value, store, captures, lap=7, step_ms=40)
    report = next(event for event in reversed(events) if event["event"] == "radio_region_report")
    assert report["comparison"]["reference"]["lap_number"] == 4
    assert report["comparison"]["elapsed_delta_s"] == pytest.approx(-0.08)
    radio.cancel()
    collect_lap(controller, value, store, captures, lap=8)
    assert not controller.tasks.active()
    captures.replace(controller.region_capture_requests())
    captures.poll()
    assert not store._tracker.records


def test_explicit_update_selects_latest_reference_and_cancels_old_speech():
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    controller.handle_request(COMPARE, now=10)
    captures.replace(controller.region_capture_requests())
    captures.poll()
    collect_lap(controller, value, store, captures, lap=6)
    controller.handle_request("update task 2 " + COMPARE, now=10)
    task = controller.tasks.active()[0]
    assert task.version == 2 and task.region_reference.lap_number == 6 and task.first_lap == 8
    assert all(message.owner != "lap_reports" for message in radio.pending)
    controller.handle_request("list tasks", now=10)
    assert "fixed observed lap 6, compound 16" in radio.pending[-1].text


@pytest.mark.parametrize("request_text", [COMPARE, COMPARE.replace("next 3 laps", "next lap"),
                                   "update task 1 " + COMPARE.replace("metres", "meters")])
def test_comparison_request_is_explicit_bounded_and_uses_numeric_window(request_text):
    action = parse_task_request(request_text)
    assert action.region_compare and action.region_bounds_m == (10, 50)
    assert action.laps in (1, 3)


def test_missing_reference_explains_need_to_collect_without_creating_task():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    controller.handle_request("cancel task 1", now=10)
    controller.handle_request(COMPARE, now=10)
    assert not controller.tasks.active()
    assert "Track this numeric region first" in radio.pending[-1].text


@pytest.mark.parametrize("change", ["compound", "window", "unknown_tyres", "pit", "invalid", "non_racing"])
def test_reference_selection_requires_matching_window_and_known_on_track_compound(change):
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    request = COMPARE
    if change == "compound":
        value["drivers"][0]["tyre"]["value"]["actual_compound"] = 17
    elif change == "window":
        request = COMPARE.replace("10 to 50", "15 to 50")
    elif change == "unknown_tyres":
        value["drivers"][0]["tyre"] = {"status": "unavailable"}
    else:
        field = {"pit": "pit_status_id", "invalid": "current_lap_invalid_id", "non_racing": "result_status_id"}[change]
        value["drivers"][0]["lap"][field] = 1
    controller.feed(value, now=10)
    controller.handle_request(request, now=10)
    assert not controller.tasks.active()


@pytest.mark.parametrize("change", ["scope", "history", "track_length", "cancel_all"])
def test_context_discontinuities_clear_bounded_references(change):
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    assert controller.region_progress.references
    if change == "scope":
        value["scope"]["epoch"] += 1
    elif change == "history":
        value["event_cursor"] = value["event_floor"] = 20
    elif change == "track_length":
        value["context"]["value"]["track_length_m"] = 110
    else:
        controller.handle_request("cancel all tracking", now=10)
    controller.feed(value, now=10)
    assert not controller.region_progress.references


@pytest.mark.parametrize("pause", ["stale", "unavailable"])
def test_pause_preserves_completed_region_reference_and_fixed_task_reference(pause):
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    controller.handle_request(COMPARE, now=10)
    task, = controller.tasks.active()
    reference = task.region_reference
    references = deepcopy(controller.region_progress.references)
    assert reference is not None and controller.region_capture_requests()
    if pause == "stale":
        controller.check_freshness(now=14)
    else:
        controller.feed({"status": "unavailable", "reason": "replay_paused"}, now=14)
    assert controller.tasks.active() == (task,)
    assert controller.region_progress.references == references
    assert controller.region_capture_requests() == []
    controller.handle_request(COMPARE, now=14)
    assert controller.tasks.active() == (task,)
    value["events"] = []
    controller.feed(value, now=15)
    assert controller.tasks.active() == (task,)
    assert controller.tasks.active()[0].region_reference is reference
    assert controller.region_progress.references == references
    assert controller.region_capture_requests()


@pytest.mark.parametrize("change", ["invalid", "unknown_invalid", "compound", "window", "track_length", "connected",
                                   "time_gap", "speed_gap", "partial_minimum", "elapsed", "sample_count", "authority",
                                   "minimum_outside", "minimum_boolean", "zero_elapsed"])
def test_partial_or_mismatched_measurements_cannot_become_references(change):
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    event = deepcopy(value["events"][0])
    evidence = deepcopy(evidence)
    measurement = evidence["measurement"]
    if change == "invalid":
        event["previous_observed_invalid"] = 1
    elif change == "unknown_invalid":
        event["previous_observed_invalid"] = None
    elif change == "compound":
        evidence["actual_compound"] = True
    elif change == "window":
        measurement["analysis_window_m"] = [11, 50]
    elif change == "track_length":
        measurement["track_length_m"] = 110
    elif change == "connected":
        measurement["source_session_time_connected"] = False
    elif change in ("time_gap", "speed_gap"):
        measurement["supported_grid_coverage"]["time_s" if change == "time_gap" else "speed_mps"] = 0.99
    elif change == "partial_minimum":
        measurement["observations"]["minimum_speed"]["status"] = "observed_minimum_partial_window"
    elif change == "elapsed":
        measurement["elapsed"]["elapsed_s"] = None
    elif change == "sample_count":
        measurement["source_sample_count"] = True
    elif change == "minimum_outside":
        measurement["observations"]["minimum_speed"]["distance_m"] = 60
    elif change == "minimum_boolean":
        measurement["observations"]["minimum_speed"]["speed_kph"] = True
    elif change == "zero_elapsed":
        measurement["elapsed"]["elapsed_s"] = 0
    else:
        measurement["coaching_eligible"] = True
    assert measured_reference(RegionWindow(10, 50, 100), event, evidence) is None


@pytest.mark.parametrize("reference_bracket,current_bracket,direction", [
    ([15, 20], [25, 30], "later"), ([25, 30], [15, 20], "earlier"),
    ([15, 20], [15, 20], "direction unresolved"), ([15, 20], None, "unavailable")])
def test_brake_displacement_is_a_bracket_not_a_fabricated_point(reference_bracket, current_bracket, direction):
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    window = RegionWindow(10, 50, 100)
    event = deepcopy(value["events"][0])
    evidence["measurement"]["observations"]["braking"]["distance_bracket_m"] = reference_bracket
    reference = measured_reference(window, event, evidence)
    event["lap_number"] = evidence["lap_number"] = 6
    evidence["measurement"]["observations"]["braking"]["distance_bracket_m"] = current_bracket
    text, result = RegionProgress().compare(reference, event, evidence)
    assert direction in text
    assert "tyre age, fuel, conditions and traffic not established" in text
    assert result["diagnostic_only"] and not result["coaching_eligible"]


def test_tyre_change_during_task_abstains_instead_of_comparing_another_compound():
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    reference = next(iter(controller.region_progress.references.values()))
    event = deepcopy(value["events"][0])
    event["lap_number"] = evidence["lap_number"] = 6
    evidence["actual_compound"] = 17
    text, comparison = controller.region_progress.compare(reference, event, evidence)
    assert comparison is None and "tyre compound differs" in text


def test_reference_is_immutable_and_serialized_views_are_detached():
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    reference = next(iter(controller.region_progress.references.values()))
    with pytest.raises(FrozenInstanceError):
        reference.lap_number = 9
    view = reference.view()
    view["brake_bracket_m"][0] = 999
    assert reference.brake_bracket_m == (15, 20)


def test_reference_memory_is_bounded_and_never_ranked_as_a_best_attempt():
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    event = deepcopy(value["events"][0])
    progress = RegionProgress()
    for index in range(10):
        evidence["actual_compound"] = index + 1
        progress.observe(RegionWindow(10, 50, 100), event, evidence)
    assert len(progress.references) == 8
    evidence["actual_compound"] = 10
    evidence["lap_number"] = event["lap_number"] = 6
    evidence["measurement"]["elapsed"]["elapsed_s"] = 0.9
    progress.observe(RegionWindow(10, 50, 100), event, evidence)
    assert list(progress.references.values())[-1].elapsed_s == 0.9
    assert list(progress.references.values())[-1].lap_number == 6


def test_registry_rejects_reference_for_wrong_window_or_future_lap():
    window = RegionWindow(10, 50, 100)
    reference = RegionReference(4, 16, window, 0.4, 170, (15, 20))
    registry = LapReportRegistry()
    for lap, candidate in [(4, window), (6, RegionWindow(15, 50, 100))]:
        with pytest.raises(ValueError, match="earlier fixed reference"):
            registry.install(QuestionPlan(("self",), ("regional_observations",)), (None,),
                             cursor=0, lap=lap, laps=1, region_window=candidate, region_reference=reference)


def test_polling_compatibility_mode_uses_the_same_fixed_reference_gates():
    controller, radio, events, value = ready()
    collect(controller, radio, value)
    complete(controller, radio, value)
    controller.handle_request(COMPARE.replace("next 3 laps", "next lap"), now=10.9)
    task = controller.tasks.active()[0]
    assert task.region_reference.lap_number == 4
    radio.cancel()
    for index in range(13):
        push(controller, value, lap=6, distance=float(index * 5), clock=index * 50,
             speed=180 - index, brake=0.5 if 4 <= index <= 8 else 0.0)
    push(controller, value, lap=7)
    report = next(event for event in reversed(events) if event["event"] == "radio_region_report")
    assert report["comparison"]["elapsed_delta_s"] == pytest.approx(0)
    assert "within 0.001 seconds" in report["text"]


def test_cancelled_comparison_cannot_emit_or_reactivate_on_later_completions():
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    controller.handle_request(COMPARE, now=10)
    captures.replace(controller.region_capture_requests())
    captures.poll()
    controller.handle_request("cancel task 2", now=10)
    captures.replace(controller.region_capture_requests())
    captures.poll()
    count = sum(event["event"] == "radio_region_report" for event in events)
    collect_lap(controller, value, store, captures, lap=6)
    assert not controller.tasks.active()
    assert sum(event["event"] == "radio_region_report" for event in events) == count


def test_unknown_brake_onset_does_not_discard_supported_time_and_minimum_comparison():
    controller, radio, events, value, clock, store, client, captures, evidence = baseline()
    event = deepcopy(value["events"][0])
    evidence["measurement"]["observations"]["braking"]["status"] = "left_censored"
    reference = measured_reference(RegionWindow(10, 50, 100), event, evidence)
    assert reference.brake_bracket_m is None
    event["lap_number"] = evidence["lap_number"] = 6
    text, comparison = RegionProgress().compare(reference, event, evidence)
    assert comparison is not None and comparison["brake_shift_bracket_m"] is None
    assert "Brake-onset shift unavailable" in text
