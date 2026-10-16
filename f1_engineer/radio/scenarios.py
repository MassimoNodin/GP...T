from __future__ import annotations

from copy import deepcopy

from .command import DryRunPlayback
from .controller import RadioController
from .diagnostics import emit, radio_event_log
from .scheduler import RadioMessage, RadioScheduler


SCENARIOS = ("closing", "pace", "quiet", "session-change", "mid-lap", "busy-radio")


class ScenarioPlayback(DryRunPlayback):
    now = 0.0
    busy_until = 0.0

    def busy(self):
        return self.now < self.busy_until


def _snapshot():
    drivers = []
    for car, position, distance in ((0, 2, 10_020), (1, 1, 10_120), (2, 3, 9_920)):
        drivers.append({"car_index": car, "identity": {
            "car_index": car, "tenure_ordinal": 1,
            "participant_identity_fingerprint": ("a", "b", "c")[car] * 64},
            "tyre": {"status": "available", "value": {"actual_compound": 16}},
            "lap": {"current_lap_number": int(distance // 5000) + 1, "car_position": position,
                    "current_lap_time_ms": 0, "last_lap_time_ms": 100000,
                    "lap_distance_m": float(distance % 5000), "total_distance_m": float(distance),
                    "pit_status_id": 0, "result_status_id": 2, "current_lap_invalid_id": 0,
                    "delta_to_car_in_front_ms": 8000, "delta_to_race_leader_ms": (8000, 0, 16000)[car]}})
    return {"schema_version": 1, "status": "available", "generation": "1" * 32,
            "session_id": "2" * 32, "scope": {"session_uid": 99, "packet_format": 2025, "epoch": 0},
            "player_car_index": 0, "received_monotonic_ns": 100_000_000_000,
            "age_s": 0.05, "event_cursor": 0, "event_floor": 0, "events": [], "drivers": drivers,
            "context": {"status": "available", "value": {
                "session_uid": 99, "packet_format": 2025, "session_type": "race", "track_length_m": 5000}}}


def play_scenario(name, *, notify):
    if name not in SCENARIOS:
        raise ValueError("unknown radio scenario")
    automatic_starts = 0
    def record(event):
        nonlocal automatic_starts
        if event.get("event") == "radio_proactive_started":
            automatic_starts += 1
        notify({"scenario": name, **event})
    playback = ScenarioPlayback()
    radio = RadioScheduler(playback, notify=record)
    controller = RadioController(radio, notify=record)
    value = _snapshot()
    if name in ("mid-lap", "busy-radio"):
        value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = 5000
    now = 0.0
    controller.feed(value, now=now)
    if name == "quiet":
        radio.quiet()
    durations = (99000, 100000, 100300, 100700) if name == "pace" else (100000,) * (4 if name == "quiet" else 3)
    if name in ("mid-lap", "busy-radio"):
        durations = ()
    try:
        if name in ("mid-lap", "busy-radio"):
            for index in range(1, 35):
                now = playback.now = float(index)
                value["received_monotonic_ns"] += 1_000_000_000
                value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = 5000 - index * 30
                if name == "busy-radio" and index == 29:
                    playback.busy_until = 32
                    radio.submit(RadioMessage("answer:scenario", radio.scope, "Your last lap comparison.",
                                              "answer", now + 10), now=now)
                    radio.tick(now=now)
                controller.feed(value, now=now)
                controller.poll_proactive(now=now)
                radio.tick(now=now)
        for index, duration in enumerate(durations):
            number = index + 3
            gap = 8000 if name == "pace" else 4000 - index * 400
            value["drivers"][2]["lap"]["delta_to_car_in_front_ms"] = gap
            for elapsed in range(1000, duration, 1000):
                now += 1
                value["received_monotonic_ns"] += 1_000_000_000
                progress = 20 + 4980 * elapsed / duration
                for row, offset in zip(value["drivers"], (0, 100, -100)):
                    distance = (number - 1) * 5000 + progress + offset
                    row["lap"].update(current_lap_number=int(distance // 5000) + 1,
                                      total_distance_m=distance, lap_distance_m=distance % 5000,
                                      current_lap_time_ms=elapsed)
                controller.feed(value, now=now)
                controller.poll_proactive(now=now)
                radio.tick(now=now)
            now += 1
            value["received_monotonic_ns"] += 1_000_000_000
            for row, offset in zip(value["drivers"], (0, 100, -100)):
                distance = number * 5000 + 20 + offset
                row["lap"].update(current_lap_number=int(distance // 5000) + 1,
                                  total_distance_m=float(distance), lap_distance_m=float(distance % 5000),
                                  current_lap_time_ms=0, last_lap_time_ms=duration)
            value["event_cursor"] += 1
            value["events"] = [{"id": value["event_cursor"], "scope": deepcopy(value["scope"]),
                                "driver": deepcopy(value["drivers"][0]["identity"]),
                                "kind": "lap_completed", "qualification": "observed_lap_transition",
                                "lap_number": number, "lap_time_ms": duration, "previous_observed_invalid": 0,
                                "received_monotonic_ns": value["received_monotonic_ns"]}]
            controller.feed(value, now=now)
            if name == "session-change" and index == 2:
                value["scope"]["epoch"] += 1
                value["events"] = []
                controller.feed(value, now=now)
            radio.tick(now=now)
            if name == "quiet" and index == 2:
                radio.muted = False
        expected = 1 if name in ("closing", "pace", "mid-lap", "busy-radio") else 0
        success = automatic_starts == expected
        notify({"event": "radio_scenario_complete", "scenario": name,
                "automatic_starts": automatic_starts, "expected_automatic_starts": expected,
                "success": success, "audio": False, "backend": False, "model": False})
        return success
    finally:
        radio.close()


def run_scenarios(args):
    with radio_event_log(args.log_file):
        selected = SCENARIOS if args.scenario == "all" else (args.scenario,)
        outcomes = [play_scenario(name, notify=emit) for name in selected]
    return 0 if all(outcomes) else 2
