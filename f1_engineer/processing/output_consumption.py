from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..telemetry.canonical import CarSample


def consume_trace_outputs(
    samples: tuple[CarSample, ...],
    attempts: tuple[Any, ...],
    *,
    write_sample: Callable[[CarSample], None],
    finish_attempt: Callable[[Any], None],
) -> None:
    """Consume trace outputs in pipeline order for both replay and live sinks.

    An attempt with no samples in this output batch is sealed before other
    attempts are handled. Samples are then written in their original order and
    newly completed attempts are sealed. Sinks must make finishing idempotent.
    Keeping this ordering outside the file/database sink lets source adapters
    share the same evidence-consumption rule.
    """
    sample_ids = {sample.attempt_id for sample in samples}
    for attempt in attempts:
        if attempt.attempt_id not in sample_ids:
            finish_attempt(attempt)
    for sample in samples:
        write_sample(sample)
    for attempt in attempts:
        finish_attempt(attempt)

