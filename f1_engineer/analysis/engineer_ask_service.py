from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..tracks.registry import TrackModelCatalog
from .engineer_ask import (
    ENGINEER_ASK_VERSION,
    EngineerAskRoute,
    answer_message,
    select_report_facts,
)
from .engineer_query import query_engineer_evidence
from .ollama_runtime import OllamaUnavailable, OllamaRuntime
from .service import ComparisonPolicy, compare_attempts


_ANALYSIS_GATE = threading.Lock()


async def answer_engineer_question(
    database_path: str | Path,
    selection: Mapping[str, object],
    question: str,
    *,
    runtime: OllamaRuntime,
    track_model_catalog: TrackModelCatalog | None = None,
) -> dict[str, object]:
    intent = selection.get("intent")
    allowed_routes = (
        ("attempt_summary",)
        if intent == "attempt_summary"
        else (
            ("region_comparison", "lap_debrief")
            if _lap_debrief_is_selectable(selection, track_model_catalog)
            else ("region_comparison",)
        )
        if intent == "region_comparison"
        else ()
    )
    if not allowed_routes:
        return _unavailable(selection, "selection_unsupported")

    try:
        route, model = await runtime.route_question(
            question,
            selection_kind="attempt" if intent == "attempt_summary" else "pair",
            allowed_routes=allowed_routes,
        )
    except OllamaUnavailable as exc:
        return _unavailable(selection, exc.reason)
    if route.route == "unsupported":
        return {
            "schema_version": 1,
            "analysis_version": ENGINEER_ASK_VERSION,
            "status": "unsupported",
            "route": "unsupported",
            "focus": "overview",
            "message": answer_message("unsupported", "overview", facts_available=False),
            "selection": _selection_projection(selection),
            "report": None,
            "debrief": None,
            "debrief_evidence": None,
            "model": model,
            "diagnostic_only": True,
            "coaching_eligible": False,
            "ranking_eligible": False,
        }

    if route.route == "attempt_summary":
        report = await _run_analysis(query_engineer_evidence, database_path, selection)
        report = _focused_report(report, route)
        return _report_answer(selection, route, model, report)

    if route.route == "region_comparison":
        report = await _run_analysis(
            query_engineer_evidence,
            database_path,
            selection,
            track_model_catalog=track_model_catalog,
        )
        report = _focused_report(report, route)
        return _report_answer(selection, route, model, report)

    if route.route == "lap_debrief":
        return await _lap_debrief_answer(
            database_path,
            selection,
            route,
            model,
            track_model_catalog=track_model_catalog,
        )

    return _unavailable(selection, "router_route_unavailable", model=model)


def _focused_report(
    source: Mapping[str, object], route: EngineerAskRoute
) -> dict[str, object]:
    report = dict(source)
    raw_facts = report.get("facts")
    fact_list = (
        list(raw_facts)
        if isinstance(raw_facts, Sequence) and not isinstance(raw_facts, (str, bytes))
        else []
    )
    selected = select_report_facts(report, route.focus)
    report["facts"] = selected
    existing_omitted = report.get("omitted_fact_count")
    report["omitted_fact_count"] = (
        existing_omitted if isinstance(existing_omitted, int) and not isinstance(existing_omitted, bool) else 0
    ) + max(0, len(fact_list) - len(selected))
    return report


def _report_answer(
    selection: Mapping[str, object],
    route: EngineerAskRoute,
    model: Mapping[str, object],
    report: Mapping[str, object],
) -> dict[str, object]:
    facts = report.get("facts")
    warnings = report.get("warnings")
    facts_available = bool(facts) or bool(warnings)
    source_status = report.get("status")
    status = (
        "unavailable"
        if source_status == "unavailable"
        else "partial"
        if source_status == "partial" or report.get("omitted_fact_count", 0) or not facts
        else "answered"
    )
    return {
        "schema_version": 1,
        "analysis_version": ENGINEER_ASK_VERSION,
        "status": status,
        "route": route.route,
        "focus": route.focus,
        "message": answer_message(route.route, route.focus, facts_available=facts_available),
        "selection": _selection_projection(selection),
        "report": dict(report),
        "debrief": None,
        "debrief_evidence": None,
        "model": dict(model),
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
    }


async def _lap_debrief_answer(
    database_path: str | Path,
    selection: Mapping[str, object],
    route: EngineerAskRoute,
    model: Mapping[str, object],
    *,
    track_model_catalog: TrackModelCatalog | None,
) -> dict[str, object]:
    if not _lap_debrief_is_selectable(selection, track_model_catalog):
        return _unavailable(selection, "lap_debrief_selection_unavailable", model=model)
    assert track_model_catalog is not None
    entry = track_model_catalog.resolve_entry(
        str(selection["track_model_id"]), int(selection["track_model_revision"])
    )
    try:
        comparison = await _run_analysis(
            compare_attempts,
            database_path,
            str(selection["target_attempt_key"]),
            str(selection["reference_attempt_key"]),
            policy=ComparisonPolicy(str(selection["comparison_policy"])),
            track_model=entry.model,
            track_model_catalog=track_model_catalog,
        )
    except (OSError, sqlite3.Error):
        return _unavailable(selection, "comparison_source_unavailable", model=model)
    except ValueError as exc:
        reason = str(exc)
        allowed_reasons = {
            "unsupported_comparison_policy",
            "target and reference must be different lap attempts",
            "attempt_pair_identity_mismatch",
            "unsupported_mode",
            "unknown_mode",
            "unknown_context",
            "incomplete_context",
            "context_changed",
            "unknown_track",
            "invalid_track_length",
            "track_model_id_and_revision_must_be_selected_together",
            "unknown_track_model_revision",
            "local_draft_track_model_not_available_for_comparison",
            "track_model_region_not_found",
        }
        reason_code = reason if reason in allowed_reasons else "comparison_unavailable"
        return _unavailable(selection, reason_code, model=model)
    raw_debrief = comparison.get("lap_debrief")
    debrief = _mapping(raw_debrief)
    if (
        debrief is None
        or debrief.get("schema_version") != 1
        or debrief.get("analysis_version") != "lap-debrief-v1"
        or debrief.get("diagnostic_only") is not True
        or debrief.get("coaching_eligible") is not False
        or debrief.get("status") not in {"available", "partial", "abstained"}
    ):
        return _unavailable(selection, "lap_debrief_unavailable", model=model)
    debrief_status = debrief.get("status")
    debrief_evidence = _lap_debrief_evidence_projection(comparison)
    if debrief_evidence is None:
        return _unavailable(selection, "lap_debrief_evidence_limit_exceeded", model=model)
    return {
        "schema_version": 1,
        "analysis_version": ENGINEER_ASK_VERSION,
        "status": "answered" if debrief_status == "available" else "partial" if debrief_status == "partial" else "unavailable",
        "route": route.route,
        "focus": route.focus,
        "message": answer_message(route.route, route.focus, facts_available=debrief_status != "abstained"),
        "selection": _selection_projection(selection),
        "report": None,
        "debrief": dict(debrief),
        "debrief_evidence": debrief_evidence,
        "model": dict(model),
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
    }


def _selection_projection(selection: Mapping[str, object]) -> dict[str, object]:
    allowed = (
        "intent",
        "target_attempt_key",
        "reference_attempt_key",
        "comparison_policy",
        "track_model_id",
        "track_model_revision",
        "region_identifier",
    )
    return {key: selection[key] for key in allowed if key in selection}


def _unavailable(
    selection: Mapping[str, object],
    reason: str,
    *,
    model: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "analysis_version": ENGINEER_ASK_VERSION,
        "status": "unavailable",
        "route": None,
        "focus": None,
        "message": "The selected evidence could not be used for this question. Try a supported question or reopen the exact attempt selection.",
        "reason": reason[:96],
        "selection": _selection_projection(selection),
        "report": None,
        "debrief": None,
        "debrief_evidence": None,
        "model": dict(model) if model else None,
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
    }


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _lap_debrief_is_selectable(
    selection: Mapping[str, object],
    catalog: TrackModelCatalog | None,
) -> bool:
    if (
        catalog is None
        or selection.get("comparison_policy") != ComparisonPolicy.TIME_TRIAL.value
    ):
        return False
    model_id = selection.get("track_model_id")
    revision = selection.get("track_model_revision")
    region_id = selection.get("region_identifier")
    if (
        not isinstance(model_id, str)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or not isinstance(region_id, str)
    ):
        return False
    try:
        entry = catalog.resolve_entry(model_id, revision)
    except ValueError:
        return False
    return entry.origin != "local_draft" and any(
        region.identifier == region_id for region in entry.model.corners
    )


def _lap_debrief_evidence_projection(
    comparison: Mapping[str, object],
) -> dict[str, object] | None:
    target = _project_mapping(
        comparison.get("target"),
        (
            "attempt_key",
            "run_id",
            "session_uid",
            "car_index",
            "trace_sha256",
            "trace_schema_version",
            "lap_time_ms",
        ),
    )
    reference = _project_mapping(
        comparison.get("reference"),
        (
            "attempt_key",
            "run_id",
            "session_uid",
            "car_index",
            "trace_sha256",
            "trace_schema_version",
            "lap_time_ms",
        ),
    )
    track = _project_mapping(comparison.get("track"), ("track_length_m",))
    brief = _project_mapping(
        comparison.get("comparison_brief"),
        ("schema_version", "analysis_version", "status", "facts", "limitations", "limits"),
    )
    ranking = _project_ranking(comparison.get("corner_loss_candidates"))
    corner_brief = _project_corner_brief(comparison.get("corner_comparison_brief"))
    corner_analysis = _project_mapping(
        comparison.get("corner_analysis"),
        ("diagnostic_only", "source", "model"),
    )
    if any(item is None for item in (target, reference, track, brief, ranking, corner_brief, corner_analysis)):
        return None
    evidence: dict[str, object] = {
        "schema_version": 1,
        "artifact_kind": "engineer_ask_lap_debrief_evidence",
        "comparison_policy": comparison.get("comparison_policy"),
        "target": target,
        "reference": reference,
        "track": track,
        "official_lap_time_difference_s": comparison.get("official_lap_time_difference_s"),
        "comparison_brief": brief,
        "corner_loss_candidates": ranking,
        "corner_comparison_brief": corner_brief,
        "corner_analysis": corner_analysis,
    }
    try:
        encoded = json.dumps(
            evidence, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    return evidence if len(encoded) <= 28 * 1_024 else None


def _project_ranking(value: object) -> dict[str, object] | None:
    ranking = _project_mapping(
        value,
        (
            "schema_version",
            "analysis_version",
            "policy_version",
            "status",
            "coaching_eligible",
            "omitted_candidate_count",
            "gate_reasons",
            "gate_reasons_omitted_count",
            "ranked_candidates",
            "source",
        ),
    )
    if ranking is None:
        return None
    source = _project_mapping(
        ranking.get("source"),
        ("target", "reference", "model", "reference_selection", "capture_evidence"),
    )
    if source is not None:
        ranking["source"] = source
    model = _project_mapping(
        source.get("model") if source else None,
        (
            "model_id",
            "revision",
            "validation_status",
            "registered",
            "approved_for_candidate_ranking",
            "origin",
            "content_sha256",
            "model_content_sha256",
            "approval_provenance",
        ),
    )
    if model is not None and source is not None:
        source["model"] = model
    return ranking


def _project_corner_brief(value: object) -> dict[str, object] | None:
    brief = _project_mapping(
        value,
        (
            "schema_version",
            "analysis_version",
            "status",
            "coaching_eligible",
            "gate_reasons",
            "gate_reasons_omitted_count",
            "omitted_region_count",
            "regions",
        ),
    )
    if brief is None:
        return None
    raw_regions = brief.get("regions")
    if not isinstance(raw_regions, Sequence) or isinstance(raw_regions, (str, bytes)):
        return brief
    projected: list[dict[str, object]] = []
    for raw_region in raw_regions:
        region = _project_mapping(
            raw_region,
            (
                "rank",
                "region_id",
                "region_label",
                "analysis_window_m",
                "facts",
                "connected_support",
                "provenance",
            ),
        )
        if region is None:
            return None
        projected.append(region)
    brief["regions"] = projected
    return brief


def _project_mapping(value: object, fields: Sequence[str]) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {field: value[field] for field in fields if field in value}


async def _run_analysis(function: Any, *args: Any, **kwargs: Any) -> Any:
    if not _ANALYSIS_GATE.acquire(blocking=False):
        raise OllamaUnavailable("analysis_busy")
    work = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        result = await asyncio.shield(work)
    except asyncio.CancelledError:
        work.add_done_callback(lambda _done: _ANALYSIS_GATE.release())
        raise
    except BaseException:
        _ANALYSIS_GATE.release()
        raise
    _ANALYSIS_GATE.release()
    return result
