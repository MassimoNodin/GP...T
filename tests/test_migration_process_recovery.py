import subprocess
import sys

import pytest

from f1_engineer.analysis.session_comparison import compare_session_laps
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore
from tests.test_session_evidence import admitted_packets, completed_pair


@pytest.mark.parametrize("failpoint", ["after_journal_commit", "before_publication_commit"])
def test_process_death_recovers_committed_batches_without_duplicates(tmp_path, failpoint):
    database_path = tmp_path / "crashed.sqlite3"
    child = """
import os,sys
from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore
from tests.test_session_evidence import admitted_packets
store=EvidenceStore(sys.argv[1])
coordinator=SessionCoordinator(store,'process-recovery')
packets=list(admitted_packets())
for offset in range(0,len(packets),32):
    if offset >= 64:
        def crash(stage):
            if stage == sys.argv[2]:
                os._exit(23)
        coordinator.fault_injector=crash
    coordinator.journal_batch(packets[offset:offset+32])
    coordinator.publish_pending()
raise RuntimeError('crash point was not exercised')
"""
    result = subprocess.run([sys.executable, "-c", child, str(database_path), failpoint], timeout=30)
    assert result.returncode == 23
    store = EvidenceStore(database_path)
    with store.connect() as database:
        assert database.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        admitted = database.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
        assert admitted == 96
    recovered = SessionCoordinator(store, "process-recovery")
    assert recovered.sequence == admitted
    for packet in list(admitted_packets())[admitted:]:
        recovered.ingest(packet)
    session, target, reference = completed_pair(store)
    report = compare_session_laps(store, session, target["id"], reference["id"])
    with store.connect() as database:
        assert database.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 44
    recovered.finish()
    recovered.close()
    reopened = EvidenceStore(database_path)
    assert reopened.report(report["id"]) == report
    with reopened.connect() as database:
        assert database.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 66
        assert database.execute("PRAGMA quick_check").fetchone()[0] == "ok"
