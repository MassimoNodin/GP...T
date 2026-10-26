from __future__ import annotations

import math


def _integer(value, minimum=0, maximum=2**64 - 1):
    return type(value) is int and minimum <= value <= maximum


def qualified_history(row: dict, frame: dict | None):
    history = row.get("reported_history")
    if (not isinstance(history, dict) or history.get("status") != "available"
            or history.get("qualification") != "game_reported_scalar_timing"
            or history.get("coaching_eligible") is not False or not isinstance(frame, dict)):
        return None
    source = history.get("source")
    if (not isinstance(source, dict) or source.get("identity") != row.get("identity")
            or source.get("scope") != {key: frame.get(key) for key in ("session_uid", "packet_format", "epoch")}
            or any(not _integer(source.get(key)) for key in
                   ("frame_ordinal", "frame_identifier", "received_monotonic_ns", "captured_at_ns", "source_sequence"))
            or not _integer(frame.get("frame_ordinal"))
            or not _integer(frame.get("lap_received_monotonic_ns"))
            or source["frame_ordinal"] > frame["frame_ordinal"]
            or source["received_monotonic_ns"] > frame["lap_received_monotonic_ns"]
            or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in (source.get("session_time_s"), frame.get("session_time_s")))
            or source["session_time_s"] > frame["session_time_s"]
            or not _integer(history.get("first_attributable_lap"), 1, 256)
            or not _integer(history.get("reported_lap_count"), 0, 100)
            or not isinstance(history.get("laps"), list) or len(history["laps"]) > 100
            or not isinstance(row.get("lap"), dict)
            or not _integer(row["lap"].get("current_lap_number"), 1, 255)):
        return None
    previous = 0
    for lap in history["laps"]:
        if (not isinstance(lap, dict)
                or not _integer(lap.get("lap_number"), history["first_attributable_lap"], 100)
                or not previous < lap["lap_number"] < row["lap"]["current_lap_number"]
                or lap["lap_number"] > history["reported_lap_count"]
                or not _integer(lap.get("lap_time_ms"), 1, 86_400_000)
                or not _integer(lap.get("validity_flags"), 0, 255)):
            return None
        previous = lap["lap_number"]
    return history


def reported_lap_fact(row: dict, frame: dict | None, number: int) -> str | None:
    history = qualified_history(row, frame)
    if history is None:
        return None
    lap = next((lap for lap in history["laps"] if lap["lap_number"] == number), None)
    if lap is None:
        return None
    if lap["validity_flags"] & ~15:
        return f"game-reported lap {number} unavailable; unknown validity flags"
    validity = "valid" if lap["validity_flags"] & 1 else "invalid"
    return (f"game-reported {validity} lap {number} {lap['lap_time_ms'] / 1000:.3f} seconds; "
            "published timing, not a fully observed coaching reference")


def reported_lap_sector_fact(row: dict, frame: dict | None, number: int, sector: int) -> str | None:
    if not isinstance(row, dict) or not _integer(number, 1, 255) or not _integer(sector, 1, 3):
        return None
    history = qualified_history(row, frame)
    if history is None:
        return None
    lap = next((lap for lap in history["laps"] if lap["lap_number"] == number), None)
    if lap is None:
        return None
    unavailable = f"game-reported lap {number} sector {sector} unavailable"
    flags = lap["validity_flags"]
    if flags & ~15:
        return unavailable + "; unknown validity flags"
    if not flags & (1 << sector):
        return unavailable + "; sector is invalid"
    timings = lap.get("sector_times_ms")
    if (not isinstance(timings, list) or len(timings) != 3
            or not _integer(timings[sector - 1], 1, 86_400_000)):
        return unavailable + "; reported sector time missing"
    validity = "valid" if flags & 1 else "invalid"
    return (f"game-reported {validity} lap {number} sector {sector} "
            f"{timings[sector - 1] / 1000:.3f} seconds; "
            "published timing, not a fully observed coaching reference")


def reported_best_fact(row: dict, frame: dict | None, *, live_age_s: float):
    history = qualified_history(row, frame)
    if history is None:
        return None
    source_age = live_age_s + (frame["lap_received_monotonic_ns"]
                               - history["source"]["received_monotonic_ns"]) / 1e9
    if not 0 <= source_age < 3:
        return "session-best lap unavailable; game-reported history is stale", None
    if history["reported_lap_count"] < row["lap"]["current_lap_number"] - 1:
        return "session-best lap unavailable; game-reported table is incomplete", None
    best = history.get("session_best")
    if (not isinstance(best, dict) or history.get("session_best_reason") is not None
            or not _integer(best.get("lap_number"), 1, 100)
            or not _integer(best.get("lap_time_ms"), 1, 86_400_000)):
        return ("session-best lap unavailable; the game's best-lap selector is not verified "
                "as a valid completed lap for this driver"), None
    selected = next((lap for lap in history["laps"] if lap["lap_number"] == best["lap_number"]), None)
    valid = [lap for lap in history["laps"] if lap["validity_flags"] & 1]
    if (selected is None or not selected["validity_flags"] & 1
            or any(lap["validity_flags"] & ~15 for lap in history["laps"])
            or selected["lap_time_ms"] != best["lap_time_ms"]
            or best["lap_time_ms"] != min(lap["lap_time_ms"] for lap in valid)):
        return "session-best lap unavailable; game-reported selector conflicts with verified timings", None
    return (f"game-reported session-best valid lap {best['lap_number']} "
            f"{best['lap_time_ms'] / 1000:.3f} seconds; published timing, not a coaching reference"), source_age
