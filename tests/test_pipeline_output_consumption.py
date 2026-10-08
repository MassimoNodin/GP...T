from types import SimpleNamespace

from f1_engineer.processing.output_consumption import consume_trace_outputs


def test_trace_outputs_seal_empty_attempts_before_writing_then_seal_completed():
    samples = (
        SimpleNamespace(attempt_id="active"),
        SimpleNamespace(attempt_id="completed"),
    )
    attempts = (
        SimpleNamespace(attempt_id="completed"),
        SimpleNamespace(attempt_id="empty"),
    )
    events = []

    consume_trace_outputs(
        samples,
        attempts,
        write_sample=lambda sample: events.append(("sample", sample.attempt_id)),
        finish_attempt=lambda attempt: events.append(("finish", attempt.attempt_id)),
    )

    assert events == [
        ("finish", "empty"),
        ("sample", "active"),
        ("sample", "completed"),
        ("finish", "completed"),
        ("finish", "empty"),
    ]


def test_trace_outputs_preserves_sample_order_and_empty_batches():
    samples = tuple(SimpleNamespace(attempt_id=f"a-{index}") for index in range(3))
    events = []

    consume_trace_outputs(
        samples,
        (),
        write_sample=lambda sample: events.append(sample.attempt_id),
        finish_attempt=lambda attempt: events.append(f"finish:{attempt.attempt_id}"),
    )

    assert events == ["a-0", "a-1", "a-2"]
