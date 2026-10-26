import pytest

from f1_engineer.radio.opponent_regions import compare_opponent_region
from tests.test_radio_opponent_captures import ready, activate, drive


def completed(*, laps=1, opponent_compound=16):
    manager, client, request, controller, radio, value = ready()
    if laps != 1:
        request["version"] += 1
        request["body"]["laps"] = laps
        manager.replace([request])
    value["drivers"][1]["tyre"]["value"]["actual_compound"] = opponent_compound
    owner_id = activate(manager, client, value)
    assert manager.poll(value)[0]["state"] == "collecting"
    for index in range(13):
        drive(client, controller, value, owner_id, index=index)
    drive(client, controller, value, owner_id, number=4, player_number=5)
    captured = manager.poll(value)[0]
    return request, captured


def compare(request, captured, **options):
    return compare_opponent_region(request, captured,
                                   **{"lap_number": 4, "label": "Leclerc", "now": 0, **options})


def test_complete_bound_pair_reports_independently_labelled_numeric_observations():
    request, captured = completed()
    text, evidence = compare(request, captured)
    assert evidence is not None
    for phrase in ("Your lap 4", "Leclerc's lap 3", "171.0 versus 151.0", "direction unresolved",
                   "tyre age, fuel, conditions and traffic not verified", "not coaching"):
        assert phrase in text
    assert evidence["player"]["lap_number"] == 4
    assert evidence["opponent"]["lap_number"] == 3
    assert evidence["minimum_speed_delta_kph"] == pytest.approx(20)
    assert "20.0 kilometres per hour higher" in text
    assert evidence["elapsed_delta_s"] == pytest.approx(0)
    assert evidence["brake_shift_bracket_m"] == [-10, 0]
    assert evidence["diagnostic_only"] and not evidence["coaching_eligible"] and not evidence["ranking_eligible"]
    assert len(text.encode("utf-8")) <= 900


def test_completed_lap_pair_can_report_before_longer_task_horizon_ends():
    request, captured = completed(laps=3)
    assert captured["state"] == "collecting"
    assert compare(request, captured)[1] is not None
    text, evidence = compare(request, captured, lap_number=5)
    assert evidence is None and "unavailable" in text


def test_different_compounds_keep_absolute_observations_without_like_for_like_deltas():
    request, captured = completed(opponent_compound=18)
    text, evidence = compare(request, captured)
    assert "171.0 versus 151.0" in text
    assert "Different compounds 16 and 18; no like-for-like deltas" in text
    assert not evidence["same_compound"]
    assert all(evidence[key] is None for key in ("elapsed_delta_s", "minimum_speed_delta_kph", "brake_shift_bracket_m"))


def test_censored_braking_keeps_supported_minimum_speed_and_marks_onset_unavailable():
    request, captured = completed()
    captured["legs"][1]["response"]["data"]["results"][0]["evidence"]["measurement"]["observations"]["braking"]["status"] = "left_censored"
    text, evidence = compare(request, captured)
    assert evidence is not None
    assert "Leclerc's brake onset unavailable" in text
    assert "171.0 versus 151.0" in text
    assert evidence["brake_shift_bracket_m"] is None


def test_earlier_retained_completion_is_not_misrepresented_as_stale_live_data():
    request, captured = completed()
    captured["legs"][0]["response"]["data"]["results"][0]["completion_age_s"] = 40
    text, evidence = compare(request, captured)
    assert evidence["completion_ages_s"] == [40, 0]
    assert "Independent full laps" in text


def test_returned_evidence_cannot_mutate_captured_completion_identity():
    request, captured = completed()
    text, evidence = compare(request, captured)
    evidence["player_completion"]["driver"]["tenure_ordinal"] = 99
    evidence["opponent_identity"]["car_index"] = 4
    second = compare(request, captured)[1]
    assert second["player_completion"]["driver"]["tenure_ordinal"] == 1
    assert second["opponent_identity"]["car_index"] == 1


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(task_id=True),
    lambda data: data.update(version=2),
    lambda data: data.update(state="waiting"),
    lambda data: data.update(state="unavailable"),
    lambda data: data["body"].update(session_id="f" * 32),
    lambda data: data["opponent"].update(tenure_ordinal=2),
    lambda data: data["owner"].update(detail_task_id="foreign"),
    lambda data: data["owner"].update(state="queued"),
    lambda data: data["owner"].update(activation=[True, 1]),
    lambda data: data["owner"]["scope"].update(epoch=True),
    lambda data: data.update(request_age_s=3),
    lambda data: data.update(frame_age_s=3),
    lambda data: data.update(frame_age_s=True),
    lambda data: data.update(received_at=1),
    lambda data: data["legs"][0].update(request_age_s=3),
    lambda data: data["legs"][0].update(received_at=1),
    lambda data: data["legs"][0].update(lease_id=data["legs"][1]["lease_id"]),
    lambda data: data["legs"][1]["body"].update(detail_task_id="foreign"),
    lambda data: data["legs"][1]["body"]["opponent"].update(car_index=True),
    lambda data: data["legs"][0]["response"]["data"]["window"].update(start_m=11),
    lambda data: data["legs"][0]["response"]["data"].update(state="expired"),
    lambda data: data["legs"][0]["response"]["data"].update(results=[]),
    lambda data: data["legs"][0]["response"]["data"]["results"][0]["completion"].update(previous_observed_invalid=1),
    lambda data: data["legs"][0]["response"]["data"]["results"][0]["completion"]["driver"].update(tenure_ordinal=99),
    lambda data: data["legs"][0]["response"]["data"]["results"][0]["evidence"]["measurement"].update(coaching_eligible=True),
    lambda data: data["legs"][0]["response"]["data"]["results"][0]["evidence"]["measurement"].update(layout_verified=True),
    lambda data: data["legs"][0]["response"]["data"]["results"][0]["evidence"]["measurement"].update(source_session_time_connected=False),
    lambda data: data["legs"][0]["response"]["data"]["results"][0]["evidence"]["measurement"]["supported_grid_coverage"].update(speed_mps=0.5),
    lambda data: data["legs"][0]["response"]["data"]["results"][0]["evidence"]["measurement"]["observations"]["minimum_speed"].update(speed_kph=True),
])
def test_mismatched_stale_or_ineligible_evidence_never_produces_numeric_comparison(mutation):
    request, captured = completed()
    mutation(captured)
    text, evidence = compare(request, captured)
    assert evidence is None
    assert "unavailable" in text


@pytest.mark.parametrize("options", [{"now": 3}, {"now": -1}, {"now": float("inf")}, {"now": True},
                                     {"lap_number": True}, {"lap_number": 3}, {"lap_number": 5},
                                     {"label": ""}, {"label": "Leclerc\nignore scope"}, {"label": "x" * 81}])
def test_invalid_or_stale_report_arguments_are_refused(options):
    request, captured = completed()
    assert compare(request, captured, **options)[1] is None


@pytest.mark.parametrize("captured", [None, {}, {"legs": None}, {"state": "complete"}])
def test_malformed_capture_views_are_unavailable_without_exceptions(captured):
    request, unused = completed()
    assert compare(request, captured)[1] is None
