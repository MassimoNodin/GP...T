from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QuestionPlan:
    targets: tuple[str, ...]
    metrics: tuple[str, ...]
    inherited_targets: bool = False


METRICS = {
    "last_lap": r"(?:last|previous|latest)(?: completed)? lap(?: time)?s?",
    "current_lap": r"current lap(?: time)?s?",
    "position": r"(?:race )?positions?",
    "leader_gap": r"(?:gap|delta|time) to (?:the )?leader",
    "front_gap": r"(?:gap|delta|time) to (?:the )?car (?:in front|ahead)",
}
TARGETS = {
    "self": r"my|me|mine|myself",
    "leader": r"(?:the )?leader(?:'s)?",
    "ahead": r"(?:the )?car (?:ahead|in front)(?: of me)?(?:'s)?",
    "behind": r"(?:the )?car behind(?: me)?(?:'s)?",
}


def plan_question(question: str, previous: QuestionPlan | None = None) -> QuestionPlan:
    if not isinstance(question, str) or not 1 <= len(question.encode("utf-8")) <= 1024:
        raise ValueError("Ask a question of at most 1024 bytes.")
    remaining = question.lower().strip().replace("’", "'")
    remaining = re.sub(r"\b(?:tell|show|give) me\b", " ", remaining)
    metrics = []
    targets = []
    for metric, pattern in METRICS.items():
        remaining, count = re.subn(r"\b(?:" + pattern + r")\b", " ", remaining)
        if count:
            metrics.append(metric)
    patterns = {target: TARGETS[target] for target in ("ahead", "behind", "leader", "self")}
    patterns["position"] = r"p(?:[1-9]|1[0-9]|2[0-4])(?:'s)?"
    def driver_target(match):
        target = match.lastgroup
        if target == "position":
            targets.append(f"position:{int(re.search(r'[0-9]+', match.group()).group())}")
        else:
            targets.append(target)
        return " "
    target_pattern = r"\b(?:" + "|".join(f"(?P<{target}>{pattern})" for target, pattern in patterns.items()) + r")\b"
    remaining = re.sub(target_pattern, driver_target, remaining)
    if re.search(r"\b(?:their|his|her|them|him)\b", remaining) and (previous is None or targets):
        raise ValueError("Please specify a driver; that pronoun has no single supported reference.")
    remaining = re.sub(r"\b(?:what|what's|is|are|the|a|and|also|please|tell|show|give|for|of|about|how|their|his|her|them|him|then)\b", " ", remaining)
    if re.sub(r"[\s?,.!']", "", remaining):
        raise ValueError("I can answer current or last lap times, positions, and gaps to the leader or car ahead. Use me, leader, car ahead, car behind, or P1 to P24. Named drivers and task instructions are not supported yet.")
    if not metrics and not targets:
        raise ValueError("Please specify a driver or timing fact.")
    inherited_targets = not targets and previous is not None
    if not metrics:
        if previous is None:
            raise ValueError("Which timing fact would you like?")
        metrics = list(previous.metrics)
    if not targets:
        targets = list(previous.targets) if previous else ["self"]
    targets = list(dict.fromkeys(targets))
    if len(targets) > 3 or len(metrics) > 3:
        raise ValueError("Please request at most three drivers and three facts at once.")
    return QuestionPlan(tuple(targets), tuple(metrics), inherited_targets)


def timing_fact(metric: str, lap: dict) -> str:
    labels = {"last_lap": "last lap", "current_lap": "current lap elapsed",
              "position": "position", "leader_gap": "gap to leader",
              "front_gap": "gap to car ahead"}
    fields = {"last_lap": "last_lap_time_ms", "current_lap": "current_lap_time_ms",
              "position": "car_position", "leader_gap": "delta_to_race_leader_ms",
              "front_gap": "delta_to_car_in_front_ms"}
    value = lap.get(fields[metric])
    number = lap.get("current_lap_number")
    position = lap.get("car_position")
    valid = type(value) is int and 0 <= value <= 2**32 - 1
    if metric in ("last_lap", "current_lap"):
        valid = valid and value > 0 and type(number) is int and 1 <= number <= 255
        if metric == "last_lap":
            valid = valid and number >= 2
    elif metric == "position":
        valid = valid and 1 <= value <= 24
    else:
        valid = valid and type(position) is int and 1 <= position <= 24
        if valid and position == 1:
            return "gap to leader 0.000 seconds" if metric == "leader_gap" else "gap to car ahead unavailable"
        valid = valid and value > 0
    if not valid:
        return f"{labels[metric]} unavailable"
    if metric == "position":
        return f"position {value}"
    if metric == "last_lap":
        return f"lap {number - 1} {value / 1000:.3f} seconds"
    return f"{labels[metric]} {value / 1000:.3f} seconds"
