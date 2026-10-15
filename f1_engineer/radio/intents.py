from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from ..driver_names import driver_name_key
from .questions import METRICS


INTENT_VERSION = "radio-intent-v1"
MAX_TRANSCRIPT_BYTES = 1024
MAX_CONTEXT_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 4096

ACTIONS = ("timing_question", "control", "memory", "task_list", "task_cancel_all", "task_cancel",
           "task_extend", "task_create", "task_update", "region_reply", "smalltalk", "unsupported", "unclear")
TASK_KINDS = ("lap_report", "consistency", "target_pace", "watch", "region", "paired_region", "focus")
CONTROLS = ("quiet", "resume", "cancel_speech", "repeat")
MEMORIES = ("last_request", "last_report")
TOPICS = ("strategy", "tyre_life", "fuel", "setup", "driving_advice", "other")
TASK_ONLY_METRICS = frozenset({"consistency", "target_pace", "race_watch"})
TIMING_METRICS = tuple(metric for metric in METRICS if metric not in TASK_ONLY_METRICS)
BASE_TARGETS = ("self", "leader", "ahead", "behind")
FIELDS = ("action", "heard_as", "corrections", "targets", "metrics", "lap_number", "laps", "task_id",
          "task_kind", "tolerance_ms", "region_start_m", "region_end_m", "region_name",
          "compare_previous_region", "opponent", "control", "memory", "unsupported_topic")

# Exact phrases the radio acts on locally, instantly and without a network round trip.
LOCAL_CONTROLS = {
    "quiet": "quiet", "quiet radio": "quiet", "pause coaching": "quiet",
    "resume coaching": "resume", "resume radio": "resume",
    "cancel speech": "cancel", "stop speaking": "cancel",
    "repeat that": "repeat", "repeat message": "repeat",
}
CONTROL_PHRASES = {"quiet": "quiet radio", "resume": "resume radio",
                   "cancel_speech": "cancel speech", "repeat": "repeat that"}
MEMORY_PHRASES = {"last_request": "what was my last request", "last_report": "what did you last report"}
REPLIES = {
    "smalltalk": "Copy.",
    "unclear": "Didn't catch that. Say again.",
    "strategy": "I can't call race strategy. I can report timing, gaps and tracking.",
    "tyre_life": "I can't predict tyre life. I can report current tyre age and compound.",
    "fuel": "Fuel isn't available in this feed.",
    "setup": "I can't advise on car setup from this feed.",
    "driving_advice": "I can't give driving advice yet. I can track braking and minimum speed at a corner.",
    "other": "I can't help with that one.",
}
METRIC_PHRASES = {
    "last_sector1": "last lap sector 1 time", "last_sector2": "last lap sector 2 time",
    "last_sector3": "last lap sector 3 time", "current_sector1": "current lap sector 1 time",
    "current_sector2": "current lap sector 2 time", "observed_best": "best observed lap time",
    "session_best": "session best lap time", "fastest_sector": "fastest sector time",
    "fuel_level": "fuel level", "tyre_age": "tyre age", "tyre_compound": "tyre compound",
    "speed": "speed", "brake_input": "brake input", "leader_gap_change": "gap change to the leader",
    "front_gap_change": "gap change to the car ahead", "lap_comparison": "last lap comparison",
    "last_lap": "last lap time", "current_lap": "current lap time", "position": "position",
    "leader_gap": "gap to the leader", "front_gap": "gap to the car ahead",
    "track_name": "track name", "weather": "weather",
}
TARGET_PHRASES = {"self": "my", "leader": "the leader's", "ahead": "the car ahead's", "behind": "the car behind's"}
_SAFE_NAME = re.compile(r"[^\W_](?:[^\W_]|[ '\-.]){0,79}")


@dataclass(frozen=True, slots=True)
class Rendered:
    kind: str  # "request": text for the existing request handler; "reply": speak text directly
    text: str


def _fail(reason="radio_intent_malformed"):
    raise ValueError(reason)


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 99_999


def safe_name(value) -> bool:
    return (isinstance(value, str) and len(value.encode("utf-8")) <= 80
            and _SAFE_NAME.fullmatch(value) is not None)


def safe_region(value) -> bool:
    return safe_name(value) and re.search(r"\b(?:with|for)\b", value.casefold()) is None


def _text(value, limit):
    return isinstance(value, str) and len(value.encode("utf-8", errors="strict")) <= limit


def _target(value):
    if value in BASE_TARGETS:
        return True
    if isinstance(value, str) and value.startswith("position:"):
        return re.fullmatch(r"position:(?:[1-9]|1[0-9]|2[0-4])", value) is not None
    return isinstance(value, str) and value.startswith("name:") and safe_name(value[5:])


def parse_intent(value, *, context=None) -> dict:
    """Validate one interpreted radio request; with a context, also pin names and task numbers to it."""
    if isinstance(value, (str, bytes)):
        try:
            if len(value) > MAX_OUTPUT_BYTES:
                _fail("radio_intent_limit_exceeded")
            value = json.loads(value)
        except (ValueError, RecursionError, OverflowError):
            _fail()
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        _fail()
    action = value["action"]
    if action not in ACTIONS:
        _fail("radio_intent_choice_unsupported")
    try:
        heard_ok = _text(value["heard_as"], 1024)
    except UnicodeError:
        heard_ok = False
    corrections = value["corrections"]
    if (not heard_ok or not isinstance(corrections, list) or len(corrections) > 6
            or any(not isinstance(item, dict) or set(item) != {"heard", "meant"}
                   or not _text(item["heard"], 120) or not _text(item["meant"], 120) for item in corrections)):
        _fail()
    targets, metrics = value["targets"], value["metrics"]
    if (not isinstance(targets, list) or len(targets) > 3 or not all(_target(item) for item in targets)
            or not isinstance(metrics, list) or len(metrics) > 3
            or not all(item in TIMING_METRICS for item in metrics)):
        _fail("radio_intent_choice_unsupported")
    optional = (("lap_number", 1, 255), ("laps", 1, 20), ("task_id", 1, 999_999_999), ("tolerance_ms", 1, 5000))
    if any(value[key] is not None and not _integer(value[key], low, high) for key, low, high in optional):
        _fail("radio_intent_choice_unsupported")
    if (value["task_kind"] is not None and value["task_kind"] not in TASK_KINDS
            or value["control"] is not None and value["control"] not in CONTROLS
            or value["memory"] is not None and value["memory"] not in MEMORIES
            or value["unsupported_topic"] is not None and value["unsupported_topic"] not in TOPICS):
        _fail("radio_intent_choice_unsupported")
    start, end = value["region_start_m"], value["region_end_m"]
    if (start is None) != (end is None) or any(item is not None and not _number(item) for item in (start, end)):
        _fail("radio_intent_choice_unsupported")
    if value["region_name"] is not None and not safe_region(value["region_name"]):
        _fail("radio_intent_choice_unsupported")
    if type(value["compare_previous_region"]) is not bool:
        _fail()
    if value["opponent"] is not None and not safe_name(value["opponent"]):
        _fail("radio_intent_choice_unsupported")
    region = start is not None or value["region_name"] is not None
    kind, opponent = value["task_kind"], value["opponent"]
    required = {
        "timing_question": bool(targets) and bool(metrics or value["lap_number"]),
        "control": value["control"] is not None,
        "memory": value["memory"] is not None,
        "task_extend": value["laps"] is not None,
        "region_reply": region,
        "unsupported": value["unsupported_topic"] is not None,
        "task_create": kind is not None,
        "task_update": kind is not None and value["task_id"] is not None,
    }.get(action, True)
    if action in ("task_create", "task_update") and required:
        required = {
            "lap_report": bool(targets) and bool(metrics or value["lap_number"]),
            "target_pace": value["tolerance_ms"] is not None,
            "watch": opponent is not None,
            "region": region,
            "paired_region": opponent is not None,
        }.get(kind, True)
    if not required:
        _fail("radio_intent_incomplete")
    if context is not None:
        names = {driver_name_key(driver["name"]) for driver in context["drivers"]}
        mentioned = [item[5:] for item in targets if item.startswith("name:")] + ([opponent] if opponent else [])
        if any(driver_name_key(name) not in names for name in mentioned):
            _fail("radio_intent_choice_unsupported")
        active = {task["task_id"] for task in context["tasks"]}
        if value["task_id"] is not None and action in ("task_cancel", "task_extend", "task_update") \
                and value["task_id"] not in active:
            _fail("radio_intent_choice_unsupported")
    return {key: (list(value[key]) if isinstance(value[key], list) else value[key]) for key in FIELDS}


def parse_context(value) -> dict:
    """Validate the context the radio sends with a transcript; every list is bounded."""
    if not isinstance(value, dict) or set(value) != {"drivers", "track", "regions", "tasks", "recent",
                                                     "pending_region", "glossary"}:
        _fail("radio_context_invalid")
    drivers, regions, tasks, recent = value["drivers"], value["regions"], value["tasks"], value["recent"]
    glossary, pending, track = value["glossary"], value["pending_region"], value["track"]
    if (not isinstance(drivers, list) or len(drivers) > 24 or not isinstance(regions, list) or len(regions) > 64
            or not isinstance(tasks, list) or len(tasks) > 8 or not isinstance(recent, list) or len(recent) > 3
            or not isinstance(glossary, dict) or set(glossary) != {"terms", "mishearings"}
            or track is not None and not _text(track, 80)):
        _fail("radio_context_invalid")
    for driver in drivers:
        if (not isinstance(driver, dict) or set(driver) != {"name", "race_number", "position", "role"}
                or not safe_name(driver["name"])
                or driver["race_number"] is not None and not _integer(driver["race_number"], 0, 255)
                or driver["position"] is not None and not _integer(driver["position"], 1, 24)
                or driver["role"] not in ("player", "leader", "ahead", "behind", "other")):
            _fail("radio_context_invalid")
    if not all(safe_region(label) for label in regions):
        _fail("radio_context_invalid")
    for task in tasks:
        if (not isinstance(task, dict) or set(task) != {"task_id", "summary"}
                or not _integer(task["task_id"], 1, 999_999_999) or not _text(task["summary"], 240)):
            _fail("radio_context_invalid")
    for turn in recent:
        if (not isinstance(turn, dict) or set(turn) != {"heard", "reply"}
                or not _text(turn["heard"], 240) or not _text(turn["reply"], 240)):
            _fail("radio_context_invalid")
    if pending is not None and (not isinstance(pending, dict) or set(pending) != {"opponent", "laps", "focus"}
                                or pending["opponent"] is not None and not safe_name(pending["opponent"])
                                or not _integer(pending["laps"], 1, 20) or type(pending["focus"]) is not bool):
        _fail("radio_context_invalid")
    for key in ("terms", "mishearings"):
        entries = glossary[key]
        if (not isinstance(entries, dict) or len(entries) > 64
                or any(not _text(heard, 64) or not _text(meant, 120) for heard, meant in entries.items())):
            _fail("radio_context_invalid")
    return json.loads(json.dumps(value))


def intent_schema(context: dict) -> dict:
    """Closed JSON Schema for one request; driver names are limited to the live roster."""
    names = [f"name:{driver['name']}" for driver in context["drivers"]]
    positions = [f"position:{number}" for number in range(1, 25)]
    nullable = lambda kind, values=None: ({"type": [kind, "null"], "enum": [*values, None]} if values
                                          else {"type": [kind, "null"]})
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "heard_as": {"type": "string"},
            "corrections": {"type": "array", "items": {
                "type": "object", "properties": {"heard": {"type": "string"}, "meant": {"type": "string"}},
                "required": ["heard", "meant"], "additionalProperties": False}},
            "targets": {"type": "array", "items": {"type": "string", "enum": [*BASE_TARGETS, *positions, *names]}},
            "metrics": {"type": "array", "items": {"type": "string", "enum": list(TIMING_METRICS)}},
            "lap_number": nullable("integer"),
            "laps": nullable("integer"),
            "task_id": nullable("integer", [task["task_id"] for task in context["tasks"]]),
            "task_kind": nullable("string", TASK_KINDS),
            "tolerance_ms": nullable("integer"),
            "region_start_m": nullable("number"),
            "region_end_m": nullable("number"),
            "region_name": nullable("string"),
            "compare_previous_region": {"type": "boolean"},
            "opponent": nullable("string", [driver["name"] for driver in context["drivers"]]),
            "control": nullable("string", CONTROLS),
            "memory": nullable("string", MEMORIES),
            "unsupported_topic": nullable("string", TOPICS),
        },
        "required": list(FIELDS),
        "additionalProperties": False,
    }


def _metres(value) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _question(intent) -> str:
    targets = []
    for target in intent["targets"]:
        if target.startswith("name:"):
            targets.append(f'"{driver_name_key(target[5:])}"')
        elif target.startswith("position:"):
            targets.append(f"p{target[9:]}'s")
        else:
            targets.append(TARGET_PHRASES[target])
    facts = [METRIC_PHRASES[metric] for metric in intent["metrics"]]
    if intent["lap_number"] is not None:
        facts.insert(0, f"lap {intent['lap_number']} time")
    return " and ".join(targets) + " " + " and ".join(facts)


def _task(intent) -> str:
    kind, laps = intent["task_kind"], intent["laps"]
    duration = f" for the next {laps} laps" if laps is not None else ""
    opponent = driver_name_key(intent["opponent"]) if intent["opponent"] is not None else None
    start, end = intent["region_start_m"], intent["region_end_m"]
    window = f"from {_metres(start)} to {_metres(end)} metres" if start is not None else None
    name = intent["region_name"]
    count = f" for the next {laps or 1} laps"
    if kind == "lap_report":
        question = _question(intent)
        if laps == 1:
            return f"tell me at the end of next lap {question}"
        return f"tell me at the end of each lap {question}{duration}"
    if kind == "consistency":
        return "track my consistency" + duration
    if kind == "target_pace":
        ms = intent["tolerance_ms"]
        return f"help me stay within {ms // 1000}.{ms % 1000:03d} seconds of my best valid lap" + duration
    if kind == "watch":
        return f'watch "{opponent}"' + duration
    if kind == "focus":
        if window is not None:
            return f"coach my region {window}" + count
        if name is not None:
            return f"coach my region at {name.casefold()}" + count
        return "coach my worst corner" + duration
    reference = "my previous observed region" if intent["compare_previous_region"] else opponent
    if kind == "paired_region" and window is None and name is None:
        return f"track {opponent}'s braking points and minimum speed compared to me" + count
    if window is not None:
        if reference is None:
            return f"track my braking and minimum speed {window}" + count
        return f"compare my braking and minimum speed {window} with {reference}" + count
    if reference is None:
        return f"track my braking and minimum speed at {name.casefold()}" + count
    return f"compare my braking and minimum speed at {name.casefold()} with {reference}" + count


def render_intent(intent: dict) -> Rendered:
    """Turn a validated intent into text the existing parsers and controller already handle."""
    action = intent["action"]
    if action == "timing_question":
        return Rendered("request", _question(intent))
    if action == "control":
        return Rendered("request", CONTROL_PHRASES[intent["control"]])
    if action == "memory":
        return Rendered("request", MEMORY_PHRASES[intent["memory"]])
    if action == "task_list":
        return Rendered("request", "list tasks")
    if action == "task_cancel_all":
        return Rendered("request", "cancel all tracking")
    if action == "task_cancel":
        task_id = intent["task_id"]
        return Rendered("request", f"cancel task {task_id}" if task_id is not None else "cancel that")
    if action == "task_extend":
        task_id, laps = intent["task_id"], intent["laps"]
        return Rendered("request", f"extend task {task_id} by {laps} laps" if task_id is not None
                        else f"track that for another {laps} laps")
    if action in ("task_create", "task_update"):
        text = _task(intent)
        if action == "task_update":
            text = f"update task {intent['task_id']} {text}"
        return Rendered("request", text)
    if action == "region_reply":
        start, end = intent["region_start_m"], intent["region_end_m"]
        if start is not None:
            return Rendered("request", f"use {_metres(start)} to {_metres(end)} metres")
        return Rendered("request", f"use {intent['region_name'].casefold()}")
    if action == "unsupported":
        return Rendered("reply", REPLIES[intent["unsupported_topic"]])
    return Rendered("reply", REPLIES["smalltalk" if action == "smalltalk" else "unclear"])


def summarize(intent: dict) -> dict:
    """Compact, loggable view of an interpreted request."""
    keys = ("action", "heard_as", "corrections", "targets", "metrics", "task_kind", "control", "memory",
            "unsupported_topic")
    return {key: intent[key] for key in keys if intent.get(key) not in (None, [], False)}
