import json
import socket
import time
from types import SimpleNamespace

import pytest

from f1_engineer.processing.runtime import LiveSessionRuntime
from f1_engineer.processing.evidence import EvidenceStore
from f1_engineer.udp.source import UDPSource
from tests.test_session_evidence import admitted_packets


@pytest.mark.parametrize("kernel_dropped", [None, 0, 7])
def test_runtime_preserves_socket_loss_observation_without_combining_queue_losses(kernel_dropped):
    runtime = LiveSessionRuntime(None, host="127.0.0.1", port=49077)
    runtime.source = SimpleNamespace(
        pending_count=0,
        independent_receiver=True,
        receive_buffer_bytes=212992,
        kernel_receive_drops=kernel_dropped,
        stats=SimpleNamespace(received=12, dropped=2, socket_errors=0),
    )
    status = runtime.status()
    assert status["kernel_dropped"] == kernel_dropped
    assert status["dropped"] == 2
    assert status["received"] == 12
    assert status["receiver_mode"] == "independent_thread"
    assert status["receive_buffer_bytes"] == 212992


def test_runtime_without_a_socket_has_unknown_kernel_losses():
    runtime = LiveSessionRuntime(None, host="127.0.0.1", port=49077)
    assert runtime.status()["kernel_dropped"] is None


def test_observed_kernel_losses_interrupt_evidence_once_per_increase(tmp_path, monkeypatch):
    observed = [None]
    monkeypatch.setattr(UDPSource, "kernel_receive_drops", property(lambda self: observed[0]))
    store = EvidenceStore(tmp_path / "kernel-loss.sqlite3")
    runtime = LiveSessionRuntime(store, host="127.0.0.1", port=0, stale_after_s=10)

    def overflow_count():
        with store.connect() as database:
            rows = database.execute("SELECT payload FROM metadata WHERE kind='gap'").fetchall()
        return sum(json.loads(row[0])["reason"] == "kernel_receive_overflow" for row in rows)

    def wait_until(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        pytest.fail("kernel loss fence was not observed before the deadline")

    runtime.start()
    try:
        assert runtime.state == "listening"
        port = runtime.source._receiver_socket.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(next(admitted_packets()).payload, ("127.0.0.1", port))
        wait_until(lambda: runtime.processed == 1)
        observed[0] = 3
        wait_until(lambda: overflow_count() == 1)
        assert store.sessions()[0]["lifecycle"] == "interrupted"
        observed[0] = None
        time.sleep(0.1)
        observed[0] = 3
        time.sleep(0.1)
        assert overflow_count() == 1
        observed[0] = 5
        wait_until(lambda: overflow_count() == 2)
        assert runtime.status()["kernel_dropped"] == 5
        assert runtime.status()["dropped"] == 0
    finally:
        runtime.close()
