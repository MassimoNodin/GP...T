from __future__ import annotations

import json
import math
import sqlite3
from typing import Any, Mapping


MAX_PLAYER_CAR_SETUP_ATTEMPT_OBSERVATIONS = 256
MAX_VISIBLE_PLAYER_CAR_SETUP_OBSERVATIONS = 16
MAX_PLAYER_CAR_SETUP_CONTEXT_BYTES = 32_768
MAX_ATTEMPT_SCOPE_JSON_BYTES = 32_768
MAX_CAR_SETUP_SOURCE_JSON_BYTES = 8_192
MAX_PROCESSING_METRICS_JSON_BYTES = 65_536

_INTEGER_FIELDS = (
    "front_wing",
    "rear_wing",
    "on_throttle_differential",
    "off_throttle_differential",
    "front_suspension",
    "rear_suspension",
    "front_anti_roll_bar",
    "rear_anti_roll_bar",
    "front_suspension_height",
    "rear_suspension_height",
    "brake_pressure_percent",
    "brake_bias_percent",
    "engine_braking_percent",
    "ballast",
)
_FLOAT_FIELDS = (
    "front_camber",
    "rear_camber",
    "front_toe",
    "rear_toe",
    "rear_left_tyre_pressure_psi",
    "rear_right_tyre_pressure_psi",
    "front_left_tyre_pressure_psi",
    "front_right_tyre_pressure_psi",
    "fuel_load",
)
_SETUP_FIELDS = frozenset((*_INTEGER_FIELDS, *_FLOAT_FIELDS, "invalid_fields"))


def _unknown(reason: str, *, scope: Mapping[str, object] | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "unknown",
        "continuity_claim": False,
        "reason": reason,
        "scope": dict(scope or {}),
        "at_start": {"status": "unknown", "reason": reason},
        "observations": [],
        "observed_change_count": 0,
        "unknown_event_count": 0,
        "observations_omitted_count": 0,
    }


def _json_object(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _setup(raw: object) -> dict[str, object] | None:
    value = _json_object(raw)
    if value is None or set(value) != _SETUP_FIELDS:
        return None
    result: dict[str, object] = {}
    for field in _INTEGER_FIELDS:
        item = value.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 255:
            return None
        result[field] = item
    for field in _FLOAT_FIELDS:
        item = value.get(field)
        if item is None:
            result[field] = None
        else:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return None
            try:
                numeric = float(item)
            except (TypeError, ValueError, OverflowError):
                return None
            if not math.isfinite(numeric):
                return None
            result[field] = numeric
    invalid_fields = value.get("invalid_fields")
    if (
        not isinstance(invalid_fields, list)
        or any(not isinstance(item, str) or item not in _FLOAT_FIELDS for item in invalid_fields)
        or len(set(invalid_fields)) != len(invalid_fields)
    ):
        return None
    result["invalid_fields"] = invalid_fields
    return result


def _finite_optional(raw: object) -> float | None | bool:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return False
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return False
    return value if math.isfinite(value) else False


def _source(row: Mapping[str, Any], *, age_frames: int | None = None) -> dict[str, object]:
    source: dict[str, object] = {
        "frame_ordinal": int(row["frame_ordinal"]),
        "frame_identifier": int(row["frame_identifier"]),
        "overall_frame_identifier": int(row["overall_frame_identifier"]),
        "session_time_s": row["session_time_s"],
        "packet_format": row["packet_format"],
        "association_epoch": int(row["association_epoch"]),
        "player_car_index": row["player_car_index"],
        "source_packet_count": int(row["source_packet_count"]),
    }
    if age_frames is not None:
        source["age_frames"] = age_frames
    return source


def _row_state(
    row: Mapping[str, Any] | None,
    *,
    expected_player: int,
    age_frames: int | None = None,
) -> tuple[dict[str, object] | None, float | None, str | None, dict[str, object] | None]:
    if row is None:
        return None, None, "setup_not_reported_before_attempt_start", None
    source = _source(row, age_frames=age_frames)
    if row["status"] != "observed":
        return None, None, str(row["reason"] or "setup_observation_unavailable"), source
    if row["player_car_index"] is None or int(row["player_car_index"]) != expected_player:
        return None, None, "player_slot_mismatch", source
    if bool(row["setup_json_oversized"]):
        return None, None, "setup_payload_exceeds_byte_limit", source
    setup = _setup(row["setup_json"])
    next_wing = _finite_optional(row["next_front_wing_value"])
    if setup is None or next_wing is False:
        return None, None, "setup_payload_unavailable", source
    return setup, next_wing, None, source


def load_attempt_player_car_setup_context(
    connection: sqlite3.Connection, attempt_key: str
) -> dict[str, object] | None:
    """Bind admitted player setup snapshots to an attempt start and its later frames."""
    attempt = connection.execute(
        f"""SELECT l.attempt_key,l.car_index,l.start_frame_ordinal,l.end_frame_ordinal,
                  CASE WHEN length(CAST(l.attempt_json AS BLOB)) <= {MAX_ATTEMPT_SCOPE_JSON_BYTES}
                       THEN l.attempt_json ELSE NULL END AS attempt_json,
                  length(CAST(l.attempt_json AS BLOB)) > {MAX_ATTEMPT_SCOPE_JSON_BYTES}
                       AS attempt_json_oversized,
                  s.run_id,s.session_uid,r.status AS processing_status,
                  CASE WHEN length(CAST(r.metrics_json AS BLOB)) <= {MAX_PROCESSING_METRICS_JSON_BYTES}
                       THEN r.metrics_json ELSE NULL END AS metrics_json,
                  length(CAST(r.metrics_json AS BLOB)) > {MAX_PROCESSING_METRICS_JSON_BYTES}
                       AS metrics_json_oversized
             FROM lap_attempts l JOIN sessions s USING(session_key)
             JOIN processing_runs r USING(run_id)
            WHERE l.attempt_key = ?""",
        (attempt_key,),
    ).fetchone()
    if attempt is None:
        return None
    if bool(attempt["attempt_json_oversized"]):
        return _unknown("attempt_scope_exceeds_byte_limit")
    attempt_json = _json_object(attempt["attempt_json"])
    if attempt_json is None:
        return _unknown("attempt_scope_json_invalid")

    metrics_unavailable = bool(attempt["metrics_json_oversized"])
    run_setup_history_truncated = False
    metrics = _json_object(attempt["metrics_json"])
    if attempt["metrics_json"] is not None and metrics is None:
        metrics_unavailable = True
    if metrics is not None:
        capture_quality = metrics.get("capture_quality")
        if not isinstance(capture_quality, dict):
            metrics_unavailable = True
        else:
            run_setup_history_truncated = (
                capture_quality.get("player_car_setup_observation_run_truncated") is True
            )

    start_ordinal = attempt["start_frame_ordinal"]
    end_ordinal = attempt["end_frame_ordinal"]
    player = int(attempt["car_index"])
    packet_format = attempt_json.get("start_association_packet_format")
    association_epoch = attempt_json.get("start_association_epoch")
    scope = {
        "player_car_index": player,
        "packet_format": packet_format,
        "association_epoch": association_epoch,
        "start_frame_ordinal": start_ordinal,
        "end_frame_ordinal": end_ordinal,
    }
    if (
        attempt["processing_status"] != "complete"
        or isinstance(start_ordinal, bool)
        or not isinstance(start_ordinal, int)
        or isinstance(packet_format, bool)
        or not isinstance(packet_format, int)
        or isinstance(association_epoch, bool)
        or not isinstance(association_epoch, int)
    ):
        return _unknown("attempt_scope_unavailable", scope=scope)

    common = (attempt["run_id"], attempt["session_uid"], packet_format, association_epoch)
    baseline = connection.execute(
        f"""SELECT frame_ordinal,frame_identifier,overall_frame_identifier,
                  packet_format,association_epoch,player_car_index,session_time_s,
                  status,reason,
                  CASE WHEN length(CAST(setup_json AS BLOB)) <= {MAX_CAR_SETUP_SOURCE_JSON_BYTES}
                       THEN setup_json ELSE NULL END AS setup_json,
                  length(CAST(setup_json AS BLOB)) > {MAX_CAR_SETUP_SOURCE_JSON_BYTES}
                       AS setup_json_oversized,
                  next_front_wing_value,source_packet_count
             FROM player_car_setup_observations
            WHERE run_id = ? AND session_uid = ? AND packet_format = ?
              AND association_epoch = ? AND frame_ordinal <= ?
            ORDER BY frame_ordinal DESC LIMIT 1""",
        (*common, start_ordinal),
    ).fetchone()

    during: list[sqlite3.Row] = []
    query_truncated = False
    if isinstance(end_ordinal, int) and not isinstance(end_ordinal, bool):
        during = connection.execute(
            f"""SELECT frame_ordinal,frame_identifier,overall_frame_identifier,
                      packet_format,association_epoch,player_car_index,session_time_s,
                      status,reason,
                      CASE WHEN length(CAST(setup_json AS BLOB)) <= {MAX_CAR_SETUP_SOURCE_JSON_BYTES}
                           THEN setup_json ELSE NULL END AS setup_json,
                      length(CAST(setup_json AS BLOB)) > {MAX_CAR_SETUP_SOURCE_JSON_BYTES}
                           AS setup_json_oversized,
                      next_front_wing_value,source_packet_count
                 FROM player_car_setup_observations
                WHERE run_id = ? AND session_uid = ? AND packet_format = ?
                  AND association_epoch = ? AND frame_ordinal > ? AND frame_ordinal <= ?
                ORDER BY frame_ordinal LIMIT ?""",
            (
                *common,
                start_ordinal,
                int(end_ordinal),
                MAX_PLAYER_CAR_SETUP_ATTEMPT_OBSERVATIONS + 1,
            ),
        ).fetchall()
        query_truncated = len(during) > MAX_PLAYER_CAR_SETUP_ATTEMPT_OBSERVATIONS
        if query_truncated:
            during = during[:MAX_PLAYER_CAR_SETUP_ATTEMPT_OBSERVATIONS]

    age_frames = start_ordinal - int(baseline["frame_ordinal"]) if baseline else None
    start_setup, start_next_wing, start_reason, start_source = _row_state(
        baseline, expected_player=player, age_frames=age_frames
    )
    at_start: dict[str, object] = {
        "status": "reported" if start_setup is not None else "unknown",
        "setup": start_setup,
        "next_front_wing_value": start_next_wing,
        "source": start_source,
    }
    if start_reason is not None:
        at_start["reason"] = start_reason

    observations: list[dict[str, object]] = []
    current = (start_setup, start_next_wing) if start_setup is not None else None
    observed_count = int(start_setup is not None)
    observed_change_count = 0
    unknown_event_count = 0
    for row in during:
        setup, next_wing, reason, source = _row_state(row, expected_player=player)
        entry: dict[str, object] = {
            "status": "reported" if setup is not None else "unknown",
            "source": source,
        }
        if setup is not None:
            entry["setup"] = setup
            entry["next_front_wing_value"] = next_wing
            observed_count += 1
            signature = (tuple(setup.items()), next_wing)
            if current is not None and (
                tuple(current[0].items()), current[1]
            ) != signature:
                entry["change"] = "reported_setup_changed"
                observed_change_count += 1
            current = (setup, next_wing)
        else:
            entry["reason"] = reason or "setup_observation_unavailable"
            unknown_event_count += 1
            current = None
        if len(observations) < MAX_VISIBLE_PLAYER_CAR_SETUP_OBSERVATIONS:
            observations.append(entry)

    attempt_scope_changed = (
        attempt_json.get("start_association_epoch")
        != attempt_json.get("association_epoch")
        or attempt_json.get("start_association_packet_format")
        != attempt_json.get("association_packet_format")
    )
    association_scope_assessable = attempt_json.get("association_scope_assessable") is True
    truncated = query_truncated or any(
        row["status"] == "truncated"
        for row in ([baseline] if baseline is not None else []) + during
    )
    if (
        truncated
        or unknown_event_count
        or attempt_scope_changed
        or not association_scope_assessable
        or end_ordinal is None
        or (baseline is not None and start_reason is not None)
        or metrics_unavailable
        or run_setup_history_truncated
    ):
        status = "incomplete"
    elif start_setup is None:
        status = "unknown"
    elif observed_change_count:
        status = "observed_changed"
    elif observed_count >= 2:
        status = "observed_unchanged"
    else:
        status = "observed"

    result: dict[str, object] = {
        "schema_version": 1,
        "status": status,
        "continuity_claim": False,
        "scope": scope,
        "at_start": at_start,
        "observations": observations,
        "observation_count": observed_count + unknown_event_count,
        "observed_change_count": observed_change_count,
        "unknown_event_count": unknown_event_count,
        "observations_omitted_count": max(
            0, len(during) - MAX_VISIBLE_PLAYER_CAR_SETUP_OBSERVATIONS
        ),
        "limits": {
            "maximum_attempt_observations": MAX_PLAYER_CAR_SETUP_ATTEMPT_OBSERVATIONS,
            "maximum_visible_observations": MAX_VISIBLE_PLAYER_CAR_SETUP_OBSERVATIONS,
            "maximum_context_bytes": MAX_PLAYER_CAR_SETUP_CONTEXT_BYTES,
        },
    }
    reasons = []
    if end_ordinal is None:
        reasons.append("attempt_end_unobserved")
    if attempt_scope_changed:
        reasons.append("attempt_association_scope_changed")
    if not association_scope_assessable:
        reasons.append("attempt_association_scope_unassessable")
    if truncated:
        reasons.append("setup_observation_history_truncated")
    if metrics_unavailable:
        reasons.append("processing_metrics_unavailable")
    if run_setup_history_truncated:
        reasons.append("run_setup_observation_history_truncated")
    if reasons:
        result["reasons"] = reasons

    while (
        len(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        > MAX_PLAYER_CAR_SETUP_CONTEXT_BYTES
        and observations
    ):
        observations.pop()
        result["observations_omitted_count"] = (
            int(result["observations_omitted_count"]) + 1
        )
    if (
        len(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        > MAX_PLAYER_CAR_SETUP_CONTEXT_BYTES
    ):
        return _unknown("setup_context_summary_exceeds_byte_limit", scope=scope)
    return result
