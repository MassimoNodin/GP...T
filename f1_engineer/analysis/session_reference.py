from __future__ import annotations

import math
from typing import Any, Protocol

from ..processing.evidence import EvidenceUnavailable, digest, encode
from .service import TimeTrialContextError, stable_practice_qualifying_context, stable_time_trial_context


POLICY = "session-reference-v1"
MAX_CANDIDATES = 512
PAGE_SIZE = 100


class SessionReferenceProvider(Protocol):
    def attempt(self, attempt_id: str, *, session: str | None = None) -> dict[str, Any]: ...

    def attempts(self, session: str, *, limit: int = 100,
                 after: str = "") -> list[dict[str, Any]]: ...

    def evidence(self, attempt_id: str, *, session: str | None = None
                 ) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...


def _integer(value: object, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _signature(attempt: dict[str, Any]) -> tuple[object, ...]:
    segments = attempt["payload"].get("context_segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("unknown_context")
    normalized = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("context"), dict):
            raise ValueError("unknown_context")
        context = segment["context"]
        if not _integer(context.get("track_id")):
            raise ValueError("unknown_track")
        if context.get("packet_format") != attempt["manifest"]["format"]:
            raise ValueError("context_packet_format_mismatch")
        if isinstance(context.get("track_length_m"), bool):
            raise ValueError("invalid_track_length")
        if any(not _integer(context.get(field)) for field in (
            "formula_id", "equal_car_performance_id", "steering_assist_id",
            "braking_assist_id", "gearbox_assist_id",
        )):
            raise ValueError("unknown_context_settings")
        normalized.append((segment.get("from_frame_identifier", 0), context))
    if normalized[0][1].get("session_type") == "time_trial":
        for _, context in normalized:
            if any(context.get(field) is None for field in (
                "weather_id", "weather_name", "track_temperature_c", "air_temperature_c"
            )):
                raise ValueError("unknown_conditions")
            if (
                not _integer(context["weather_id"])
                or not isinstance(context["weather_name"], str)
                or not context["weather_name"].strip()
            ):
                raise ValueError("unknown_conditions")
            if any(
                isinstance(context[field], bool)
                or not isinstance(context[field], (int, float))
                or not math.isfinite(context[field])
                for field in ("track_temperature_c", "air_temperature_c")
            ):
                raise ValueError("unknown_conditions")
        _, signature = stable_time_trial_context(tuple(normalized), attempt["id"], require_conditions=True)
    else:
        _, signature = stable_practice_qualifying_context(tuple(normalized), attempt["id"])
    return signature


def _eligibility(attempt: dict[str, Any], session: str
                 ) -> tuple[list[str], tuple[object, ...] | None]:
    reasons = []
    payload, manifest = attempt.get("payload"), attempt.get("manifest")
    if not isinstance(payload, dict) or not isinstance(manifest, dict):
        return ["invalid_attempt_metadata"], None
    if attempt.get("session") != session:
        reasons.append("attempt_not_in_session")
    if attempt.get("role") != "player":
        reasons.append("not_player_evidence")
    driver = attempt.get("driver")
    if not isinstance(driver, str) or not driver or manifest.get("driver") != driver:
        reasons.append("verified_driver_unavailable")
    if attempt.get("binding_verified") is False:
        reasons.append("driver_binding_unverified")
    if not _integer(payload.get("car_index")):
        reasons.append("car_identity_unavailable")
    if not _integer(manifest.get("epoch")) or not _integer(manifest.get("format"), 1):
        reasons.append("session_identity_unavailable")
    readiness = attempt.get("readiness")
    if not isinstance(readiness, dict) or readiness.get("state") != "published":
        reasons.append("evidence_not_published")
    checks = (
        (payload.get("disposition") == "completed", "not_completed"),
        (payload.get("game_valid") is True, "game_validity_required"),
        (payload.get("start_observed") is True, "lap_start_unobserved"),
        (payload.get("pit_encountered") is False, "pit_encountered_or_unknown"),
        (_integer(payload.get("lap_time_ms"), 1), "positive_lap_time_required"),
    )
    reasons.extend(reason for passes, reason in checks if not passes)
    if not _integer(attempt.get("rows"), 1) or not _integer(payload.get("sample_count"), 1):
        reasons.append("observation_rows_unavailable")
    elif attempt["rows"] != payload["sample_count"]:
        reasons.append("missing_observation_rows")
    qualifications = manifest.get("qualifications")
    if not isinstance(qualifications, list) or any(not isinstance(item, str) for item in qualifications):
        reasons.append("evidence_qualifications_unknown")
    else:
        reasons.extend(sorted(set(qualifications)))
    signature = None
    try:
        signature = _signature(attempt)
    except TimeTrialContextError as exception:
        reasons.append(exception.reason_code)
    except (KeyError, TypeError, ValueError) as exception:
        reasons.append(str(exception).split(":", 1)[0] if isinstance(exception, ValueError)
                       else "invalid_context")
    return list(dict.fromkeys(reasons)), signature


def _fingerprint(attempt: dict[str, Any]) -> dict[str, Any]:
    return {field: attempt.get(field) for field in (
        "id", "session", "role", "driver", "binding_verified", "manifest", "payload", "rows", "readiness"
    )}


def _hash_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite": repr(value)}
    if isinstance(value, dict):
        return {key: _hash_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_hash_value(item) for item in value]
    return value


def select_session_reference(provider: SessionReferenceProvider, session: str, target_id: str,
                             *, reference_id: str | None = None) -> dict[str, Any]:
    """Choose diagnostic evidence using committed, provider-verified binding metadata.

    Trace integrity remains the evidence reader's responsibility. The scan counts
    non-self revisions, including ineligible ones; a 513th witness fails closed.
    Legacy providers without a binding marker remain assumed provider-verified.
    """
    result: dict[str, Any] = {
        "schema_version": 1, "policy": POLICY, "status": "unavailable", "reason": None,
        "target": target_id, "reference": None, "candidate_set_hash": "", "exclusions": [],
        "diagnostic_only": True, "coaching_eligible": False, "ranking_eligible": False,
    }
    snapshots: dict[str, dict[str, Any]] = {}

    def finish(reason: str | None, reference: str | None = None) -> dict[str, Any]:
        result.update(status="available" if reference is not None else "unavailable",
                      reason=reason, reference=reference)
        result["exclusions"].sort(key=lambda item: item["id"])
        result["candidate_set_hash"] = digest(encode({
            "policy": POLICY, "session": session, "target": target_id,
            "explicit_reference": reference_id,
            "candidates": [_hash_value(snapshots[key]) for key in sorted(snapshots)],
            "scan_failure": reason if reason in (
                "candidate_scan_budget_exceeded", "invalid_candidate_page", "candidate_scan_unavailable"
            ) else None,
        }))
        return result

    try:
        target = provider.attempt(target_id, session=session)
    except EvidenceUnavailable as exception:
        return finish(f"target_unavailable:{exception}")
    snapshots[target_id] = _fingerprint(target)
    reasons, target_signature = _eligibility(target, session)
    if target.get("id") != target_id:
        reasons.append("attempt_identity_mismatch")
    if reasons:
        result["exclusions"].append({"id": target_id, "reasons": reasons})
        return finish("target_invalid")

    candidates = []
    if reference_id is not None:
        if reference_id == target_id:
            result["exclusions"].append({"id": target_id, "reasons": ["target_attempt"]})
            return finish("explicit_reference_invalid")
        try:
            candidate = provider.attempt(reference_id, session=session)
        except EvidenceUnavailable as exception:
            result["exclusions"].append({"id": reference_id, "reasons": [str(exception)]})
            return finish("explicit_reference_invalid")
        if candidate.get("id") != reference_id:
            result["exclusions"].append({"id": reference_id, "reasons": ["attempt_identity_mismatch"]})
            return finish("explicit_reference_invalid")
        candidates.append(candidate)
        snapshots[reference_id] = _fingerprint(candidate)
    else:
        after = ""
        while True:
            try:
                page = provider.attempts(session, limit=PAGE_SIZE, after=after)
            except EvidenceUnavailable:
                return finish("candidate_scan_unavailable")
            if not isinstance(page, list) or len(page) > PAGE_SIZE:
                return finish("invalid_candidate_page")
            for candidate in page:
                if not isinstance(candidate, dict):
                    return finish("invalid_candidate_page")
                identifier = candidate.get("id")
                if not isinstance(identifier, str) or identifier <= after:
                    return finish("invalid_candidate_page")
                after = identifier
                if identifier == target_id:
                    if _fingerprint(candidate) != snapshots[target_id]:
                        return finish("invalid_candidate_page")
                    result["exclusions"].append({"id": identifier, "reasons": ["target_attempt"]})
                    continue
                if len(candidates) == MAX_CANDIDATES:
                    return finish("candidate_scan_budget_exceeded")
                snapshots[identifier] = _fingerprint(candidate)
                candidates.append(candidate)
            if len(page) < PAGE_SIZE:
                break

    eligible = []
    for candidate in candidates:
        reasons, signature = _eligibility(candidate, session)
        payload = candidate.get("payload") or {}
        manifest = candidate.get("manifest") or {}
        if candidate.get("driver") != target["driver"]:
            reasons.append("incompatible_driver")
        if not isinstance(payload, dict) or payload.get("car_index") != target["payload"]["car_index"]:
            reasons.append("incompatible_car")
        if not isinstance(manifest, dict) or any(manifest.get(field) != target["manifest"][field]
                                               for field in ("epoch", "format")):
            reasons.append("incompatible_session_identity")
        if signature != target_signature:
            reasons.append("incompatible_context")
        if reasons:
            result["exclusions"].append({"id": candidate["id"], "reasons": list(dict.fromkeys(reasons))})
        else:
            eligible.append(candidate)
    if not eligible:
        return finish("explicit_reference_invalid" if reference_id is not None else "no_eligible_reference")
    selected = min(eligible, key=lambda candidate: (candidate["payload"]["lap_time_ms"], candidate["id"]))
    return finish(None, selected["id"])
