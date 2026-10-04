from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from ..storage.query import load_attempt_engineer_summary_metadata
from ..tracks.registry import TrackModelCatalog
from .paired_region_service import PairedRegionReportUnavailable, compare_attempt_regions


ENGINEER_QUERY_SCHEMA_VERSION = 1
ENGINEER_QUERY_VERSION = "engineer-query-v1"
ENGINEER_QUERY_MAX_ID_LENGTH = 256
ENGINEER_QUERY_MAX_FACTS = 6
ENGINEER_QUERY_MAX_WARNINGS = 8
ENGINEER_QUERY_MAX_TEXT_LENGTH = 240

_COMPARISON_POLICIES = frozenset({"time_trial", "practice_qualifying"})
_WARNING_PRIORITY = {
    "capture_incomplete": 0,
    "capture_completion_unknown": 1,
    "recording_loss_reported": 2,
    "replay_frame_exclusions_reported": 3,
    "import_decode_errors": 4,
    "processing_run_incomplete": 5,
    "lifecycle_evidence_incomplete": 6,
    "attempt_superseded": 7,
    "lifecycle_unassessed": 8,
    "game_invalid": 9,
    "attempt_not_completed": 10,
    "timing_evidence_truncated": 11,
    "context_changed": 12,
    "session_context_unknown": 13,
    "recording_loss_counters_unknown": 14,
    "replay_frame_counters_unknown": 15,
    "session_history_timing_unavailable": 16,
    "measurement_unavailable": 40,
}


@dataclass(frozen=True, slots=True)
class AttemptSummaryRequest:
    intent: Literal["attempt_summary"]
    target_attempt_key: str


@dataclass(frozen=True, slots=True)
class RegionComparisonRequest:
    intent: Literal["region_comparison"]
    target_attempt_key: str
    reference_attempt_key: str
    comparison_policy: Literal["time_trial", "practice_qualifying"]
    track_model_id: str
    track_model_revision: int
    region_identifier: str


EngineerQueryRequest: TypeAlias = AttemptSummaryRequest | RegionComparisonRequest


def parse_engineer_query_request(value: object) -> EngineerQueryRequest:
    """Parse a strict intent-discriminated request shared by API and CLI."""
    if not isinstance(value, Mapping):
        raise ValueError("engineer_query_request_must_be_an_object")
    intent = value.get("intent")
    if intent == "attempt_summary":
        _require_exact_fields(value, {"intent", "target_attempt_key"})
        return AttemptSummaryRequest(
            intent="attempt_summary",
            target_attempt_key=_identity(value.get("target_attempt_key"), "target_attempt_key"),
        )
    if intent == "region_comparison":
        _require_exact_fields(
            value,
            {
                "intent",
                "target_attempt_key",
                "reference_attempt_key",
                "comparison_policy",
                "track_model_id",
                "track_model_revision",
                "region_identifier",
            },
        )
        policy = value.get("comparison_policy")
        if not isinstance(policy, str) or policy not in _COMPARISON_POLICIES:
            raise ValueError("unsupported_comparison_policy")
        revision = value.get("track_model_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("track_model_revision_must_be_a_positive_integer")
        return RegionComparisonRequest(
            intent="region_comparison",
            target_attempt_key=_identity(value.get("target_attempt_key"), "target_attempt_key"),
            reference_attempt_key=_identity(value.get("reference_attempt_key"), "reference_attempt_key"),
            comparison_policy=policy,  # type: ignore[arg-type]
            track_model_id=_identity(value.get("track_model_id"), "track_model_id"),
            track_model_revision=revision,
            region_identifier=_identity(value.get("region_identifier"), "region_identifier"),
        )
    raise ValueError("unsupported_engineer_query_intent")


def query_engineer_evidence(
    database_path: str | Path,
    request: EngineerQueryRequest | Mapping[str, object],
    *,
    track_model_catalog: TrackModelCatalog | None = None,
) -> dict[str, object]:
    """Build a small deterministic explanation from persisted evidence."""
    selected = parse_engineer_query_request(request)
    if isinstance(selected, AttemptSummaryRequest):
        return _attempt_summary(database_path, selected)
    return _region_comparison(database_path, selected, track_model_catalog)


def _attempt_summary(
    database_path: str | Path, request: AttemptSummaryRequest
) -> dict[str, object]:
    source = load_attempt_engineer_summary_metadata(
        database_path, request.target_attempt_key
    )
    selected = {"target_attempt_key": request.target_attempt_key}
    if source is None:
        return _report(
            request.intent,
            "unavailable",
            selected,
            reason_codes=["attempt_unavailable"],
            verification_scope="metadata_only",
        )

    attempt = _mapping(source.get("attempt")) or {}
    context = _mapping(source.get("context")) or {}
    latest_context = _mapping(source.get("latest_context")) or {}
    processing = _mapping(source.get("processing")) or {}
    capture = _mapping(source.get("capture")) or {}
    trace = _mapping(source.get("trace_metadata")) or {}
    timing = _mapping(source.get("timing_evidence")) or {}
    metrics = _mapping(processing.get("metrics")) or {}
    capture_quality = _mapping(metrics.get("capture_quality")) or {}
    import_summary = _mapping(metrics.get("summary")) or {}
    completion = _mapping(capture.get("completion")) or {}
    metadata_limits = _mapping(source.get("metadata_limits")) or {}

    facts: list[dict[str, object]] = []
    facts.append(
        _fact(
            "attempt_disposition",
            f"Attempt {attempt.get('attempt_number')} is {attempt.get('disposition', 'unknown')}; lap number {attempt.get('lap_number', 'unknown')}.",
            ["lap_attempts.attempt_number", "lap_attempts.disposition", "lap_attempts.lap_number"],
        )
    )

    context_segment_count = _integer(source.get("context_segment_count"))
    context_parts = _session_context_parts(context)
    latest_context_parts = _session_context_parts(latest_context)
    context_is_unknown = any(value in {"unknown", "unsupported", "unavailable"} for value in context_parts)
    if context_segment_count is not None and context_segment_count > 1:
        context_text = "Recorded context changed; initial: " + (
            ", ".join(context_parts) if context_parts else "unknown"
        ) + ". Latest session snapshot: " + (
            ", ".join(latest_context_parts) if latest_context_parts else "unknown"
        ) + "."
    else:
        context_text = "Recorded session context: " + (
            ", ".join(context_parts) if context_parts else "mode and track unknown"
        ) + "."
    facts.append(
        _fact(
            "session_context",
            context_text,
            ["lap_context_segments.ordinal=0", "lap_context_segments.count", "sessions.context_json"],
        )
    )

    game_valid = attempt.get("game_valid")
    validity_text = (
        "The game marked this lap valid."
        if game_valid is True
        else "The game marked this lap invalid."
        if game_valid is False
        else "Game validity was not reported."
    )
    facts.append(_fact("game_validity", validity_text, ["lap_attempts.game_valid"]))

    lap_time_ms = _integer(attempt.get("lap_time_ms"))
    if lap_time_ms is not None and lap_time_ms >= 0:
        facts.append(
            _fact(
                "recorded_lap_time",
                f"Recorded lap time: {_lap_time_text(lap_time_ms)}.",
                ["lap_attempts.lap_time_ms"],
            )
        )

    timing_facts, timing_warnings = _timing_evidence_facts(timing)
    facts.extend(timing_facts)
    facts.append(
        _fact(
            "lifecycle_assessment",
            "Lifecycle was assessed for this attempt."
            if attempt.get("lifecycle_assessed") is True
            else "Lifecycle assessment is unavailable for this attempt.",
            ["lap_attempts.lifecycle_assessed", "lap_attempts.superseded"],
        )
    )

    warnings: list[dict[str, object]] = []
    _add_attempt_warnings(warnings, attempt, source, capture, processing, completion)
    warnings.extend(timing_warnings)
    if context_segment_count is None:
        warnings.append(_warning("session_context_unknown", "Session context is unavailable.", ["lap_context_segments.ordinal=0"]))
    elif context_segment_count > 1:
        warnings.append(_warning("context_changed", "Session context changed during this attempt.", ["lap_context_segments.count"]))
    elif not context_parts or context_is_unknown:
        warnings.append(_warning("session_context_unknown", "Session mode and track are unknown.", ["lap_context_segments.ordinal=0"]))
    for limit_key, text in (
        ("attempt_reasons_truncated", "Attempt exclusion metadata exceeded its read limit."),
        ("context_truncated", "Session context metadata exceeded its read limit."),
        ("latest_context_truncated", "Latest session snapshot metadata exceeded its read limit."),
        ("timing_evidence_truncated", "Reported timing evidence exceeded its read limit."),
        ("capture_completion_truncated", "Capture completion metadata exceeded its read limit."),
        ("processing_metrics_truncated", "Processing metrics exceeded their read limit."),
    ):
        if metadata_limits.get(limit_key) is True:
            code = "timing_evidence_truncated" if limit_key == "timing_evidence_truncated" else "metadata_read_limit"
            source_field = "sessions.context_json" if limit_key == "latest_context_truncated" else f"metadata_limits.{limit_key}"
            warnings.append(_warning(code, text, [source_field]))

    provenance = {
        "verification_scope": "metadata_only",
        "run_id": _bounded_string(_mapping(source.get("scope")) or {}, "run_id"),
        "session_uid": _bounded_string(_mapping(source.get("scope")) or {}, "session_uid"),
        "capture": {
            "sha256": _bounded_string(capture, "sha256"),
            "byte_size": _integer(capture.get("byte_size")),
            "complete": capture.get("complete") if isinstance(capture.get("complete"), bool) else None,
            "footer_status": _bounded_string(completion, "status"),
        },
        "processing": {
            "status": _bounded_string(processing, "status"),
            "pipeline_version": _bounded_string(processing, "pipeline_version"),
            "import_counters": _bounded_counters(import_summary, (
                "packet_count", "malformed_packet_count", "lap_data_errors",
                "car_telemetry_errors", "motion_decode_errors", "car_status_decode_errors",
                "car_damage_decode_errors", "event_decode_errors", "session_history_decode_errors",
            )),
            "replay_counters": _bounded_counters(capture_quality, (
                "import_late_packets_ignored", "import_frame_overflow_packets_dropped",
            "lifecycle_events_dropped", "lifecycle_evidence_truncated_session_count",
            "lifecycle_reconciliation_truncated_session_count",
            "session_history_packets_dropped",
            )),
        },
        "trace_metadata": {
            "ready": trace.get("ready") if isinstance(trace.get("ready"), bool) else None,
            "row_count": _integer(trace.get("row_count")),
            "sha256": _bounded_string(trace, "sha256"),
            "schema_version": _integer(trace.get("schema_version")),
            "checksum_verified": False,
        },
        "timing_status": _bounded_string(timing, "status") or "unknown",
    }
    status = "partial" if warnings else "available"
    return _report(
        request.intent,
        status,
        selected,
        facts=facts,
        warnings=warnings,
        provenance=provenance,
    )


def _region_comparison(
    database_path: str | Path,
    request: RegionComparisonRequest,
    catalog: TrackModelCatalog | None,
) -> dict[str, object]:
    selected = {
        "target_attempt_key": request.target_attempt_key,
        "reference_attempt_key": request.reference_attempt_key,
        "comparison_policy": request.comparison_policy,
        "track_model_id": request.track_model_id,
        "track_model_revision": request.track_model_revision,
        "region_identifier": request.region_identifier,
    }
    if catalog is None:
        return _report(
            request.intent,
            "unavailable",
            selected,
            reason_codes=["track_model_catalog_unavailable"],
            verification_scope="not_verified",
        )
    try:
        entry = catalog.resolve_entry(request.track_model_id, request.track_model_revision)
    except ValueError:
        return _report(
            request.intent,
            "unavailable",
            selected,
            reason_codes=["track_model_unavailable"],
            verification_scope="not_verified",
        )

    try:
        report = compare_attempt_regions(
            database_path,
            request.target_attempt_key,
            request.reference_attempt_key,
            entry.model,
            model_metadata=entry.metadata(),
            policy=request.comparison_policy,
        )
    except PairedRegionReportUnavailable as exc:
        return _report(
            request.intent,
            "unavailable",
            selected,
            reason_codes=[_reason_code(exc.reason_code)],
            provenance={
                "verification_scope": "not_verified",
                "model": _model_provenance(entry.metadata()),
            },
        )
    if report is None:
        return _report(
            request.intent,
            "unavailable",
            selected,
            reason_codes=["attempt_pair_unavailable"],
            provenance={
                "verification_scope": "not_verified",
                "model": _model_provenance(entry.metadata()),
            },
        )

    regions = report.get("regions")
    selected_region = next(
        (
            _mapping(item)
            for item in regions
            if isinstance(item, Mapping)
            and item.get("identifier") == request.region_identifier
        ),
        None,
    ) if isinstance(regions, Sequence) and not isinstance(regions, (str, bytes)) else None
    report_model = _mapping(report.get("model")) or entry.metadata()
    provenance = _paired_provenance(report, report_model)
    if selected_region is None:
        return _report(
            request.intent,
            "unavailable",
            selected,
            reason_codes=["region_identifier_unavailable"],
            provenance=provenance,
        )

    facts: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    debrief = _mapping(selected_region.get("debrief"))
    raw_facts = debrief.get("facts") if debrief else None
    if isinstance(raw_facts, Sequence) and not isinstance(raw_facts, (str, bytes)):
        for item in raw_facts:
            fact = _copy_debrief_fact(item)
            if fact is not None:
                facts.append(fact)
    else:
        warnings.append(_warning("region_debrief_unavailable", "The selected region has no usable diagnostic debrief.", ["region.debrief.facts"]))

    supported = _mapping(selected_region.get("supported_differences")) or {}
    release = _mapping(supported.get("brake_10_percent_release")) or {}
    release_fact = _brake_release_fact(release)
    if release_fact is not None:
        facts.append(release_fact)
    else:
        reason = (
            "supported_measurement_malformed"
            if release.get("status") == "supported"
            else _reason_code(release.get("unavailable_reason"))
        )
        warnings.append(
            _warning(
                "measurement_unavailable",
                f"10% brake-threshold release measurement unavailable ({reason}).",
                ["region.supported_differences.brake_10_percent_release.unavailable_reason"],
            )
        )

    omissions = debrief.get("omissions") if debrief else None
    if isinstance(omissions, Sequence) and not isinstance(omissions, (str, bytes)):
        for omission in omissions:
            item = _mapping(omission)
            if item is None:
                continue
            kind = _safe_label(item.get("kind")) or "diagnostic"
            reason = _reason_code(item.get("reason_code"))
            omission_text = item.get("text")
            warnings.append(
                _warning(
                    "measurement_unavailable",
                    omission_text
                    if isinstance(omission_text, str)
                    and len(omission_text) <= ENGINEER_QUERY_MAX_TEXT_LENGTH
                    else f"{kind.replace('_', ' ')} measurement unavailable ({reason}).",
                    ["region.debrief.omissions"],
                )
            )

    report_warnings = _mapping(report.get("warnings")) or {}
    for side in ("target", "reference"):
        side_warnings = report_warnings.get(side)
        if not isinstance(side_warnings, Sequence) or isinstance(side_warnings, (str, bytes)):
            continue
        for warning in side_warnings:
            item = _mapping(warning)
            if item is None:
                continue
            code = _reason_code(item.get("code"))
            text = _safe_label(item.get("text"))
            if text:
                warnings.append(
                    _warning(
                        code,
                        text,
                        [f"warnings.{side}.{code}"],
                        sides=[side],
                    )
                )
    facts, omitted_facts = _bound_facts(facts)
    ordered_warnings, omitted_warnings = _bound_warnings(warnings)
    status = "partial" if ordered_warnings or omitted_warnings or omitted_facts else "available"
    return _report(
        request.intent,
        status,
        selected,
        facts=facts,
        warnings=ordered_warnings,
        omitted_fact_count=omitted_facts,
        omitted_warning_count=omitted_warnings,
        provenance=provenance,
    )


def _add_attempt_warnings(
    warnings: list[dict[str, object]],
    attempt: Mapping[str, object],
    source: Mapping[str, object],
    capture: Mapping[str, object],
    processing: Mapping[str, object],
    completion: Mapping[str, object],
) -> None:
    metrics = _mapping(processing.get("metrics")) or {}
    capture_quality = _mapping(metrics.get("capture_quality")) or {}
    import_summary = _mapping(metrics.get("summary")) or {}
    if attempt.get("disposition") != "completed":
        warnings.append(_warning("attempt_not_completed", "This attempt is partial or abandoned.", ["lap_attempts.disposition"]))
    if attempt.get("game_valid") is False:
        warnings.append(_warning("game_invalid", "The game marked this lap invalid.", ["lap_attempts.game_valid"]))
    if attempt.get("game_valid") is None:
        warnings.append(_warning("game_validity_unknown", "Game validity is unknown.", ["lap_attempts.game_valid"]))
    if attempt.get("superseded") is True:
        warnings.append(_warning("attempt_superseded", "Lifecycle evidence supersedes this attempt.", ["lap_attempts.superseded"]))
    if attempt.get("lifecycle_assessed") is not True or attempt.get("superseded") is None:
        warnings.append(_warning("lifecycle_unassessed", "Lifecycle evidence is unassessed.", ["lap_attempts.lifecycle_assessed", "lap_attempts.superseded"]))

    capture_complete = capture.get("complete")
    if capture_complete is False or completion.get("status") == "incomplete":
        warnings.append(_warning("capture_incomplete", "The source capture footer is incomplete.", ["captures.complete", "captures.completion_json.status"]))
    elif capture_complete is not True or not completion:
        warnings.append(_warning("capture_completion_unknown", "Capture completion is unknown.", ["captures.complete", "captures.completion_json"]))

    recording = _mapping(completion.get("recording_counters")) or completion
    loss_keys = ("queue_dropped", "unpersisted_on_shutdown", "socket_errors")
    loss_values = [recording.get(key) for key in loss_keys]
    loss_known = [_nonnegative_counter(value) for value in loss_values]
    if any(value is not None and value > 0 for value in loss_known):
        text = "Recorder loss or socket errors were reported."
        if any(value is None for value in loss_known):
            text += " Other loss counters are unknown."
        warnings.append(_warning("recording_loss_reported", text, [f"captures.completion_json.{key}" for key in loss_keys]))
    elif any(value is None for value in loss_known):
        warnings.append(_warning("recording_loss_counters_unknown", "Some recorder loss counters are unknown.", [f"captures.completion_json.{key}" for key in loss_keys]))

    replay_keys = ("import_late_packets_ignored", "import_frame_overflow_packets_dropped")
    replay_values = [capture_quality.get(key) for key in replay_keys]
    replay_known = [_nonnegative_counter(value) for value in replay_values]
    if any(value is not None and value > 0 for value in replay_known):
        text = "Replay excluded late or overflowed frames."
        if any(value is None for value in replay_known):
            text += " Other replay counters are unknown."
        warnings.append(_warning("replay_frame_exclusions_reported", text, [f"processing.capture_quality.{key}" for key in replay_keys]))
    elif any(value is None for value in replay_known):
        warnings.append(_warning("replay_frame_counters_unknown", "Some replay frame counters are unknown.", [f"processing.capture_quality.{key}" for key in replay_keys]))

    lifecycle_limit_keys = (
        "lifecycle_events_dropped",
        "lifecycle_evidence_truncated_session_count",
        "lifecycle_reconciliation_truncated_session_count",
    )
    truncated_lifecycle = [
        key for key in lifecycle_limit_keys
        if (_integer(capture_quality.get(key)) or 0) > 0
    ]
    if truncated_lifecycle:
        warnings.append(
            _warning(
                "lifecycle_evidence_incomplete",
                "The import reported dropped or truncated lifecycle evidence.",
                [f"processing.capture_quality.{key}" for key in truncated_lifecycle],
            )
        )

    decode_keys = (
        "malformed_packet_count", "lap_data_errors", "car_telemetry_errors",
        "motion_decode_errors", "car_status_decode_errors", "car_damage_decode_errors",
        "event_decode_errors", "session_history_decode_errors",
    )
    positive_errors = [
        key
        for key in decode_keys
        if _integer(import_summary.get(key)) not in (None, 0)
    ]
    if positive_errors:
        warnings.append(_warning("import_decode_errors", "The import reported malformed packets or decoder errors.", [f"processing.summary.{key}" for key in positive_errors]))
    if processing.get("status") != "complete":
        warnings.append(_warning("processing_run_incomplete", "The processing run is not marked complete.", ["processing_runs.status"]))


def _timing_evidence_facts(
    timing: Mapping[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    facts: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    status = timing.get("status")
    if status != "matched":
        reasons = timing.get("reasons")
        reason = _reason_code(reasons[0] if isinstance(reasons, Sequence) and reasons else status)
        warnings.append(
            _warning(
                "session_history_timing_unavailable",
                f"Session History timing is unavailable ({reason}); no reported sector values are assumed.",
                ["attempt_timing_evidence.status", "attempt_timing_evidence.reasons"],
            )
        )
        return facts, warnings
    values: list[str] = []
    refs: list[str] = []
    for field, label in (
        ("reported_lap_time_ms", "lap"),
        ("sector1_time_ms", "S1"),
        ("sector2_time_ms", "S2"),
        ("sector3_time_ms", "S3"),
    ):
        value = _integer(timing.get(field))
        if value is not None and value >= 0:
            values.append(f"{label} {_lap_time_text(value)}")
            refs.append(f"attempt_timing_evidence.{field}")
    if values:
        facts.append(_fact("session_history_timing", "Session History reported " + "; ".join(values) + ".", refs))
    else:
        warnings.append(_warning("session_history_timing_unavailable", "Session History matched, but no lap or sector time values are available.", ["attempt_timing_evidence.status", "attempt_timing_evidence.reported_lap_time_ms"]))
    return facts, warnings


def _copy_debrief_fact(value: object) -> dict[str, object] | None:
    item = _mapping(value)
    if item is None:
        return None
    kind = item.get("kind")
    text = item.get("text")
    fields = item.get("source_fields")
    if (
        not isinstance(kind, str)
        or not isinstance(text, str)
        or len(text) > ENGINEER_QUERY_MAX_TEXT_LENGTH
        or not isinstance(fields, Sequence)
        or isinstance(fields, (str, bytes))
        or any(not isinstance(field, str) or len(field) > 200 for field in fields)
        or len(fields) > 12
    ):
        return None
    return {"kind": kind[:80], "text": text, "source_fields": list(fields)}


def _brake_release_fact(value: Mapping[str, object]) -> dict[str, object] | None:
    if value.get("status") != "supported" or value.get("analysis_version") != "brake-threshold-release-v1":
        return None
    target = _bracket(value.get("target_end_bracket_m"))
    reference = _bracket(value.get("reference_end_bracket_m"))
    difference = _bracket(value.get("target_minus_reference_end_bracket_m"))
    censoring = _mapping(value.get("left_censored"))
    if target is None or reference is None or difference is None or censoring is None:
        return None
    if value.get("unit") != "m" or any(not isinstance(censoring.get(side), bool) for side in ("target", "reference")):
        return None
    if not math.isclose(difference[0], target[0] - reference[1], rel_tol=0.0, abs_tol=1e-6):
        return None
    if not math.isclose(difference[1], target[1] - reference[0], rel_tol=0.0, abs_tol=1e-6):
        return None
    text = (
        "Brake <10% release: target "
        f"{_format_bracket(target)}m; ref {_format_bracket(reference)}m; "
        f"target−ref {_format_bracket(difference, signed=True)}m "
        "(positive=further)."
    )
    censored = [side for side in ("target", "reference") if censoring[side]]
    if censored:
        labels = ", ".join("target" if side == "target" else "ref" for side in censored)
        text += " Left-censored: " + labels + "."
    if len(text) > ENGINEER_QUERY_MAX_TEXT_LENGTH:
        return None
    return _fact(
        "brake_10_percent_release",
        text,
        [
            "region.supported_differences.brake_10_percent_release.target_end_bracket_m",
            "region.supported_differences.brake_10_percent_release.reference_end_bracket_m",
            "region.supported_differences.brake_10_percent_release.target_minus_reference_end_bracket_m",
            "region.supported_differences.brake_10_percent_release.left_censored",
        ],
    )


def _paired_provenance(
    report: Mapping[str, object], model: Mapping[str, object]
) -> dict[str, object]:
    attempts = _mapping(report.get("attempts")) or {}
    target = _mapping(attempts.get("target")) or {}
    reference = _mapping(attempts.get("reference")) or {}
    target_capture = _mapping(target.get("capture")) or {}
    reference_capture = _mapping(reference.get("capture")) or {}
    return {
        "verification_scope": "checksummed_trace_analysis",
        "run_id": _bounded_string(target, "run_id"),
        "capture": {
            "target_sha256": _bounded_string(target_capture, "sha256"),
            "reference_sha256": _bounded_string(reference_capture, "sha256"),
            "target_complete": target_capture.get("complete") if isinstance(target_capture.get("complete"), bool) else None,
            "reference_complete": reference_capture.get("complete") if isinstance(reference_capture.get("complete"), bool) else None,
        },
        "attempts": {
            "target": _trace_provenance(target),
            "reference": _trace_provenance(reference),
        },
        "model": _model_provenance(model),
        "source_analysis_version": _bounded_string(report, "analysis_version"),
        "region_analysis_version": _bounded_string(report, "region_analysis_version"),
    }


def _trace_provenance(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "attempt_key": _bounded_string(value, "attempt_key"),
        "trace_sha256": _bounded_string(value, "trace_sha256"),
        "trace_schema_version": _integer(value.get("trace_schema_version")),
    }


def _model_provenance(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "model_id": _bounded_string(value, "model_id"),
        "revision": _integer(value.get("revision")),
        "content_sha256": _bounded_string(value, "content_sha256"),
        "origin": _bounded_string(value, "origin"),
    }


def _report(
    intent: str,
    status: Literal["available", "partial", "unavailable"],
    selected: Mapping[str, object],
    *,
    facts: Sequence[Mapping[str, object]] = (),
    warnings: Sequence[Mapping[str, object]] = (),
    reason_codes: Sequence[str] = (),
    omitted_fact_count: int = 0,
    omitted_warning_count: int | None = None,
    provenance: Mapping[str, object] | None = None,
    verification_scope: str | None = None,
) -> dict[str, object]:
    bounded_facts, extra_facts = _bound_facts(facts)
    bounded_warnings, extra_warnings = _bound_warnings(warnings)
    result = {
        "schema_version": ENGINEER_QUERY_SCHEMA_VERSION,
        "analysis_version": ENGINEER_QUERY_VERSION,
        "artifact_kind": "engineer_query",
        "intent": intent,
        "status": status,
        "reason_codes": list(reason_codes),
        "selected": dict(selected),
        "facts": bounded_facts,
        "omitted_fact_count": max(0, omitted_fact_count) + extra_facts,
        "warnings": bounded_warnings,
        "omitted_warning_count": extra_warnings if omitted_warning_count is None else max(0, omitted_warning_count) + extra_warnings,
        "provenance": dict(provenance or {"verification_scope": verification_scope or "not_verified"}),
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
    }
    return result


def _bound_facts(
    values: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], int]:
    accepted: list[dict[str, object]] = []
    omitted = 0
    for item in values:
        if len(accepted) >= ENGINEER_QUERY_MAX_FACTS:
            omitted += 1
            continue
        text = item.get("text")
        fields = item.get("source_fields")
        kind = item.get("kind")
        if (
            not isinstance(text, str)
            or len(text) > ENGINEER_QUERY_MAX_TEXT_LENGTH
            or not isinstance(kind, str)
            or not isinstance(fields, Sequence)
            or isinstance(fields, (str, bytes))
            or len(fields) > 12
            or any(not isinstance(field, str) or len(field) > 200 for field in fields)
        ):
            omitted += 1
            continue
        accepted.append({"kind": kind[:80], "text": text, "source_fields": list(fields)})
    return accepted, omitted


def _bound_warnings(
    values: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], int]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for item in values:
        code = _reason_code(item.get("code"))
        text = item.get("text")
        fields = item.get("source_fields", [])
        if (
            not isinstance(text, str)
            or len(text) > ENGINEER_QUERY_MAX_TEXT_LENGTH
            or not isinstance(fields, Sequence)
            or isinstance(fields, (str, bytes))
            or any(not isinstance(field, str) or len(field) > 200 for field in fields)
        ):
            continue
        key = (code, text)
        grouped_item = grouped.setdefault(
            key,
            {"code": code, "text": text, "source_fields": [], "sides": []},
        )
        grouped_fields = grouped_item["source_fields"]
        assert isinstance(grouped_fields, list)
        for field in fields:
            if field not in grouped_fields and len(grouped_fields) < 12:
                grouped_fields.append(field)
        side = item.get("sides")
        if isinstance(side, Sequence) and not isinstance(side, (str, bytes)):
            sides = grouped_item["sides"]
            assert isinstance(sides, list)
            for name in side:
                if name in {"target", "reference"} and name not in sides:
                    sides.append(name)
    ordered = sorted(
        grouped.values(),
        key=lambda item: (_WARNING_PRIORITY.get(str(item["code"]), 30), str(item["code"]), str(item["text"])),
    )
    omitted = max(0, len(ordered) - ENGINEER_QUERY_MAX_WARNINGS)
    return ordered[:ENGINEER_QUERY_MAX_WARNINGS], omitted


def _fact(kind: str, text: str, source_fields: Sequence[str]) -> dict[str, object]:
    return {
        "kind": kind[:80],
        "text": text[:ENGINEER_QUERY_MAX_TEXT_LENGTH],
        "source_fields": list(source_fields[:12]),
    }


def _warning(
    code: str,
    text: str,
    source_fields: Sequence[str],
    *,
    sides: Sequence[str] = (),
) -> dict[str, object]:
    return {
        "code": _reason_code(code),
        "text": text[:ENGINEER_QUERY_MAX_TEXT_LENGTH],
        "source_fields": list(source_fields[:12]),
        "sides": list(sides),
    }


def _require_exact_fields(value: Mapping[str, object], allowed: set[str]) -> None:
    unknown = set(value) - allowed
    missing = allowed - set(value)
    if unknown:
        raise ValueError("unknown_engineer_query_request_field")
    if missing:
        raise ValueError("engineer_query_request_field_missing")


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > ENGINEER_QUERY_MAX_ID_LENGTH:
        raise ValueError(f"invalid_{field}")
    return value


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _nonnegative_counter(value: object) -> int | None:
    result = _integer(value)
    return result if result is not None and result >= 0 else None


def _bounded_string(value: Mapping[str, object], key: str) -> str | None:
    result = value.get(key)
    return result if isinstance(result, str) and len(result) <= 256 else None


def _bounded_counters(value: Mapping[str, object], keys: Sequence[str]) -> dict[str, int | None]:
    return {key: _integer(value.get(key)) for key in keys}


def _context_label(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value[:80].replace("_", " ")
    return None


def _session_context_parts(context: Mapping[str, object]) -> list[str]:
    return [
        value
        for value in (
            _context_label(context.get("session_type")),
            _context_label(context.get("game_mode")),
            _context_label(context.get("track_name")),
        )
        if value is not None
    ]


def _lap_time_text(value_ms: int) -> str:
    minutes, remainder = divmod(value_ms, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    if minutes:
        return f"{minutes}:{seconds:02d}.{milliseconds:03d}"
    return f"{seconds}.{milliseconds:03d}s"


def _reason_code(value: object) -> str:
    if isinstance(value, str) and value:
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789_"
        normalized = "".join(character if character.lower() in allowed else "_" for character in value.lower())
        return normalized[:96].strip("_") or "evidence_unavailable"
    return "evidence_unavailable"


def _safe_label(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value[:160]
    return None


def _bracket(value: object) -> tuple[float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        return None
    first, second = value
    if (
        not isinstance(first, (int, float))
        or isinstance(first, bool)
        or not isinstance(second, (int, float))
        or isinstance(second, bool)
        or not math.isfinite(float(first))
        or not math.isfinite(float(second))
        or float(second) < float(first)
    ):
        return None
    return (float(first), float(second))


def _format_bracket(value: tuple[float, float], *, signed: bool = False) -> str:
    scale = 1_000
    tolerance = 1e-9
    lower = math.floor(value[0] * scale + tolerance) / scale
    upper = math.ceil(value[1] * scale - tolerance) / scale
    if signed:
        return f"[{lower:+.3f}, {upper:+.3f}]"
    return f"[{lower:.3f}, {upper:.3f}]"


def import_summary_value(source: Mapping[str, object], key: str) -> object:
    processing = _mapping(source.get("processing")) or {}
    metrics = _mapping(processing.get("metrics")) or {}
    summary = _mapping(metrics.get("summary")) or {}
    return summary.get(key)
