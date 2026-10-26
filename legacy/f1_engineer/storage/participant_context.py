from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping


MAX_PLAYER_PARTICIPANT_ATTEMPT_OBSERVATIONS = 128
MAX_VISIBLE_PLAYER_PARTICIPANT_OBSERVATIONS = 16
MAX_PLAYER_PARTICIPANT_CONTEXT_BYTES = 16_384
MAX_ATTEMPT_SCOPE_JSON_BYTES = 32_768
MAX_PARTICIPANT_SOURCE_JSON_BYTES = 4_096
MAX_PROCESSING_METRICS_JSON_BYTES = 65_536

_REPORTED_FIELDS = (
    "ai_controlled",
    "driver_id",
    "network_id",
    "team_id",
    "my_team",
    "race_number",
    "nationality_id",
    "name",
    "your_telemetry",
    "tech_level",
    "platform_id",
)
_BOOLEAN_FIELDS = frozenset({"ai_controlled", "my_team"})
_STRING_FIELDS = frozenset({"name"})
_INTEGER_RANGES = {
    "driver_id": (0, 65_535),
    "network_id": (0, 65_535),
    "team_id": (0, 65_535),
    "race_number": (0, 255),
    "nationality_id": (0, 255),
    "your_telemetry": (0, 255),
    "tech_level": (0, 65_535),
    "platform_id": (0, 255),
}


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


def _participant(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    participant = {field: value.get(field) for field in _REPORTED_FIELDS}
    for field in _BOOLEAN_FIELDS:
        if not isinstance(participant[field], bool):
            return None
    for field in _STRING_FIELDS:
        item = participant[field]
        if not isinstance(item, str):
            return None
        try:
            name_bytes = item.encode("utf-8")
        except UnicodeEncodeError:
            return None
        if len(name_bytes) > 96:
            return None
    for field, (minimum, maximum) in _INTEGER_RANGES.items():
        item = participant[field]
        if (
            isinstance(item, bool)
            or not isinstance(item, int)
            or not minimum <= item <= maximum
        ):
            return None
    return participant


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
    row: Mapping[str, Any] | None, *, expected_player: int, age_frames: int | None = None
) -> tuple[dict[str, object] | None, str | None, dict[str, object] | None]:
    if row is None:
        return None, "participant_not_reported", None
    source = _source(row, age_frames=age_frames)
    if row["status"] != "observed":
        return None, str(row["reason"] or "participant_observation_unavailable"), source
    if row["player_car_index"] is None or int(row["player_car_index"]) != expected_player:
        return None, "player_slot_mismatch", source
    if bool(row["participant_json_oversized"]):
        return None, "participant_payload_exceeds_byte_limit", source
    participant = _participant(row["participant_json"])
    if participant is None:
        return None, "participant_payload_unavailable", source
    return participant, None, source


def _signature(participant: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(participant.get(field) for field in _REPORTED_FIELDS)


def load_attempt_player_participant_context(
    connection: sqlite3.Connection, attempt_key: str
) -> dict[str, object] | None:
    """Bind persisted admitted Participant observations to one attempt boundary."""
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
    try:
        attempt_json = json.loads(attempt["attempt_json"])
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError):
        return _unknown("attempt_scope_json_invalid")
    if not isinstance(attempt_json, dict):
        return _unknown("attempt_scope_unavailable")

    metrics_unavailable = bool(attempt["metrics_json_oversized"])
    run_participant_history_truncated = False
    if attempt["metrics_json"] is not None and not metrics_unavailable:
        try:
            metrics = json.loads(attempt["metrics_json"])
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError):
            metrics_unavailable = True
        else:
            if not isinstance(metrics, dict):
                metrics_unavailable = True
            else:
                capture_quality = metrics.get("capture_quality")
                if not isinstance(capture_quality, dict):
                    metrics_unavailable = True
                else:
                    run_participant_history_truncated = (
                        capture_quality.get(
                            "player_participant_observation_run_truncated"
                        )
                        is True
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

    common = (
        attempt["run_id"],
        attempt["session_uid"],
        packet_format,
        association_epoch,
    )
    baseline = connection.execute(
        f"""SELECT frame_ordinal,frame_identifier,overall_frame_identifier,
                  packet_format,association_epoch,player_car_index,session_time_s,
                  status,reason,active_car_count,
                  CASE WHEN length(CAST(participant_json AS BLOB)) <= {MAX_PARTICIPANT_SOURCE_JSON_BYTES}
                       THEN participant_json ELSE NULL END AS participant_json,
                  length(CAST(participant_json AS BLOB)) > {MAX_PARTICIPANT_SOURCE_JSON_BYTES}
                       AS participant_json_oversized,
                  source_packet_count
             FROM player_participant_observations
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
                      status,reason,active_car_count,
                      CASE WHEN length(CAST(participant_json AS BLOB)) <= {MAX_PARTICIPANT_SOURCE_JSON_BYTES}
                           THEN participant_json ELSE NULL END AS participant_json,
                      length(CAST(participant_json AS BLOB)) > {MAX_PARTICIPANT_SOURCE_JSON_BYTES}
                           AS participant_json_oversized,
                      source_packet_count
                 FROM player_participant_observations
                WHERE run_id = ? AND session_uid = ? AND packet_format = ?
                  AND association_epoch = ? AND frame_ordinal > ? AND frame_ordinal <= ?
                ORDER BY frame_ordinal LIMIT ?""",
            (
                *common,
                start_ordinal,
                int(end_ordinal),
                MAX_PLAYER_PARTICIPANT_ATTEMPT_OBSERVATIONS + 1,
            ),
        ).fetchall()
        query_truncated = len(during) > MAX_PLAYER_PARTICIPANT_ATTEMPT_OBSERVATIONS
        if query_truncated:
            during = during[:MAX_PLAYER_PARTICIPANT_ATTEMPT_OBSERVATIONS]

    age_frames = (
        start_ordinal - int(baseline["frame_ordinal"])
        if baseline is not None
        else None
    )
    start_participant, start_reason, start_source = _row_state(
        baseline, expected_player=player, age_frames=age_frames
    )
    at_start: dict[str, object] = {
        "status": "reported" if start_participant is not None else "unknown",
        "participant": start_participant,
        "source": start_source,
    }
    if start_reason is not None:
        at_start["reason"] = start_reason

    observations: list[dict[str, object]] = []
    current = start_participant
    observed_count = int(start_participant is not None)
    observed_change_count = 0
    unknown_event_count = 0
    for row in during:
        participant, reason, source = _row_state(row, expected_player=player)
        entry: dict[str, object] = {
            "status": "reported" if participant is not None else "unknown",
            "source": source,
        }
        if participant is not None:
            entry["participant"] = participant
            observed_count += 1
            if current is not None and _signature(current) != _signature(participant):
                entry["change"] = "reported_participant_changed"
                observed_change_count += 1
            current = participant
        else:
            entry["reason"] = reason or "participant_observation_unavailable"
            unknown_event_count += 1
            current = None
        if len(observations) < MAX_VISIBLE_PLAYER_PARTICIPANT_OBSERVATIONS:
            observations.append(entry)

    attempt_scope_changed = (
        attempt_json.get("start_association_epoch")
        != attempt_json.get("association_epoch")
        or attempt_json.get("start_association_packet_format")
        != attempt_json.get("association_packet_format")
    )
    association_scope_assessable = attempt_json.get(
        "association_scope_assessable"
    ) is True
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
        or run_participant_history_truncated
    ):
        status = "incomplete"
    elif start_participant is None:
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
            0, len(during) - MAX_VISIBLE_PLAYER_PARTICIPANT_OBSERVATIONS
        ),
        "limits": {
            "maximum_attempt_observations": MAX_PLAYER_PARTICIPANT_ATTEMPT_OBSERVATIONS,
            "maximum_visible_observations": MAX_VISIBLE_PLAYER_PARTICIPANT_OBSERVATIONS,
            "maximum_context_bytes": MAX_PLAYER_PARTICIPANT_CONTEXT_BYTES,
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
        reasons.append("participant_observation_history_truncated")
    if metrics_unavailable:
        reasons.append("processing_metrics_unavailable")
    if run_participant_history_truncated:
        reasons.append("run_participant_observation_history_truncated")
    if reasons:
        result["reasons"] = reasons

    while (
        len(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        > MAX_PLAYER_PARTICIPANT_CONTEXT_BYTES
        and observations
    ):
        observations.pop()
        result["observations_omitted_count"] = int(result["observations_omitted_count"]) + 1
    if (
        len(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        > MAX_PLAYER_PARTICIPANT_CONTEXT_BYTES
    ):
        return _unknown("participant_context_summary_exceeds_byte_limit", scope=scope)
    return result
