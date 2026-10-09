from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
import time
from contextlib import contextmanager
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

from ..errors import ProtocolError
from ..pipeline import TelemetryPipeline
from ..sessions.lap_tracker import LapAttempt, LapDisposition
from ..sessions.lifecycle import LifecycleEvent, reconcile_attempt_lifecycle
from ..storage.lock import ImportRunLock
from ..udp.models import RawDatagram
from .evidence import CHUNK_ROWS, MAX_STAGED_ROWS_PER_CAR, PROCESSOR_VERSION, EvidenceStore, EvidenceUnavailable, digest, encode
from .output_consumption import consume_trace_outputs


PUBLICATION_BATCH_ROWS = 32
PUBLICATION_INTERVAL_S = 0.020


class SessionCoordinator:
    """One serialized owner for live and replay. Raw admission precedes publication."""

    def __init__(self, store: EvidenceStore, source: str, *, recover: bool = True) -> None:
        self.store = store
        self.source = source
        self.pipeline = TelemetryPipeline(max_open_frames=64, max_pending_packets=512)
        self.sequence = 0
        self.admitted_sequence = 0
        self.last_journal_s = 0.0
        self.gap_epoch = 0
        self._session_ids: OrderedDict[int, str] = OrderedDict()
        self._staging_counts: OrderedDict[tuple[str, int], int] = OrderedDict()
        self._binding_payloads: OrderedDict[str, Any] = OrderedDict()
        self.failed = False
        self.finished = False
        self.before_commit = None
        self.fault_injector = None
        self._ownership = ImportRunLock(store.path.with_name(store.path.name + "." + digest(source)[:16] + ".lock"))
        try:
            self._ownership.__enter__()
        except ValueError as exception:
            raise EvidenceUnavailable("evidence_source_already_owned") from exception
        self._writer = sqlite3.connect(store.path, timeout=10)
        self._writer.row_factory = sqlite3.Row
        self._writer.execute("PRAGMA synchronous=FULL")
        self._writer.execute("PRAGMA cache_size=-4096")
        with store.connect() as database:
            existing = database.execute(
                "SELECT * FROM generations WHERE source=? AND state IN ('running','recovering','failed') ORDER BY rowid DESC LIMIT 1",
                (source,),
            ).fetchone() if recover else None
            if existing:
                if existing["version"] != PROCESSOR_VERSION:
                    self.close()
                    raise EvidenceUnavailable("processor_version_requires_new_generation")
                self.generation = existing["id"]
                database.execute("UPDATE generations SET state='recovering' WHERE id=?", (self.generation,))
            else:
                self.generation = uuid.uuid4().hex
                database.execute("INSERT INTO generations(id,source,version,state) VALUES (?,?,?,'running')",
                                 (self.generation, source, PROCESSOR_VERSION))
                database.execute("INSERT INTO metadata VALUES (?,?,?,?,?)", (
                    self.generation, 0, "processor_config", 0,
                    encode({"processor_version": PROCESSOR_VERSION, "ledger_schema": 2,
                            "max_open_frames": 64, "max_pending_packets": 512, "reorder_window_frames": 3,
                            "context_history_limit": 512, "attempt_context_limit": 64,
                            "staging_limit_per_car": MAX_STAGED_ROWS_PER_CAR}),
                ))
        if existing:
            try:
                self._recover()
            except Exception:
                self.close()
                raise

    def _session(self, uid: int | str) -> str:
        uid = int(uid)
        if uid not in self._session_ids:
            row = self._writer.execute("SELECT id FROM sessions WHERE generation=? AND uid=? ORDER BY occurrence DESC LIMIT 1",
                                       (self.generation, str(uid))).fetchone()
            if row is None:
                raise EvidenceUnavailable("logical_session_not_established")
            self._session_ids[uid] = row["id"]
            while len(self._session_ids) > 128:
                self._session_ids.popitem(last=False)
        return self._session_ids[uid]

    def _recover(self) -> None:
        with self.store.connect() as database:
            checkpoint = database.execute("SELECT committed_sequence FROM generations WHERE id=?", (self.generation,)).fetchone()[0]
        while True:
            with self.store.connect() as database:
                batch = database.execute("SELECT * FROM journal WHERE generation=? AND sequence>? ORDER BY sequence LIMIT 32",
                                         (self.generation, self.sequence)).fetchall()
            if not batch:
                break
            tail = []
            for row in batch:
                self.admitted_sequence = row["sequence"]
                if row["sequence"] <= checkpoint:
                    self.sequence = row["sequence"]
                    self._process(row["kind"], bytes(row["payload"]), json.loads(row["metadata"]), persist=False)
                else:
                    tail.append(row)
                    if len(tail) == PUBLICATION_BATCH_ROWS:
                        self._publish_entries(tail)
                        tail = []
            if tail:
                self._publish_entries(tail)
        with self.store.connect() as database:
            database.execute("UPDATE generations SET state=? WHERE id=?", ("finished" if self.finished else "running", self.generation))

    @contextmanager
    def _transaction(self):
        try:
            with self._writer:
                yield self._writer
        except Exception:
            self._staging_counts.clear()
            self._binding_payloads.clear()
            raise

    def close(self) -> None:
        self._writer.close()
        self._ownership.__exit__()

    def _fault(self, stage: str) -> None:
        if self.fault_injector:
            self.fault_injector(stage)

    def ingest(self, raw: RawDatagram) -> None:
        self._admit("datagram", raw.payload, self._raw_metadata(raw))

    def _raw_metadata(self, raw: RawDatagram) -> dict[str, Any]:
        if len(raw.payload) > 65535 or len(raw.source_host) > 255:
            raise EvidenceUnavailable("source_datagram_budget_exceeded")
        return {
            "sequence": raw.sequence, "captured_at_ns": raw.captured_at_ns,
            "monotonic_ns": raw.monotonic_ns, "source_host": raw.source_host,
            "source_port": raw.source_port,
        }

    @property
    def pending_publication(self) -> int:
        return self.admitted_sequence - self.sequence

    def journal(self, raw: RawDatagram) -> None:
        self.journal_batch((raw,))

    def journal_batch(self, packets: Sequence[RawDatagram]) -> None:
        if self.failed:
            raise EvidenceUnavailable("coordinator_requires_recovery")
        if self.finished:
            raise EvidenceUnavailable("generation_finished")
        if not packets:
            return
        if self.pending_publication + len(packets) > PUBLICATION_BATCH_ROWS:
            raise EvidenceUnavailable("publication_batch_budget_exceeded")
        entries = [
            (self.generation, self.admitted_sequence + index + 1, "datagram", raw.payload,
             encode({**self._raw_metadata(raw), "payload_sha256": hashlib.sha256(raw.payload).hexdigest()}))
            for index, raw in enumerate(packets)
        ]
        try:
            started = time.perf_counter()
            with self._transaction() as database:
                database.executemany("INSERT INTO journal VALUES (?,?,?,?,?)", entries)
            self.admitted_sequence += len(packets)
            self.last_journal_s = time.perf_counter() - started
            self._fault("after_journal_commit")
        except Exception:
            self._mark_failed()
            raise

    def publish_pending(self) -> int:
        if self.failed:
            raise EvidenceUnavailable("coordinator_requires_recovery")
        rows = self._writer.execute("""SELECT * FROM journal WHERE generation=? AND sequence>?
            ORDER BY sequence LIMIT ?""", (self.generation, self.sequence, PUBLICATION_BATCH_ROWS)).fetchall()
        if not rows:
            return 0
        try:
            self._publish_entries(rows)
        except Exception:
            self._mark_failed()
            raise
        return sum(row["kind"] == "datagram" for row in rows)

    def _publish_entries(self, rows: Sequence[sqlite3.Row]) -> None:
        with self._transaction() as database:
            database.execute("BEGIN IMMEDIATE")
            for row in rows:
                self.sequence = row["sequence"]
                self._process(row["kind"], bytes(row["payload"]), json.loads(row["metadata"]), database=database)
            self._fault("before_publication_commit")
            if self.before_commit:
                self.before_commit()

    def gap(self, reason: str) -> None:
        self._admit("gap", b"", {"reason": reason})

    def finish(self) -> None:
        self._admit("finish", b"", {})

    def _admit(self, kind: str, payload: bytes, metadata: dict[str, Any]) -> None:
        if self.pending_publication:
            self.publish_pending()
        metadata = self._append(kind, payload, metadata, synchronous=True)
        try:
            self._process(kind, payload, metadata)
        except Exception:
            self._mark_failed()
            raise

    def _mark_failed(self) -> None:
        self.failed = True
        try:
            with self.store.connect() as database:
                database.execute("UPDATE generations SET state='failed' WHERE id=?", (self.generation,))
        except sqlite3.Error:
            pass

    def _append(self, kind: str, payload: bytes, metadata: dict[str, Any], *, synchronous: bool = False) -> dict[str, Any]:
        if self.failed:
            raise EvidenceUnavailable("coordinator_requires_recovery")
        if self.finished:
            raise EvidenceUnavailable("generation_finished")
        self.admitted_sequence += 1
        if synchronous:
            self.sequence = self.admitted_sequence
        metadata = {**metadata, "payload_sha256": hashlib.sha256(payload).hexdigest()}
        try:
            started = time.perf_counter()
            with self._transaction() as database:
                database.execute("INSERT INTO journal VALUES (?,?,?,?,?)",
                                 (self.generation, self.admitted_sequence, kind, payload, encode(metadata)))
            self.last_journal_s = time.perf_counter() - started
            self._fault("after_journal_commit")
        except Exception:
            self._mark_failed()
            raise
        return metadata

    def _input_route(self, payload: bytes, metadata: dict[str, Any], *, persist: bool) -> dict[str, Any]:
        if "retired_input" in metadata or "restart_occurrence" in metadata:
            return metadata
        if not persist:
            row = self._writer.execute("""SELECT payload FROM metadata WHERE generation=? AND sequence=?
                AND kind='input_route' AND ordinal=0""", (self.generation, self.sequence)).fetchone()
            return {**metadata, **json.loads(row[0])} if row else metadata
        raw_metadata = {key: value for key, value in metadata.items() if key != "payload_sha256"}
        try:
            packet = self.pipeline.decoder.decode(RawDatagram(payload=payload, **raw_metadata))
            if packet.header.session_uid == 0:
                metadata = {**metadata, "retired_input": True}
            prior = self._writer.execute("SELECT lifecycle FROM sessions WHERE generation=? AND uid=? ORDER BY occurrence DESC LIMIT 1",
                                         (self.generation, str(packet.header.session_uid))).fetchone()
            if prior and prior["lifecycle"] == "ended":
                event = self.pipeline.event_decoder.decode(packet)
                verified_start = (event.error is None and event.code == "SSTA"
                                  and packet.header.session_uid == self.pipeline.sessions.current_session_uid)
                metadata = {**metadata, "restart_occurrence": verified_start, "retired_input": not verified_start}
        except ProtocolError:
            pass
        return metadata

    def _process(self, kind: str, payload: bytes, metadata: dict[str, Any], *, persist: bool = True,
                 database: sqlite3.Connection | None = None) -> None:
        if persist and database is None:
            with self._transaction() as database:
                database.execute("BEGIN IMMEDIATE")
                self._process(kind, payload, metadata, database=database)
                self._fault("before_publication_commit")
                if self.before_commit:
                    self.before_commit()
            return
        output = None
        error = None
        metadata = dict(metadata)
        expected_hash = metadata.pop("payload_sha256", None)
        if expected_hash != hashlib.sha256(payload).hexdigest():
            raise EvidenceUnavailable("journal_checksum_mismatch")
        if kind == "datagram":
            metadata = self._input_route(payload, metadata, persist=persist)
            if persist and (metadata.get("retired_input") or metadata.get("restart_occurrence")):
                self._metadata(database, "input_route", [{"retired_input": metadata.get("retired_input", False),
                                                         "restart_occurrence": metadata.get("restart_occurrence", False)}])
        restart_occurrence = metadata.pop("restart_occurrence", False)
        retired_input = metadata.pop("retired_input", False)
        prior_output = None
        if restart_occurrence:
            prior_output = self.pipeline.finish_with_outputs(interruption_reason="authoritative_new_occurrence")
            self.pipeline = TelemetryPipeline(max_open_frames=64, max_pending_packets=512)
        if kind == "datagram" and not retired_input:
            try:
                output = self.pipeline.process(RawDatagram(payload=payload, **{**metadata, "sequence": self.sequence}))
            except ProtocolError as exception:
                error = str(exception)
        elif kind == "finish":
            output = self.pipeline.finish_with_outputs()
            self.finished = True
        elif kind == "gap":
            output = self.pipeline.finish_with_outputs(interruption_reason=metadata["reason"])
            self.gap_epoch += 1
        if not persist:
            self.pipeline.release_consumed_history()
            return
        if database is not None:
            if prior_output is not None:
                self._consume(database, prior_output)
            if output is not None:
                self._consume(database, output)
            if kind == "gap":
                self._metadata(database, "gap", [metadata])
                database.execute("UPDATE sessions SET lifecycle='interrupted',acquisition='stale' WHERE generation=? AND lifecycle!='ended'", (self.generation,))
            if error:
                self._metadata(database, "decode_error", [{"error": error}])
            if retired_input:
                self._metadata(database, "retired_input", [{"reason": "ended_or_retired_logical_session"}])
            if kind == "finish":
                database.execute("UPDATE sessions SET lifecycle='ended',acquisition='stopped' WHERE generation=?", (self.generation,))
            database.execute("UPDATE generations SET committed_sequence=?,gap_epoch=?,state=? WHERE id=?",
                             (self.sequence, self.gap_epoch, "finished" if kind == "finish" else "running", self.generation))
        self.pipeline.release_consumed_history()

    def _metadata(self, database: sqlite3.Connection, kind: str, values: Sequence[Any]) -> None:
        database.executemany("INSERT OR IGNORE INTO metadata VALUES (?,?,?,?,?)", [
            (self.generation, self.sequence, kind, ordinal, encode(value))
            for ordinal, value in enumerate(values)
        ])

    def _consume(self, database: sqlite3.Connection, output: Any) -> None:
        for event in getattr(output, "session_events", ()):
            if event.kind == "session_started":
                occurrence = database.execute("SELECT COALESCE(MAX(occurrence),0)+1 FROM sessions WHERE generation=? AND uid=?",
                                              (self.generation, str(event.session_uid))).fetchone()[0]
                session = uuid.uuid5(uuid.NAMESPACE_URL, f"{self.generation}:{event.session_uid}:{occurrence}").hex
                database.execute("INSERT INTO sessions VALUES (?,?,?,'active','receiving',NULL,?)",
                                 (session, self.generation, str(event.session_uid), occurrence))
                self._session_ids[event.session_uid] = session
                while len(self._session_ids) > 128:
                    self._session_ids.popitem(last=False)
            else:
                session = self._session(event.session_uid)
            if event.kind == "session_ended":
                database.execute("UPDATE sessions SET lifecycle='ended' WHERE id=?", (session,))
        current_uid = self.pipeline.sessions.current_session_uid
        if current_uid is not None and (not hasattr(output, "packet") or output.packet.header.session_uid == current_uid):
            database.execute("UPDATE sessions SET lifecycle=CASE WHEN lifecycle='ended' THEN lifecycle ELSE 'active' END,acquisition='receiving' WHERE id=?",
                             (self._session(current_uid),))
        for change in getattr(output, "context_history_changes", ()):
            if change.context is not None:
                database.execute("UPDATE sessions SET context=? WHERE id=?", (encode(change.context.to_dict()), self._session(change.session_uid)))
        progress = getattr(output, "session_progress", None)
        if progress is not None and current_uid is not None:
            self._metadata(database, "session_progress", [progress])
            row = database.execute("SELECT context FROM sessions WHERE id=?", (self._session(current_uid),)).fetchone()
            context = json.loads(row["context"]) if row and row["context"] else {}
            context["progress"] = progress
            database.execute("UPDATE sessions SET context=? WHERE id=?", (encode(context), self._session(current_uid)))
        for name in ("context_history_changes", "lifecycle_events", "session_history",
                     "player_participant_observations", "player_car_setup_observations", "car_slot_tenures"):
            self._metadata(database, name, getattr(output, name, ()))
        frames = {frame.session_uid: self.pipeline.frame_ordinal(frame.session_uid) for frame in output.completed_frames}
        tenures = (*getattr(output, "car_slot_tenures", ()), *self.pipeline.car_lap_inventory.active_tenures(frames))
        for tenure in tenures:
            session = self._session(tenure.session_uid)
            driver = digest(f"{session}:{tenure.lifecycle_epoch}:{tenure.car_index}:{tenure.tenure_ordinal}:{tenure.participant_identity_fingerprint}")
            if self._binding_payloads.get(driver) == tenure:
                continue
            database.execute("""INSERT INTO bindings VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id)
                DO UPDATE SET end_frame=MAX(bindings.end_frame,excluded.end_frame),
                payload=CASE WHEN excluded.end_frame>=bindings.end_frame THEN excluded.payload ELSE bindings.payload END""", (
                driver, session, tenure.lifecycle_epoch, tenure.packet_format, tenure.car_index,
                tenure.tenure_ordinal, tenure.start_frame_ordinal, tenure.end_frame_ordinal_exclusive,
                tenure.participant_identity_fingerprint, encode(tenure),
            ))
            self._binding_payloads[driver] = tenure
            self._binding_payloads.move_to_end(driver)
            while len(self._binding_payloads) > 512:
                self._binding_payloads.popitem(last=False)
        source_ranges = {(frame.session_uid, frame.overall_frame_identifier):
                         sorted({packet.source_sequence for packet in frame.packets if packet.source_sequence is not None})
                         for frame in output.completed_frames}
        for observation in output.car_observations:
            record = observation.to_record()
            record["gap_epoch"] = self.gap_epoch
            record["source_sequences"] = source_ranges.get((observation.session_uid, observation.frame_identifier), [])
            self._stage_observation(database, (
                self.generation, str(observation.session_uid), observation.car_index,
                observation.frame_ordinal, observation.lifecycle_epoch, observation.packet_format,
                self.sequence, self.gap_epoch, encode(record),
            ))
        observation_rows = {(row.session_uid, row.car_index, row.frame_identifier): row for row in output.car_observations}
        def write_player_sample(sample):
            observation = observation_rows.get((sample.session_uid, sample.car_index, sample.frame_identifier))
            if observation is None:
                return
            staged = database.execute("SELECT payload FROM staging WHERE generation=? AND uid=? AND car=? AND frame=?",
                                      (self.generation, str(sample.session_uid), sample.car_index, observation.frame_ordinal)).fetchone()
            if staged is not None:
                record = {**json.loads(staged["payload"]), **sample.to_record()}
                database.execute("UPDATE staging SET payload=? WHERE generation=? AND uid=? AND car=? AND frame=?",
                                 (encode(record), self.generation, str(sample.session_uid), sample.car_index, observation.frame_ordinal))
        consume_trace_outputs(output.car_samples, output.lap_attempts, write_sample=write_player_sample,
                              finish_attempt=lambda attempt: self._publish(database, attempt, "player", attempt.association_epoch,
                                                                            attempt.association_packet_format))
        for observed in output.observed_car_lap_attempts:
            self._publish(database, observed.attempt, "opponent", observed.lifecycle_epoch, observed.packet_format)
        for event in output.lifecycle_events:
            self._reconcile(database, event)
            if event.event_kind == "session_end_annotation":
                database.execute("UPDATE sessions SET lifecycle='ended' WHERE id=?", (self._session(event.session_uid),))
        for uid, car in {(row.session_uid, row.car_index) for row in output.car_observations}:
            self._prune_staging(database, str(uid), car)

    def _staging_count(self, database: sqlite3.Connection, uid: str, car: int) -> int:
        key = (uid, car)
        if key not in self._staging_counts:
            self._staging_counts[key] = database.execute(
                "SELECT COUNT(*) FROM staging WHERE generation=? AND uid=? AND car=?",
                (self.generation, uid, car),
            ).fetchone()[0]
        self._staging_counts.move_to_end(key)
        while len(self._staging_counts) > 128:
            self._staging_counts.popitem(last=False)
        return self._staging_counts[key]

    def _stage_observation(self, database: sqlite3.Connection, values: tuple[Any, ...]) -> None:
        generation, uid, car, frame, epoch, packet_format, sequence, gap_epoch, payload = values
        count = self._staging_count(database, uid, car)
        inserted = database.execute("""INSERT INTO staging VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(generation,uid,car,frame) DO NOTHING""", values).rowcount
        if inserted:
            self._staging_counts[(uid, car)] = count + inserted
        else:
            database.execute("""UPDATE staging SET epoch=?,format=?,sequence=?,gap_epoch=?,payload=?
                WHERE generation=? AND uid=? AND car=? AND frame=?""",
                (epoch, packet_format, sequence, gap_epoch, payload, generation, uid, car, frame))

    def _prune_staging(self, database: sqlite3.Connection, uid: str, car: int) -> None:
        excess = self._staging_count(database, uid, car) - MAX_STAGED_ROWS_PER_CAR
        if excess <= 0:
            return
        removed = database.execute("""DELETE FROM staging WHERE generation=? AND uid=? AND car=? AND frame <=
            (SELECT frame FROM staging WHERE generation=? AND uid=? AND car=?
            ORDER BY frame ASC LIMIT 1 OFFSET ?)""",
            (self.generation, uid, car, self.generation, uid, car, excess - 1)).rowcount
        self._staging_counts[(uid, car)] -= removed

    def _publish(self, database: sqlite3.Connection, attempt: LapAttempt, role: str,
                 epoch: int | None, packet_format: int | None) -> None:
        session = self._session(attempt.session_uid)
        revision = digest(f"{session}:{role}:{epoch}:{attempt.attempt_id}")
        if database.execute("SELECT 1 FROM attempts WHERE id=?", (revision,)).fetchone():
            return
        start, end = attempt.start_frame_ordinal, attempt.end_frame_ordinal
        owner = database.execute("""SELECT id FROM bindings WHERE session=? AND car=? AND epoch=? AND format=?
            AND start_frame<=? AND end_frame>? ORDER BY start_frame DESC LIMIT 2""",
            (session, attempt.car_index, epoch, packet_format, start, end)).fetchall()
        driver = owner[0]["id"] if len(owner) == 1 else None
        scope_safe = (attempt.association_scope_assessable
                      and attempt.start_association_epoch == epoch
                      and attempt.start_association_packet_format == packet_format)
        state = "published" if driver and scope_safe and attempt.disposition == LapDisposition.COMPLETED else "quarantined"
        reason = "completed_observed_lap" if state == "published" else "incomplete_or_unresolved_ownership"
        cursor = database.execute("""SELECT payload,sequence,gap_epoch FROM staging WHERE generation=? AND uid=?
            AND car=? AND frame>=? AND frame<=? AND epoch=? AND format=? ORDER BY frame""",
            (self.generation, str(attempt.session_uid), attempt.car_index, start, end, epoch, packet_format))
        chunks: list[str] = []
        rows = total_bytes = 0
        first_sequence = last_sequence = None
        gap_epochs: set[int] = set()
        while batch := cursor.fetchmany(CHUNK_ROWS):
            records = [json.loads(row["payload"]) for row in batch]
            content = encode(records)
            chunk_hash = digest(content)
            byte_count = len(content.encode())
            database.execute("INSERT OR IGNORE INTO chunks VALUES (?,?,?,?)", (chunk_hash, content, len(batch), byte_count))
            self._fault("after_chunk_seal")
            chunks.append(chunk_hash)
            rows += len(batch)
            total_bytes += byte_count
            sequences = [sequence for record in records for sequence in record["source_sequences"]]
            if sequences:
                first_sequence = min(sequences) if first_sequence is None else min(first_sequence, *sequences)
                last_sequence = max(sequences) if last_sequence is None else max(last_sequence, *sequences)
            gap_epochs.update(row["gap_epoch"] for row in batch)
        qualifications = []
        if any(segment.context is None for segment in attempt.context_segments):
            qualifications.append("session_context_unknown_or_truncated")
        if rows != attempt.sample_count:
            qualifications.append("missing_observation_rows")
        if len(gap_epochs) > 1:
            qualifications.append("acquisition_gap")
        if not attempt.start_observed:
            qualifications.append("lap_start_unobserved")
        manifest = {"schema": PROCESSOR_VERSION, "chunks": chunks, "driver": driver,
                    "epoch": epoch, "format": packet_format, "start_frame": start, "end_frame": end,
                    "source_start": first_sequence, "source_end": last_sequence,
                    "qualifications": qualifications}
        self._fault("before_manifest_insert")
        database.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?)", (
            revision, session, driver, attempt.attempt_id, role, encode(manifest),
            encode(attempt.to_dict()), rows, total_bytes, self.sequence,
        ))
        database.execute("INSERT INTO dispositions VALUES (?,?,?,?)", (revision, self.sequence, state, reason))
        self._fault("after_manifest_insert")
        if end is not None:
            removed = database.execute("DELETE FROM staging WHERE generation=? AND uid=? AND car=? AND frame<=?",
                             (self.generation, str(attempt.session_uid), attempt.car_index, end))
            key = (str(attempt.session_uid), attempt.car_index)
            if key in self._staging_counts:
                self._staging_counts[key] -= removed.rowcount

    def _reconcile(self, database: sqlite3.Connection, event: LifecycleEvent) -> None:
        if event.event_kind not in ("flashback", "session_time_regression", "malformed_or_unsupported"):
            return
        cursor = database.execute("SELECT id,payload FROM attempts WHERE session=?", (self._session(event.session_uid),))
        for row in cursor:
            payload = json.loads(row["payload"])
            payload["disposition"] = LapDisposition(payload["disposition"])
            payload["context_segments"] = ()
            payload["exclusion_reasons"] = tuple(payload["exclusion_reasons"])
            assessed, _, _, _ = reconcile_attempt_lifecycle((LapAttempt(**payload),), (event,))
            if assessed[0].superseded or not assessed[0].lifecycle_assessed:
                database.execute("INSERT OR IGNORE INTO dispositions VALUES (?,?,?,?)", (
                    row["id"], self.sequence, "superseded" if assessed[0].superseded else "quarantined", event.cause,
                ))
