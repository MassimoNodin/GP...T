import pytest

from f1_engineer.processing.evidence import EvidenceUnavailable
from f1_engineer.processing.materialization import HistoricalMaterializer, MaterializationLimits
from tests.test_detail_evidence import evidence_packets, run_generation, _matching_attempt


def deferred_fixture(tmp_path):
    store, coordinator = run_generation(tmp_path / "source.sqlite3", "demand_v1", "materialization-source")
    for raw in evidence_packets():
        coordinator.ingest(raw)
    session, attempt = _matching_attempt(store, role="opponent", car=3)
    return store, coordinator, session, attempt


def snapshot(store):
    with store.connect() as database:
        return {table: [tuple(row) for row in database.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("attempts", "chunks", "dispositions", "journal", "generations", "reports")}


def test_materialization_matches_full_without_mutating_source(tmp_path):
    store, coordinator, session, original = deferred_fixture(tmp_path)
    full, full_coordinator = run_generation(tmp_path / "full.sqlite3", "full", "comparison-full")
    try:
        for raw in evidence_packets():
            full_coordinator.ingest(raw)
        full_session, full_attempt = _matching_attempt(full, role="opponent", car=3)
        _, expected = full.evidence(full_attempt["id"], session=full_session)
        before = snapshot(store)
        materializer = HistoricalMaterializer(store)
        metadata, records = materializer.materialize(original["id"], session=session)
        assert records == expected
        assert metadata["id"] == original["id"]
        assert metadata["session"] == session
        assert metadata["driver"] == original["driver"]
        assert metadata["readiness"]["state"] == "published"
        assert metadata["materialization"]["source_revision"] == original["id"]
        assert metadata["materialization"]["journal_end"] == original["published_sequence"]
        assert snapshot(store) == before
        repeated, repeated_records = materializer.materialize(original["id"], session=session)
        assert repeated_records == records
        assert repeated == metadata
        assert repeated["materialization"]["journal_hash"] == metadata["materialization"]["journal_hash"]
        assert materializer.status()["completed"] == 2
        assert store.attempt(original["id"], session=session)["readiness"]["state"] == "deferred"
    finally:
        coordinator.close()
        full_coordinator.close()


def test_materialization_wrong_session_and_metadata_lookup(tmp_path):
    store, coordinator, session, original = deferred_fixture(tmp_path)
    try:
        assert store.attempt(original["id"], session=session) == original
        for lookup in (store.attempt, HistoricalMaterializer(store).materialize):
            with pytest.raises(EvidenceUnavailable, match="attempt_not_in_session"):
                lookup(original["id"], session="not-the-session")
    finally:
        coordinator.close()


@pytest.mark.parametrize("limits,reason", [
    (MaterializationLimits(journal_entries=1), "source_budget"),
    (MaterializationLimits(source_bytes=1), "source_budget"),
    (MaterializationLimits(temporary_bytes=1), "storage_budget"),
])
def test_materialization_limits_release_admission(tmp_path, limits, reason):
    store, coordinator, session, original = deferred_fixture(tmp_path)
    try:
        service = HistoricalMaterializer(store, limits=limits)
        for _ in range(2):
            with pytest.raises(EvidenceUnavailable, match=reason):
                service.materialize(original["id"], session=session)
        assert service.status()["failed"] == 2
        assert service.status()["active"] is False
    finally:
        coordinator.close()


def test_materialization_cancel_and_timeout(tmp_path):
    store, coordinator, session, original = deferred_fixture(tmp_path)
    try:
        service = HistoricalMaterializer(store)
        with pytest.raises(EvidenceUnavailable, match="cancelled"):
            service.materialize(original["id"], session=session, cancelled=lambda: True)
        ticks = iter((0.0, 31.0))
        service = HistoricalMaterializer(store, clock=lambda: next(ticks))
        with pytest.raises(EvidenceUnavailable, match="time_budget"):
            service.materialize(original["id"], session=session)
        assert service.status()["active"] is False
    finally:
        coordinator.close()


def test_materialization_busy(tmp_path):
    store, coordinator, session, original = deferred_fixture(tmp_path)
    try:
        service = HistoricalMaterializer(store)
        service._admission.acquire()
        with pytest.raises(EvidenceUnavailable, match="materialization_busy"):
            service.materialize(original["id"], session=session)
        service._admission.release()
    finally:
        coordinator.close()


@pytest.mark.parametrize("damage,reason", [
    ("checksum", "checksum_mismatch"),
    ("missing", "prefix_incomplete"),
    ("version", "processor_version"),
    ("uncommitted", "not_committed"),
    ("kind", "unknown_journal_kind"),
    ("metadata", "journal_metadata_invalid"),
])
def test_materialization_fails_closed_on_source_damage(tmp_path, damage, reason):
    store, coordinator, session, original = deferred_fixture(tmp_path)
    try:
        with store.connect() as database:
            database.execute("DROP TRIGGER immutable_journal")
            database.execute("DROP TRIGGER retain_journal")
            if damage == "checksum":
                database.execute("UPDATE journal SET payload=x'00' WHERE sequence=1")
            elif damage == "missing":
                database.execute("DELETE FROM journal WHERE sequence=1")
            elif damage == "version":
                database.execute("UPDATE generations SET version='unknown'")
            elif damage == "uncommitted":
                database.execute("UPDATE generations SET committed_sequence=0")
            elif damage == "metadata":
                database.execute("UPDATE journal SET metadata='not-json' WHERE sequence=1")
            else:
                database.execute("UPDATE journal SET kind='unknown' WHERE sequence=1")
        with pytest.raises(EvidenceUnavailable, match=reason):
            HistoricalMaterializer(store).materialize(original["id"], session=session)
    finally:
        coordinator.close()


def test_materialization_published_is_reused(tmp_path):
    store, coordinator, session, _ = deferred_fixture(tmp_path)
    try:
        _, published = _matching_attempt(store, role="player", car=0)
        assert HistoricalMaterializer(store).materialize(published["id"], session=session) == store.evidence(
            published["id"], session=session)
    finally:
        coordinator.close()


@pytest.mark.parametrize("kwargs", [{"journal_entries": 100001}, {"duration_s": 0},
                                   {"source_bytes": True}, {"duration_s": float("nan")}])
def test_materialization_limits_cannot_raise_contract_caps(kwargs):
    with pytest.raises(ValueError):
        MaterializationLimits(**kwargs)


def test_materialization_keeps_repeated_uid_occurrence_identity(tmp_path):
    from tests.helpers import make_datagram
    from tests.test_lap_tracking import SESSION_UID
    from tests.test_session_evidence import admitted_packets

    store, coordinator = run_generation(tmp_path / "occurrences.sqlite3", "demand_v1", "occurrence-replay")
    try:
        for raw in admitted_packets(frames=50):
            coordinator.ingest(raw)
        coordinator.ingest(make_datagram(packet_id=3, session_uid=SESSION_UID, frame=51,
                                        session_time=2.55, body=b"SEND" + bytes(12)))
        for frame in range(52, 57):
            coordinator.ingest(make_datagram(packet_id=255, session_uid=SESSION_UID,
                                            frame=frame, session_time=2.6))
        coordinator.ingest(make_datagram(packet_id=3, session_uid=SESSION_UID, frame=1,
                                        session_time=0, body=b"SSTA" + bytes(12)))
        for raw in admitted_packets(frames=50):
            coordinator.ingest(raw)
        session = next(item["id"] for item in store.sessions() if item["occurrence"] == 2)
        attempt = next(item for item in store.attempts(session)
                       if item["role"] == "opponent" and item["payload"]["car_index"] == 3
                       and item["payload"]["lap_number"] == 2)
        metadata, records = HistoricalMaterializer(store).materialize(attempt["id"], session=session)
        assert metadata["session"] == session
        assert metadata["driver"] == attempt["driver"]
        assert records
    finally:
        coordinator.close()


def test_materialization_demand_mutations_preserve_journal_sequences(tmp_path):
    from f1_engineer.processing.detail_demand import DetailCommand

    store, coordinator = run_generation(tmp_path / "demands.sqlite3", "demand_v1", "demand-replay")
    try:
        packets = tuple(evidence_packets())
        for raw in packets[:10]:
            coordinator.ingest(raw)
        session = store.sessions()[0]["id"]
        binding = next(item["binding_id"] for item in coordinator.detail_demand_queue.targets(session)
                       if item["car_index"] == 4)
        coordinator.apply_detail_command(DetailCommand("post", "unrelated-task", session, binding))
        for raw in packets[10:]:
            coordinator.ingest(raw)
        _, attempt = _matching_attempt(store, role="opponent", car=3)
        metadata, records = HistoricalMaterializer(store).materialize(attempt["id"], session=session)
        assert records
        assert metadata["materialization"]["journal_end"] == attempt["published_sequence"]
        assert coordinator.detail_demand_queue.get("unrelated-task").state == "active"
    finally:
        coordinator.close()


@pytest.mark.parametrize("stage", ["after_journal_commit", "before_publication_commit", "after_chunk_seal", "before_manifest_insert"])
def test_materialization_faults_clean_temporary_storage_and_preserve_source(tmp_path, monkeypatch, stage):
    from pathlib import Path
    from f1_engineer.processing import materialization as module

    store, coordinator, session, attempt = deferred_fixture(tmp_path)
    original_coordinator = module.SessionCoordinator
    original_temporary = module.TemporaryDirectory
    paths = []

    def temporary(*args, **kwargs):
        directory = original_temporary(*args, **kwargs)
        paths.append(Path(directory.name))
        return directory

    def create(*args, **kwargs):
        owner = original_coordinator(*args, **kwargs)
        def fault(current):
            if current == stage:
                raise RuntimeError("injected materialization fault")
        owner.fault_injector = fault
        return owner

    monkeypatch.setattr(module, "TemporaryDirectory", temporary)
    monkeypatch.setattr(module, "SessionCoordinator", create)
    try:
        before = snapshot(store)
        service = HistoricalMaterializer(store)
        with pytest.raises(RuntimeError, match="injected materialization fault"):
            service.materialize(attempt["id"], session=session)
        assert snapshot(store) == before
        assert all(not directory.exists() for directory in paths)
        assert service.status()["active"] is False
        monkeypatch.setattr(module, "SessionCoordinator", original_coordinator)
        assert service.materialize(attempt["id"], session=session)[1]
    finally:
        coordinator.close()


def test_materialized_provider_is_session_checked_and_isolated(tmp_path):
    from f1_engineer.processing.materialization import MaterializedEvidenceProvider

    store, coordinator, session, attempt = deferred_fixture(tmp_path)
    try:
        metadata, records = HistoricalMaterializer(store).materialize(attempt["id"], session=session)
        provider = MaterializedEvidenceProvider(store)
        provider.add(metadata, records)
        metadata["manifest"]["qualifications"].append("changed")
        records.clear()
        assert provider.evidence(attempt["id"], session=session)[1]
        assert "changed" not in provider.attempt(attempt["id"], session=session)["manifest"]["qualifications"]
        assert next(item for item in provider.attempts(session) if item["id"] == attempt["id"])["readiness"]["state"] == "published"
        with pytest.raises(EvidenceUnavailable, match="attempt_not_in_session"):
            provider.evidence(attempt["id"], session="wrong")
        bad = provider.attempt(attempt["id"], session=session)
        bad["driver"] = "wrong-driver"
        with pytest.raises(EvidenceUnavailable, match="identity_mismatch"):
            provider.add(bad, provider.evidence(attempt["id"], session=session)[1])
    finally:
        coordinator.close()


def test_metadata_binding_verification_detects_missing_tenure(tmp_path):
    store, coordinator, session, attempt = deferred_fixture(tmp_path)
    try:
        assert store.attempt(attempt["id"], session=session)["binding_verified"] is True
        with store.connect() as database:
            database.execute("DELETE FROM bindings WHERE id=?", (attempt["driver"],))
        assert store.attempt(attempt["id"], session=session)["binding_verified"] is False
        assert next(item for item in store.attempts(session) if item["id"] == attempt["id"])["binding_verified"] is False
    finally:
        coordinator.close()


def test_materialization_preserves_unknown_context_qualification(tmp_path):
    from tests.test_session_evidence import admitted_packets

    store, coordinator = run_generation(tmp_path / "unknown-context.sqlite3", "demand_v1", "unknown-context")
    try:
        for raw in admitted_packets(frames=50):
            coordinator.ingest(raw)
        session, attempt = _matching_attempt(store, role="opponent", car=3)
        metadata, records = HistoricalMaterializer(store).materialize(attempt["id"], session=session)
        assert records
        assert "session_context_unknown_or_truncated" in metadata["manifest"]["qualifications"]
        assert metadata["readiness"]["state"] == "published"
    finally:
        coordinator.close()
