from __future__ import annotations

import copy
import json
import math
import sqlite3
import struct
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from ..udp import lap_data as _lap_data
from ..udp.car_setups import CarSetupsDecoder
from ..udp.decoder import PacketDecoder
from ..udp.events import EventDecoder
from ..udp.header import HEADER_SIZE, _HEADER
from ..udp.models import RawDatagram
from ..udp.participants import ParticipantsDecoder
from ..udp.session_context import SessionContextDecoder
from .evidence import PROCESSOR_VERSION, EvidenceStore, EvidenceUnavailable


SUMMARY_VERSION = "session-summary-v1"

_LAP_RECORD = _lap_data._LAP_DATA_V1_CAR
# Indexes into one unpacked Lap Data record (see udp/lap_data.py::_decode_car).
_LAP_LAST_LAP_MS = 0
_LAP_NUMBER = 14
_LAP_PIT_STATUS = 15
_LAP_PIT_STOPS = 16
_LAP_BODY = {
    2025: (22, _lap_data._F1_25_LAP_DATA_V1_BODY_SIZE),
    2026: (24, _lap_data._SEASON_PACK_2026_LAP_DATA_V1_BODY_SIZE),
}
_SESSION_PREFIX = struct.Struct("<BbbBHBbBHHBBBBBB")
_SESSION_BODY = {2025: 724, 2026: 897}

_WEATHER = {0: "clear", 1: "light_cloud", 2: "overcast", 3: "light_rain", 4: "heavy_rain", 5: "storm"}
_SAFETY_CAR_TYPE = {0: ("safety_car", "Safety car"), 1: ("safety_car", "Safety car"),
                    2: ("virtual_safety_car", "Virtual safety car"),
                    3: ("formation_lap_safety_car", "Formation lap safety car")}
_SAFETY_CAR_EVENT = {0: "deployed", 1: "coming in", 2: "returned", 3: "race resumes"}
_RETIREMENT_REASON = {0: "invalid", 1: "retired", 2: "finished", 3: "terminal damage", 4: "inactive",
                      5: "not enough laps completed", 6: "black flagged", 7: "red flagged",
                      8: "mechanical failure", 9: "session skipped", 10: "session simulated"}
_PENALTY_TYPE = {0: "drive-through", 1: "stop-go", 2: "grid penalty", 3: "penalty reminder",
                 4: "time penalty", 5: "warning", 6: "disqualified", 7: "removed from formation lap",
                 8: "parked too long", 9: "tyre regulations", 10: "lap invalidated",
                 11: "this and next lap invalidated", 12: "lap invalidated", 13: "this and next lap invalidated",
                 14: "this and previous lap invalidated", 15: "this and previous lap invalidated",
                 16: "retired", 17: "black flag timer"}
_INFRINGEMENT = {0: "blocking by slow driving", 1: "blocking by wrong way driving",
                 2: "reversing off the start line", 3: "big collision", 4: "small collision",
                 5: "collision, failed to hand back position", 6: "collision, failed to hand back positions",
                 7: "corner cutting, gained time", 8: "corner cutting overtake", 9: "corner cutting overtakes",
                 10: "crossed pit exit lane", 11: "ignoring blue flags", 12: "ignoring yellow flags",
                 13: "ignoring drive-through", 14: "too many drive-throughs", 17: "pit lane speeding",
                 18: "parked for too long", 19: "ignoring tyre regulations", 20: "too many penalties",
                 21: "multiple warnings", 22: "approaching disqualification",
                 25: "lap invalidated, corner cutting", 26: "lap invalidated, running wide",
                 30: "lap invalidated, wall riding", 31: "lap invalidated, flashback used",
                 32: "lap invalidated, reset to track", 33: "blocking the pit lane", 34: "jump start",
                 35: "safety car collision", 36: "safety car illegal overtake",
                 37: "safety car exceeding allowed pace", 38: "virtual safety car exceeding allowed pace",
                 39: "formation lap below allowed speed", 40: "formation lap parking",
                 41: "retired, mechanical failure", 42: "retired, terminally damaged",
                 43: "safety car falling too far back", 44: "black flag timer",
                 45: "unserved stop-go", 46: "unserved drive-through", 47: "engine component change",
                 48: "gearbox change", 49: "parc ferme change", 50: "league grid penalty",
                 51: "retry penalty", 52: "illegal time gain", 53: "mandatory pit stop"}
# Event codes that carry no listed event (button presses, start lights, unknown 2026 layouts).
_SUPPRESSED_CODES = frozenset({"BUTN", "STLG", "TMPT", "PMEN", "PMDI", "OVEN", "OVDI"})
_PLAIN_EVENTS = {
    "SSTA": ("session_started", "Session started"), "SEND": ("session_ended", "Session ended"),
    "DRSE": ("drs_enabled", "DRS enabled"), "DRSD": ("drs_disabled", "DRS disabled"),
    "CHQF": ("chequered_flag", "Chequered flag"), "LGOT": ("lights_out", "Lights out"),
    "RDFL": ("red_flag", "Red flag"),
}
_MIN_PLAUSIBLE_CAPTURE_NS = 946_684_800 * 1_000_000_000  # 2000-01-01T00:00:00Z
_MAX_PLAUSIBLE_CAPTURE_NS = 4_102_444_800 * 1_000_000_000  # 2100-01-01T00:00:00Z

_SCAN_SQL = (
    "SELECT j.sequence,j.payload,m.payload AS route FROM journal j LEFT JOIN metadata m "
    "ON m.generation=j.generation AND m.sequence=j.sequence AND m.kind='input_route' AND m.ordinal=0 "
    "WHERE j.generation=? AND j.sequence>=? AND j.sequence<=? AND j.kind='datagram' ORDER BY j.sequence"
)


@dataclass(frozen=True)
class SummaryLimits:
    scan_rows: int = 600_000
    scan_bytes: int = 384 * 1024 * 1024
    duration_s: float = 10.0
    max_events: int = 400
    max_weather_points: int = 200
    cache_entries: int = 16
    concurrent_scans: int = 2

    def __post_init__(self) -> None:
        for name in ("scan_rows", "scan_bytes", "max_events", "max_weather_points",
                     "cache_entries", "concurrent_scans"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("invalid session summary limits")
        if not isinstance(self.duration_s, (int, float)) or not 0 < self.duration_s <= 60:
            raise ValueError("invalid session summary limits")


def _number(value: float, digits: int = 3) -> float | None:
    return round(float(value), digits) if math.isfinite(value) else None


def _utc(nanoseconds: int) -> str:
    moment = datetime.fromtimestamp(nanoseconds // 1_000_000_000, tz=timezone.utc)
    moment = moment.replace(microsecond=(nanoseconds % 1_000_000_000) // 1000)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _format_lap_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    minutes, remainder = divmod(milliseconds, 60_000)
    return f"{minutes}:{remainder // 1000:02d}.{remainder % 1000:03d}"


class _Scan:
    def __init__(self, limits: SummaryLimits) -> None:
        self.limits = limits
        self.rows = 0
        self.bytes = 0
        self.stop_reason = "end_of_committed_journal"
        self.current_uid: int | None = None
        self.occurrences: dict[int, int] = {}
        self.candidate_uid: int | None = None
        self.candidate_count = 0
        self.found = False
        self.first_sequence: int | None = None
        self.last_sequence: int | None = None
        self.time_min: float | None = None
        self.time_max: float | None = None
        self.player_car: int | None = None
        self.packet_format: int | None = None
        self.events: list[dict[str, Any]] = []
        self.by_kind: dict[str, int] = {}
        self.observed = 0
        self.omitted_minor = 0
        self.omitted_budget = 0
        self.ignored_codes: dict[str, int] = {}
        self.seen_events: set[tuple[Any, ...]] = set()
        self.weather: list[dict[str, Any]] = []
        self.weather_omitted = 0
        self.weather_key: tuple[int, int, int] | None = None
        self.session_fields: tuple[Any, ...] | None = None
        self.lap_payload: bytes | None = None
        self.lap_format: int | None = None
        self.pit_active: bool | None = None
        self.last_lap_number: int | None = None
        self.pit_stop_count: int | None = None
        self.pending_fastest: list[tuple[dict[str, Any], int, int, float]] = []
        self.participants_payload: tuple[int, bytes] | None = None
        self.setup_first: dict[str, Any] | None = None
        self.setup_last: dict[str, Any] | None = None
        self.setup_changes = 0
        self.setup_decode_errors = 0
        self.setup_invalid_fields = False
        self.settings_first: dict[str, Any] | None = None
        self.settings_last: dict[str, Any] | None = None
        self.settings_decode_errors = 0

    @property
    def complete(self) -> bool:
        return self.stop_reason in ("end_of_committed_journal", "session_run_ended")


class SessionSummaryService:
    """Read-only derivation of a session timeline from its committed journal range."""

    def __init__(self, store: EvidenceStore, *, limits: SummaryLimits | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.limits = limits or SummaryLimits()
        self._clock = clock
        self._admission = threading.BoundedSemaphore(self.limits.concurrent_scans)
        self._cache_lock = threading.Lock()
        self._cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._packets = PacketDecoder()
        self._events = EventDecoder()
        self._participants = ParticipantsDecoder()
        self._setups = CarSetupsDecoder()
        self._session_context = SessionContextDecoder()

    def status(self) -> dict[str, Any]:
        with self._cache_lock:
            return {"version": SUMMARY_VERSION, "cached": len(self._cache),
                    "cache_entries": self.limits.cache_entries}

    def summary(self, session_id: str) -> dict[str, Any]:
        with self.store.connect() as database:
            database.execute("PRAGMA query_only=ON")
            database.execute("BEGIN")
            session = database.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise EvidenceUnavailable("session_not_found")
            generation = database.execute("SELECT * FROM generations WHERE id=?",
                                          (session["generation"],)).fetchone()
            if generation is None or generation["version"] != PROCESSOR_VERSION:
                raise EvidenceUnavailable("summary_processor_version_unavailable")
            committed = int(generation["committed_sequence"])
            key = (session_id, committed, session["lifecycle"], session["acquisition"], session["context"])
            with self._cache_lock:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    return copy.deepcopy(cached)
            if not self._admission.acquire(blocking=False):
                raise EvidenceUnavailable("summary_busy")
            try:
                result = self._derive(database, session, committed)
            finally:
                self._admission.release()
        if result["scan"]["stop_reason"] != "time_budget":
            with self._cache_lock:
                self._cache[key] = copy.deepcopy(result)
                self._cache.move_to_end(key)
                while len(self._cache) > self.limits.cache_entries:
                    self._cache.popitem(last=False)
        return result

    # -- derivation -------------------------------------------------------------------------------

    def _derive(self, database: sqlite3.Connection, session: sqlite3.Row, committed: int) -> dict[str, Any]:
        started = self._clock()
        deadline = started + self.limits.duration_s
        uid = int(session["uid"])
        scan = _Scan(self.limits)
        start_sequence = 1
        database.set_progress_handler(lambda: 1 if self._clock() > deadline else 0, 20_000)
        try:
            boundary = database.execute(
                "SELECT sequence FROM metadata WHERE generation=? AND kind='logical_session' "
                "AND json_extract(payload,'$.session_id')=? ORDER BY sequence LIMIT 1",
                (session["generation"], session["id"]),
            ).fetchone()
            scan_end = committed
            if boundary is not None:
                start_sequence = boundary[0]
                following = database.execute(
                    "SELECT MIN(sequence) FROM metadata WHERE generation=? AND kind='logical_session' "
                    "AND sequence>? AND sequence<=?", (session["generation"], start_sequence, committed),
                ).fetchone()[0]
                if following is not None:
                    scan_end = following - 1
            self._scan(database, session, scan, start_sequence, scan_end, uid, deadline,
                       authoritative=boundary is not None)
        except sqlite3.OperationalError as exception:
            if "interrupt" not in str(exception).lower():
                raise
            scan.stop_reason = "time_budget"
        finally:
            database.set_progress_handler(None, 0)
        first_ns = last_ns = None
        if scan.first_sequence is not None:
            first_ns = self._captured_ns(database, session["generation"], scan.first_sequence)
            last_ns = self._captured_ns(database, session["generation"], scan.last_sequence)
        names = self._driver_names(scan)
        events = self._finalize_events(scan, names)
        context = json.loads(session["context"]) if session["context"] else {}
        wall = self._wall_clock(session, scan, first_ns, last_ns)
        session_time = {"start_s": None, "end_s": None, "duration_s": None}
        if scan.time_min is not None and scan.time_max is not None:
            session_time = {"start_s": _number(scan.time_min), "end_s": _number(scan.time_max),
                            "duration_s": _number(scan.time_max - scan.time_min)}
        progress = context.get("progress") or {}
        scheduled = scan.session_fields[9] if scan.session_fields else progress.get("duration_s")
        return {
            "version": SUMMARY_VERSION,
            "session": {
                "id": session["id"], "uid": session["uid"], "occurrence": session["occurrence"],
                "lifecycle": session["lifecycle"], "acquisition": session["acquisition"],
                "track_id": context.get("track_id"), "track_name": context.get("track_name"),
                "session_type": context.get("session_type"), "total_laps": context.get("total_laps"),
                "packet_format": context.get("packet_format"), "track_length_m": context.get("track_length_m"),
                "rule_set": context.get("rule_set"), "rule_set_id": context.get("rule_set_id"),
            },
            **wall,
            "session_time": session_time,
            "scheduled_duration_s": scheduled,
            "player": {"car_index": scan.player_car, "last_lap_number": scan.last_lap_number,
                       "pit_stop_count": scan.pit_stop_count},
            "weather": scan.weather,
            "player_car_setup": self._setup_result(scan),
            "assists": {
                "status": "available" if scan.settings_first and scan.complete and not scan.settings_decode_errors else (
                    "partial" if scan.settings_first else "unavailable"
                ),
                "source": "session_packet",
                "first_observed": scan.settings_first,
                "last_observed": scan.settings_last,
                "reason": None if scan.settings_first and scan.complete and not scan.settings_decode_errors else (
                    "summary_scan_incomplete" if not scan.complete else (
                        "session_settings_decode_error" if scan.settings_decode_errors else "session_settings_not_recorded"
                    )
                ),
                "decode_errors": scan.settings_decode_errors,
                "damage_setting": None,
                "damage_setting_reason": "damage_setting_not_in_telemetry",
            },
            "events": events,
            "totals": {
                "by_kind": dict(sorted(scan.by_kind.items())),
                "observed": scan.observed, "listed": len(events),
                "omitted_minor": scan.omitted_minor, "omitted_budget": scan.omitted_budget,
                "weather_points_omitted": scan.weather_omitted,
                "truncated": scan.omitted_budget > 0 or scan.weather_omitted > 0 or not scan.complete,
                "ignored_codes": dict(sorted(scan.ignored_codes.items())),
            },
            "scan": {
                "complete": scan.complete, "stop_reason": scan.stop_reason,
                "committed_sequence": committed, "start_sequence": start_sequence,
                "first_sequence": scan.first_sequence, "last_sequence": scan.last_sequence,
                "rows_scanned": scan.rows, "bytes_scanned": scan.bytes,
                "limits": {"rows": self.limits.scan_rows, "bytes": self.limits.scan_bytes,
                           "duration_s": self.limits.duration_s, "events": self.limits.max_events},
            },
        }

    def _scan(self, database: sqlite3.Connection, session: sqlite3.Row, scan: _Scan, start: int,
              committed: int, target_uid: int, deadline: float, *, authoritative: bool = False) -> None:
        limits = self.limits
        target_occurrence = int(session["occurrence"])
        for sequence, payload, route_json in database.execute(_SCAN_SQL, (session["generation"], start, committed)):
            scan.rows += 1
            scan.bytes += len(payload) + len((route_json or "").encode("utf-8"))
            if scan.rows > limits.scan_rows:
                scan.stop_reason = "row_budget"
                return
            if scan.bytes > limits.scan_bytes:
                scan.stop_reason = "byte_budget"
                return
            if scan.rows & 0x3FF == 0 and self._clock() > deadline:
                scan.stop_reason = "time_budget"
                return
            if len(payload) < HEADER_SIZE:
                continue
            (packet_format, _year, _major, _minor, version, packet_id, uid, session_time, frame,
             _overall, player, _secondary) = _HEADER.unpack_from(payload)
            if uid == 0 or packet_format not in (2025, 2026) or not math.isfinite(session_time):
                continue
            route = json.loads(route_json) if route_json else {}
            if route.get("retired_input"):
                continue
            if authoritative:
                belongs = uid == target_uid
            else:
                if route.get("restart_occurrence"):
                    scan.occurrences[uid] = scan.occurrences.get(uid, 0) + 1
                    scan.current_uid = uid
                elif not self._route(scan, payload, packet_id, uid):
                    continue
                belongs = scan.current_uid == target_uid and scan.occurrences.get(target_uid) == target_occurrence
            if not belongs:
                if scan.found:
                    scan.stop_reason = "session_run_ended"
                    return
                continue
            scan.found = True
            if scan.first_sequence is None:
                scan.first_sequence = sequence
            scan.last_sequence = sequence
            scan.player_car = player
            scan.packet_format = packet_format
            scan.time_min = session_time if scan.time_min is None else min(scan.time_min, session_time)
            scan.time_max = session_time if scan.time_max is None else max(scan.time_max, session_time)
            if packet_id == 2:
                self._lap_data(scan, payload, packet_format, player, session_time)
            elif packet_id == 1:
                self._session(scan, payload, packet_format, version, session_time)
            elif packet_id == 3:
                self._event(scan, sequence, payload, packet_format, player, session_time, frame)
            elif packet_id == 4:
                scan.participants_payload = (packet_format, payload)
            elif packet_id == 5:
                self._setup(scan, sequence, payload, player, session_time)

    @staticmethod
    def _route(scan: _Scan, payload: bytes, packet_id: int, uid: int) -> bool:
        """Mirror coordinator run ownership: later uids need three rows, restarts need SSTA."""
        if scan.current_uid is None:
            scan.current_uid = uid
            scan.occurrences[uid] = 1
            return True
        if uid == scan.current_uid:
            scan.candidate_uid = None
            scan.candidate_count = 0
            return True
        if uid in scan.occurrences:
            if packet_id == 3 and payload[HEADER_SIZE:HEADER_SIZE + 4] == b"SSTA":
                scan.occurrences[uid] += 1
                scan.current_uid = uid
                scan.candidate_uid = None
                scan.candidate_count = 0
                return True
            return False
        scan.candidate_count = scan.candidate_count + 1 if scan.candidate_uid == uid else 1
        scan.candidate_uid = uid
        if scan.candidate_count < 3:
            return False
        scan.current_uid = uid
        scan.occurrences[uid] = 1
        scan.candidate_uid = None
        scan.candidate_count = 0
        return True

    def _session(self, scan: _Scan, payload: bytes, packet_format: int, version: int,
                 session_time: float) -> None:
        body = payload[HEADER_SIZE:]
        if version != 1 or len(body) != _SESSION_BODY[packet_format]:
            scan.settings_decode_errors += 1
            return
        fields = _SESSION_PREFIX.unpack_from(body)
        scan.session_fields = fields
        decoded = self._session_context.decode(self._decode(payload))
        if decoded.context is not None:
            context = decoded.context.to_dict()
            observation = {
                "session_time_s": _number(session_time),
                **{key: context[key] for key in (
                    "steering_assist_id", "braking_assist_id", "gearbox_assist_id",
                    "equal_car_performance_id", "rule_set_id", "rule_set",
                )},
            }
            if scan.settings_first is None:
                scan.settings_first = observation
            scan.settings_last = observation
        else:
            scan.settings_decode_errors += 1
        key = (fields[0], fields[1], fields[2])
        if key == scan.weather_key:
            return
        scan.weather_key = key
        if len(scan.weather) >= self.limits.max_weather_points:
            scan.weather_omitted += 1
            return
        scan.weather.append({"session_time_s": _number(session_time), "weather_id": fields[0],
                             "weather": _WEATHER.get(fields[0]), "track_temperature_c": fields[1],
                             "air_temperature_c": fields[2]})

    def _decode(self, payload: bytes):
        return self._packets.decode(RawDatagram(
            sequence=0, captured_at_ns=0, monotonic_ns=0, source_host="", source_port=0,
            payload=payload,
        ))

    def _setup(self, scan: _Scan, sequence: int, payload: bytes, player: int,
               session_time: float) -> None:
        result = self._setups.decode(self._decode(payload))
        if result.setups is None or not 0 <= player < len(result.setups.cars):
            scan.setup_decode_errors += 1
            return
        observation = {
            "sequence": sequence, "session_time_s": _number(session_time),
            "player_car_index": player, "setup": asdict(result.setups.cars[player]),
        }
        scan.setup_invalid_fields |= bool(result.setups.cars[player].invalid_fields)
        if scan.setup_first is None:
            scan.setup_first = observation
        if scan.setup_last is not None and (
            any(value is not None and scan.setup_last["setup"][field] is not None
                and value != scan.setup_last["setup"][field]
                for field, value in observation["setup"].items() if field != "invalid_fields")
            or scan.setup_last["player_car_index"] != player
        ):
            scan.setup_changes += 1
        scan.setup_last = observation

    @staticmethod
    def _setup_result(scan: _Scan) -> dict[str, Any]:
        reason = (
            "summary_scan_incomplete" if not scan.complete else
            "car_setup_decode_error" if scan.setup_decode_errors else
            "car_setup_fields_invalid" if scan.setup_invalid_fields else
            "car_setup_not_recorded" if scan.setup_first is None else None
        )
        return {
            "status": "unavailable" if scan.setup_first is None else (
                "partial" if reason else "available"
            ),
            "source": "car_setups_packet", "first_observed": scan.setup_first,
            "last_observed": scan.setup_last, "observed_change_count": scan.setup_changes,
            "decode_errors": scan.setup_decode_errors, "reason": reason,
        }

    def _lap_data(self, scan: _Scan, payload: bytes, packet_format: int, player: int,
                  session_time: float) -> None:
        count, size = _LAP_BODY[packet_format]
        if len(payload) - HEADER_SIZE != size or player >= count:
            return
        scan.lap_payload = payload
        scan.lap_format = packet_format
        record = _LAP_RECORD.unpack_from(payload, HEADER_SIZE + player * _LAP_RECORD.size)
        in_pit = record[_LAP_PIT_STATUS] != 0
        lap = record[_LAP_NUMBER] or None
        if lap is not None:
            scan.last_lap_number = lap
        scan.pit_stop_count = record[_LAP_PIT_STOPS]
        if scan.pit_active is not None and in_pit != scan.pit_active:
            kind, label = ("pit_entry", "Pit entry") if in_pit else ("pit_exit", "Pit exit")
            self._add(scan, {"kind": kind, "label": label, "session_time_s": _number(session_time),
                             "lap": lap, "car_index": player, "is_player": True,
                             "detail": {"pit_stop_count": record[_LAP_PIT_STOPS]}, "code": None})
        scan.pit_active = in_pit
        if scan.pending_fastest:
            remaining = []
            for event, car, lap_ms, expires in scan.pending_fastest:
                if car < count:
                    car_record = _LAP_RECORD.unpack_from(payload, HEADER_SIZE + car * _LAP_RECORD.size)
                    if abs(car_record[_LAP_LAST_LAP_MS] - lap_ms) <= 1 and car_record[_LAP_NUMBER] > 1:
                        event["lap"] = car_record[_LAP_NUMBER] - 1
                        continue
                if session_time <= expires:
                    remaining.append((event, car, lap_ms, expires))
            scan.pending_fastest = remaining

    @staticmethod
    def _car_lap(scan: _Scan, car: int) -> int | None:
        if scan.lap_payload is None or scan.lap_format is None:
            return None
        count, _size = _LAP_BODY[scan.lap_format]
        if car >= count:
            return None
        record = _LAP_RECORD.unpack_from(scan.lap_payload, HEADER_SIZE + car * _LAP_RECORD.size)
        return record[_LAP_NUMBER] or None

    def _event(self, scan: _Scan, sequence: int, payload: bytes, packet_format: int, player: int,
               session_time: float, frame: int) -> None:
        raw = RawDatagram(sequence=sequence, captured_at_ns=0, monotonic_ns=0, source_host="",
                          source_port=0, payload=payload)
        try:
            decoded = self._events.decode(self._packets.decode(raw))
        except Exception:  # malformed rows are counted, never fatal for a read-side summary
            scan.ignored_codes["malformed"] = scan.ignored_codes.get("malformed", 0) + 1
            return
        code = decoded.code or "????"
        if decoded.error is not None:
            name = f"{code}:{decoded.error}" if code != "????" else decoded.error
            scan.ignored_codes[name] = scan.ignored_codes.get(name, 0) + 1
            return
        if code in _SUPPRESSED_CODES:
            scan.ignored_codes[code] = scan.ignored_codes.get(code, 0) + 1
            return
        identity = (code, decoded.details, frame)
        if identity in scan.seen_events:
            return
        if len(scan.seen_events) < 10_000:
            scan.seen_events.add(identity)
        details = decoded.details
        count = _LAP_BODY[packet_format][0]
        time_s = _number(session_time)

        def car(index: int) -> int | None:
            return index if index < count else None

        def record(kind: str, label: str, car_index: int | None, detail: dict[str, Any], *,
                   lap: int | None = None, minor: bool = False) -> dict[str, Any] | None:
            if lap is None and car_index is not None:
                lap = self._car_lap(scan, car_index)
            event = {"kind": kind, "label": label, "session_time_s": time_s, "lap": lap,
                     "car_index": car_index, "is_player": car_index is not None and car_index == player,
                     "detail": detail, "code": code}
            if minor and not event["is_player"]:
                scan.by_kind[kind] = scan.by_kind.get(kind, 0) + 1
                scan.observed += 1
                scan.omitted_minor += 1
                return None
            return self._add(scan, event)

        if code in _PLAIN_EVENTS:
            kind, label = _PLAIN_EVENTS[code]
            record(kind, label, None, {})
        elif code == "FTLP":
            index, lap_time = struct.unpack_from("<Bf", details)
            if not math.isfinite(lap_time) or lap_time <= 0:
                return
            event = record("fastest_lap", f"Fastest lap {_format_lap_time(lap_time)} - {{car}}", car(index),
                           {"lap_time_s": _number(lap_time), "lap_time_ms": int(round(lap_time * 1000))},
                           lap=None)
            if event is not None and event["car_index"] is not None:
                event["lap"] = None
                scan.pending_fastest.append((event, event["car_index"], int(round(lap_time * 1000)),
                                             session_time + 5.0))
                self._lap_data_resolve(scan, event, event["car_index"], int(round(lap_time * 1000)))
        elif code == "RTMT":
            index, reason = struct.unpack_from("<BB", details)
            text = _RETIREMENT_REASON.get(reason, f"reason {reason}")
            if reason == 2:
                record("finish", "{car} finished", car(index), {"reason": text, "reason_id": reason})
            else:
                record("retirement", f"{{car}} retired ({text})", car(index),
                       {"reason": text, "reason_id": reason})
        elif code == "RCWN":
            record("race_winner", "{car} won the race", car(details[0]), {})
        elif code == "PENA":
            penalty, infringement, index, other, seconds, lap, places = struct.unpack_from("<7B", details)
            penalty_text = _PENALTY_TYPE.get(penalty, f"penalty {penalty}")
            infringement_text = _INFRINGEMENT.get(infringement, f"infringement {infringement}")
            label = f"{{car}}: {penalty_text}"
            if penalty == 4 and seconds:
                label = f"{{car}}: +{seconds}s time penalty"
            record("penalty", f"{label} ({infringement_text})", car(index),
                   {"penalty": penalty_text, "penalty_id": penalty, "infringement": infringement_text,
                    "infringement_id": infringement, "time_s": seconds,
                    "other_car_index": car(other) if other != 255 else None,
                    "lap_number": lap or None, "places_gained": places}, lap=lap or None)
        elif code == "SPTP":
            index, speed, overall, personal, fastest_index, fastest_speed = struct.unpack_from("<BfBBBf", details)
            if not math.isfinite(speed):
                return
            record("speed_trap", f"Speed trap {speed:.0f} km/h - {{car}}", car(index),
                   {"speed_kph": _number(speed, 1), "overall_fastest": bool(overall),
                    "driver_fastest": bool(personal),
                    "fastest_car_index": car(fastest_index),
                    "fastest_speed_kph": _number(fastest_speed, 1)},
                   minor=not overall)
        elif code == "DTSV":
            record("drive_through_served", "{car} served drive-through", car(details[0]), {})
        elif code == "SGSV":
            record("stop_go_served", "{car} served stop-go", car(details[0]), {})
        elif code == "FLBK":
            record("flashback", "Flashback used", player if player < count else None,
                   {"target_session_time_s": _number(decoded.target_session_time_s)
                    if decoded.target_session_time_s is not None else None,
                    "target_frame_identifier": decoded.target_frame_identifier})
        elif code == "OVTK":
            overtaker, overtaken = details[0], details[1]
            if player in (overtaker, overtaken):
                record("overtake", "{car} overtook {other}", car(overtaker),
                       {"overtaken_car_index": car(overtaken)}, minor=False)
            else:
                record("overtake", "{car} overtook {other}", car(overtaker),
                       {"overtaken_car_index": car(overtaken)}, minor=True)
        elif code == "SCAR":
            sc_type, sc_event = details[0], details[1]
            kind, name = _SAFETY_CAR_TYPE.get(sc_type, ("safety_car", "Safety car"))
            event_text = _SAFETY_CAR_EVENT.get(sc_event, f"event {sc_event}")
            record(kind, f"{name}: {event_text}", None,
                   {"safety_car_type_id": sc_type, "event": event_text, "event_id": sc_event})
        elif code == "COLL":
            first, second = details[0], details[1]
            record("collision", "{car} and {other} collided", car(first),
                   {"other_car_index": car(second)})
        else:
            scan.ignored_codes[code] = scan.ignored_codes.get(code, 0) + 1

    @staticmethod
    def _lap_data_resolve(scan: _Scan, event: dict[str, Any], car: int, lap_ms: int) -> None:
        if scan.lap_payload is None or scan.lap_format is None or car >= _LAP_BODY[scan.lap_format][0]:
            return
        record = _LAP_RECORD.unpack_from(scan.lap_payload, HEADER_SIZE + car * _LAP_RECORD.size)
        if abs(record[_LAP_LAST_LAP_MS] - lap_ms) <= 1 and record[_LAP_NUMBER] > 1:
            event["lap"] = record[_LAP_NUMBER] - 1
            scan.pending_fastest = [item for item in scan.pending_fastest if item[0] is not event]

    def _add(self, scan: _Scan, event: dict[str, Any]) -> dict[str, Any] | None:
        scan.by_kind[event["kind"]] = scan.by_kind.get(event["kind"], 0) + 1
        scan.observed += 1
        if len(scan.events) >= self.limits.max_events:
            scan.omitted_budget += 1
            return None
        scan.events.append(event)
        return event

    # -- presentation -----------------------------------------------------------------------------

    def _driver_names(self, scan: _Scan) -> dict[int, str]:
        if scan.participants_payload is None:
            return {}
        packet_format, payload = scan.participants_payload
        try:
            raw = RawDatagram(sequence=0, captured_at_ns=0, monotonic_ns=0, source_host="",
                              source_port=0, payload=payload)
            result = self._participants.decode(self._packets.decode(raw))
        except Exception:
            return {}
        if result.participants is None:
            return {}
        return {index: car.name for index, car in enumerate(result.participants.cars) if car.name.strip()}

    @staticmethod
    def _finalize_events(scan: _Scan, names: dict[int, str]) -> list[dict[str, Any]]:
        def who(index: int | None) -> str:
            if index is None:
                return "Unknown car"
            return names.get(index) or f"Car {index}"

        for event in scan.events:
            detail = event["detail"]
            other = detail.get("overtaken_car_index", detail.get("other_car_index"))
            event["label"] = event["label"].replace("{car}", who(event["car_index"])).replace("{other}", who(other))
            event["driver_name"] = names.get(event["car_index"]) if event["car_index"] is not None else None
            if other is not None and "overtaken_car_index" in detail:
                detail["overtaken_driver_name"] = names.get(other)
        return scan.events

    def _captured_ns(self, database: sqlite3.Connection, generation: str, sequence: int | None) -> int | None:
        if sequence is None:
            return None
        row = database.execute("SELECT metadata FROM journal WHERE generation=? AND sequence=?",
                               (generation, sequence)).fetchone()
        try:
            value = json.loads(row["metadata"])["captured_at_ns"] if row else None
        except (TypeError, ValueError, KeyError):
            return None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _wall_clock(session: sqlite3.Row, scan: _Scan, first_ns: int | None, last_ns: int | None
                    ) -> dict[str, Any]:
        result: dict[str, Any] = {"started_at_utc": None, "ended_at_utc": None, "last_observed_at_utc": None,
                                  "time_source": "journal_captured_at_ns", "time_reason": None}

        def plausible(value: int | None) -> bool:
            return value is not None and _MIN_PLAUSIBLE_CAPTURE_NS <= value < _MAX_PLAUSIBLE_CAPTURE_NS

        if scan.first_sequence is None:
            result["time_reason"] = ("scan_budget_exceeded_before_session" if not scan.complete
                                     else "no_session_packets_in_committed_journal")
            return result
        if not plausible(first_ns):
            result["time_reason"] = ("captured_at_unavailable" if first_ns is None
                                     else "captured_at_not_wall_clock")
            return result
        result["started_at_utc"] = _utc(first_ns)
        if plausible(last_ns):
            result["last_observed_at_utc"] = _utc(last_ns)
        if session["lifecycle"] == "active":
            result["time_reason"] = "session_active"
        elif not scan.complete:
            result["time_reason"] = f"scan_incomplete_{scan.stop_reason}"
        elif plausible(last_ns):
            result["ended_at_utc"] = result["last_observed_at_utc"]
        return result
