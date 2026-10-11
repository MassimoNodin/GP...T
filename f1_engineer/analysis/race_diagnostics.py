from __future__ import annotations

import math
from typing import Any, Protocol

from ..processing.evidence import EvidenceUnavailable


REPORT_VERSION = 1
MAX_TRACE_ROWS = 100_000
RACE_SESSION_TYPE_IDS = frozenset({15, 16, 17})
_FRAME_MASK = 0xFFFFFFFF
_MAX_SESSION_TIME_GAP_S = 1.0


class RaceDiagnosticsProvider(Protocol):
    def evidence(
        self, attempt_id: str, *, session: str | None = None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...


def build_session_race_diagnostics(
    provider: RaceDiagnosticsProvider, session: str, revision: str
) -> dict[str, object]:
    """Describe one published v2 race lap from its bound evidence rows."""
    result: dict[str, object] = {
        "schema_version": REPORT_VERSION,
        "policy": "session-race-diagnostics-v1",
        "status": "unavailable",
        "reason": None,
        "session": session,
        "revision": revision,
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
    }
    if not isinstance(session, str) or not session or not isinstance(revision, str) or not revision:
        return _unavailable(result, "invalid_request_identity")
    try:
        evidence = provider.evidence(revision, session=session)
    except EvidenceUnavailable as exc:
        return _unavailable(result, str(exc))
    except Exception:
        return _unavailable(result, "evidence_unavailable")

    if (
        not isinstance(evidence, tuple)
        or len(evidence) != 2
        or not isinstance(evidence[0], dict)
        or not isinstance(evidence[1], list)
    ):
        return _unavailable(result, "evidence_shape_invalid")
    attempt, rows = evidence
    reasons, context = _admission_reasons(attempt, rows, session, revision)
    if reasons:
        return _unavailable(result, *reasons)

    distances = [_finite(row.get("lap_distance_m")) for row in rows]
    distances = [value for value in distances if value is not None and value >= 0]
    speeds = [_finite(row.get("speed_mps")) for row in rows]
    speeds = [value for value in speeds if value is not None and value >= 0]
    fuel_values = [
        value
        for row in rows
        if row.get("car_status_available") is True
        and (value := _finite(row.get("fuel_in_tank_reported"))) is not None
        and value >= 0
    ]
    tyre_counts: dict[int, int] = {}
    for row in rows:
        compound = row.get("visual_tyre_compound")
        if row.get("car_status_available") is True and type(compound) is int and compound >= 0:
            tyre_counts[compound] = tyre_counts.get(compound, 0) + 1
    fuel_report = _observed_or_missing(fuel_values)
    tyre_report = _compound_observations(tyre_counts)
    qualifications = []
    if fuel_report["status"] != "observed":
        qualifications.append("fuel_context_unknown")
    if tyre_report["status"] != "observed":
        qualifications.append("tyre_context_unknown")
    qualifications.extend(("traffic_context_unknown", "tyre_stints_unknown"))
    sparse_frames = sum(
        current["frame_ordinal"] - previous["frame_ordinal"] > 1
        or ((current["frame_identifier"] - previous["frame_identifier"]) & _FRAME_MASK) > 1
        for previous, current in zip(rows, rows[1:])
    )
    if sparse_frames:
        qualifications.append("nonconsecutive_observation_frames")
    result.update(
        status="available",
        source={
            "revision": revision,
            "session": session,
            "driver": attempt["driver"],
            "car_index": attempt["payload"]["car_index"],
            "epoch": attempt["manifest"]["epoch"],
            "packet_format": attempt["manifest"]["format"],
            "lap_number": attempt["payload"].get("lap_number"),
        },
        lap={
            "lap_time_ms": attempt["payload"]["lap_time_ms"],
            "sample_count": len(rows),
            "nonconsecutive_frame_transitions": sparse_frames,
            "distance_start_m": min(distances) if distances else None,
            "distance_end_m": max(distances) if distances else None,
            "speed_min_mps": min(speeds) if speeds else None,
            "speed_max_mps": max(speeds) if speeds else None,
        },
        context={
            "race_session_type_id": context["session_type_id"],
            "fuel": fuel_report,
            "tyres": tyre_report,
            "traffic": {"status": "unknown", "reason": "not_retained_in_v2_rows"},
            "tyre_stints": {"status": "unknown", "reason": "not_in_single_lap_evidence"},
        },
        qualifications=qualifications,
    )
    return result


def _admission_reasons(
    attempt: dict[str, Any], rows: list[dict[str, Any]], session: str, revision: str
) -> tuple[list[str], dict[str, object]]:
    reasons: list[str] = []
    payload = attempt.get("payload")
    manifest = attempt.get("manifest")
    readiness = attempt.get("readiness")
    if not isinstance(payload, dict) or not isinstance(manifest, dict):
        return ["invalid_attempt_metadata"], {}
    if attempt.get("id") != revision or attempt.get("session") != session:
        reasons.append("revision_session_mismatch")
    if attempt.get("role") != "player":
        reasons.append("not_player_evidence")
    if attempt.get("binding_verified") is not True:
        reasons.append("driver_binding_unverified")
    driver = attempt.get("driver")
    if not isinstance(driver, str) or not driver or manifest.get("driver") != driver:
        reasons.append("verified_driver_unavailable")
    if not isinstance(readiness, dict) or readiness.get("state") != "published":
        reasons.append("evidence_not_published")
    if payload.get("disposition") != "completed":
        reasons.append("not_completed")
    if payload.get("game_valid") is not True:
        reasons.append("game_validity_required")
    if payload.get("start_observed") is not True:
        reasons.append("lap_start_unobserved")
    if payload.get("pit_encountered") is not False:
        reasons.append("pit_encountered_or_unknown")
    if not _integer(payload.get("lap_time_ms"), 1):
        reasons.append("positive_lap_time_required")
    epoch, packet_format = manifest.get("epoch"), manifest.get("format")
    if not _integer(epoch) or not _integer(packet_format, 1):
        reasons.append("session_identity_unavailable")
    start_frame, end_frame = manifest.get("start_frame"), manifest.get("end_frame")
    if not _integer(start_frame) or not _integer(end_frame) or end_frame < start_frame:
        reasons.append("manifest_frame_bounds_unknown")
    qualifications = manifest.get("qualifications")
    if not isinstance(qualifications, list) or any(not isinstance(item, str) for item in qualifications):
        reasons.append("evidence_qualifications_unknown")
    elif qualifications:
        reasons.extend(qualifications)
    declared_rows = attempt.get("rows")
    sample_count = payload.get("sample_count")
    if not _integer(declared_rows, 1) or not _integer(sample_count, 1):
        reasons.append("observation_rows_unavailable")
    elif declared_rows != sample_count or declared_rows != len(rows):
        reasons.append("missing_observation_rows")
    if len(rows) > MAX_TRACE_ROWS:
        reasons.append("trace_row_limit_exceeded")
    if not rows or any(not isinstance(row, dict) for row in rows):
        reasons.append("trace_rows_invalid")
        return list(dict.fromkeys(reasons)), {}

    segments = payload.get("context_segments")
    valid_contexts: list[dict[str, object]] = []
    if not isinstance(segments, list) or not segments:
        reasons.append("unknown_context")
    else:
        for segment in segments:
            context = segment.get("context") if isinstance(segment, dict) else None
            if not isinstance(context, dict):
                reasons.append("unknown_context")
                continue
            session_type_id = context.get("session_type_id")
            if not _integer(session_type_id) or session_type_id not in RACE_SESSION_TYPE_IDS:
                reasons.append("not_supported_race_session_type")
            if context.get("packet_format") != packet_format:
                reasons.append("context_packet_format_mismatch")
            if not _integer(context.get("track_id")):
                reasons.append("unknown_track")
            length = _finite(context.get("track_length_m"))
            if length is None or length <= 0:
                reasons.append("unknown_track_length")
            if context.get("session_uid") is not None and str(context["session_uid"]) != str(payload.get("session_uid")):
                reasons.append("context_session_identity_mismatch")
            valid_contexts.append(context)
    if valid_contexts and any(
        _race_context_key(context) != _race_context_key(valid_contexts[0])
        for context in valid_contexts[1:]
    ):
        reasons.append("race_context_changed")

    row_epochs: set[int] = set()
    gap_epochs: set[int] = set()
    previous: dict[str, object] | None = None
    for row in rows:
        if (
            str(row.get("session_uid")) != str(payload.get("session_uid"))
            or row.get("car_index") != payload.get("car_index")
            or row.get("lap_number") != payload.get("lap_number")
        ):
            reasons.append("trace_ownership_mismatch")
        if row.get("packet_format") != packet_format or row.get("lifecycle_epoch") != epoch:
            reasons.append("trace_session_identity_mismatch")
        row_epoch = row.get("lifecycle_epoch")
        gap_epoch = row.get("gap_epoch")
        if _integer(row_epoch):
            row_epochs.add(row_epoch)
        if not _integer(gap_epoch):
            reasons.append("trace_gap_context_unknown")
        else:
            gap_epochs.add(gap_epoch)
        ordinal = row.get("frame_ordinal")
        frame = row.get("frame_identifier")
        session_time = _finite(row.get("session_time_s"))
        if not _integer(ordinal) or not _integer(frame) or session_time is None or session_time < 0:
            reasons.append("trace_anchor_invalid")
        elif _integer(start_frame) and _integer(end_frame) and not start_frame <= ordinal <= end_frame:
            reasons.append("trace_outside_manifest_frame_bounds")
        if previous is not None:
            prior_ordinal = previous.get("frame_ordinal")
            prior_frame = previous.get("frame_identifier")
            prior_time = _finite(previous.get("session_time_s"))
            if _integer(ordinal) and _integer(prior_ordinal) and ordinal <= prior_ordinal:
                reasons.append("trace_frame_regression")
            if _integer(frame) and _integer(prior_frame):
                frame_step = (frame - prior_frame) & _FRAME_MASK
                if frame_step == 0 or frame_step >= (1 << 31):
                    reasons.append("trace_frame_discontinuity")
            if session_time is not None and prior_time is not None:
                delta = session_time - prior_time
                if delta <= 0 or delta > _MAX_SESSION_TIME_GAP_S:
                    reasons.append("trace_time_gap_or_regression")
        previous = row
    if len(row_epochs) != 1 or (row_epochs and row_epochs != {epoch}):
        reasons.append("trace_epoch_unknown_or_changed")
    if len(gap_epochs) != 1:
        reasons.append("trace_acquisition_gap_or_unknown")

    context = valid_contexts[0] if valid_contexts else {}
    return list(dict.fromkeys(reasons)), context


def _race_context_key(context: dict[str, object]) -> tuple[object, ...]:
    fields = (
        "session_type_id", "packet_format", "track_id", "track_length_m", "game_mode",
        "game_mode_id", "rule_set", "rule_set_id", "formula_id", "equal_car_performance_id",
    )
    return tuple(context.get(field) for field in fields)


def _observed_or_missing(values: list[object]) -> dict[str, object]:
    observed = [value for value in values if value is not None]
    if not observed:
        return {"status": "unknown", "reason": "field_not_retained_or_unavailable"}
    return {"status": "observed", "sample_count": len(observed), "minimum": min(observed), "maximum": max(observed)}


def _compound_observations(counts: dict[int, int]) -> dict[str, object]:
    if not counts:
        return {"status": "unknown", "reason": "field_not_retained_or_unavailable"}
    return {
        "status": "observed",
        "compounds": [
            {"compound_id": compound, "sample_count": counts[compound]}
            for compound in sorted(counts)
        ],
    }


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _unavailable(result: dict[str, object], *reasons: str) -> dict[str, object]:
    stable_reasons = list(dict.fromkeys(reason for reason in reasons if reason))
    result["reason"] = stable_reasons[0] if stable_reasons else "evidence_unavailable"
    result["reasons"] = stable_reasons
    return result
