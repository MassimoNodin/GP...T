from __future__ import annotations

import re
from dataclasses import dataclass

from ..driver_names import driver_name_key

@dataclass(frozen=True, slots=True)
class QuestionPlan:
    targets: tuple[str, ...]
    metrics: tuple[str, ...]
    inherited_targets: bool = False


METRICS = {
    "consistency": r"consistency",
    "target_pace": r"target pace",
    "race_watch": r"catch or defend watch",
    "leader_gap_change": r"gap change to (?:the )?leader|(?:gap|delta|time) to (?:the )?leader change that lap|delta that lap",
    "front_gap_change": r"gap change to (?:the )?car (?:in front|ahead)|(?:gap|delta|time) to (?:the )?car (?:in front|ahead) change that lap",
    "lap_comparison": r"(?:last|previous|latest)(?: completed)? lap comparisons?",
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


def plan_question(question: str, previous: QuestionPlan | None = None, *, names: tuple[str, ...] = ()) -> QuestionPlan:
    if not isinstance(question, str) or not 1 <= len(question.encode("utf-8")) <= 1024:
        raise ValueError("Ask a question of at most 1024 bytes.")
    remaining = driver_name_key(question)
    comparison = re.fullmatch(r"compare my (?:last|previous|latest)(?: completed)? lap(?: time)? with (.+?)[?.!]*", remaining)
    if comparison:
        remaining = comparison.group(1) + " last lap comparison"
    if "__named_" in remaining:
        raise ValueError("Please specify a supported driver name or timing fact.")
    named_targets = []
    reserved = set("what is are the a and also please tell show give for of about how their his her them him then my me mine myself lap last current quiet resume cancel repeat status quit submit forget ask listen finish".split())
    reserved_patterns = "|".join((*TARGETS.values(), *METRICS.values(), r"p(?:[1-9]|1[0-9]|2[0-4])"))
    name_patterns = []
    known_names = {driver_name_key(name) for name in names}
    for name in sorted(known_names, key=lambda value: (-len(value), value)):
        escaped = re.escape(name)
        name_patterns.append(f'"{escaped}"' + r"(?:'s)?")
        if (not any(word in reserved for word in name.split()) and not name.isdecimal()
                and re.fullmatch(reserved_patterns, name) is None):
            name_patterns.append(escaped + r"(?:'s)?")
    def named_target(match):
        text = match.group()
        alias = text[1:text.rfind('\"')] if text.startswith('\"') else text
        if alias not in known_names:
            alias = alias.removesuffix("'s")
        named_targets.append("name:" + alias)
        return f" __named_{len(named_targets) - 1}__ "
    if name_patterns:
        remaining = re.sub(r"(?<!\w)(?:" + "|".join(name_patterns) + r")(?!\w)", named_target, remaining)
    remaining = re.sub(r"\b(?:tell|show|give) me\b", " ", remaining)
    metrics = []
    targets = []
    for metric, pattern in METRICS.items():
        remaining, count = re.subn(r"\b(?:" + pattern + r")\b", " ", remaining)
        if count:
            metrics.append(metric)
    patterns = {target: TARGETS[target] for target in ("ahead", "behind", "leader", "self")}
    patterns["position"] = r"p(?:[1-9]|1[0-9]|2[0-4])(?:'s)?"
    patterns["named"] = r"__named_[0-9]+__"
    def driver_target(match):
        target = match.lastgroup
        if target == "position":
            targets.append(f"position:{int(re.search(r'[0-9]+', match.group()).group())}")
        elif target == "named":
            targets.append(named_targets[int(re.search(r'[0-9]+', match.group()).group())])
        else:
            targets.append(target)
        return " "
    target_pattern = r"\b(?:" + "|".join(f"(?P<{target}>{pattern})" for target, pattern in patterns.items()) + r")\b"
    remaining = re.sub(target_pattern, driver_target, remaining)
    if comparison is not None and not targets:
        raise ValueError("Please specify a driver to compare with your last lap.")
    if re.search(r"\b(?:their|his|her|them|him)\b", remaining) and (previous is None or targets):
        raise ValueError("Please specify a driver; that pronoun has no single supported reference.")
    remaining = re.sub(r"\b(?:what|what's|is|are|the|a|and|also|please|tell|show|give|for|of|about|how|their|his|her|them|him|then)\b", " ", remaining)
    if re.sub(r"[\s?,.!']", "", remaining):
        raise ValueError("I can answer current or last lap times, last lap comparisons to you, positions, leader/front-car gaps and your lap-boundary gap changes. Use me, leader, car ahead, car behind, P1 to P24, or an available driver's full name or surname. Other facts and task instructions are not supported yet.")
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


def lap_comparison(lap: dict, player_lap: dict | None) -> str:
    unavailable = "last lap comparison unavailable"
    if not isinstance(player_lap, dict):
        return unavailable + "; player reference missing"
    number, player_number = lap.get("current_lap_number"), player_lap.get("current_lap_number")
    if not all(type(value) is int and 2 <= value <= 255 for value in (number, player_number)):
        return unavailable + "; completed lap number missing"
    if number != player_number:
        return unavailable + f"; driver last completed lap {number - 1}, your last completed lap {player_number - 1}"
    elapsed, player_elapsed = lap.get("last_lap_time_ms"), player_lap.get("last_lap_time_ms")
    if not all(type(value) is int and 1 <= value <= 2**32 - 1 for value in (elapsed, player_elapsed)):
        return unavailable + "; reported lap time missing"
    delta = elapsed - player_elapsed
    prefix = f"reported lap {number - 1} "
    if delta == 0:
        return prefix + "matched your time"
    direction = "slower" if delta > 0 else "faster"
    return prefix + f"{abs(delta) / 1000:.3f} seconds {direction} than you"


def timing_fact(metric: str, lap: dict, player_lap: dict | None = None) -> str:
    if metric == "lap_comparison":
        return lap_comparison(lap, player_lap)
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
