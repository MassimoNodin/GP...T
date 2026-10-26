from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from ..udp.models import PacketId
from ..udp.session_history import SessionHistoryDecoder


@dataclass(slots=True)
class _DriverHistory:
    identity: dict
    first_frame: int
    first_lap: int
    lap_number: int
    lap_time_ms: int
    completed_times: dict[int, int] = field(default_factory=dict)
    source: dict | None = None
    rows: tuple = ()
    best_lap: int = 0
    reason: str = "session_history_not_received"


class ReportedSessionHistory:
    def __init__(self) -> None:
        self._scope = None
        self._session_time = None
        self._drivers: dict[int, _DriverHistory] = {}
        self._decoder = SessionHistoryDecoder()

    def clear(self) -> None:
        self._scope = None
        self._session_time = None
        self._drivers.clear()

    def observe_live(self, current: dict) -> None:
        scope = (current["scope"], current["player_car_index"])
        if (self._scope != scope or self._session_time is not None
                and current["session_time_s"] < self._session_time):
            self.clear()
            self._scope = copy.deepcopy(scope)
        self._session_time = current["session_time_s"]
        present = set()
        for row in current["drivers"][:24]:
            car = row["car_index"]
            lap = row["lap"]
            number = lap["current_lap_number"]
            if type(number) is not int or not 1 <= number <= 255:
                continue
            present.add(car)
            prior = self._drivers.get(car)
            reset = (prior is None or prior.identity != row["identity"]
                     or number - prior.lap_number not in (0, 1)
                     or (number == prior.lap_number and lap["current_lap_time_ms"] < prior.lap_time_ms)
                     or (prior.source is not None
                         and current["session_time_s"] < prior.source["session_time_s"]
                         and current["frame_ordinal"] >= prior.source["frame_ordinal"]))
            if reset:
                at_start = lap["current_lap_time_ms"] == 0 and lap["lap_distance_m"] <= 0
                prior = _DriverHistory(copy.deepcopy(row["identity"]), current["frame_ordinal"],
                                       number if at_start else number + 1, number,
                                       lap["current_lap_time_ms"])
                self._drivers[car] = prior
            else:
                if number == prior.lap_number + 1 and lap["last_lap_time_ms"] > 0:
                    prior.completed_times[prior.lap_number] = lap["last_lap_time_ms"]
                    prior.completed_times = {number: timing for number, timing in prior.completed_times.items()
                                             if number <= 100}
                prior.lap_number = number
                prior.lap_time_ms = lap["current_lap_time_ms"]
        self._drivers = {car: value for car, value in self._drivers.items() if car in present}

    def consume(self, packets, *, frame_ordinal: int, epoch: int, assessable: bool,
                conflicted: bool = False) -> None:
        if not assessable or conflicted:
            self.clear()
            return
        if self._scope is None:
            return
        scope, player = self._scope
        candidates = {}
        for packet in packets:
            if packet.packet_kind is not PacketId.SESSION_HISTORY:
                continue
            if not packet.body:
                for state in self._drivers.values():
                    state.source = None
                    state.rows = ()
                    state.reason = "session_history_source_unqualified"
                return
            car = packet.body[0]
            if car not in self._drivers:
                continue
            candidates.setdefault(car, []).append(packet)
        for car, group in candidates.items():
            state = self._drivers[car]
            reason = None
            histories = []
            for packet in group:
                decoded = self._decoder.decode(packet, frame_ordinal=frame_ordinal)
                header = packet.header
                if (decoded.history is None or epoch != scope["epoch"]
                        or header.session_uid != scope["session_uid"]
                        or int(packet.packet_format) != scope["packet_format"]
                        or header.player_car_index != player
                        or frame_ordinal < state.first_frame
                        or not math.isfinite(header.session_time)
                        or type(packet.received_monotonic_ns) is not int
                        or packet.received_monotonic_ns < 0
                        or type(packet.captured_at_ns) is not int or packet.captured_at_ns < 0):
                    reason = "session_history_source_unqualified"
                    break
                histories.append(decoded.history)
            if reason is None and any(packet.body != group[0].body
                                      or packet.header.session_time != group[0].header.session_time
                                      or packet.header.overall_frame_identifier != group[0].header.overall_frame_identifier
                                      for packet in group[1:]):
                reason = "session_history_conflicted"
            if reason is not None:
                state.source = None
                state.rows = ()
                state.reason = reason
                continue
            packet = group[-1]
            history = histories[-1]
            if state.source is not None and frame_ordinal <= state.source["frame_ordinal"]:
                continue
            state.source = {"scope": copy.deepcopy(scope), "identity": copy.deepcopy(state.identity),
                            "frame_ordinal": frame_ordinal,
                            "frame_identifier": history.overall_frame_identifier,
                            "session_time_s": history.session_time_s,
                            "received_monotonic_ns": packet.received_monotonic_ns,
                            "captured_at_ns": packet.captured_at_ns,
                            "source_sequence": history.source_sequence}
            state.rows = history.lap_history
            state.best_lap = history.best_lap_time_lap_number
            state.reason = None

    def view(self, row: dict, current: dict) -> dict:
        result = {"status": "unavailable", "reason": "session_history_not_received",
                  "qualification": "game_reported_scalar_timing", "coaching_eligible": False,
                  "source": None, "laps": [], "session_best": None,
                  "session_best_reason": "session_history_not_received"}
        state = self._drivers.get(row["car_index"])
        if state is None or state.identity != row["identity"]:
            return result
        result["reason"] = state.reason
        result["session_best_reason"] = state.reason
        if state.source is None:
            return result
        source = state.source
        if (source["frame_ordinal"] > current["frame_ordinal"]
                or source["session_time_s"] > current["session_time_s"]
                or source["received_monotonic_ns"] > current["received_monotonic_ns"]):
            result.update(reason="session_history_ahead_of_live_lap",
                          session_best_reason="session_history_ahead_of_live_lap")
            return result
        completed = [lap for lap in state.rows
                     if state.first_lap <= lap.lap_index + 1 < state.lap_number
                     and state.completed_times.get(lap.lap_index + 1) == lap.lap_time_ms]
        result.update(status="available", reason=None, source=copy.deepcopy(source),
                      first_attributable_lap=state.first_lap, reported_lap_count=len(state.rows),
                      laps=[{"lap_number": lap.lap_index + 1, "lap_time_ms": lap.lap_time_ms,
                             "sector_times_ms": [getattr(lap, f"sector{sector}_time_ms")
                                                 if getattr(lap, f"sector{sector}_time_available") else None
                                                 for sector in (1, 2, 3)],
                             "validity_flags": lap.validity_flags} for lap in completed])
        best = next((lap for lap in completed if lap.lap_index + 1 == state.best_lap), None)
        valid = [lap for lap in state.rows if lap.lap_index + 1 < state.lap_number
                 and lap.validity_flags & 1 and not lap.unknown_validity_bits and lap.lap_time_ms > 0]
        if current["received_monotonic_ns"] - source["received_monotonic_ns"] >= 3_000_000_000:
            reason = "session_history_stale"
        elif len(state.rows) < state.lap_number - 1:
            reason = "session_history_table_incomplete"
        elif best is None:
            reason = "reported_best_not_attributable_or_completed"
        elif not best.validity_flags & 1 or best.unknown_validity_bits or best.lap_time_ms <= 0:
            reason = "reported_best_invalid"
        elif any(lap.unknown_validity_bits for lap in state.rows[:state.lap_number - 1]):
            reason = "session_history_validity_unknown"
        elif any(lap.validity_flags & 1 and lap.lap_time_ms <= 0
                 for lap in state.rows[:state.lap_number - 1]):
            reason = "session_history_valid_timing_missing"
        elif not valid or best.lap_time_ms != min(lap.lap_time_ms for lap in valid):
            reason = "reported_best_selector_conflicted"
        else:
            reason = None
            result["session_best"] = {"lap_number": state.best_lap, "lap_time_ms": best.lap_time_ms}
        result["session_best_reason"] = reason
        return result
