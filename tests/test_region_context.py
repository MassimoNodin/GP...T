from copy import deepcopy

import pytest

from f1_engineer.analysis.region_context import (
    RegionSampleContext, regional_sample_context, scoped_region_context, validate_region_context,
)
from f1_engineer.radio.region_progress import measured_reference, RegionProgress
from f1_engineer.radio.regions import RegionWindow
from tests.test_radio_opponent_captures import ready, activate, drive
from tests.test_radio_opponent_regions import compare


def collected(*, mutate=None):
    manager, client, request, controller, radio, value = ready()
    value["context"]["value"].update(weather_id=0, track_temperature_c=30, air_temperature_c=20)
    for row, age in zip(value["drivers"], (3, 5)):
        row["tyre"]["value"]["age_laps"] = age
    owner = activate(manager, client, value)
    assert manager.poll(value)[0]["state"] == "collecting"
    for index in range(13):
        if mutate is not None:
            mutate(value, index)
        drive(client, controller, value, owner, index=index)
    drive(client, controller, value, owner, number=4, player_number=5)
    return request, manager.poll(value)[0]


def test_actual_paired_capture_retains_context_for_each_original_region_sample():
    request, captured = collected()
    text, evidence = compare(request, captured)
    assert "Reported tyre ages 3 to 3 versus 5 to 5" in text
    assert "not verified equivalence" in text and "not coaching" in text
    assert len(text.encode("utf-8")) <= 900
    context = evidence["reported_context_comparison"]
    assert context["different_reported_fields"] == ["tyre_age_laps"]
    assert context["missing_reported_fields"] == []
    assert not context["continuous_equivalence_verified"]
    assert not context["fuel_verified"] and not context["traffic_verified"]
    assert evidence["missing_context"] == ["fuel", "traffic", "continuous_condition_equivalence"]
    for leg, age in zip(captured["legs"], (3, 5)):
        data = leg["response"]["data"]["results"][0]["evidence"]
        observed = data["observed_context"]
        assert observed["sample_count"] == data["measurement"]["source_sample_count"]
        assert observed["reported_ranges"] == {"tyre_age_laps": [age, age], "weather_id": [0, 0],
                                                "track_temperature_c": [30, 30], "air_temperature_c": [20, 20]}
        assert validate_region_context(observed, observed["sample_count"]) == observed


def test_region_context_retains_changes_but_excludes_later_outside_region_values():
    def change(value, index):
        if index >= 4:
            value["context"]["value"].update(weather_id=1, track_temperature_c=32)
        if index >= 11:
            value["context"]["value"].update(weather_id=5, track_temperature_c=80)
    request, captured = collected(mutate=change)
    evidence = compare(request, captured)[1]
    observed = evidence["player"]["observed_context"]["reported_ranges"]
    assert observed["weather_id"] == [0, 1] and observed["track_temperature_c"] == [30, 32]


@pytest.mark.parametrize("missing_index,expected", [(0, [3, 3]), (1, None)])
def test_context_coverage_includes_retained_pre_window_bracketing_sample_only(missing_index, expected):
    def change(value, index):
        value["drivers"][0]["tyre"]["value"]["age_laps"] = None if index == missing_index else 3
    request, captured = collected(mutate=change)
    text, evidence = compare(request, captured)
    assert evidence["player"]["observed_context"]["reported_ranges"]["tyre_age_laps"] == expected
    if expected is None:
        assert "Some reported context unavailable" in text


@pytest.mark.parametrize("fault", ["missing_age", "age_bool", "missing_context", "wrong_uid", "wrong_format",
                                    "weather_bool", "temperature_range"])
def test_missing_or_misbound_context_is_not_backfilled_and_does_not_invent_coaching(fault):
    def change(value, index):
        if index == 4:
            if fault in {"missing_age", "age_bool"}:
                value["drivers"][0]["tyre"]["value"]["age_laps"] = None if fault == "missing_age" else True
            elif fault == "missing_context":
                value["context"]["status"] = "unavailable"
            elif fault == "wrong_uid":
                value["context"]["value"]["session_uid"] += 1
            elif fault == "wrong_format":
                value["context"]["value"]["packet_format"] = 2026
            elif fault == "weather_bool":
                value["context"]["value"]["weather_id"] = False
            else:
                value["context"]["value"]["track_temperature_c"] = 128
        if index == 5:
            value["drivers"][0]["tyre"]["value"]["age_laps"] = 3
            value["context"] = {"status": "available", "value": {"session_uid": 99, "packet_format": 2025,
                "track_length_m": 100.0, "weather_id": 0, "track_temperature_c": 30, "air_temperature_c": 20}}
    request, captured = collected(mutate=change)
    text, evidence = compare(request, captured)
    if fault in {"missing_context", "wrong_uid", "wrong_format"}:
        assert evidence is None and "unavailable" in text.lower()
        return
    assert evidence is not None and not evidence["coaching_eligible"]
    ranges = evidence["player"]["observed_context"]["reported_ranges"]
    if fault in {"missing_age", "age_bool"}:
        assert ranges["tyre_age_laps"] is None
    elif fault == "temperature_range":
        assert ranges["track_temperature_c"] is None
    else:
        assert ranges["weather_id"] is None


def test_context_views_and_reference_results_do_not_share_mutable_capture_data():
    request, captured = collected()
    data = captured["legs"][0]["response"]["data"]["results"][0]
    window = RegionWindow(10, 50, 100)
    reference = measured_reference(window, data["completion"], data["evidence"])
    data["evidence"]["observed_context"]["reported_ranges"]["tyre_age_laps"][0] = 200
    view = reference.view()
    view["observed_context"]["reported_ranges"]["tyre_age_laps"][0] = 255
    assert reference.observed_context["reported_ranges"]["tyre_age_laps"] == [3, 3]


@pytest.mark.parametrize("fault", ["count", "bool_count", "unknown_field", "reversed", "range", "eligibility", "continuous"])
def test_malformed_context_is_unavailable_without_discarding_supported_numeric_evidence(fault):
    request, captured = collected()
    data = captured["legs"][0]["response"]["data"]["results"][0]
    observed = data["evidence"]["observed_context"]
    if fault == "count":
        observed["sample_count"] += 1
    elif fault == "bool_count":
        observed["sample_count"] = True
    elif fault == "unknown_field":
        observed["fuel"] = 0
    elif fault == "reversed":
        observed["reported_ranges"]["tyre_age_laps"] = [5, 3]
    elif fault == "range":
        observed["reported_ranges"]["weather_id"] = [0, 255]
    elif fault == "eligibility":
        observed["coaching_eligible"] = True
    else:
        observed["continuous_context_verified"] = True
    text, evidence = compare(request, captured)
    assert evidence is not None and evidence["player"]["observed_context"] is None
    assert "tyre age, fuel, conditions and traffic not verified" in text


def test_previous_fixed_reference_keeps_original_context_and_discloses_a_difference():
    request, captured = collected()
    original = captured["legs"][0]["response"]["data"]["results"][0]
    window = RegionWindow(10, 50, 100)
    reference = measured_reference(window, original["completion"], original["evidence"])
    current = deepcopy(original)
    current["completion"]["lap_number"] = current["evidence"]["lap_number"] = 5
    current["evidence"]["observed_context"]["reported_ranges"]["tyre_age_laps"] = [4, 4]
    text, evidence = RegionProgress().compare(reference, current["completion"], current["evidence"])
    assert "Reported tyre ages 4 to 4 versus 3 to 3" in text and "not coaching" in text
    assert evidence["reported_context_comparison"]["different_reported_fields"] == ["tyre_age_laps"]


def test_missing_sample_context_stays_missing_after_later_observations_and_view_mutation():
    values = {"tyre_age_laps": 3, "weather_id": 0, "track_temperature_c": 30, "air_temperature_c": 20}
    observed = RegionSampleContext()
    observed.observe(values)
    observed.observe({**values, "weather_id": None})
    observed.observe(values)
    view = observed.view()
    view["reported_ranges"]["tyre_age_laps"][0] = 99
    assert observed.view()["reported_ranges"]["weather_id"] is None
    assert observed.view()["reported_ranges"]["tyre_age_laps"] == [3, 3]


def test_scope_and_field_type_validation_is_independent_of_numeric_measurements():
    assert scoped_region_context({"scope": {"session_uid": 99, "packet_format": 2025},
        "context": {"status": "available", "value": {"session_uid": True, "packet_format": 2025}}}) is None
    assert regional_sample_context({"weather_id": 6}, {"tyre": {"status": "available", "value": {"age_laps": False}}}) == {
        "tyre_age_laps": None, "weather_id": None, "track_temperature_c": None, "air_temperature_c": None}
