from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Protocol

from ..processing.evidence import EvidenceUnavailable
from .session_comparison import POLICY_VERSION, measure_completed_laps


ADAPTER_VERSION = "session-measurement-adapter-v1"
ANALYSIS_VERSION = f"{POLICY_VERSION}+{ADAPTER_VERSION}"


class SessionMeasurementProvider(Protocol):
    def attempt(self, attempt_id: str, *, session: str) -> dict[str, Any]: ...

    def attempts(self, session: str, *, limit: int = 100,
                 after: str = "") -> list[dict[str, Any]]: ...

    def evidence(self, attempt_id: str, *, session: str
                 ) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...


class MeasurementCache(Protocol):
    def get_or_compute(self, key: str,
                       compute: Callable[[], dict[str, Any]]) -> dict[str, Any]: ...


def analyze_session_pair(
    provider: SessionMeasurementProvider,
    session: str,
    target_id: str,
    reference_id: str,
    *,
    policy: str = "observed_session_distance",
    cache: MeasurementCache | None = None,
) -> dict[str, Any]:
    """Return diagnostic numeric regions, never coaching or reference ranking."""
    from .session_reference import select_session_reference

    selection = select_session_reference(
        provider, session, target_id, reference_id=reference_id
    )
    if selection.get("status") != "available":
        raise EvidenceUnavailable(selection.get("reason") or "reference_unavailable")

    def compute() -> dict[str, Any]:
        return measure_completed_laps(
            provider, session, target_id, reference_id, policy=policy
        )

    if cache is None:
        measured = compute()
    else:
        from .evidence_cache import measurement_cache_key

        key = measurement_cache_key(
            provider.attempt(target_id, session=session),
            provider.attempt(reference_id, session=session),
            analysis_version=ANALYSIS_VERSION,
            policy=policy,
        )
        measured = cache.get_or_compute(key, compute)

    return {
        **deepcopy(measured),
        "schema_version": 1,
        "analysis_version": ANALYSIS_VERSION,
        "diagnostic_only": True,
        "coaching_eligible": False,
        "ranking_eligible": False,
        "reference_selection": deepcopy(selection),
    }
