"""Explicit diagnostic requests, with request-owned Gate A leases.

Future binding mode is separate from immutable revision mode. Releases may
remain queued; rejection is surfaced without mutating the owner's queue.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import threading
import time
import uuid
import weakref
from copy import deepcopy

from ..processing.detail_demand import DetailCommand
from ..processing.evidence import EvidenceUnavailable, MAX_READ_BYTES, MAX_READ_ROWS, digest, encode
from .ai_admission import EngineerAIGate, PROCESS_ENGINEER_AI_GATE
from .ollama_runtime import OllamaUnavailable
from .session_measurement import analyze_session_pair
from .session_reference import select_session_reference


REQUEST_TIMEOUT_S = 30.0
LIVE_REQUEST_TIMEOUT_S = 270.0
LIVE_WAIT_S = 240.0
LIVE_POLL_S = 0.1
_clock = time.monotonic
_sleep = asyncio.sleep
_materializer_gates = weakref.WeakKeyDictionary()
_materializer_gates_lock = threading.Lock()


def _materializer_gate(materializer):
    with _materializer_gates_lock:
        gate = _materializer_gates.get(materializer)
        if gate is None:
            gate = EngineerAIGate()
            _materializer_gates[materializer] = gate
        return gate


def _result(status, reason, *, selection=None, report=None, provenance=None, message=None):
    return {
        "schema_version": 1, "status": status, "reason": reason,
        "selection": deepcopy(selection), "provenance": deepcopy(provenance or {}),
        "report": deepcopy(report),
        "message": message or "No eligible measured comparison is available.",
        "diagnostic_only": True, "coaching_eligible": False, "ranking_eligible": False,
    }


def _checked_attempt(provider, session, identifier):
    attempt = provider.attempt(identifier, session=session)
    if attempt.get("id") != identifier or attempt.get("session") != session:
        raise EvidenceUnavailable("attempt_identity_mismatch")
    return attempt


class _Overlay:
    def __init__(self, provider):
        self.provider = provider
        self.derived = {}

    def attempt(self, attempt_id, *, session=None):
        if attempt_id in self.derived:
            metadata = self.derived[attempt_id][0]
            if session is not None and metadata["session"] != session:
                raise EvidenceUnavailable("attempt_not_in_session")
            return deepcopy(metadata)
        return self.provider.attempt(attempt_id, session=session)

    def attempts(self, session, *, limit=100, after=""):
        page = self.provider.attempts(session, limit=limit, after=after)
        return [self.attempt(item["id"], session=session)
                if item["id"] in self.derived else item for item in page]

    def evidence(self, attempt_id, *, session=None):
        if attempt_id in self.derived:
            self.attempt(attempt_id, session=session)
            return deepcopy(self.derived[attempt_id])
        return self.provider.evidence(attempt_id, session=session)

    def add(self, original, materialized):
        if len(self.derived) >= 2:
            raise EvidenceUnavailable("materialization_overlay_budget_exceeded")
        metadata, rows = materialized
        provenance = metadata.get("materialization") or {}
        if (not original.get("driver")
                or provenance.get("source_revision") != original["id"]
                or provenance.get("source_session") != original["session"]
                or provenance.get("source_manifest_hash") != digest(encode(original["manifest"]))
                or any(metadata.get(key) != original.get(key) for key in
                       ("id", "session", "driver", "original_id", "role", "payload"))
                or any((metadata.get("manifest") or {}).get(key) != original["manifest"].get(key)
                       for key in ("driver", "epoch", "format", "start_frame", "end_frame"))):
            raise EvidenceUnavailable("materialization_identity_mismatch")
        if (metadata.get("readiness") or {}).get("state") != "published":
            raise EvidenceUnavailable("materialization_coverage_unavailable")
        if not isinstance(rows, list) or len(rows) > MAX_READ_ROWS:
            raise EvidenceUnavailable("analysis_read_budget_exceeded")
        if len(encode([metadata, rows]).encode("utf-8")) > MAX_READ_BYTES:
            raise EvidenceUnavailable("analysis_read_budget_exceeded")
        copied = deepcopy(metadata)
        copied.update(id=original["id"], session=original["session"], driver=original["driver"])
        copied["manifest"]["driver"] = original["driver"]
        self.derived[original["id"]] = copied, deepcopy(rows)


def _current_binding(live_runtime, session, binding_id):
    targets = live_runtime.detail_targets(session)
    matches = [item for item in (targets or ()) if item.get("binding_id") == binding_id]
    if len(matches) != 1:
        raise EvidenceUnavailable("detail_target_not_current")
    if matches[0].get("is_player") is not False:
        raise EvidenceUnavailable("detail_target_is_player_or_unknown")
    return matches[0]


def _snapshot_value(snapshot, key):
    return snapshot.get(key) if isinstance(snapshot, dict) else getattr(snapshot, key, None)


def _scan_attempts(provider, session):
    candidates = []
    after = ""
    while True:
        page = provider.attempts(session, limit=100, after=after)
        if not isinstance(page, list) or len(page) > 100:
            raise EvidenceUnavailable("invalid_candidate_page")
        for item in page:
            identifier = item.get("id")
            if (not isinstance(identifier, str) or identifier <= after
                    or item.get("session") != session):
                raise EvidenceUnavailable("invalid_candidate_page")
            after = identifier
            candidates.append(item)
            if len(candidates) > 512:
                raise EvidenceUnavailable("candidate_scan_budget_exceeded")
        if len(page) < 100:
            return candidates


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _future_target(provider, session, binding_id, target, existing, detail):
    activation = detail.get("activation_frame_ordinal")
    effective = detail.get("effective_sequence")
    if not _integer(activation) or not _integer(effective):
        raise EvidenceUnavailable("detail_activation_boundary_unavailable")
    eligible = []
    for item in _scan_attempts(provider, session):
        manifest, payload = item.get("manifest") or {}, item.get("payload") or {}
        if (item["id"] in existing or item.get("driver") != binding_id
                or manifest.get("driver") != binding_id or item.get("binding_verified") is not True
                or item.get("role") != "opponent"
                or (item.get("readiness") or {}).get("state") != "published"
                or payload.get("disposition") != "completed" or payload.get("game_valid") is not True
                or payload.get("start_observed") is not True or payload.get("pit_encountered") is not False
                or not _integer(payload.get("lap_time_ms")) or payload["lap_time_ms"] <= 0
                or manifest.get("qualifications") != []
                or any(not _integer(manifest.get(key)) for key in
                       ("start_frame", "end_frame", "source_start", "source_end"))
                or manifest["start_frame"] < activation or manifest["source_start"] <= effective
                or manifest["end_frame"] < manifest["start_frame"]
                or manifest["source_end"] < manifest["source_start"]
                or manifest.get("epoch") != target.get("epoch")
                or manifest.get("format") != target.get("packet_format")
                or payload.get("car_index") != target.get("car_index")):
            continue
        eligible.append(item)
    if not eligible:
        return None
    chosen = min(eligible, key=lambda item: (
        item["manifest"]["start_frame"], item["manifest"]["end_frame"], item["id"]
    ))
    metadata, rows = provider.evidence(chosen["id"], session=session)
    if metadata != chosen:
        raise EvidenceUnavailable("evidence_changed_during_request")
    if (not isinstance(rows, list) or not 0 < len(rows) <= MAX_READ_ROWS
            or len(rows) != chosen.get("rows") or len(rows) != chosen["payload"].get("sample_count")
            or len(encode([metadata, rows]).encode("utf-8")) > MAX_READ_BYTES):
        raise EvidenceUnavailable("future_trace_coverage_unavailable")
    return chosen


async def _wait_live(provider, session, binding_id, live_runtime, detail):
    if live_runtime is None:
        raise EvidenceUnavailable("detail_runtime_unavailable")
    target = _current_binding(live_runtime, session, binding_id)
    existing = {item["id"] for item in await asyncio.to_thread(_scan_attempts, provider, session)}
    _current_binding(live_runtime, session, binding_id)
    task_id = uuid.uuid4().hex
    status, queued = live_runtime.enqueue_detail_command(
        DetailCommand("post", task_id, session, binding_id)
    )
    detail.update(task_id=task_id, session_id=session, binding_id=binding_id)
    if status != 202:
        raise EvidenceUnavailable(queued.get("reason") or "detail_command_rejected")
    try:
        deadline = _clock() + LIVE_WAIT_S
        while True:
            if _current_binding(live_runtime, session, binding_id) != target:
                raise EvidenceUnavailable("detail_target_identity_changed")
            snapshot = live_runtime.detail_task(task_id)
            if snapshot is not None:
                if (_snapshot_value(snapshot, "session_id") != session
                        or _snapshot_value(snapshot, "binding_id") != binding_id
                        or _snapshot_value(snapshot, "task_id") != task_id):
                    raise EvidenceUnavailable("detail_task_identity_mismatch")
                state = _snapshot_value(snapshot, "state")
                for key in ("state", "coverage_state", "activation_frame_ordinal", "effective_sequence"):
                    detail[key] = _snapshot_value(snapshot, key)
                if state in {"rejected", "expired", "released"}:
                    raise EvidenceUnavailable(_snapshot_value(snapshot, "reason")
                                              or "detail_task_unavailable")
                if state == "active" and detail["coverage_state"] == "available":
                    chosen = await asyncio.to_thread(_future_target, provider, session, binding_id,
                                                     target, existing, detail)
                    if _current_binding(live_runtime, session, binding_id) != target:
                        raise EvidenceUnavailable("detail_target_identity_changed")
                    current = live_runtime.detail_task(task_id)
                    if (current is None or _snapshot_value(current, "state") != "active"
                            or _snapshot_value(current, "coverage_state") != "available"
                            or any(_snapshot_value(current, key) != detail[key] for key in
                                   ("activation_frame_ordinal", "effective_sequence", "session_id", "binding_id"))):
                        raise EvidenceUnavailable("detail_task_changed_during_request")
                    return chosen, None if chosen else "future_trace_not_published"
            remaining = deadline - _clock()
            if remaining <= 0:
                return None, "detail_wait_timeout"
            await _sleep(min(LIVE_POLL_S, remaining))
    finally:
        status, cleanup = live_runtime.enqueue_detail_command(
            DetailCommand("release", task_id, session, binding_id)
        )
        detail["cleanup"] = {
            "status": "queued" if status == 202 else "rejected",
            "reason": None if status == 202 else cleanup.get("reason") or "detail_release_rejected",
        }


def _message(report):
    difference = report.get("lap_time_difference_ms")
    if (isinstance(difference, bool) or not isinstance(difference, (int, float))
            or not math.isfinite(difference)):
        raise EvidenceUnavailable("lap_time_measurement_unavailable")
    if difference > 0:
        text = f"The target lap is {difference:g} ms slower than the reference lap."
    elif difference < 0:
        text = f"The target lap is {abs(difference):g} ms faster than the reference lap."
    else:
        text = "The measured target and reference lap times are equal (0 ms difference)."
    text += " This is a diagnostic measurement, not coaching, ranking, or a causal explanation."
    qualifications = report.get("qualifications") or []
    if qualifications:
        text += " Qualifications: " + ", ".join(str(item) for item in qualifications) + "."
    return text


async def answer_session_question(
    provider, session: str, target_id: str | None, question: str, *,
    reference_id: str | None = None, materializer=None,
    allow_materialization: bool = False, live_runtime=None,
    binding_id: str | None = None, runtime=None, cache=None,
) -> dict:
    """Admit evidence before routing; serialize request-scoped historical work.

Exactly one revision or future binding is required. A future request without
an explicit reference returns only an observed trace summary, never inference.
Cancelled materialization workers retain their shared gate until completion.
"""
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        return _result("unsupported", "question_invalid")
    if ((target_id is None) == (binding_id is None)
            or target_id is not None and (not isinstance(target_id, str) or not target_id)
            or binding_id is not None and (not isinstance(binding_id, str) or not binding_id)):
        return _result("unsupported", "request_target_invalid")
    overlay = _Overlay(provider)
    selection = None
    report = None
    provenance = {"session": session, "target": target_id, "reference": reference_id,
                  "origin": "future_binding" if binding_id is not None else "revision",
                  "materializations": {}}
    materialization_lease = None
    cancelled = threading.Event()

    async def prepare(identifier):
        nonlocal materialization_lease
        original = await asyncio.to_thread(_checked_attempt, overlay, session, identifier)
        if (original.get("readiness") or {}).get("state") != "deferred":
            return
        if not allow_materialization:
            raise EvidenceUnavailable("historical_materialization_not_authorized")
        if materializer is None:
            raise EvidenceUnavailable("materializer_unavailable")
        if materialization_lease is None:
            materialization_lease = _materializer_gate(materializer).try_acquire()
            if materialization_lease is None:
                raise EvidenceUnavailable("materialization_busy")
        kwargs = {"session": session}
        if "cancelled" in inspect.signature(materializer.materialize).parameters:
            kwargs["cancelled"] = cancelled.is_set
        def materialize_and_copy():
            materialized = materializer.materialize(identifier, **kwargs)
            if cancelled.is_set():
                raise EvidenceUnavailable("materialization_cancelled")
            overlay.add(original, materialized)

        work = asyncio.create_task(asyncio.to_thread(materialize_and_copy))
        materialization_lease.retain_until(work)
        await asyncio.shield(work)
        provenance["materializations"][identifier] = deepcopy(
            overlay.derived[identifier][0].get("materialization") or {}
        )

    try:
        async with asyncio.timeout(LIVE_REQUEST_TIMEOUT_S if binding_id is not None else REQUEST_TIMEOUT_S):
            if binding_id is not None:
                detail = provenance["detail"] = {}
                try:
                    async with asyncio.timeout(LIVE_WAIT_S):
                        future, pending = await _wait_live(provider, session, binding_id,
                                                           live_runtime, detail)
                except TimeoutError:
                    future, pending = None, "detail_wait_timeout"
                if detail.get("cleanup", {}).get("status") == "rejected":
                    return _result("unavailable", detail["cleanup"]["reason"], provenance=provenance)
                if pending:
                    return _result("pending", pending, provenance=provenance)
                target_id = future["id"]
                provenance["target"] = target_id
                if reference_id is None:
                    selection = {"target": target_id, "reference": None, "origin": "future_binding"}
                    report = {
                        "kind": "attempt_summary", "target": {
                            "revision": target_id, "driver": binding_id,
                            "readiness": deepcopy(future["readiness"]),
                        },
                        "coverage": {"observation_rows": future["rows"],
                                     "manifest": deepcopy(future["manifest"])},
                        "qualifications": ["diagnostic_trace_only", "no_comparative_measurement"],
                        "diagnostic_only": True, "coaching_eligible": False, "ranking_eligible": False,
                    }
                    return _result("available", None, selection=selection, report=report,
                                   provenance=provenance,
                                   message="Complete observed lap evidence is available. "
                                           "This is a diagnostic trace summary, not coaching or ranking.")
            else:
                await prepare(target_id)
            if reference_id is not None:
                await prepare(reference_id)
            selection = await asyncio.to_thread(
                select_session_reference, overlay, session, target_id, reference_id=reference_id
            )
            if selection.get("status") != "available":
                return _result("unavailable", selection.get("reason") or "reference_unavailable",
                               selection=selection, provenance=provenance)
            chosen = selection.get("reference")
            if not isinstance(chosen, str) or (reference_id is not None and chosen != reference_id):
                raise EvidenceUnavailable("reference_identity_mismatch")
            provenance["reference"] = chosen
            report = await asyncio.to_thread(analyze_session_pair, overlay, session, target_id,
                                             chosen, cache=cache)
            if report.get("status", "available") != "available":
                raise EvidenceUnavailable(report.get("reason") or "measurement_unavailable")
            message = _message(report)
            response = _result("available", None, selection=selection, report=report,
                               provenance=provenance, message=message)
            if runtime is None:
                return response
            ai_lease = PROCESS_ENGINEER_AI_GATE.try_acquire()
            if ai_lease is None:
                raise EvidenceUnavailable("engineer_ai_busy")
            work = None
            try:
                work = asyncio.create_task(runtime.route_question(
                    question, selection_kind="pair", allowed_routes=("region_comparison",)
                ))
                ai_lease.retain_until(work)
                route, model = await asyncio.shield(work)
            except BaseException:
                if work is not None:
                    work.cancel()
                raise
            finally:
                ai_lease.close()
            response["model"] = deepcopy(model)
            response["routing"] = {"route": route.route, "focus": route.focus}
            if route.route != "region_comparison":
                response.update(status="unsupported", reason="question_unsupported",
                                message="This question is outside diagnostic pair comparison.")
            return response
    except TimeoutError:
        return _result("unavailable", "engineer_request_timeout", selection=selection,
                       report=report, provenance=provenance)
    except (EvidenceUnavailable, OllamaUnavailable) as exception:
        return _result("unavailable", getattr(exception, "reason", str(exception)),
                       selection=selection, report=report, provenance=provenance)
    finally:
        cancelled.set()
        if materialization_lease is not None:
            materialization_lease.close()
