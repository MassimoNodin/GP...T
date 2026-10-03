from __future__ import annotations

from pathlib import Path

from .service import (
    ComparisonPolicy,
    validate_comparison_pair_policy,
)
from .trajectory import TrajectoryPreviewUnavailable
from .trajectory_comparison import build_trajectory_comparison_preview
from .trajectory_service import load_observed_trajectory_preview
from .source_limits import (
    ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
    ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
)
from ..storage.query import (
    AttemptTraceReadLimitError,
    StoredAttemptTrace,
    load_attempt_policy_metadata,
)
from ..storage.run_summaries import get_processing_run_summary


def compare_observed_trajectories(
    database_path: str | Path,
    target_attempt_key: str,
    reference_attempt_key: str,
    *,
    policy: ComparisonPolicy | str = ComparisonPolicy.TIME_TRIAL,
) -> dict[str, object]:
    """Return bounded paired paths after metadata-only policy validation."""
    try:
        policy = ComparisonPolicy(policy)
    except ValueError as exc:
        raise TrajectoryPreviewUnavailable("unsupported_comparison_policy") from exc
    try:
        target = load_attempt_policy_metadata(
            database_path,
            target_attempt_key,
            max_context_segments=ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
            max_context_bytes=ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
        )
        reference = load_attempt_policy_metadata(
            database_path,
            reference_attempt_key,
            max_context_segments=ANALYSIS_SOURCE_CONTEXT_SEGMENT_LIMIT,
            max_context_bytes=ANALYSIS_SOURCE_CONTEXT_BYTE_LIMIT,
        )
    except AttemptTraceReadLimitError as exc:
        raise TrajectoryPreviewUnavailable(
            f"trajectory_source_{exc.limit_kind}_limit_exceeded"
        ) from exc
    if target is None or reference is None:
        raise TrajectoryPreviewUnavailable("trajectory_pair_provenance_unavailable")
    validate_comparison_pair_policy(target, reference, policy=policy)
    if not all(
        getattr(target, key) == getattr(reference, key)
        for key in ("run_id", "session_uid", "car_index")
    ):
        raise TrajectoryPreviewUnavailable("trajectory_pair_scope_mismatch")

    target_preview = load_observed_trajectory_preview(
        database_path, target_attempt_key
    )
    reference_preview = load_observed_trajectory_preview(
        database_path, reference_attempt_key
    )
    if target_preview is None or reference_preview is None:
        raise TrajectoryPreviewUnavailable("trajectory_attempt_unavailable")
    target_run = get_processing_run_summary(database_path, target.run_id)
    reference_run = (
        target_run
        if target.run_id == reference.run_id
        else get_processing_run_summary(database_path, reference.run_id)
    )
    comparison = {
        "comparison_policy": policy.value,
        "target": _attempt_evidence(target),
        "reference": _attempt_evidence(reference),
        "processing_run_evidence": {
            "target": target_run,
            "reference": reference_run,
        },
    }
    return build_trajectory_comparison_preview(
        target_preview,
        reference_preview,
        comparison,
    )


def _attempt_evidence(attempt: StoredAttemptTrace) -> dict[str, object]:
    return {
        "attempt_key": attempt.attempt_key,
        "run_id": attempt.run_id,
        "session_uid": attempt.session_uid,
        "car_index": attempt.car_index,
        "disposition": attempt.disposition,
        "lap_time_ms": attempt.lap_time_ms,
        "game_valid": attempt.game_valid,
        "reference_eligible": attempt.reference_eligible,
        "superseded": attempt.superseded,
        "lifecycle_assessed": attempt.lifecycle_assessed,
        "lifecycle_exclusions": (
            ["superseded_by_flashback"]
            if attempt.superseded is True
            else ["lifecycle_evidence_unassessed"]
            if not attempt.lifecycle_assessed or attempt.superseded is None
            else []
        ),
        "exclusion_reasons": list(attempt.exclusion_reasons),
        "trace_sha256": attempt.trace_sha256,
        "trace_schema_version": attempt.trace_schema_version,
        "source_sample_count": (
            attempt.source_sample_count
            if attempt.source_sample_count is not None
            else len(attempt.samples)
        ),
    }
