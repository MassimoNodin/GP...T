from copy import deepcopy

import pytest

from tests.test_radio_lap_history import history_live, publish, records


def benchmark_live(*, both=False, laps=2):
    controller, radio, events, value = history_live()
    value["context"] = {"status": "available", "value": {"session_uid": 99, "packet_format": 2025,
                        "formula_id": 0, "weather_id": 0, "track_temperature_c": 30,
                        "air_temperature_c": 20}}
    for row in value["drivers"]:
        row["lap"]["pit_stop_count"] = 0
        row["tyre"]["value"].update(visual_compound=16, age_laps=3)
    value["drivers"][0]["tyre"]["value"].update(actual_compound=18, visual_compound=18, age_laps=10)
    value["drivers"][2]["lap"]["delta_to_race_leader_ms"] = 7000
    controller.feed(value, now=10.2)
    now = 10.3
    for car in ((1, 2) if both else (1,)):
        for number in range(3, 4 + laps):
            publish(controller, value, car=car, number=number, elapsed=103500 + (car - 1) * 1000, now=now)
            now += 0.1
    radio.cancel()
    return controller, radio, events, value


def pit_event(controller, value, kind, *, now=12, edit=None):
    player = value["drivers"][0]
    prior_receipt = value["received_monotonic_ns"]
    field = [{"identity": deepcopy(row["identity"]), "position": row["lap"]["car_position"],
              "leader_gap_ms": 0 if row["lap"]["car_position"] == 1 else row["lap"]["delta_to_race_leader_ms"]}
             for row in value["drivers"]]
    old_tyre = deepcopy(player["tyre"]["value"]) if player["tyre"]["status"] == "available" else None
    value["event_cursor"] += 1
    value["received_monotonic_ns"] += 100_000_000
    event = {"id": value["event_cursor"], "scope": deepcopy(value["scope"]),
             "driver": deepcopy(player["identity"]), "kind": kind,
             "received_monotonic_ns": value["received_monotonic_ns"]}
    if kind == "pit_entered":
        event.update(pre_pit_field=field, pre_pit_received_monotonic_ns=prior_receipt,
                     pre_pit_position=player["lap"]["car_position"],
                     pre_pit_stop_count=player["lap"]["pit_stop_count"], pre_pit_tyre=old_tyre)
        player["lap"].update(pit_status_id=1, car_position=20)
    else:
        player["lap"]["pit_status_id"] = 0
        player["lap"]["pit_stop_count"] += 1
        player["tyre"] = {"status": "available", "value": {"actual_compound": 16,
                           "visual_compound": 16, "age_laps": 0}}
    if edit:
        edit(event)
    value["events"] = [event]
    controller.feed(value, now=now)
    return event


def reports(events):
    return [event for event in events if event["event"] == "radio_pit_benchmark"]


def test_confirmed_pit_and_tyre_change_speaks_one_identified_session_benchmark():
    controller, radio, events, value = benchmark_live()
    pit_event(controller, value, "pit_entered")
    assert not reports(events)
    pit_event(controller, value, "pit_exited", now=12.5)
    report, = reports(events)
    assert report["evidence"]["sample_count"] == 2
    assert report["evidence"]["lap_numbers"] == [4, 5]
    assert report["evidence"]["average_ms"] == 103500
    assert report["evidence"]["pre_pit_position"] == 2
    assert "You're on soft tyres, actual compound 16" in report["text"]
    assert "Charles Leclerc, 1 place ahead before your stop" in report["text"]
    assert "1 minute 43.500 seconds across 2 observed clean laps" in report["text"]
    assert "tyre ages 3 to 3 laps" in report["text"]
    assert "not a guaranteed target" in report["text"]
    assert radio.pending[-1].owner == "pit_benchmark"
    controller.feed(value, now=12.6)
    assert len(reports(events)) == 1


@pytest.mark.parametrize("front_gap,back_gap,expected", [(0, 3000, 2), (0, 7000, 1), (0, 5000, 1)])
def test_pre_pit_race_gap_then_ahead_break_position_distance_ties(front_gap, back_gap, expected):
    controller, radio, events, value = benchmark_live(both=True)
    value["drivers"][1]["lap"]["delta_to_race_leader_ms"] = front_gap
    value["drivers"][2]["lap"]["delta_to_race_leader_ms"] = back_gap
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    report, = reports(events)
    assert report["evidence"]["reference_identity"] == controller.lap_history.get(value["drivers"][expected]).identity


def test_post_pit_positions_do_not_change_reference_ranking():
    controller, radio, events, value = benchmark_live(both=True)
    pit_event(controller, value, "pit_entered")
    value["drivers"][1]["lap"]["car_position"] = 1
    value["drivers"][2]["lap"]["car_position"] = 19
    pit_event(controller, value, "pit_exited", now=12.5)
    assert reports(events)[0]["evidence"]["reference_pre_pit_position"] == 1


def test_next_nearest_eligible_reference_is_used_if_closest_has_no_same_compound_laps():
    controller, radio, events, value = benchmark_live(both=True)
    value["drivers"][1]["lap"]["car_position"] = 1
    value["drivers"][2]["lap"]["car_position"] = 5
    state = controller.lap_history.get(value["drivers"][1])
    state.laps.clear()
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    assert reports(events)[0]["evidence"]["reference_pre_pit_position"] == 5


def test_one_lap_is_a_single_lap_reference_not_an_average_trend():
    controller, radio, events, value = benchmark_live(laps=1)
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    assert "single-lap reference not a trend" in reports(events)[0]["text"]
    assert "averaged" not in reports(events)[0]["text"]


@pytest.mark.parametrize("fault", ["no_entry", "no_stop_increment", "multiple_stops", "unknown_stops", "no_tyre_change",
                                  "unknown_old_tyre", "wrong_driver", "old_exit", "future_exit"])
def test_unconfirmed_or_unrelated_pit_events_cannot_announce_a_benchmark(fault):
    controller, radio, events, value = benchmark_live()
    if fault != "no_entry":
        pit_event(controller, value, "pit_entered", edit=(lambda event: event.update(pre_pit_tyre=None)) if fault == "unknown_old_tyre" else None)
    def edit(event):
        player = value["drivers"][0]
        if fault == "no_stop_increment":
            player["lap"]["pit_stop_count"] = 0
        elif fault == "multiple_stops":
            player["lap"]["pit_stop_count"] = 2
        elif fault == "unknown_stops":
            player["lap"]["pit_stop_count"] = None
        elif fault == "no_tyre_change":
            player["tyre"]["value"].update(actual_compound=18, visual_compound=18, age_laps=10)
        elif fault == "wrong_driver":
            event["driver"] = deepcopy(value["drivers"][1]["identity"])
        elif fault == "old_exit":
            event["received_monotonic_ns"] -= 3_000_000_000
        elif fault == "future_exit":
            event["received_monotonic_ns"] += 1
    pit_event(controller, value, "pit_exited", now=12.5, edit=edit)
    assert not reports(events)


def test_same_compound_replacement_requires_observed_age_reset():
    controller, radio, events, value = benchmark_live()
    value["drivers"][0]["tyre"]["value"].update(actual_compound=16, visual_compound=16)
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    assert len(reports(events)) == 1


@pytest.mark.parametrize("fault,reason", [
    ("missing_position", "pre_pit_position_unavailable"), ("stale_positions", "pre_pit_position_unavailable"),
    ("duplicate_position", "pre_pit_position_disputed"), ("missing_player", "pre_pit_position_disputed"),
    ("no_reference", "no_eligible_same_compound_reference"), ("different_compound", "no_eligible_same_compound_reference"),
    ("unknown_gap", "pre_pit_gap_tie_break_unavailable"), ("zero_follower_gap", "pre_pit_gap_tie_break_unavailable"),
])
def test_unavailable_reference_or_ranking_is_explained_without_false_comparison(fault, reason):
    controller, radio, events, value = benchmark_live(both=True)
    if fault == "no_reference":
        for state in controller.lap_history.drivers.values():
            state.laps.clear()
    def edit(event):
        if fault == "missing_position":
            event["pre_pit_position"] = None
        elif fault == "stale_positions":
            event["pre_pit_received_monotonic_ns"] -= 3_000_000_000
        elif fault == "duplicate_position":
            event["pre_pit_field"][1]["position"] = 2
        elif fault == "missing_player":
            event["pre_pit_field"] = event["pre_pit_field"][1:]
        elif fault in ("unknown_gap", "zero_follower_gap"):
            event["pre_pit_field"][2]["leader_gap_ms"] = None if fault == "unknown_gap" else 0
    pit_event(controller, value, "pit_entered", edit=edit)
    pit_event(controller, value, "pit_exited", now=12.5,
              edit=(lambda event: value["drivers"][0]["tyre"]["value"].update(actual_compound=17)) if fault == "different_compound" else None)
    assert reports(events)[0]["evidence"]["reason"] == reason
    assert reports(events)[0]["evidence"]["sample_count"] == 0


@pytest.mark.parametrize("fault", ["identity", "session", "stale", "history_gap", "rewind"])
def test_pending_pit_cannot_survive_context_invalidation(fault):
    controller, radio, events, value = benchmark_live()
    pit_event(controller, value, "pit_entered")
    if fault == "identity":
        value["drivers"][0]["identity"]["tenure_ordinal"] += 1
    elif fault == "session":
        value["session_id"] = "3" * 32
    elif fault == "stale":
        controller.check_freshness(now=16)
    elif fault == "history_gap":
        value["event_cursor"] += 2
        value["event_floor"] = value["event_cursor"] - 1
    else:
        value["drivers"][0]["lap"]["current_lap_number"] = 1
    controller.feed(value, now=12.4)
    assert controller.pit_benchmarks.pending is None
    pit_event(controller, value, "pit_exited", now=12.5)
    assert not reports(events)


@pytest.mark.parametrize("mode", ["quiet", "listening"])
def test_radio_quiet_and_listening_policy_suppresses_benchmark_speech(mode):
    controller, radio, events, value = benchmark_live()
    pit_event(controller, value, "pit_entered")
    if mode == "quiet":
        radio.quiet()
    else:
        radio.suspended = True
    pit_event(controller, value, "pit_exited", now=12.5)
    assert not radio.pending
    assert any(event.get("reason") == mode for event in events if event["event"] == "radio_dropped")


def test_queued_and_repeatable_benchmark_is_invalidated_when_reference_driver_changes():
    controller, radio, events, value = benchmark_live()
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    radio.tick(now=12.5)
    frozen = radio.capture_repeat_reference()
    value["drivers"][1]["identity"]["tenure_ordinal"] += 1
    controller.feed(value, now=12.6)
    assert radio.active is None and not radio.pending
    assert not radio.repeat_captured(frozen, now=12.6)


def test_missing_exit_tyre_data_can_complete_on_next_fresh_frame_of_same_lap():
    controller, radio, events, value = benchmark_live()
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5,
              edit=lambda event: value["drivers"][0].update(tyre={"status": "unavailable"}))
    assert not reports(events)
    value["drivers"][0]["tyre"] = {"status": "available", "value": {"actual_compound": 16, "age_laps": 0}}
    controller.feed(value, now=12.6)
    assert len(reports(events)) == 1


def test_missing_exit_tyre_data_does_not_emit_late_benchmark_on_another_lap():
    controller, radio, events, value = benchmark_live()
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5,
              edit=lambda event: value["drivers"][0].update(tyre={"status": "unavailable"}))
    value["drivers"][0]["lap"]["current_lap_number"] += 1
    value["drivers"][0]["tyre"] = {"status": "available", "value": {"actual_compound": 16, "age_laps": 0}}
    controller.feed(value, now=12.6)
    assert not reports(events) and controller.pit_benchmarks.pending is None


def test_material_weather_difference_and_unknown_ages_are_disclosed():
    controller, radio, events, value = benchmark_live()
    state = controller.lap_history.get(value["drivers"][1])
    for conditions in state.conditions.values():
        conditions.tyre_age_range = None
    value["context"]["value"]["weather_id"] = 1
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    text = reports(events)[0]["text"]
    assert "Tyre-age range unavailable" in text and "weather differs from now" in text


def test_latest_clean_contiguous_lap_set_is_identified_not_combined_across_gaps():
    controller, radio, events, value = benchmark_live(laps=3)
    state = controller.lap_history.get(value["drivers"][1])
    state.laps.remove(state.laps[1])
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    assert reports(events)[0]["evidence"]["lap_numbers"] == [6]


def test_tyres_age_drop_during_lap_and_pit_events_exclude_interrupted_laps():
    controller, radio, events, value = benchmark_live(laps=1)
    row = value["drivers"][1]
    row["tyre"]["value"]["age_laps"] = 0
    controller.feed(value, now=11.5)
    publish(controller, value, number=5, now=11.6)
    assert [record.lap_number for record in records(controller, value)] == [4]


def test_known_temperature_changes_are_disclosed_with_reference_ranges():
    controller, radio, events, value = benchmark_live()
    value["context"]["value"].update(track_temperature_c=35, air_temperature_c=22)
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    text = reports(events)[0]["text"]
    assert "Track temperature was 30 to 30 degrees, now 35" in text
    assert "Air temperature was 20 to 20 degrees, now 22" in text


def test_reference_discontinuity_invalidates_queued_benchmark_without_identity_change():
    controller, radio, events, value = benchmark_live()
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    assert radio.pending[-1].owner == "pit_benchmark"
    publish(controller, value, kind="driver_discontinuity", now=12.6)
    assert not radio.pending


@pytest.mark.parametrize("fault", ["wrong_formula", "missing_context", "unknown_visual", "malformed_visual"])
def test_tyre_labels_are_not_guessed_from_actual_compound_ids(fault):
    controller, radio, events, value = benchmark_live()
    if fault == "wrong_formula":
        value["context"]["value"]["formula_id"] = 1
    elif fault == "missing_context":
        value["context"] = {"status": "unavailable"}
    pit_event(controller, value, "pit_entered")
    def edit(event):
        if fault in ("unknown_visual", "malformed_visual"):
            value["drivers"][0]["tyre"]["value"]["visual_compound"] = None if fault == "unknown_visual" else []
    pit_event(controller, value, "pit_exited", now=12.5, edit=edit)
    assert "New tyres, compound 16" in reports(events)[0]["text"]
    assert "You're on soft" not in reports(events)[0]["text"]


def test_unknown_nonplayer_pre_pit_position_is_disclosed_without_claiming_global_closest():
    controller, radio, events, value = benchmark_live()
    def edit(event):
        event["pre_pit_field"][2]["position"] = None
    pit_event(controller, value, "pit_entered", edit=edit)
    pit_event(controller, value, "pit_exited", now=12.5)
    assert "selection uses known positions only" in reports(events)[0]["text"]


def test_lap_with_known_weather_transition_is_excluded_from_benchmark_set():
    controller, radio, events, value = benchmark_live()
    state = controller.lap_history.get(value["drivers"][1])
    for conditions in state.conditions.values():
        conditions.weather_ids = (0, 1)
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    assert reports(events)[0]["evidence"]["reason"] == "no_eligible_same_compound_reference"


def test_explicit_pit_entry_and_exit_exclude_in_and_out_laps_from_history():
    controller, radio, events, value = history_live()
    publish(controller, value, number=3, now=11)
    publish(controller, value, kind="pit_entered", now=11.1)
    publish(controller, value, number=4, now=11.2)
    publish(controller, value, kind="pit_exited", now=11.3)
    publish(controller, value, number=5, now=11.4)
    publish(controller, value, number=6, now=11.5)
    assert [record.lap_number for record in records(controller, value)] == [6]


def test_started_benchmark_can_finish_without_ten_second_proactive_cutoff():
    controller, radio, events, value = benchmark_live()
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    radio.tick(now=12.5)
    radio.tick(now=23)
    assert radio.active is not None and radio.active.owner == "pit_benchmark"


def test_queued_benchmark_still_cannot_start_after_dispatch_freshness_deadline():
    controller, radio, events, value = benchmark_live()
    pit_event(controller, value, "pit_entered")
    pit_event(controller, value, "pit_exited", now=12.5)
    radio.tick(now=16)
    assert not radio.pending and radio.active is None
