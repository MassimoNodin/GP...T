import json

from f1_engineer.processing.coordinator import SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore, digest, encode
from tests.test_session_evidence import admitted_packets


def test_sealed_chunk_bytes_match_canonical_record_reencoding(tmp_path):
    store = EvidenceStore(tmp_path / "chunks.sqlite3")
    coordinator = SessionCoordinator(store, "canonical-chunks")
    try:
        packets = list(admitted_packets(frames=70))
        for offset in range(0, len(packets), 32):
            coordinator.journal_batch(packets[offset:offset + 32])
            coordinator.publish_pending()
        coordinator.finish()
    finally:
        coordinator.close()
    with store.connect() as database:
        chunks = database.execute("SELECT * FROM chunks").fetchall()
    assert chunks
    for chunk in chunks:
        records = json.loads(chunk["payload"])
        canonical = encode(records)
        assert chunk["payload"].encode("utf-8") == canonical.encode("utf-8")
        assert chunk["hash"] == digest(canonical)
        assert chunk["bytes"] == len(canonical.encode("utf-8"))
