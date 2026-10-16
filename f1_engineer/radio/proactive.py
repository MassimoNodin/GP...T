from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field

from .gaps import integer
from .watches import RaceWatchTracker


@dataclass(frozen=True, slots=True)
class GapNotice:
    role: str
    subject: str
    revision: int
    trend: str
    lap_number: int
    gap_ms: int
    change_ms: int
    near: bool
    boundaries: tuple[tuple[int, int], ...] = ()

    @property
    def rank(self):
        return (self.near, self.role == "behind" and self.trend == "closing", self.trend == "closing")

    @property
    def text(self):
        actions = {("ahead", "closing"): "You are catching the car ahead",
                   ("behind", "closing"): "The car behind is closing",
                   ("ahead", "opening"): "The car ahead is pulling away",
                   ("behind", "opening"): "You are pulling away from the car behind"}
        return (f"{actions[self.role, self.trend]}. Gap {self.gap_ms / 1000:.1f} seconds, "
                f"{'down' if self.change_ms < 0 else 'up'} {abs(self.change_ms) / 1000:.1f} "
                "over two laps.")

    def evidence(self):
        return {"role": self.role, "subject": self.subject, "trend": self.trend,
                "lap_number": self.lap_number, "gap_ms": self.gap_ms,
                "change_ms": self.change_ms, "near": self.near,
                "boundaries": self.boundaries}


@dataclass
class GapState:
    subject: str
    token: tuple
    sample: tuple[str, int, int]
    history: list[tuple[int, int]] = field(default_factory=list)
    trend: str | None = None
    neutral_boundaries: int = 0
    near: bool = False
    attention: bool = False
    revision: int = 0


class ProactiveGapTracker:
    def __init__(self):
        self._revision = 0
        self.clear()

    def clear(self):
        self.states = {}
        self.number = None
        self.received_at = None
        self.age_s = 0.0

    def observe(self, player, resolve, *, now, age_s, track_length_m, eligible):
        number = player["lap"].get("current_lap_number")
        if (not eligible or not integer(number, 1, 255)
                or self.received_at is not None and not 0 <= now - self.received_at < 3
                or self.number is not None and number - self.number not in (0, 1)):
            self.clear()
        if not eligible or not integer(number, 1, 255):
            return
        self.number, self.received_at, self.age_s = number, now, age_s
        for role in ("ahead", "behind"):
            row = resolve(role)
            sample, token, _reason = RaceWatchTracker.sample(
                player, row, resolve, track_length_m=track_length_m)
            distances = [participant["lap"].get("total_distance_m")
                         for participant in (player, row) if participant is not None]
            physical = (type(track_length_m) in (int, float) and math.isfinite(track_length_m)
                        and 0 < track_length_m <= 20_000 and len(distances) == 2
                        and all(type(distance) in (int, float) and math.isfinite(distance)
                                for distance in distances))
            if (sample is None or not physical or sample[0] != role
                    or not 0 < abs(distances[1] - distances[0]) < track_length_m
                    or (distances[1] > distances[0]) != (role == "ahead")):
                self.states.pop(role, None)
                continue
            subject = json.dumps(row["identity"], sort_keys=True, separators=(",", ":"))
            state = self.states.get(role)
            if (state is None or state.subject != subject or state.token != token
                    or sample[2] - state.sample[2] not in (0, 1)):
                self._revision += 1
                state = GapState(subject, token, sample, revision=self._revision)
                self.states[role] = state
            state.sample = sample

    def complete(self, number):
        notices = []
        if self.number != number + 1:
            self.clear()
            return notices
        for role, state in self.states.items():
            if role == "ahead" and state.sample[2] != self.number:
                state.history.clear()
                state.trend = None
                state.neutral_boundaries = 0
                state.near = False
                state.attention = False
                self._revision += 1
                state.revision = self._revision
                continue
            if state.history and state.history[-1][0] == number:
                continue
            if state.history and state.history[-1][0] != number - 1:
                state.history.clear()
                state.trend = None
                state.neutral_boundaries = 0
                state.near = False
                state.attention = False
            state.history.append((number, state.sample[1]))
            state.history = state.history[-3:]
            if len(state.history) < 3:
                continue
            changes = [state.history[index][1] - state.history[index - 1][1] for index in (1, 2)]
            total = sum(changes)
            trend = ("closing" if all(change <= -100 for change in changes) and total <= -300 else
                     "opening" if all(change >= 100 for change in changes) and total >= 300 else None)
            if trend is None:
                held = (state.trend == "closing" and total < -100 and max(changes) <= 100
                        or state.trend == "opening" and total > 100 and min(changes) >= -100)
                state.neutral_boundaries = 0 if held else state.neutral_boundaries + 1
                if state.neutral_boundaries >= 2:
                    state.trend = None
                    state.near = False
                    state.attention = False
                    self._revision += 1
                    state.revision = self._revision
                continue
            gap = state.sample[1]
            near = trend == "closing" and gap <= (2500 if state.near else 2000)
            attention = gap <= (11_000 if state.attention else 10_000)
            changed = (trend != state.trend or near and not state.near
                       or attention and not state.attention)
            state.trend, state.near, state.neutral_boundaries = trend, near, 0
            state.attention = attention
            self._revision += 1
            state.revision = self._revision
            if changed:
                notices.append(GapNotice(role, state.subject, state.revision, trend, number,
                                         gap, total, near, tuple(state.history)))
        return sorted(notices, key=lambda notice: notice.rank, reverse=True)

    def validate(self, notice, *, now):
        if (self.received_at is None or now < self.received_at
                or not 0 <= now - self.received_at + self.age_s < 3):
            return "observations_stale"
        state = self.states.get(notice.role)
        if (state is None or state.subject != notice.subject or state.revision != notice.revision
                or state.trend != notice.trend):
            return "development_superseded"
        if abs(state.sample[1] - notice.gap_ms) > 500:
            return "gap_changed"
        return None


class ProactiveSpeechPolicy:
    def __init__(self):
        self.clear()

    def clear(self):
        self.started = deque(maxlen=2)
        self.topics = {}

    def suppression(self, notice, *, now):
        if notice.gap_ms > 10_000:
            return "outside_attention_range"
        if self.started and now - self.started[-1] < 30:
            return "global_cooldown"
        if len(self.started) == 2 and now - self.started[0] < 120:
            return "chatter_budget"
        previous = self.topics.get(notice.role)
        if previous is not None and now - previous < 90:
            return "topic_cooldown"
        return None

    def record_started(self, notice, *, now):
        self.started.append(now)
        self.topics[notice.role] = now
