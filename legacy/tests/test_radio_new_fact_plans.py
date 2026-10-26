from copy import deepcopy

import pytest

from f1_engineer.radio.questions import QuestionPlan, plan_question
from f1_engineer.radio.reported_history import qualified_history, reported_lap_sector_fact


@pytest.mark.parametrize("phrase,metric", [
    ("my lap count", "lap_count"),
    ("what's my current lap count?", "lap_count"),
    ("what lap am I on?", "lap_count"),
    ("laps remaining", "laps_remaining"),
    ("my laps left", "laps_remaining"),
    ("how many laps are left?", "laps_remaining"),
    ("how many laps remaining?", "laps_remaining"),
    ("how many laps to go?", "laps_remaining"),
    ("my best lap", "session_best"),
    ("my fastest lap time", "session_best"),
    ("my session fastest valid lap", "session_best"),
    ("my PB", "session_best"),
    ("my best observed lap", "observed_best"),
    ("my best observed valid lap time", "observed_best"),
    ("my current lap time", "current_lap"),
])
def test_new_fact_plans(phrase, metric):
    assert plan_question(phrase) == QuestionPlan(("self",), (metric,))


@pytest.mark.parametrize("number", ["9", "nine", "255"])
@pytest.mark.parametrize("sector", ["1", "two", "three"])
def test_named_lap_sector_is_one_fact(number, sector):
    expected_number = 9 if number == "nine" else int(number)
    expected_sector = {"two": 2, "three": 3}.get(sector, sector)
    assert plan_question(f"my lap {number} sector {sector} time").metrics == (
        f"lap_number:{expected_number}:sector:{expected_sector}",)


@pytest.mark.parametrize("phrase", [
    "my lap 256 sector 2 time", "my lap 0 sector 2 time",
    "my lap 9 sector 0 time", "my lap 9 sector 4 time",
    "my lap 9 sector 20 time", "my lap 9 sector two in traffic",
])
def test_named_lap_sector_rejects_unsupported_numbers_and_filters(phrase):
    with pytest.raises(ValueError):
        plan_question(phrase)


def test_named_lap_time_is_not_added_unless_requested():
    assert plan_question("my lap 9 sector 2 time and lap 9 time").metrics == (
        "lap_number:9:sector:2", "lap_number:9")
    assert plan_question("my and leader lap nine sector two").targets == ("self", "leader")
    assert plan_question("my lap nine time").metrics == ("lap_number:9",)


@pytest.mark.parametrize("direction", ["ahead", "behind"])
def test_canonical_neighbour_facts_do_not_consume_driver_targets(direction):
    plan = plan_question(f"Leclerc neighbour {direction} and last lap", names=("Leclerc",))
    assert plan == QuestionPlan(("name:leclerc",), (f"car_{direction}", "last_lap"))
    assert plan_question(f"Leclerc car {direction}", names=("Leclerc",)) == QuestionPlan(
        ("name:leclerc",), (f"car_{direction}",))


def test_new_facts_inherit_targets_and_leave_observed_best_distinct():
    prior = plan_question("leader position")
    assert plan_question("and PB?", prior) == QuestionPlan(("leader",), ("session_best",), True)
    assert plan_question("my PB and best observed lap").metrics == ("observed_best", "session_best")


@pytest.fixture
def reported_sample():
    frame = {"session_uid": 99, "packet_format": 2025, "epoch": 2,
             "frame_ordinal": 20, "lap_received_monotonic_ns": 20_000_000_000,
             "session_time_s": 20.0}
    identity = {"car_index": 0, "tenure_ordinal": 1}
    source = {"identity": deepcopy(identity),
              "scope": {key: frame[key] for key in ("session_uid", "packet_format", "epoch")},
              "frame_ordinal": 19, "frame_identifier": 19,
              "received_monotonic_ns": 19_000_000_000, "captured_at_ns": 19_000_000_000,
              "source_sequence": 19, "session_time_s": 19.0}
    row = {"identity": identity,
           "lap": {"current_lap_number": 11, "sector1_time_ms": 1000, "sector2_time_ms": 2000},
           "reported_history": {"status": "available", "qualification": "game_reported_scalar_timing",
                                "coaching_eligible": False, "source": source,
                                "first_attributable_lap": 9, "reported_lap_count": 10,
                                "laps": [
                                    {"lap_number": 9, "lap_time_ms": 100_000,
                                     "sector_times_ms": [30_000, 35_000, 35_000], "validity_flags": 15},
                                    {"lap_number": 10, "lap_time_ms": 90_000,
                                     "sector_times_ms": [29_000, 30_000, 31_000], "validity_flags": 15},
                                ]}}
    assert qualified_history(row, frame) is not None
    return row, frame


@pytest.mark.parametrize("sector,elapsed", [(1, "30.000"), (2, "35.000"), (3, "35.000")])
def test_reported_named_sector_uses_only_selected_lap(reported_sample, sector, elapsed):
    row, frame = reported_sample
    original = deepcopy(row)
    text = reported_lap_sector_fact(row, frame, 9, sector)
    assert f"game-reported valid lap 9 sector {sector} {elapsed} seconds" in text
    assert "published timing, not a fully observed coaching reference" in text
    assert row == original


@pytest.mark.parametrize("number,sector", [(True, 2), (9.0, 2), ("9", 2), (0, 2), (256, 2),
                                          (9, True), (9, 2.0), (9, "2"), (9, 0), (9, 4)])
def test_reported_named_sector_guards_argument_types(reported_sample, number, sector):
    assert reported_lap_sector_fact(*reported_sample, number, sector) is None


@pytest.mark.parametrize("flags", [None, True, "15", 15.0, -1, 256, 31, 255])
def test_reported_named_sector_requires_known_validity_flags(reported_sample, flags):
    row, frame = reported_sample
    row["reported_history"]["laps"][0]["validity_flags"] = flags
    text = reported_lap_sector_fact(row, frame, 9, 2)
    assert text is None or "unknown validity flags" in text
    assert text is None or "seconds" not in text


@pytest.mark.parametrize("sector", [1, 2, 3])
def test_reported_named_sector_requires_its_own_validity_bit(reported_sample, sector):
    row, frame = reported_sample
    row["reported_history"]["laps"][0]["validity_flags"] = 15 & ~(1 << sector)
    text = reported_lap_sector_fact(row, frame, 9, sector)
    assert "sector is invalid" in text and "seconds" not in text


def test_valid_sector_on_invalid_lap_discloses_lap_invalidity(reported_sample):
    row, frame = reported_sample
    row["reported_history"]["laps"][0]["validity_flags"] = 14
    assert "game-reported invalid lap 9 sector 2 35.000 seconds" in reported_lap_sector_fact(row, frame, 9, 2)


@pytest.mark.parametrize("timings", [None, {}, "30000,35000,35000", [], [30_000, 35_000],
                                    [30_000, 35_000, 35_000, 1], (30_000, 35_000, 35_000),
                                    [30_000, None, 35_000], [30_000, True, 35_000],
                                    [30_000, "35000", 35_000], [30_000, 35_000.0, 35_000],
                                    [30_000, 0, 35_000], [30_000, -1, 35_000],
                                    [30_000, 86_400_001, 35_000]])
def test_missing_or_bad_selected_sector_never_borrows_newer_timing(reported_sample, timings):
    row, frame = reported_sample
    row["reported_history"]["laps"][0]["sector_times_ms"] = timings
    text = reported_lap_sector_fact(row, frame, 9, 2)
    assert "reported sector time missing" in text and "seconds" not in text


def test_selected_sector_does_not_require_other_sector_times(reported_sample):
    row, frame = reported_sample
    row["reported_history"]["laps"][0]["sector_times_ms"] = [None, 35_000, None]
    assert "35.000 seconds" in reported_lap_sector_fact(row, frame, 9, 2)
    assert "reported sector time missing" in reported_lap_sector_fact(row, frame, 9, 3)


@pytest.mark.parametrize("fault", ["row", "frame", "identity", "scope", "future", "coaching", "missing_lap"])
def test_named_sector_requires_qualified_exact_lap_history(reported_sample, fault):
    row, frame = reported_sample
    history = row["reported_history"]
    if fault == "row":
        row = None
    elif fault == "frame":
        frame = None
    elif fault == "identity":
        history["source"]["identity"]["tenure_ordinal"] += 1
    elif fault == "scope":
        history["source"]["scope"]["epoch"] += 1
    elif fault == "future":
        history["source"]["frame_ordinal"] = 21
    elif fault == "coaching":
        history["coaching_eligible"] = True
    else:
        history["laps"].pop(0)
    assert reported_lap_sector_fact(row, frame, 9, 2) is None

