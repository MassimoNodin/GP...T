from copy import deepcopy

import pytest

from f1_engineer.analysis.region_context import RegionSampleContext
from f1_engineer.radio.focus_coach import compatible_focus_context, choose_focus_reference, choose_focus_loss, focus_cue
from f1_engineer.radio.region_progress import RegionReference
from f1_engineer.radio.regions import RegionWindow
from f1_engineer.radio.tasks import LapReportRegistry, parse_task_request
from f1_engineer.radio.questions import QuestionPlan
from tests.test_radio_region_captures import backend_ready, finish_backend
from tests.test_radio_region_progress import collect_lap


REQUEST = "coach my corner from 10 to 50 metres for next three laps"
CONTEXT = {"weather_id": 0, "tyre_age_laps": 2, "track_temperature_c": 30, "air_temperature_c": 20}


def reference(*, number=4, elapsed=0.4, compound=16, values=None, brake=(15, 20)):
    context = RegionSampleContext()
    for sample in range(2):
        context.observe(CONTEXT if values is None else values)
    return RegionReference(number, compound, RegionWindow(10, 50, 100), elapsed, 171, brake, context.view())


def baseline():
    controller, radio, events, value, clock, store, client, captures = backend_ready()
    value["context"]["value"].update(track_id=0, weather_id=0, track_temperature_c=30, air_temperature_c=20)
    value["drivers"][0]["tyre"]["value"]["age_laps"] = 2
    finish_backend(controller, value, store)
    controller.feed(value, now=10, region_captures=captures.poll())
    radio.cancel()
    return controller, radio, events, value, clock, store, client, captures


@pytest.mark.parametrize("request_text,count,bounds,name", [
    (REQUEST, 3, (10, 50), None),
    ("help me improve my region from 10 to 50 meters for the next lap", 1, (10, 50), None),
    ("coach my corner at turn 1 for next three laps", 3, None, "turn 1"),
    ("update task 2 " + REQUEST, 3, (10, 50), None),
])
def test_explicit_focus_target_is_bounded_and_distinct_from_diagnostic_tracking(request_text, count, bounds, name):
    action = parse_task_request(request_text)
    assert action.focus_coach and action.region_compare
    assert (action.laps, action.region_bounds_m, action.region_name) == (count, bounds, name)


def test_best_retained_admitted_reference_is_not_silently_the_latest_or_other_compound():
    fastest = reference(number=4, elapsed=0.3)
    candidates = [fastest, reference(number=5), reference(number=3, elapsed=0.2, compound=17),
                  reference(number=2, elapsed=0.1, values={**CONTEXT, "weather_id": 3})]
    selected = choose_focus_reference(candidates, fastest.window, 16, CONTEXT)
    assert selected.lap_number == 4
    selected.observed_context["reported_ranges"]["weather_id"][0] = 5
    assert fastest.observed_context["reported_ranges"]["weather_id"] == [0, 0]


@pytest.mark.parametrize("field,value", [("weather_id", 3), ("tyre_age_laps", 6),
                                       ("track_temperature_c", 36), ("air_temperature_c", 26),
                                       ("weather_id", None), ("tyre_age_laps", None),
                                       ("track_temperature_c", None), ("air_temperature_c", None),
                                       ("weather_id", True), ("weather_id", 99)])
def test_reference_policy_refuses_missing_or_materially_different_reported_context(field, value):
    candidate = reference()
    with pytest.raises(ValueError, match="focus context policy"):
        choose_focus_reference([candidate], candidate.window, 16, {**CONTEXT, field: value})


def test_focus_tolerances_are_explicit_not_claimed_as_verified_equivalence():
    candidate = reference()
    assert compatible_focus_context(candidate.observed_context, {**CONTEXT, "tyre_age_laps": 5,
                                    "track_temperature_c": 35, "air_temperature_c": 25}) == (True, None)
    mixed = deepcopy(candidate.observed_context)
    mixed["reported_ranges"]["weather_id"] = [0, 1]
    assert not compatible_focus_context(mixed, CONTEXT)[0]
    assert "rather than a speed target" in focus_cue(reference(brake=None))
    assert "Do not move braking later" in focus_cue(candidate)


def test_backend_focus_reports_progress_once_and_keeps_one_fixed_reference_until_expiry():
    controller, radio, events, value, clock, store, client, captures = baseline()
    controller.handle_request(REQUEST, now=10)
    task = controller.tasks.active()[0]
    assert task.focus_coach and task.region_reference.lap_number == 4
    assert (task.first_lap, task.until_lap) == (6, 8)
    assert "One focus:" in radio.pending[-1].text and "admission pending" in radio.pending[-1].text
    assert len(radio.pending[-1].text.encode()) <= 1024
    captures.replace(controller.region_capture_requests())
    captures.poll()
    radio.cancel()
    for number in (6, 7, 8):
        collect_lap(controller, value, store, captures, lap=number, step_ms=40)
        report = next(event for event in reversed(events) if event["event"] == "radio_focus_report")
        assert report["reference_lap"] == 4
        assert report["coaching"]["comparison"]["elapsed_delta_s"] == pytest.approx(-0.08)
        assert report["coaching"]["suggestion_only"] is True
        assert report["coaching"]["layout_verified"] is False
        assert report["coaching"]["comparison"]["coaching_eligible"] is False
        assert report["text"].count("One focus:") == 1
        assert len(radio.pending[-1].text.encode()) <= 1024
        assert "Fuel, traffic and physical layout unverified" in radio.pending[-1].text
        before = len([event for event in events if event["event"] == "radio_focus_report"])
        controller.feed(value, now=10, region_captures=captures.poll())
        assert len([event for event in events if event["event"] == "radio_focus_report"]) == before
        radio.cancel()
    assert not controller.tasks.active()
    assert task.region_reference.lap_number == 4
    controller.handle_request("track that for another two laps", now=10)
    assert not controller.tasks.active()


@pytest.mark.parametrize("change", ["missing_reference", "weather", "age", "temperature", "tyres", "pit", "invalid", "sparse"])
def test_no_coaching_task_starts_without_complete_qualified_same_compound_evidence(change):
    controller, radio, events, value, clock, store, client, captures = baseline()
    if change == "missing_reference":
        controller.region_progress.focus_candidates.clear()
    elif change == "weather":
        value["context"]["value"]["weather_id"] = 3
    elif change == "temperature":
        value["context"]["value"]["track_temperature_c"] = 40
    elif change == "age":
        value["drivers"][0]["tyre"]["value"]["age_laps"] = 20
    elif change == "tyres":
        value["drivers"][0]["tyre"]["value"]["actual_compound"] = 17
    elif change == "sparse":
        controller._backend_regions = False
    else:
        value["drivers"][0]["lap"]["pit_status_id" if change == "pit" else "current_lap_invalid_id"] = 1
    controller.feed(value, now=10)
    controller.handle_request(REQUEST, now=10)
    assert not controller.tasks.active()
    assert "One focus:" not in radio.pending[-1].text


def test_changed_conditions_refuse_progress_without_replacing_reference_or_new_suggestion():
    controller, radio, events, value, clock, store, client, captures = baseline()
    controller.handle_request(REQUEST, now=10)
    captures.replace(controller.region_capture_requests())
    captures.poll()
    value["context"]["value"]["weather_id"] = 3
    collect_lap(controller, value, store, captures, lap=6)
    report = next(event for event in reversed(events) if event["event"] == "radio_focus_report")
    assert report["coaching"] is None and "weather changed" in report["text"]
    assert "One focus:" not in report["text"]
    assert controller.tasks.active()[0].region_reference.lap_number == 4


def test_extension_preserves_focus_policy_and_fixed_reference_then_cancel_removes_owned_speech():
    controller, radio, events, value, clock, store, client, captures = baseline()
    controller.handle_request(REQUEST, now=10)
    original = controller.tasks.active()[0]
    controller.handle_request("track that for another two laps", now=10)
    extended = controller.tasks.active()[0]
    assert extended.focus_coach and extended.region_reference == original.region_reference
    assert extended.version == original.version + 1
    controller.handle_request("what are you tracking", now=10)
    assert "repeatability focus" in radio.pending[-1].text
    controller.handle_request(f"cancel task {extended.task_id}", now=10)
    assert not controller.tasks.active()
    assert not any(message.owner == "lap_reports" for message in radio.pending)


def test_region_reference_candidates_are_bounded_and_reset_with_session_scope():
    controller, radio, events, value, clock, store, client, captures = baseline()
    for number in range(1, 80):
        controller.region_progress.focus_candidates.append(reference(number=number))
    assert len(controller.region_progress.focus_candidates) == 64
    value["scope"]["epoch"] += 1
    controller.feed(value, now=10)
    assert not controller.region_progress.focus_candidates


def test_registry_rejects_fake_focus_without_fixed_reference_and_distinguishes_duplicate_policy():
    registry = LapReportRegistry()
    plan = QuestionPlan(("self",), ("regional_observations",))
    candidate = reference()
    with pytest.raises(ValueError, match="fixed complete"):
        registry.install(plan, (None,), cursor=1, lap=6, laps=3,
                         region_window=candidate.window, focus_coach=True)
    first = registry.install(plan, (None,), cursor=1, lap=6, laps=3,
                             region_window=candidate.window, region_reference=candidate)
    second = registry.install(plan, (None,), cursor=1, lap=6, laps=3,
                              region_window=candidate.window, region_reference=candidate, focus_coach=True)
    assert first.task_id != second.task_id


def test_worst_corner_selects_only_a_supported_current_lap_loss_and_preserves_original_request():
    controller, radio, events, value, clock, store, client, captures = baseline()
    controller.handle_request("track my braking and minimum speed from 10 to 50 metres for next lap", now=10)
    captures.replace(controller.region_capture_requests())
    captures.poll()
    collect_lap(controller, value, store, captures, lap=6, step_ms=60)
    radio.cancel()
    question = "help me improve my worst corner for three laps"
    controller.handle_request(question, now=10)
    task = controller.tasks.active()[0]
    assert task.focus_coach and (task.first_lap, task.until_lap) == (8, 10)
    assert task.region_reference.lap_number == 4
    selection = next(event for event in events if event["event"] == "radio_focus_selection")["selection"]
    assert selection["target_lap"] == 6 and selection["loss_s"] == pytest.approx(0.08)
    assert selection["whole_circuit_ranking"] is False
    assert "not a whole-circuit worst-corner ranking" in radio.pending[-1].text
    assert "One focus:" in radio.pending[-1].text and len(radio.pending[-1].text.encode()) <= 1024
    assert controller.conversation.context()[-1]["request"] == question


@pytest.mark.parametrize("change", ["track", "length", "context", "player", "scope"])
def test_focus_context_change_ends_task_and_discards_old_region_references(change):
    controller, radio, events, value, clock, store, client, captures = baseline()
    controller.handle_request(REQUEST, now=10)
    if change == "track":
        value["context"]["value"]["track_id"] = 1
    elif change == "length":
        value["context"]["value"]["track_length_m"] = 110
    elif change == "context":
        value["context"]["status"] = "unavailable"
    elif change == "player":
        value["drivers"][0]["identity"]["tenure_ordinal"] += 1
    else:
        value["scope"]["epoch"] += 1
    controller.feed(value, now=10)
    assert not controller.tasks.active()
    assert not controller.region_progress.focus_candidates


def test_loss_selection_ignores_old_current_windows_other_compounds_and_ineligible_conditions():
    player = {"lap": {"current_lap_number": 6, "current_lap_invalid_id": 0,
                      "pit_status_id": 0, "result_status_id": 2},
              "tyre": {"status": "available", "value": {"actual_compound": 16}}}
    candidates = [reference(number=3, elapsed=0.2), reference(number=5, elapsed=0.5),
                  reference(number=4, elapsed=0.9), reference(number=5, elapsed=1, compound=17)]
    selected = choose_focus_loss(candidates, player, CONTEXT)
    assert selected[1]["reference_lap"] == 3 and selected[1]["target_lap"] == 5
    assert selected[1]["loss_s"] == pytest.approx(0.3)
    assert choose_focus_loss(candidates, player, {**CONTEXT, "weather_id": 3}) is None
    assert choose_focus_loss([reference(number=3), reference(number=5, elapsed=0.3)], player, CONTEXT) is None


def test_loss_selection_identifies_largest_loss_among_distinct_retained_windows():
    player = {"lap": {"current_lap_number": 6, "current_lap_invalid_id": 0,
                      "pit_status_id": 0, "result_status_id": 2},
              "tyre": {"status": "available", "value": {"actual_compound": 16}}}
    second = RegionWindow(60, 90, 100)
    candidates = [reference(number=3, elapsed=0.2), reference(number=5, elapsed=0.5),
                  RegionReference(3, 16, second, 0.2, 120, None, reference().observed_context),
                  RegionReference(5, 16, second, 0.8, 115, None, reference().observed_context)]
    window, selection = choose_focus_loss(candidates, player, CONTEXT)
    assert window == second and selection["candidate_windows"] == 2
    assert selection["loss_s"] == pytest.approx(0.6)


def test_registry_cannot_admit_focus_with_unknown_reported_reference_context():
    registry = LapReportRegistry()
    candidate = reference(values={**CONTEXT, "weather_id": None})
    with pytest.raises(ValueError, match="known stable"):
        registry.install(QuestionPlan(("self",), ("regional_observations",)), (None,), cursor=1,
                         lap=6, laps=3, region_window=candidate.window,
                         region_reference=candidate, focus_coach=True)
