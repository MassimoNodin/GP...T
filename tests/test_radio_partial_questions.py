import pytest

from f1_engineer.radio import command
from f1_engineer.radio.questions import UNAVAILABLE_FACTS, plan_question
from tests.test_live_driver_names import named_live
from tests.test_radio_voice_submission import WHEEL, prepared


@pytest.mark.parametrize("question,metrics", [
    ("my last lap and fastest sector", {"last_lap", "fastest_sector"}),
    ("my last lap and best lap", {"last_lap", "session_best"}),
    ("my session best valid lap time and fuel remaining", {"session_best", "fuel_level"}),
    ("my and leader last lap and current fuel level", {"last_lap", "fuel_level"}),
    ("my current tyre age and tire compound", {"tyre_age", "tyre_compound"}),
])
def test_known_fact_requests_have_explicit_bounded_plans(question, metrics):
    assert set(plan_question(question).metrics) == metrics


@pytest.mark.parametrize("fact,metric", [("best lap", "session_best"), ("fastest sector", "fastest_sector"),
                                       ("fuel level", "fuel_level")])
def test_mixed_multi_driver_request_keeps_timing_answers_and_explains_missing_fact(fact, metric):
    controller, radio, events, value = named_live()
    controller.answer_question(f"my and Leclerc last lap and {fact}", now=10)
    text = radio.pending[-1].text
    assert "103.500 seconds" in text and "101.000 seconds" in text
    assert text.count(UNAVAILABLE_FACTS[metric]) == 2
    assert len(events[-1]["results"]) == 2


def test_session_best_is_not_silently_substituted_with_observed_best():
    controller, radio, events, value = named_live()
    controller.answer_question("my best lap", now=10)
    assert radio.pending[-1].text.endswith(UNAVAILABLE_FACTS["session_best"] + ".")
    assert "best fully observed" not in radio.pending[-1].text


@pytest.mark.parametrize("question", [
    "my last lap and fuel in the rain", "my last lap and fuel on soft tyres",
    "my last lap in the rain and fastest sector", "my best lap on lap 5",
    "my last lap and quit", "my last lap and change the wheel button",
    "my last lap and cancel all tracking", "my last lap and arbitraryunknownfact",
    "my last lap and fastest sector and fuel and position",
])
def test_unknown_filters_and_controls_are_not_dropped_to_manufacture_partial_answers(question):
    with pytest.raises(ValueError):
        plan_question(question)


def tyre_live():
    controller, radio, events, value = named_live()
    for index, row in enumerate(value["drivers"]):
        row["tyre"] = {"status": "available", "value": {"actual_compound": 16 + index,
                      "visual_compound": 16 + index, "age_laps": index}}
    controller.feed(value, now=10.1)
    return controller, radio, events, value


def test_zero_tyre_age_is_a_valid_measurement_and_compound_ids_are_distinguished():
    controller, radio, events, value = tyre_live()
    controller.answer_question("my tyre age and tyre compound", now=10.1)
    text = radio.pending[-1].text
    assert "current tyre age 0 laps" in text
    assert "actual tyre compound 16, visual compound 16" in text
    assert "soft" not in text


def test_supported_tyre_facts_do_not_hide_timing_results_when_opponent_status_is_missing():
    controller, radio, events, value = tyre_live()
    value["drivers"][1]["tyre"] = {"status": "unavailable"}
    controller.feed(value, now=10.2)
    controller.answer_question("my and Leclerc last lap and tyre age", now=10.2)
    text = radio.pending[-1].text
    assert "103.500 seconds" in text and "101.000 seconds" in text
    assert "current tyre age 0 laps" in text and "current tyre age unavailable" in text


@pytest.mark.parametrize("age", [None, False, -1, 256, 1.5, "2"])
def test_unknown_or_malformed_tyre_age_is_not_spoken_as_fact(age):
    controller, radio, events, value = tyre_live()
    value["drivers"][0]["tyre"]["value"]["age_laps"] = age
    controller.feed(value, now=10.2)
    controller.answer_question("my tyre age", now=10.2)
    assert "current tyre age unavailable" in radio.pending[-1].text


@pytest.mark.parametrize("compound", [None, False, 0, 256, "16"])
def test_malformed_compound_does_not_make_tyre_metadata_usable(compound):
    controller, radio, events, value = tyre_live()
    value["drivers"][0]["tyre"]["value"]["actual_compound"] = compound
    controller.feed(value, now=10.2)
    controller.answer_question("my tyre age and tyre compound", now=10.2)
    assert "current tyre age unavailable" in radio.pending[-1].text
    assert "current tyre compound unavailable" in radio.pending[-1].text


def test_unknown_visual_compound_does_not_hide_known_actual_compound():
    controller, radio, events, value = tyre_live()
    value["drivers"][0]["tyre"]["value"]["visual_compound"] = None
    controller.feed(value, now=10.2)
    controller.answer_question("my tyre compound", now=10.2)
    assert "actual tyre compound 16, visual compound unavailable" in radio.pending[-1].text


def test_partial_question_followup_preserves_metrics_and_driver_binding():
    controller, radio, events, value = named_live()
    controller.answer_question("leader last lap and fuel level", now=10)
    value["drivers"][1]["lap"]["car_position"] = 3
    value["drivers"][2]["lap"]["car_position"] = 1
    controller.feed(value, now=10.1)
    controller.answer_question("what about his last lap?", now=10.1)
    assert controller._history[-1]["bindings"][0]["car_index"] == 1
    assert "Charles Leclerc" in radio.pending[-1].text and "101.000 seconds" in radio.pending[-1].text


def test_partial_question_is_available_through_guarded_automatic_voice_submission():
    controller, radio, events, value, voice, hardware, voice_events, clock = prepared("my last lap and fuel")
    assert command.submit_ready_question(controller, voice, now=10, input_status=WHEEL)
    assert "103.500 seconds" in radio.pending[-1].text
    assert UNAVAILABLE_FACTS["fuel_level"] in radio.pending[-1].text


@pytest.mark.parametrize("metric", ["best lap", "fastest sector", "fuel level"])
def test_unavailable_facts_cannot_create_recurring_placeholder_tasks(metric):
    controller, radio, events, value = named_live()
    controller.handle_request(f"tell me at the end of each lap my last lap and {metric}", now=10)
    assert not controller.tasks.active()
    assert "Recurring reports cannot track" in radio.pending[-1].text


def test_failed_unavailable_fact_task_update_preserves_existing_task():
    controller, radio, events, value = named_live()
    controller.handle_request("tell me at the end of each lap my last lap", now=10)
    original = controller.tasks.active()[0].view()
    controller.handle_request("update task 1 tell me at the end of each lap my fuel level", now=10)
    assert controller.tasks.active()[0].view() == original


def test_keyword_driver_names_require_quotes_without_consuming_fact_words():
    plan = plan_question("my last lap and fuel level", names=("Fuel", "Level"))
    assert plan.targets == ("self",)
    quoted = plan_question('"Fuel" last lap', names=("Fuel",))
    assert quoted.targets == ("name:fuel",)


def test_partial_answers_keep_three_driver_three_fact_limit():
    controller, radio, events, value = named_live()
    controller.answer_question("my and Leclerc and Norris last lap and best lap and fuel level", now=10)
    assert len(events[-1]["results"]) == 3
    assert len(radio.pending[-1].text.encode("utf-8")) <= 1024
