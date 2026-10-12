"""Read-side lap timing and tyre enrichment for session-evidence (v2) player attempts.

v2 attempts carry lap time, validity and pit flags only. The committed raw journal still
holds the source datagrams, so sector times (Session History, with Lap Data as fallback)
and tyre state (Car Status) are derived here at read time. Nothing is written to the
evidence tables; results are cached per (session, committed journal sequence).
"""

from __future__ import annotations

import copy
import json
import struct
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Callable

from ..errors import ProtocolError
from ..udp.car_status import CarStatusDecoder
from ..udp.decoder import PacketDecoder
from ..udp.lap_data import LapDataDecoder
from ..udp.models import PacketId, RawDatagram
from ..udp.session_history import SessionHistoryDecoder, SessionHistoryLap, SessionHistoryTyreStint
from .evidence import PROCESSOR_VERSION, EvidenceStore, EvidenceUnavailable


LAP_ENRICHMENT_VERSION = "lap-enrichment-v1"
SECTOR_SUM_TOLERANCE_MS = 2
LAP_TIME_TOLERANCE_MS = 1
_HEADER_SIZE = 29
_SESSION_HISTORY_CAR_OFFSET = _HEADER_SIZE
_LAP_DATA_RING = 4

# Same label tables as analysis/quality.py, keyed by the session's formula id.
_ACTUAL_LABELS = {
    0: {16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0", 22: "C6", 7: "intermediate", 8: "wet"},
    1: {9: "dry", 10: "wet"},
    2: {11: "super soft", 12: "soft", 13: "medium", 14: "hard", 15: "wet"},
}
_VISUAL_LABELS = {
    0: {16: "soft", 17: "medium", 18: "hard", 7: "intermediate", 8: "wet"},
    1: {9: "dry", 10: "wet"},
    2: {19: "super soft", 20: "soft", 21: "medium", 22: "hard", 15: "wet"},
}


@dataclass(frozen=True)
class LapEnrichmentLimits:
    scan_rows: int = 250_000
    matched_bytes: int = 64 * 1024 * 1024
    duration_s: float = 10.0
    max_laps: int = 500
    cache_entries: int = 16
    concurrency: int = 2

    def __post_init__(self) -> None:
        maxima = (1_000_000, 256 * 1024 * 1024, 30.0, 500, 64, 4)
        values = (self.scan_rows, self.matched_bytes, self.duration_s, self.max_laps,
                  self.cache_entries, self.concurrency)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= maximum
               for value, maximum in zip(values, maxima)):
            raise ValueError("invalid lap enrichment limits")


class LapEnrichmentBusy(EvidenceUnavailable):
    pass


@dataclass
class _Target:
    index: int
    car_index: int
    lap_number: int
    start: int | None
    end: int | None
    lap_time_ms: int | None
    status: tuple[int, bytes] | None = None
    lap_data: tuple[tuple[int, bytes], ...] = ()
    resolved: bool = False


@dataclass
class _CarHistory:
    laps: dict[int, tuple[SessionHistoryLap, int]]
    stints: tuple[SessionHistoryTyreStint, ...] = ()
    last_body: bytes = b""
    packets: int = 0


def _complete(lap: SessionHistoryLap) -> bool:
    return lap.lap_time_ms > 0 and lap.sector1_time_available and lap.sector2_time_available and lap.sector3_time_available


class LapEnrichmentService:
    def __init__(self, store: EvidenceStore, *, limits: LapEnrichmentLimits | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.limits = limits or LapEnrichmentLimits()
        self.clock = clock
        self._admission = threading.BoundedSemaphore(self.limits.concurrency)
        self._cache_lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, int], dict[str, Any]] = OrderedDict()
        self._decoder = PacketDecoder()
        self._history_decoder = SessionHistoryDecoder()
        self._status_decoder = CarStatusDecoder()
        self._lap_data_decoder = LapDataDecoder()

    def status(self) -> dict[str, Any]:
        with self._cache_lock:
            return {"version": LAP_ENRICHMENT_VERSION, "cached_entries": len(self._cache),
                    "cache_capacity": self.limits.cache_entries}

    def laps(self, session_id: str) -> dict[str, Any]:
        if not self._admission.acquire(blocking=False):
            raise LapEnrichmentBusy("lap_enrichment_busy")
        try:
            return self._laps(session_id)
        finally:
            self._admission.release()

    # ------------------------------------------------------------------ derivation

    def _laps(self, session_id: str) -> dict[str, Any]:
        started = self.clock()
        with self.store.connect() as database:
            database.execute("BEGIN")
            session = database.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise EvidenceUnavailable("session_not_found")
            generation = database.execute("SELECT * FROM generations WHERE id=?",
                                          (session["generation"],)).fetchone()
            if generation is None:
                raise EvidenceUnavailable("session_generation_unavailable")
            committed = int(generation["committed_sequence"])
            key = (session_id, committed)
            with self._cache_lock:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    result = copy.deepcopy(cached)
                    result["source"]["cache"] = "hit"
                    return result
            rows = database.execute(
                """SELECT a.id,a.original_id,a.manifest,a.payload,a.published_sequence,
                          (SELECT d.state FROM dispositions d WHERE d.attempt=a.id
                           ORDER BY d.sequence DESC LIMIT 1) AS state
                   FROM attempts a WHERE a.session=? AND a.role='player'
                   ORDER BY a.published_sequence,a.id LIMIT ?""",
                (session_id, self.limits.max_laps + 1),
            ).fetchall()
            attempts_truncated = len(rows) > self.limits.max_laps
            rows = rows[: self.limits.max_laps]
            lower, upper, range_note = self._journal_range(database, session, committed)
            attempts = self._attempts(rows)
            targets = self._targets(attempts)
            scan = self._scan(database, generation, session, attempts, targets, lower, upper, started)
        return self._finish(session, generation, committed, attempts, targets, scan,
                            lower, upper, range_note, attempts_truncated, key)

    @staticmethod
    def _journal_range(database, session, committed: int) -> tuple[int, int, str | None]:
        boundary = database.execute(
            "SELECT sequence FROM metadata WHERE generation=? AND kind='logical_session' "
            "AND json_extract(payload,'$.session_id')=? AND sequence<=? ORDER BY sequence LIMIT 1",
            (session["generation"], session["id"], committed),
        ).fetchone()
        if boundary is not None:
            following = database.execute(
                "SELECT MIN(sequence) FROM metadata WHERE generation=? AND kind='logical_session' "
                "AND sequence>? AND sequence<=?", (session["generation"], boundary[0], committed),
            ).fetchone()[0]
            return boundary[0], following - 1 if following is not None else committed, "published_session_boundaries"
        siblings = database.execute(
            "SELECT id,occurrence FROM sessions WHERE generation=? AND uid=? ORDER BY occurrence",
            (session["generation"], session["uid"]),
        ).fetchall()
        if len(siblings) <= 1:
            return 1, committed, None

        def first_source(identifier: str) -> int | None:
            row = database.execute(
                "SELECT MIN(json_extract(manifest,'$.source_start')) FROM attempts WHERE session=?",
                (identifier,),
            ).fetchone()
            return row[0]

        own = first_source(session["id"])
        later = [value for value in (first_source(item["id"]) for item in siblings
                                     if item["occurrence"] > session["occurrence"]) if value is not None]
        if own is None and session["occurrence"] > 1:
            return 1, committed, "occurrence_range_unknown"
        lower = own if own is not None else 1
        upper = min(later) - 1 if later else committed
        return max(1, lower), min(committed, upper), "occurrence_range_inferred_from_attempts"

    @staticmethod
    def _attempts(rows) -> list[dict[str, Any]]:
        attempts = []
        for row in rows:
            payload = json.loads(row["payload"])
            manifest = json.loads(row["manifest"])
            attempts.append({
                "revision_id": row["id"], "payload": payload, "manifest": manifest,
                "published_sequence": row["published_sequence"], "state": row["state"],
            })
        attempts.sort(key=lambda item: (item["payload"].get("lap_number") or 0,
                                        item["payload"].get("attempt_number") or 0,
                                        item["published_sequence"], item["revision_id"]))
        return attempts

    @staticmethod
    def _targets(attempts: list[dict[str, Any]]) -> list[_Target]:
        targets = []
        for index, attempt in enumerate(attempts):
            payload, manifest = attempt["payload"], attempt["manifest"]
            end = manifest.get("source_end")
            targets.append(_Target(
                index=index, car_index=int(payload.get("car_index", -1)),
                lap_number=int(payload.get("lap_number") or 0),
                start=manifest.get("source_start"), end=end if isinstance(end, int) else None,
                lap_time_ms=payload.get("lap_time_ms"),
            ))
        return targets

    def _decode(self, sequence: int, payload: bytes):
        return self._decoder.decode(RawDatagram(sequence=sequence, captured_at_ns=0, monotonic_ns=0,
                                                source_host="", source_port=0, payload=payload))

    def _scan(self, database, generation, session, attempts, targets: list[_Target],
              lower: int, upper: int, started: float) -> dict[str, Any]:
        result: dict[str, Any] = {
            "history": {}, "rows": 0, "bytes": 0, "counts": {"session_history": 0, "car_status": 0, "lap_data": 0},
            "decode_errors": 0, "truncation": None, "last_sequence": None, "scanned_upper": upper,
            "supported": generation["version"] == PROCESSOR_VERSION and lower <= upper,
        }
        if not result["supported"] or not attempts:
            return result
        if upper - lower + 1 > self.limits.scan_rows:
            upper = lower + self.limits.scan_rows - 1
            result["scanned_upper"] = upper
            result["truncation"] = "journal_row_budget_exceeded"
        try:
            uid = struct.pack("<Q", int(session["uid"]))
        except (ValueError, struct.error) as exception:
            raise EvidenceUnavailable("session_uid_invalid") from exception
        cars = {target.car_index for target in targets if target.car_index >= 0}
        history: dict[int, _CarHistory] = {car: _CarHistory({}) for car in cars}
        result["history"] = history
        pending = sorted((target for target in targets if target.end is not None), key=lambda item: item.end)
        pointer = 0
        status: tuple[int, bytes] | None = None
        ring: deque[tuple[int, bytes]] = deque(maxlen=_LAP_DATA_RING)
        cursor = database.execute(
            """SELECT sequence,payload FROM journal WHERE generation=? AND sequence BETWEEN ? AND ?
               AND kind='datagram' AND substr(payload,8,8)=?
               AND substr(payload,7,1) IN (X'02',X'07',X'0B') ORDER BY sequence""",
            (generation["id"], lower, upper, uid),
        )
        for entry in cursor:
            sequence = entry["sequence"]
            payload = bytes(entry["payload"])
            if result["rows"] % 256 == 0 and self.clock() - started >= self.limits.duration_s:
                result["truncation"] = "time_budget_exceeded"
                break
            result["rows"] += 1
            result["bytes"] += len(payload)
            if result["bytes"] > self.limits.matched_bytes:
                result["truncation"] = "byte_budget_exceeded"
                break
            if len(payload) <= _HEADER_SIZE:
                continue
            while pointer < len(pending) and pending[pointer].end < sequence:
                pending[pointer].status = status
                pending[pointer].lap_data = tuple(ring)
                pending[pointer].resolved = True
                pointer += 1
            result["last_sequence"] = sequence
            packet_id = payload[6]
            if packet_id == int(PacketId.CAR_STATUS):
                status = (sequence, payload)
                result["counts"]["car_status"] += 1
            elif packet_id == int(PacketId.LAP_DATA):
                ring.append((sequence, payload))
                result["counts"]["lap_data"] += 1
            elif packet_id == int(PacketId.SESSION_HISTORY):
                car = payload[_SESSION_HISTORY_CAR_OFFSET]
                owner = history.get(car)
                if owner is None:
                    continue
                body = payload[_HEADER_SIZE:]
                if body == owner.last_body:
                    continue
                owner.last_body = body
                try:
                    decoded = self._history_decoder.decode(self._decode(sequence, payload), frame_ordinal=0)
                except ProtocolError:
                    result["decode_errors"] += 1
                    continue
                if decoded.history is None or decoded.history.car_index != car:
                    result["decode_errors"] += 1
                    continue
                owner.packets += 1
                result["counts"]["session_history"] += 1
                owner.stints = decoded.history.tyre_stints
                for lap in decoded.history.lap_history:
                    number = lap.lap_index + 1
                    existing = owner.laps.get(number)
                    if existing is None or _complete(lap) or not _complete(existing[0]):
                        owner.laps[number] = (lap, sequence)
        else:
            # Full range scanned: remaining targets end at or after the last matched packet.
            last = result["scanned_upper"]
            while pointer < len(pending) and pending[pointer].end <= last:
                pending[pointer].status = status
                pending[pointer].lap_data = tuple(ring)
                pending[pointer].resolved = True
                pointer += 1
            return result
        return result

    # ------------------------------------------------------------------ assembly

    def _finish(self, session, generation, committed: int, attempts, targets: list[_Target], scan: dict[str, Any],
                lower: int, upper: int, range_note: str | None, attempts_truncated: bool,
                key: tuple[str, int]) -> dict[str, Any]:
        context = {}
        if session["context"]:
            try:
                context = json.loads(session["context"])
            except ValueError:
                context = {}
        formula = context.get("formula_id") if isinstance(context.get("formula_id"), int) else None
        scan_truncation = scan["truncation"]
        rows = []
        for attempt, target in zip(attempts, targets):
            rows.append(self._row(attempt, target, scan, formula, scan_truncation))
        stints = self._stints(rows, formula)
        truncated = attempts_truncated or scan_truncation is not None
        source = {
            "version": LAP_ENRICHMENT_VERSION,
            "generation": generation["id"], "session_uid": session["uid"],
            "committed_sequence": committed,
            "journal_range": {"from": lower, "to": scan["scanned_upper"], "committed": committed},
            "range_note": range_note,
            "packets": scan["counts"], "scanned_rows": scan["rows"], "scanned_bytes": scan["bytes"],
            "decode_errors": scan["decode_errors"],
            "truncation": ("attempt_row_budget_exceeded" if attempts_truncated and scan_truncation is None
                           else scan_truncation),
            "processor_supported": scan["supported"] or not attempts,
            "limits": {"scan_rows": self.limits.scan_rows, "matched_bytes": self.limits.matched_bytes,
                       "duration_s": self.limits.duration_s, "max_laps": self.limits.max_laps},
            "derived_from": ["session_history", "car_status", "lap_data"],
            "persistence": "read_side_derivation", "cache": "miss",
        }
        result = {"session_id": session["id"], "laps": rows, "stints": stints,
                  "truncated": truncated, "source": source}
        if scan_truncation != "time_budget_exceeded":
            with self._cache_lock:
                self._cache[key] = copy.deepcopy(result)
                self._cache.move_to_end(key)
                while len(self._cache) > self.limits.cache_entries:
                    self._cache.popitem(last=False)
        return result

    def _row(self, attempt: dict[str, Any], target: _Target, scan: dict[str, Any], formula: int | None,
             scan_truncation: str | None) -> dict[str, Any]:
        payload = attempt["payload"]
        superseded = payload.get("superseded") is not None or attempt["state"] == "superseded"
        sectors, source, sectors_reason = self._sectors(payload, target, scan, scan_truncation)
        tyre, tyre_reason = self._tyre(payload, target, scan, formula, scan_truncation)
        return {
            "revision_id": attempt["revision_id"],
            "attempt_number": payload.get("attempt_number"),
            "lap_number": payload.get("lap_number"),
            "lap_time_ms": payload.get("lap_time_ms"),
            "disposition": payload.get("disposition"),
            "readiness": attempt["state"],
            "superseded": superseded,
            "start_observed": payload.get("start_observed"),
            "valid": payload.get("game_valid") is not False,
            "game_valid": payload.get("game_valid"),
            "pit": bool(payload.get("pit_encountered")),
            "sectors_ms": sectors,
            "sectors_source": source,
            "sectors_reason": sectors_reason,
            "tyre": tyre,
            "tyre_reason": tyre_reason,
        }

    def _sectors(self, payload, target: _Target, scan, scan_truncation):
        lap_time = payload.get("lap_time_ms")
        if payload.get("disposition") != "completed" or not isinstance(lap_time, int) or lap_time <= 0:
            return None, None, "lap_not_completed"
        if not scan["supported"]:
            return None, None, "journal_processor_version_unsupported"
        history = scan["history"].get(target.car_index)
        history_reason = None
        if history is None or not history.packets:
            history_reason = "session_history_not_in_journal"
        else:
            entry = history.laps.get(target.lap_number)
            if entry is None:
                history_reason = "lap_not_in_session_history"
            else:
                lap = entry[0]
                if abs(lap.lap_time_ms - lap_time) > LAP_TIME_TOLERANCE_MS:
                    history_reason = "session_history_lap_time_mismatch"
                elif not (lap.sector1_time_available and lap.sector2_time_available
                          and lap.sector3_time_available):
                    history_reason = "sectors_unavailable_in_session_history"
                else:
                    sectors = [lap.sector1_time_ms, lap.sector2_time_ms, lap.sector3_time_ms]
                    if abs(sum(sectors) - lap_time) > SECTOR_SUM_TOLERANCE_MS:
                        history_reason = "sector_sum_mismatch"
                    else:
                        return sectors, "session_history", None
        fallback, fallback_reason = self._lap_data_sectors(target, scan, lap_time)
        if fallback is not None:
            return fallback, "lap_data", None
        if target.end is None:
            return None, None, "lap_source_range_unavailable"
        if scan_truncation is not None and history_reason in (
                "session_history_not_in_journal", "lap_not_in_session_history"):
            return None, None, "journal_scan_budget_exceeded"
        if history_reason == "session_history_not_in_journal":
            return None, None, fallback_reason or history_reason
        return None, None, history_reason

    def _lap_data_sectors(self, target: _Target, scan, lap_time: int):
        if not target.lap_data:
            return None, ("journal_scan_budget_exceeded" if not target.resolved and target.end is not None
                          else "lap_data_not_in_journal")
        for sequence, payload in reversed(target.lap_data):
            if target.start is not None and sequence < target.start:
                break
            try:
                decoded = self._lap_data_decoder.decode(self._decode(sequence, payload))
            except ProtocolError:
                continue
            if decoded.lap_data is None or not 0 <= target.car_index < len(decoded.lap_data.cars):
                continue
            car = decoded.lap_data.cars[target.car_index]
            if car.current_lap_number != target.lap_number:
                continue
            sector1, sector2 = car.sector1_time_ms, car.sector2_time_ms
            sector3 = lap_time - sector1 - sector2
            if sector1 > 0 and sector2 > 0 and sector3 > 0:
                return [sector1, sector2, sector3], None
            return None, "sectors_unavailable_in_lap_data"
        return None, "lap_data_not_in_journal"

    def _tyre(self, payload, target: _Target, scan, formula: int | None, scan_truncation):
        if target.end is None:
            return None, "lap_source_range_unavailable"
        if not scan["supported"]:
            return None, "journal_processor_version_unsupported"
        reason = "car_status_not_in_journal"
        if target.status is not None:
            sequence, raw = target.status
            if target.start is None or sequence >= target.start:
                try:
                    decoded = self._status_decoder.decode(self._decode(sequence, raw))
                except ProtocolError:
                    decoded = None
                if decoded is not None and decoded.car_status is not None and \
                        0 <= target.car_index < len(decoded.car_status.cars):
                    car = decoded.car_status.cars[target.car_index]
                    return self._tyre_record(car.visual_tyre_compound, car.actual_tyre_compound,
                                             car.tyre_age_laps, "car_status", formula), None
                reason = "car_status_undecodable"
            else:
                reason = "car_status_not_in_lap"
        fallback = self._stint_tyre(scan, target, formula)
        if fallback is not None:
            return fallback, None
        if not target.resolved and scan_truncation is not None:
            return None, "journal_scan_budget_exceeded"
        return None, reason

    def _stint_tyre(self, scan, target: _Target, formula: int | None):
        history = scan["history"].get(target.car_index)
        if history is None or not history.stints or target.lap_number <= 0 or not target.resolved:
            return None
        previous_end = 0
        for stint in history.stints:
            end = stint.end_lap if stint.end_lap != 255 else 10_000
            if previous_end < target.lap_number <= end:
                return self._tyre_record(stint.visual_compound, stint.actual_compound, None,
                                         "session_history_stints", formula)
            previous_end = end
        return None

    @staticmethod
    def _tyre_record(visual: int, actual: int, age: int | None, source: str, formula: int | None) -> dict[str, Any]:
        return {
            "visual_compound": visual, "actual_compound": actual,
            "visual_label": _VISUAL_LABELS.get(formula, {}).get(visual) if formula is not None else None,
            "actual_label": _ACTUAL_LABELS.get(formula, {}).get(actual) if formula is not None else None,
            "age_laps": age, "source": source,
        }

    @staticmethod
    def _stints(rows: list[dict[str, Any]], formula: int | None) -> list[dict[str, Any]]:
        by_lap: dict[int, dict[str, Any]] = {}
        for row in rows:
            if (row["disposition"] != "completed" or row["superseded"] or row["tyre"] is None
                    or not isinstance(row["lap_number"], int)):
                continue
            current = by_lap.get(row["lap_number"])
            if current is None or (row["attempt_number"] or 0) > (current["attempt_number"] or 0):
                by_lap[row["lap_number"]] = row
        stints: list[dict[str, Any]] = []
        for lap_number in sorted(by_lap):
            tyre = by_lap[lap_number]["tyre"]
            last = stints[-1] if stints else None
            age = tyre["age_laps"]
            same = (last is not None and last["visual_compound"] == tyre["visual_compound"]
                    and last["actual_compound"] == tyre["actual_compound"]
                    and not (age is not None and last["_age"] is not None and age < last["_age"]))
            if same:
                last["end_lap"] = lap_number
                last["lap_count"] += 1
                last["end_age_laps"] = age
                last["_age"] = age
            else:
                stints.append({
                    "stint_index": len(stints), "visual_compound": tyre["visual_compound"],
                    "actual_compound": tyre["actual_compound"],
                    "visual_label": tyre["visual_label"], "actual_label": tyre["actual_label"],
                    "start_lap": lap_number, "end_lap": lap_number, "lap_count": 1,
                    "start_age_laps": age, "end_age_laps": age, "_age": age,
                })
        for stint in stints:
            del stint["_age"]
        return stints
