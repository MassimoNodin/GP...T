from __future__ import annotations

import copy
import math
from collections import Counter, deque
from dataclasses import asdict
from typing import Any

from ..driver_names import display_driver_name
from ..sessions.car_lap_inventory import VerifiedCarParticipant
from .detail_policy import DetailPolicyFrame
from ..udp.car_status import CarStatusData
from ..udp.models import PacketId


EVENT_LIMIT = 128


def _context(frame):
    context = frame.context
    reason = "session_context_missing"
    if context is not None:
        reason = "session_context_scope_mismatch"
        if context.session_uid == frame.session_uid and int(context.packet_format) == frame.packet_format:
            return {"status": "available", "reason": None, "value": context.to_dict(),
                    "layout_verified": False, "coaching_eligible": False}
    return {"status": "unavailable", "reason": reason, "value": None,
            "layout_verified": False, "coaching_eligible": False}


def _player_observation(frame, frame_identifier, session_time_s, received_monotonic_ns, packets):
    result = {"status": "unavailable", "reason": "player_telemetry_missing", "value": None,
              "source": None, "coaching_eligible": False}
    matches = [(packet, data) for packet, data in packets
               if packet.packet_kind is PacketId.CAR_TELEMETRY
               and int(packet.packet_format) == frame.packet_format
               and packet.header.session_uid == frame.session_uid
               and packet.header.overall_frame_identifier == frame_identifier
               and packet.header.player_car_index == frame.player_car_index]
    if not matches:
        return result
    if any(not math.isfinite(packet.header.session_time) or packet.header.session_time != session_time_s
           for packet, data in matches):
        return {**result, "reason": "player_telemetry_clock_mismatch"}
    if any(data.cars != matches[0][1].cars for packet, data in matches[1:]):
        return {**result, "reason": "player_telemetry_conflicted"}
    if any(type(packet.received_monotonic_ns) is not int or packet.received_monotonic_ns < 0
           or type(packet.captured_at_ns) is not int or packet.captured_at_ns < 0
           or abs(packet.received_monotonic_ns - received_monotonic_ns) >= 3_000_000_000
           for packet, data in matches):
        return {**result, "reason": "player_telemetry_provenance_unavailable"}
    packet, data = matches[-1]
    car = frame.player_car_index
    if type(car) is not int or not 0 <= car < len(data.cars):
        return {**result, "reason": "player_telemetry_slot_unavailable"}
    telemetry = data.cars[car]
    values = {"speed_kph": telemetry.speed_kph, "brake": telemetry.brake,
              "throttle": telemetry.throttle, "steering": telemetry.steering, "gear": telemetry.gear}
    if (type(telemetry.speed_kph) is not int or not 0 <= telemetry.speed_kph <= 65535
            or type(telemetry.gear) is not int or not -1 <= telemetry.gear <= 8
            or any(type(values[key]) not in (int, float) or not math.isfinite(values[key])
                   or not low <= values[key] <= high
                   for key, low, high in (("brake", 0, 1), ("throttle", 0, 1), ("steering", -1, 1)))):
        return {**result, "reason": "player_telemetry_values_invalid"}
    values.update(lap_number=frame.lap_data[car].current_lap_number,
                  lap_distance_m=frame.lap_data[car].lap_distance_m,
                  current_lap_time_ms=frame.lap_data[car].current_lap_time_ms)
    return {**result, "status": "available", "reason": None, "value": values,
            "source": {"session_uid": frame.session_uid, "packet_format": frame.packet_format,
                       "epoch": frame.lifecycle_epoch, "frame_identifier": frame_identifier,
                       "frame_ordinal": frame.frame_ordinal, "session_time_s": session_time_s,
                       "lap_received_monotonic_ns": received_monotonic_ns,
                       "telemetry_received_monotonic_ns": packet.received_monotonic_ns,
                       "telemetry_captured_at_ns": packet.captured_at_ns}}


def qualify_snapshot(snapshot: dict[str, Any], *, now_ns: int,
                     stale_after_s: float, runtime_state: str = "receiving") -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    received = result.get("received_monotonic_ns")
    age = (now_ns - received) / 1e9 if received is not None else None
    result["age_s"] = age
    reason = None
    if runtime_state != "receiving":
        reason = f"runtime_{runtime_state}"
    elif age is not None and age < 0:
        reason = "observation_clock_mismatch"
    elif age is not None and age >= stale_after_s:
        reason = "lap_observations_stale"
    if reason is not None:
        result.update(status="unavailable", reason=reason, drivers=[], events=[])
        result["context"] = {"status": "unavailable", "reason": reason, "value": None,
                             "layout_verified": False, "coaching_eligible": False}
    return result


class LiveEngineerFacts:
    def __init__(self, *, event_limit: int = EVENT_LIMIT) -> None:
        if not 1 <= event_limit <= EVENT_LIMIT:
            raise ValueError("invalid engineer event limit")
        self._events: deque[dict[str, Any]] = deque(maxlen=event_limit)
        self._cursor = 0
        self._dropped = 0
        self._current: dict[str, Any] | None = None
        self._reason = "lap_observations_not_received"

    def invalidate(self, reason: str) -> None:
        self._current = None
        self._events.clear()
        self._reason = reason

    def begin_frame(self, session_uid: int, epoch: int) -> None:
        if self._current is not None:
            scope = self._current["scope"]
            if (scope["session_uid"], scope["epoch"]) != (session_uid, epoch):
                self.invalidate("scope_changed")

    def _event(self, kind: str, driver: dict[str, Any], frame: dict[str, Any],
               **details: Any) -> None:
        self._cursor += 1
        if len(self._events) == self._events.maxlen:
            self._dropped += 1
        self._events.append({
            "id": self._cursor, "kind": kind, "scope": frame["scope"],
            "driver": driver["identity"], "frame_ordinal": frame["frame_ordinal"],
            "session_time_s": frame["session_time_s"],
            "received_monotonic_ns": frame["received_monotonic_ns"],
            "captured_at_ns": frame["captured_at_ns"], **details,
        })

    def observe(self, frame: DetailPolicyFrame, *, frame_identifier: int,
                session_time_s: float, received_monotonic_ns: int | None,
                captured_at_ns: int | None,
                statuses: dict[int, tuple[CarStatusData | None, str | None]] | None = None,
                participants: tuple[VerifiedCarParticipant, ...] = (), telemetry_packets=()) -> None:
        self.begin_frame(frame.session_uid, frame.lifecycle_epoch)
        if not frame.association_scope_assessable or frame.lap_data_conflicted:
            self.invalidate("lap_association_unavailable")
            return
        if (received_monotonic_ns is None or captured_at_ns is None
                or not math.isfinite(session_time_s)):
            self.invalidate("observation_provenance_unavailable")
            return
        scope = {"session_uid": frame.session_uid, "packet_format": frame.packet_format,
                 "epoch": frame.lifecycle_epoch}
        previous = self._current
        if previous is not None and previous["scope"] != scope:
            self.invalidate("scope_changed")
            previous = None
        if previous is not None and previous["player_car_index"] != frame.player_car_index:
            self.invalidate("player_binding_changed")
            previous = None
        if previous is not None and frame.frame_ordinal <= previous["frame_ordinal"]:
            return
        current = {
            "scope": scope, "frame_ordinal": frame.frame_ordinal,
            "frame_identifier": frame_identifier, "session_time_s": session_time_s,
            "received_monotonic_ns": received_monotonic_ns,
            "captured_at_ns": captured_at_ns, "player_car_index": frame.player_car_index,
            "drivers": [], "context": _context(frame),
        }
        counts = Counter(item.car_index for item in frame.verified_tenures)
        participant_counts = Counter(item.tenure.car_index for item in participants)
        participant_rows = {item.tenure.car_index: item for item in participants}
        prior_drivers = {row["car_index"]: row for row in previous["drivers"]} if previous else {}
        for tenure in frame.verified_tenures:
            car = tenure.car_index
            if (counts[car] != 1 or tenure.close_reason != "active"
                    or (tenure.session_uid, tenure.packet_format, tenure.lifecycle_epoch)
                    != (frame.session_uid, frame.packet_format, frame.lifecycle_epoch)
                    or not tenure.start_frame_ordinal <= frame.frame_ordinal < tenure.end_frame_ordinal_exclusive
                    or not 0 <= car < len(frame.lap_data)):
                continue
            lap = frame.lap_data[car]
            if not math.isfinite(lap.lap_distance_m) or not math.isfinite(lap.total_distance_m):
                continue
            identity = {
                "car_index": car, "tenure_ordinal": tenure.tenure_ordinal,
                "participant_identity_fingerprint": tenure.participant_identity_fingerprint,
            }
            status, status_reason = (statuses or {}).get(car, (None, "status_packet_missing"))
            tyre = None
            if status is not None:
                if status.actual_tyre_compound > 0 and status.visual_tyre_compound > 0:
                    tyre = {"actual_compound": status.actual_tyre_compound,
                            "visual_compound": status.visual_tyre_compound,
                            "age_laps": status.tyre_age_laps}
                else:
                    status_reason = "tyre_compound_unavailable"
            driver = {
                "car_index": car, "identity": identity, "lap": asdict(lap),
                "tyre": {"status": "available" if tyre is not None else "unavailable",
                         "reason": None if tyre is not None else status_reason, "value": tyre},
            }
            participant = participant_rows.get(car)
            participant_value = None
            participant_reason = "participant_binding_unavailable"
            if participant_counts[car] == 1 and participant is not None and participant.matches(tenure):
                name = display_driver_name(participant.participant.name)
                participant_reason = "participant_name_unavailable"
                if name is not None:
                    participant_value = {"name": name, "driver_id": participant.participant.driver_id,
                                         "team_id": participant.participant.team_id,
                                         "race_number": participant.participant.race_number}
            driver["participant"] = {"identity": dict(identity), "value": participant_value,
                                     "status": "available" if participant_value is not None else "unavailable",
                                     "reason": None if participant_value is not None else participant_reason}
            if car == frame.player_car_index:
                driver["observation"] = {**_player_observation(frame, frame_identifier, session_time_s,
                                                              received_monotonic_ns, telemetry_packets),
                                         "identity": dict(identity)}
            current["drivers"].append(driver)
            prior = prior_drivers.get(car)
            if prior is None or prior["identity"] != identity:
                continue
            old = prior["lap"]
            if (old["current_lap_number"] <= 0 or lap.current_lap_number <= 0
                    or old["result_status_id"] != 2 or lap.result_status_id != 2):
                continue
            lap_step = lap.current_lap_number - old["current_lap_number"]
            if lap_step not in (0, 1) or session_time_s < previous["session_time_s"]:
                self._event("driver_discontinuity", driver, current)
                continue
            if lap_step == 1:
                self._event("lap_completed", driver, current,
                            lap_number=old["current_lap_number"],
                            lap_time_ms=lap.last_lap_time_ms if lap.last_lap_time_ms > 0 else None,
                            previous_observed_invalid=old["current_lap_invalid_id"],
                            qualification="observed_lap_transition")
            elif lap.sector_id == old["sector_id"] + 1 and lap.sector_id in (1, 2):
                elapsed = lap.sector1_time_ms if lap.sector_id == 1 else lap.sector2_time_ms
                self._event("sector_completed", driver, current,
                            lap_number=lap.current_lap_number, sector=lap.sector_id,
                            sector_time_ms=elapsed if elapsed > 0 else None)
            if old["pit_status_id"] == 0 and lap.pit_status_id in (1, 2):
                pit_details = {}
                if car == frame.player_car_index:
                    pit_details = {
                        "pre_pit_received_monotonic_ns": previous["received_monotonic_ns"],
                        "pre_pit_field": [
                            {"identity": row["identity"],
                             "position": row["lap"]["car_position"] if row["lap"]["car_position"] > 0 else None}
                            for row in previous["drivers"] if row["lap"]["result_status_id"] == 2
                        ],
                    }
                self._event("pit_entered", driver, current,
                            pre_pit_position=old["car_position"] if old["car_position"] > 0 else None,
                            **pit_details)
            elif old["pit_status_id"] in (1, 2) and lap.pit_status_id == 0:
                self._event("pit_exited", driver, current)
            old_tyre = prior["tyre"]["value"]
            if (old_tyre is not None and tyre is not None
                    and old_tyre["actual_compound"] != tyre["actual_compound"]):
                self._event("tyre_compound_changed", driver, current,
                            previous_compound=old_tyre["actual_compound"],
                            actual_compound=tyre["actual_compound"])
        self._current = current
        self._reason = "verified_drivers_unavailable" if not current["drivers"] else None

    def snapshot(self) -> dict[str, Any]:
        result = {"schema_version": 1,
                  "context": {"status": "unavailable", "reason": self._reason, "value": None,
                              "layout_verified": False, "coaching_eligible": False},
                  "status": "available" if self._current and self._current["drivers"] else "unavailable",
                  "reason": self._reason, "scope": None, "drivers": [],
                  "received_monotonic_ns": None, "captured_at_ns": None,
                  "event_cursor": self._cursor, "events_dropped": self._dropped,
                  "event_floor": self._events[0]["id"] - 1 if self._events else self._cursor,
                  "events": list(self._events)}
        if self._current is not None:
            result.update(self._current)
        return copy.deepcopy(result)
