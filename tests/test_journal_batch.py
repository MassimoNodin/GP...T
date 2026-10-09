import asyncio

import pytest

from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore, EvidenceUnavailable
from f1_engineer.udp.source import UDPSource
from scripts.benchmark_session_publication import fingerprint
from tests.test_session_evidence import admitted_packets


def test_batch_admission_preserves_serial_evidence(tmp_path):
    packets = list(admitted_packets())
    results = []
    for batch_size in (1, 32):
        store = EvidenceStore(tmp_path / f"batch-{batch_size}.sqlite3")
        coordinator = SessionCoordinator(store, "batch-parity")
        assert coordinator._writer.execute("PRAGMA synchronous").fetchone()[0] == 2
        for offset in range(0, len(packets), batch_size):
            batch = packets[offset:offset + batch_size]
            coordinator.journal_batch(batch)
            assert coordinator.pending_publication == len(batch)
            assert coordinator.publish_pending() == len(batch)
        coordinator.finish()
        coordinator.close()
        results.append(fingerprint(store))
    assert results[0] == results[1]


def test_batch_is_recoverable_after_journal_commit_fault(tmp_path):
    store = EvidenceStore(tmp_path / "recovery.sqlite3")
    coordinator = SessionCoordinator(store, "batch-recovery")
    packets = list(admitted_packets())[:32]

    def fail(stage):
        if stage == "after_journal_commit":
            raise OSError("committed batch fault")

    coordinator.fault_injector = fail
    with pytest.raises(OSError):
        coordinator.journal_batch(packets)
    with store.connect() as database:
        assert database.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 32
    coordinator.close()
    recovered = SessionCoordinator(store, "batch-recovery")
    assert recovered.pending_publication == 0
    assert recovered.sequence == 32
    recovered.close()


def test_oversized_batch_is_rejected_before_admission(tmp_path):
    store = EvidenceStore(tmp_path / "budget.sqlite3")
    coordinator = SessionCoordinator(store, "budget")
    with pytest.raises(EvidenceUnavailable, match="budget"):
        coordinator.journal_batch(list(admitted_packets())[:33])
    assert coordinator.admitted_sequence == 0
    coordinator.close()


def test_receive_batch_is_bounded_and_ordered():
    async def exercise():
        source = UDPSource(queue_size=4)
        packets = list(admitted_packets())[:4]
        for packet in packets:
            source._queue.put_nowait(packet)
        assert await source.receive_batch(3) == tuple(packets[:3])
        assert source.pending_count == 1
        assert await source.receive_batch(3) == (packets[3],)
    asyncio.run(exercise())


def test_unchanged_bindings_are_not_rewritten_and_cache_is_bounded(tmp_path):
    store = EvidenceStore(tmp_path / "bindings.sqlite3")
    coordinator = SessionCoordinator(store, "binding-cache")
    binding_writes = []
    coordinator._writer.set_trace_callback(
        lambda statement: binding_writes.append(statement)
        if statement.startswith("INSERT INTO bindings") else None
    )
    packets = list(admitted_packets())
    for packet in packets:
        coordinator.ingest(packet)
    assert len(binding_writes) < len(packets) * 22
    assert len(coordinator._binding_payloads) <= 512
    coordinator.close()
