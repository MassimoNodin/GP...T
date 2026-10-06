from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable

from ..errors import ProtocolError
from ..pipeline import TelemetryPipeline
from ..recording.capture import CaptureReader
from ..sessions.lifecycle import LifecycleEvent, reconcile_attempt_lifecycle
from ..sessions.participant_context import PlayerParticipantObservation
from ..sessions.setup_context import PlayerCarSetupObservation
from ..sessions.session_history import (
    AttemptTimingEvidence,
    SessionHistoryAccumulator,
)
from ..udp.models import PacketId
from ..telemetry.canonical import CarObservation, CarSample
from .database import Database
from .lock import ImportRunLock
from .participant_context import load_attempt_player_participant_context
from .car_setup_context import load_attempt_player_car_setup_context
from .parquet import (
    MAX_OBSERVATION_CHUNK_ROWS,
    OBSERVATION_SCHEMA_VERSION,
    ParquetObservationWriter,
    ParquetTraceWriter,
    TRACE_SCHEMA_VERSION,
    sha256_file,
)


PIPELINE_VERSION = "player-traces-v18-car-lap-inventory"
MAX_STORED_LIFECYCLE_EVENTS = 100_000
MAX_STORED_PLAYER_PARTICIPANT_OBSERVATIONS = 100_000
MAX_STORED_PLAYER_PARTICIPANT_TRUNCATION_FENCES = 256
PLAYER_PARTICIPANT_OBSERVATION_WRITE_BATCH = 256
MAX_STORED_PLAYER_CAR_SETUP_OBSERVATIONS = 100_000
MAX_STORED_PLAYER_CAR_SETUP_TRUNCATION_FENCES = 256
PLAYER_CAR_SETUP_OBSERVATION_WRITE_BATCH = 256
DEFAULT_DATABASE = Path("data") / "f1-engineer.sqlite3"
IMPORT_CONFIG = {"max_open_frames": 256, "reorder_window_frames": 3}
MAX_OPEN_OBSERVATION_WRITERS = 4
MAX_OBSERVATION_CHUNKS_PER_IMPORT = 4096
MAX_OBSERVATIONS_PER_CHUNK = MAX_OBSERVATION_CHUNK_ROWS
MAX_LAP_ATTEMPT_PAGE_OFFSET = 100_000
MAX_STORED_CAR_LAP_INVENTORY_ROWS = 100_000


@dataclass(frozen=True, slots=True)
class ImportSummary:
    run_id: str
    capture_sha256: str
    status: str
    already_imported: bool
    packet_count: int = 0
    malformed_packet_count: int = 0
    session_count: int = 0
    participant_packets: int = 0
    car_setup_packets_raw: int = 0
    car_setup_packets_decoded: int = 0
    car_setup_decode_errors: int = 0
    player_car_setup_observations: int = 0
    player_car_setup_observations_dropped: int = 0
    player_car_setup_observation_truncated_sessions: int = 0
    player_car_setup_observation_run_truncated: bool = False
    attempts: int = 0
    samples: int = 0
    car_observations: int = 0
    car_observation_chunks: int = 0
    car_slot_tenures: int = 0
    observed_car_lap_attempts: int = 0
    car_lap_inventory_rows_dropped: int = 0
    missing_car_telemetry_samples: int = 0
    lap_data_packets: int = 0
    missing_car_telemetry_frames: tuple[tuple[int, int], ...] = ()
    lap_data_errors: int = 0
    car_telemetry_errors: int = 0
    participant_errors: int = 0
    motion_packets: int = 0
    motion_decode_errors: int = 0
    player_motion_samples: int = 0
    missing_player_motion_samples: int = 0
    car_status_packets: int = 0
    car_status_decode_errors: int = 0
    player_car_status_samples: int = 0
    missing_player_car_status_samples: int = 0
    car_damage_packets_raw: int = 0
    car_damage_packets_admitted: int = 0
    car_damage_packets_decoded: int = 0
    car_damage_decode_errors: int = 0
    player_car_damage_samples: int = 0
    missing_player_car_damage_samples: int = 0
    import_late_packets_ignored: int = 0
    import_frame_overflow_packets_dropped: int = 0
    event_packets: int = 0
    event_decode_errors: int = 0
    lifecycle_events: int = 0
    lifecycle_events_dropped: int = 0
    session_history_packets_admitted: int = 0
    session_history_packets_decoded: int = 0
    session_history_non_player_packets: int = 0
    session_history_player_index_mismatches: int = 0
    session_history_decode_errors: int = 0
    session_history_packets_dropped: int = 0
    session_history_candidates: int = 0
    session_history_association_work: int = 0
    session_history_matched_attempts: int = 0
    session_history_ambiguous_attempts: int = 0
    session_history_conflicting_attempts: int = 0
    session_history_unavailable_attempts: int = 0
    session_history_truncated_attempts: int = 0
    session_history_truncated_sessions: int = 0
    player_participant_observations: int = 0
    player_participant_observations_dropped: int = 0
    player_participant_observation_truncated_sessions: int = 0
    player_participant_observation_run_truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _capture_hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _assert_capture_unchanged(path: Path, expected_hash: str, expected_size: int) -> None:
    actual_hash, actual_size = _capture_hash(path)
    if actual_hash != expected_hash or actual_size != expected_size:
        raise ValueError(
            "capture changed during import; retry after the recording has stopped"
        )


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _run_id(capture_hash: str, config_json: str) -> str:
    identity = f"{capture_hash}\n{PIPELINE_VERSION}\n{config_json}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _attempt_key(run_id: str, session_uid: int, car_index: int, ordinal: int) -> str:
    return f"{run_id}:{session_uid}:{car_index}:{ordinal}"


def _trace_path_name(session_uid: int, car_index: int, ordinal: int) -> str:
    return f"session-{session_uid}-car-{car_index}-attempt-{ordinal}.parquet"


class _TraceWriterManager:
    def __init__(self, database_path: Path, run_id: str, trace_namespace: str) -> None:
        self.database_path = database_path
        self.run_id = run_id
        self.trace_namespace = trace_namespace
        self.writers: dict[str, ParquetTraceWriter] = {}
        self.results: dict[str, tuple[str, int, str, dict[str, Any]]] = {}
        self.finished_attempt_ids: set[str] = set()

    def _identity(self, attempt_id: str) -> tuple[int, int, int]:
        parts = attempt_id.split(":")
        if len(parts) != 3:
            raise ValueError(f"invalid local lap attempt identity {attempt_id!r}")
        return int(parts[0]), int(parts[1]), int(parts[2])

    def _relative_path(self, session_uid: int, car_index: int, ordinal: int) -> Path:
        return (
            Path(self.trace_namespace)
            / self.run_id
            / _trace_path_name(session_uid, car_index, ordinal)
        )

    def write_sample(self, sample: CarSample) -> None:
        if sample.attempt_id is None:
            raise ValueError("player trace samples require a lap-attempt identity")
        session_uid, car_index, ordinal = self._identity(sample.attempt_id)
        if (session_uid, car_index) != (sample.session_uid, sample.car_index):
            raise ValueError("sample and lap attempt identities disagree")
        attempt_key = _attempt_key(self.run_id, session_uid, car_index, ordinal)
        writer = self.writers.get(sample.attempt_id)
        if writer is None:
            relative = self._relative_path(session_uid, car_index, ordinal)
            writer = ParquetTraceWriter(
                self.database_path.parent / relative,
                attempt_key,
            )
            self.writers[sample.attempt_id] = writer
        writer.write(sample.to_record())

    def finish_attempt(self, attempt: Any) -> None:
        if attempt.attempt_id in self.finished_attempt_ids:
            return
        attempt_key = _attempt_key(
            self.run_id, attempt.session_uid, attempt.car_index, attempt.attempt_number
        )
        writer = self.writers.pop(attempt.attempt_id, None)
        if writer is None:
            relative = self._relative_path(
                attempt.session_uid, attempt.car_index, attempt.attempt_number
            )
            writer = ParquetTraceWriter(self.database_path.parent / relative, attempt_key)
        rows, digest, quality = writer.close()
        relative = self._relative_path(
            attempt.session_uid, attempt.car_index, attempt.attempt_number
        )
        self.results[attempt_key] = (relative.as_posix(), rows, digest, quality)
        self.finished_attempt_ids.add(attempt.attempt_id)

    def consume(self, samples: tuple[CarSample, ...], attempts: tuple[Any, ...]) -> None:
        sample_ids = {sample.attempt_id for sample in samples}
        for attempt in attempts:
            if attempt.attempt_id not in sample_ids:
                self.finish_attempt(attempt)
        for sample in samples:
            self.write_sample(sample)
        for attempt in attempts:
            self.finish_attempt(attempt)

    def finish_all(self, attempts: tuple[Any, ...]) -> None:
        for attempt in attempts:
            self.finish_attempt(attempt)

    def abort(self) -> None:
        for writer in self.writers.values():
            writer.abort()
        self.writers.clear()


@dataclass(frozen=True, slots=True)
class _ObservationChunkResult:
    session_uid: int
    packet_format: int
    lifecycle_epoch: int
    chunk_ordinal: int
    relative_path: str
    row_count: int
    sha256: str
    quality: dict[str, Any]


class _CarLapInventoryWriter:
    def __init__(self) -> None:
        self.tenures: list[Any] = []
        self.attempts: list[Any] = []
        self.tenures_dropped = 0
        self.attempts_dropped = 0

    def consume(self, tenures: tuple[Any, ...], attempts: tuple[Any, ...]) -> None:
        for tenure in tenures:
            if len(self.tenures) < MAX_STORED_CAR_LAP_INVENTORY_ROWS:
                self.tenures.append(tenure)
            else:
                self.tenures_dropped += 1
        for attempt in attempts:
            if len(self.attempts) < MAX_STORED_CAR_LAP_INVENTORY_ROWS:
                self.attempts.append(attempt)
            else:
                self.attempts_dropped += 1

    def retain_referentially_complete_attempts(self) -> None:
        tenure_keys = {
            (item.session_uid, item.packet_format, item.lifecycle_epoch,
             item.car_index, item.tenure_ordinal)
            for item in self.tenures
        }
        retained = []
        for item in self.attempts:
            key = (item.session_uid, item.packet_format, item.lifecycle_epoch,
                   item.car_index, item.tenure_ordinal)
            if key in tenure_keys:
                retained.append(item)
            else:
                self.attempts_dropped += 1
        self.attempts = retained

    @property
    def dropped_count(self) -> int:
        return self.tenures_dropped + self.attempts_dropped

    def persist(self, connection, run_id: str) -> None:
        tenure_rows = [
            (
                f"{run_id}:{item.session_uid}",
                item.packet_format,
                item.lifecycle_epoch,
                item.car_index,
                item.tenure_ordinal,
                item.start_frame_ordinal,
                item.end_frame_ordinal_exclusive,
                item.participant_frame_identifier,
                item.participant_wire_fingerprint,
                item.participant_identity_fingerprint,
                item.close_reason,
            )
            for item in self.tenures
        ]
        for offset in range(0, len(tenure_rows), 256):
            connection.executemany(
                """INSERT INTO car_slot_tenures(session_key,packet_format,
                          lifecycle_epoch,car_index,tenure_ordinal,start_frame_ordinal,
                          end_frame_ordinal_exclusive,participant_frame_identifier,
                          participant_wire_fingerprint,participant_identity_fingerprint,
                          close_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                tenure_rows[offset : offset + 256],
            )
        attempt_rows = []
        for item in self.attempts:
            attempt = item.attempt
            attempt_key = (
                f"{run_id}:car-lap:{item.session_uid}:{item.packet_format}:"
                f"{item.lifecycle_epoch}:{item.car_index}:{item.tenure_ordinal}:"
                f"{attempt.attempt_number}"
            )
            attempt_rows.append(
                (
                    attempt_key,
                    f"{run_id}:{item.session_uid}",
                    item.packet_format,
                    item.lifecycle_epoch,
                    item.car_index,
                    item.tenure_ordinal,
                    attempt.attempt_number,
                    attempt.lap_number,
                    attempt.disposition.value,
                    attempt.lap_time_ms,
                    None if attempt.game_valid is None else int(attempt.game_valid),
                    int(attempt.start_observed),
                    int(attempt.pit_encountered),
                    attempt.sample_count,
                    attempt.start_frame_ordinal,
                    attempt.end_frame_ordinal,
                    attempt.completion_frame_ordinal,
                    _json(attempt.exclusion_reasons),
                    _json([segment.to_dict() for segment in attempt.context_segments]),
                    1,
                )
            )
        for offset in range(0, len(attempt_rows), 256):
            connection.executemany(
                """INSERT INTO observed_car_lap_attempts(attempt_key,session_key,
                          packet_format,lifecycle_epoch,car_index,tenure_ordinal,
                          attempt_number,lap_number,disposition,lap_time_ms,game_valid,
                          start_observed,pit_encountered,sample_count,start_frame_ordinal,
                          end_frame_ordinal,completion_frame_ordinal,exclusion_reasons_json,
                          context_segments_json,diagnostic_only)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                attempt_rows[offset : offset + 256],
            )


class _ObservationWriterManager:
    """Publish bounded all-car chunks independently of attempt-owned traces."""

    def __init__(self, database_path: Path, run_id: str, trace_namespace: str) -> None:
        self.database_path = database_path
        self.run_id = run_id
        self.trace_namespace = trace_namespace
        self.writers: dict[tuple[int, int, int], tuple[int, ParquetObservationWriter]] = {}
        self.next_chunk_ordinal: dict[tuple[int, int, int], int] = {}
        self.stream_keys: set[tuple[int, int, int]] = set()
        self.results: list[_ObservationChunkResult] = []

    def _relative_path(
        self, key: tuple[int, int, int], chunk_ordinal: int
    ) -> Path:
        session_uid, packet_format, lifecycle_epoch = key
        return (
            Path(self.trace_namespace)
            / self.run_id
            / "observations"
            / (
                f"session-{session_uid}-format-{packet_format}-epoch-"
                f"{lifecycle_epoch}-chunk-{chunk_ordinal:05d}.parquet"
            )
        )

    def _open(self, key: tuple[int, int, int]) -> ParquetObservationWriter:
        if key not in self.stream_keys:
            if len(self.stream_keys) >= MAX_OBSERVATION_CHUNKS_PER_IMPORT:
                raise ValueError("observation_archive_chunk_limit_exceeded")
            self.stream_keys.add(key)
        if key not in self.writers and len(self.writers) >= MAX_OPEN_OBSERVATION_WRITERS:
            oldest_key = next(iter(self.writers))
            self._close_key(oldest_key)
        ordinal = self.next_chunk_ordinal.get(key, 0)
        relative = self._relative_path(key, ordinal)
        writer = ParquetObservationWriter(self.database_path.parent / relative)
        self.writers[key] = (ordinal, writer)
        return writer

    def write_observation(self, observation: CarObservation) -> None:
        key = (observation.session_uid, observation.packet_format, observation.lifecycle_epoch)
        entry = self.writers.get(key)
        writer = entry[1] if entry is not None else self._open(key)
        writer.write(observation.to_record())
        if writer.row_count >= MAX_OBSERVATIONS_PER_CHUNK:
            self._close_key(key)

    def consume(self, observations: tuple[CarObservation, ...]) -> None:
        for observation in observations:
            self.write_observation(observation)

    def _close_key(self, key: tuple[int, int, int]) -> None:
        ordinal, writer = self.writers.pop(key)
        rows, digest, quality = writer.close()
        relative = self._relative_path(key, ordinal)
        if len(self.results) >= MAX_OBSERVATION_CHUNKS_PER_IMPORT:
            (self.database_path.parent / relative).unlink(missing_ok=True)
            raise ValueError("observation_archive_chunk_limit_exceeded")
        self.results.append(
            _ObservationChunkResult(
                session_uid=key[0],
                packet_format=key[1],
                lifecycle_epoch=key[2],
                chunk_ordinal=ordinal,
                relative_path=relative.as_posix(),
                row_count=rows,
                sha256=digest,
                quality=quality,
            )
        )
        self.next_chunk_ordinal[key] = ordinal + 1

    def finish_all(self) -> tuple[_ObservationChunkResult, ...]:
        for key in tuple(self.writers):
            self._close_key(key)
        return tuple(self.results)

    def abort(self) -> None:
        for _, writer in self.writers.values():
            try:
                writer.abort()
            except OSError:
                pass
        self.writers.clear()
        for result in self.results:
            try:
                (self.database_path.parent / result.relative_path).unlink(missing_ok=True)
            except OSError:
                pass
        self.results.clear()
        self.next_chunk_ordinal.clear()
        self.stream_keys.clear()


class _PlayerParticipantObservationWriter:
    def __init__(self, connection, run_id: str) -> None:
        self.connection = connection
        self.run_id = run_id
        self.buffer: list[PlayerParticipantObservation] = []
        self.stored_count = 0
        self.dropped_count = 0
        self.truncated_session_uids: set[int] = set()
        self.truncation_marker_overflowed = False

    def consume(
        self, observations: tuple[PlayerParticipantObservation, ...]
    ) -> None:
        for observation in observations:
            if self.stored_count < MAX_STORED_PLAYER_PARTICIPANT_OBSERVATIONS:
                self.buffer.append(observation)
                self.stored_count += 1
            else:
                self.dropped_count += 1
                if (
                    observation.session_uid not in self.truncated_session_uids
                    and len(self.truncated_session_uids)
                    < MAX_STORED_PLAYER_PARTICIPANT_TRUNCATION_FENCES
                ):
                    self.truncated_session_uids.add(observation.session_uid)
                    self.buffer.append(
                        replace(
                            observation,
                            status="truncated",
                            reason="import_observation_limit_exceeded",
                            active_car_count=None,
                            participant=None,
                            source_packet_count=0,
                        )
                    )
                elif observation.session_uid not in self.truncated_session_uids:
                    self.truncation_marker_overflowed = True
            if len(self.buffer) >= PLAYER_PARTICIPANT_OBSERVATION_WRITE_BATCH:
                self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        rows = [
            (
                self.run_id,
                str(item.session_uid),
                item.frame_ordinal,
                item.frame_identifier,
                item.overall_frame_identifier,
                item.packet_format,
                item.association_epoch,
                int(item.association_scope_assessable),
                item.player_car_index,
                item.session_time_s,
                item.status,
                item.reason,
                item.active_car_count,
                _json(asdict(item.participant)) if item.participant is not None else None,
                item.source_packet_count,
            )
            for item in self.buffer
        ]
        with self.connection:
            self.connection.executemany(
                """INSERT INTO player_participant_observations(
                           run_id,session_uid,frame_ordinal,frame_identifier,
                           overall_frame_identifier,packet_format,association_epoch,
                           association_scope_assessable,player_car_index,session_time_s,
                           status,reason,active_car_count,participant_json,
                           source_packet_count)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        self.buffer.clear()


class _PlayerCarSetupObservationWriter:
    def __init__(self, connection, run_id: str) -> None:
        self.connection = connection
        self.run_id = run_id
        self.buffer: list[PlayerCarSetupObservation] = []
        self.stored_count = 0
        self.dropped_count = 0
        self.truncated_session_uids: set[int] = set()
        self.truncation_marker_overflowed = False

    def consume(
        self, observations: tuple[PlayerCarSetupObservation, ...]
    ) -> None:
        for observation in observations:
            if self.stored_count < MAX_STORED_PLAYER_CAR_SETUP_OBSERVATIONS:
                self.buffer.append(observation)
                self.stored_count += 1
            else:
                self.dropped_count += 1
                if (
                    observation.session_uid not in self.truncated_session_uids
                    and len(self.truncated_session_uids)
                    < MAX_STORED_PLAYER_CAR_SETUP_TRUNCATION_FENCES
                ):
                    self.truncated_session_uids.add(observation.session_uid)
                    self.buffer.append(
                        replace(
                            observation,
                            status="truncated",
                            reason="import_observation_limit_exceeded",
                            setup=None,
                            next_front_wing_value=None,
                            source_packet_count=0,
                        )
                    )
                elif observation.session_uid not in self.truncated_session_uids:
                    self.truncation_marker_overflowed = True
            if len(self.buffer) >= PLAYER_CAR_SETUP_OBSERVATION_WRITE_BATCH:
                self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        rows = [
            (
                self.run_id,
                str(item.session_uid),
                item.frame_ordinal,
                item.frame_identifier,
                item.overall_frame_identifier,
                item.packet_format,
                item.association_epoch,
                int(item.association_scope_assessable),
                item.player_car_index,
                item.session_time_s,
                item.status,
                item.reason,
                _json(asdict(item.setup)) if item.setup is not None else None,
                item.next_front_wing_value,
                item.source_packet_count,
            )
            for item in self.buffer
        ]
        with self.connection:
            self.connection.executemany(
                """INSERT INTO player_car_setup_observations(
                           run_id,session_uid,frame_ordinal,frame_identifier,
                           overall_frame_identifier,packet_format,association_epoch,
                           association_scope_assessable,player_car_index,session_time_s,
                           status,reason,setup_json,next_front_wing_value,
                           source_packet_count)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        self.buffer.clear()


def _ensure_capture_and_run(
    db: Database,
    *,
    run_id: str,
    capture_hash: str,
    source_path: Path,
    byte_size: int,
    config_json: str,
    output_root: Path,
) -> bool:
    connection = db.connection
    row = connection.execute(
        "SELECT status FROM processing_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is not None and row["status"] == "complete":
        expected_count = connection.execute(
            "SELECT COUNT(*) FROM lap_attempts WHERE session_key IN "
            "(SELECT session_key FROM sessions WHERE run_id = ?)",
            (run_id,),
        ).fetchone()[0]
        traces = connection.execute(
            """SELECT t.relative_path, t.sha256, t.ready FROM telemetry_files t
                 JOIN lap_attempts l USING(attempt_key)
                 JOIN sessions s USING(session_key) WHERE s.run_id = ?""",
            (run_id,),
        ).fetchall()
        outputs_valid = len(traces) == expected_count and all(
            _valid_trace_file(output_root, trace["relative_path"], trace["sha256"])
            for trace in traces
        ) and all(trace["ready"] == 1 for trace in traces)
        observation_chunks = connection.execute(
            """SELECT c.relative_path, c.sha256, c.ready, c.row_count
                 FROM car_observation_chunks c JOIN sessions s USING(session_key)
                WHERE s.run_id = ?""",
            (run_id,),
        ).fetchall()
        metrics_row = connection.execute(
            "SELECT metrics_json FROM processing_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        metrics = json.loads(metrics_row["metrics_json"]) if metrics_row and metrics_row["metrics_json"] else {}
        expected_observations = int(
            metrics.get("capture_quality", {}).get("car_observation_count", 0)
        )
        expected_chunks = int(
            metrics.get("capture_quality", {}).get("car_observation_chunk_count", 0)
        )
        observations_valid = (
            len(observation_chunks) == expected_chunks
            and sum(int(chunk["row_count"]) for chunk in observation_chunks)
            == expected_observations
            and all(
                chunk["ready"] == 1
                and _valid_trace_file(output_root, chunk["relative_path"], chunk["sha256"])
                for chunk in observation_chunks
            )
        )
        outputs_valid = outputs_valid and observations_valid
        expected_player_observations = int(
            metrics.get("capture_quality", {}).get(
                "player_participant_observation_count", 0
            )
        )
        stored_player_observations = int(
            connection.execute(
                "SELECT COUNT(*) FROM player_participant_observations WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )
        outputs_valid = outputs_valid and (
            stored_player_observations == expected_player_observations
        )
        expected_player_setup_observations = int(
            metrics.get("capture_quality", {}).get(
                "player_car_setup_observation_count", 0
            )
        )
        stored_player_setup_observations = int(
            connection.execute(
                "SELECT COUNT(*) FROM player_car_setup_observations WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )
        outputs_valid = outputs_valid and (
            stored_player_setup_observations == expected_player_setup_observations
        )
        if outputs_valid:
            return True
    connection.execute(
        """INSERT INTO captures(capture_sha256, source_path, byte_size, complete,
                  completion_json, metadata_json)
             VALUES (?, ?, ?, 0, NULL, '{}')
             ON CONFLICT(capture_sha256) DO UPDATE SET
               source_path=excluded.source_path, byte_size=excluded.byte_size""",
        (capture_hash, str(source_path.resolve()), byte_size),
    )
    connection.execute(
        """INSERT INTO processing_runs(run_id, capture_sha256, pipeline_version,
                  config_json, status, started_at_utc, finished_at_utc, error)
             VALUES (?, ?, ?, ?, 'processing', CURRENT_TIMESTAMP, NULL, NULL)
             ON CONFLICT(run_id) DO UPDATE SET status='processing',
               started_at_utc=CURRENT_TIMESTAMP, finished_at_utc=NULL, error=NULL""",
        (run_id, capture_hash, PIPELINE_VERSION, config_json),
    )
    connection.execute(
        "DELETE FROM player_participant_observations WHERE run_id = ?", (run_id,)
    )
    connection.execute(
        "DELETE FROM player_car_setup_observations WHERE run_id = ?", (run_id,)
    )
    connection.execute("DELETE FROM sessions WHERE run_id = ?", (run_id,))
    return False


def _valid_trace_file(root: Path, relative_path: str, expected_hash: str) -> bool:
    path_value = Path(relative_path)
    if path_value.is_absolute() or ".." in path_value.parts:
        return False
    path = root / path_value
    if not path.is_file():
        return False
    actual_hash, _ = _capture_hash(path)
    return actual_hash == expected_hash


def import_capture(
    capture_path: str | Path,
    database_path: str | Path = DEFAULT_DATABASE,
    *,
    progress_callback: Callable[[str, int, int, int], None] | None = None,
) -> ImportSummary:
    capture_path = Path(capture_path)
    if not capture_path.is_file():
        raise FileNotFoundError(capture_path)
    capture_hash, byte_size = _capture_hash(capture_path)
    config_json = _json(IMPORT_CONFIG)
    run_id = _run_id(capture_hash, config_json)
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    trace_namespace = database_path.name + ".traces"
    trace_root = database_path.parent / trace_namespace
    run_trace_dir = trace_root / run_id
    lock_path = (
        database_path.parent
        / f".{database_path.name}.locks"
        / f"{run_id}.lock"
    )
    with ImportRunLock(lock_path), Database(database_path) as db:
        if _ensure_capture_and_run(
            db,
            run_id=run_id,
            capture_hash=capture_hash,
            source_path=capture_path,
            byte_size=byte_size,
            config_json=config_json,
            output_root=database_path.parent,
        ):
            _assert_capture_unchanged(capture_path, capture_hash, byte_size)
            row = db.connection.execute(
                "SELECT metrics_json FROM processing_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            metrics = json.loads(row["metrics_json"]) if row and row["metrics_json"] else {}
            previous_summary = metrics.get("summary")
            if previous_summary:
                previous_summary["already_imported"] = True
                previous_summary["missing_car_telemetry_frames"] = tuple(
                    tuple(frame)
                    for frame in previous_summary.get("missing_car_telemetry_frames", ())
                )
                return ImportSummary(**previous_summary)
            return ImportSummary(
                run_id=run_id,
                capture_sha256=capture_hash,
                status="complete",
                already_imported=True,
            )
        # Make the in-progress marker durable before parsing or publishing files. A
        # crash at any later point then leaves a run that the next import can resume.
        db.connection.commit()
        trace_manager: _TraceWriterManager | None = None
        observation_manager: _ObservationWriterManager | None = None
        try:
            trace_root.mkdir(parents=True, exist_ok=True)
            resolved_root = trace_root.resolve()
            resolved_run_dir = run_trace_dir.resolve()
            if not resolved_run_dir.is_relative_to(resolved_root):
                raise ValueError("processing run trace path escapes the trace directory")
            if run_trace_dir.exists():
                shutil.rmtree(run_trace_dir)
            run_trace_dir.mkdir(parents=True, exist_ok=True)
            trace_manager = _TraceWriterManager(database_path, run_id, trace_namespace)
            observation_manager = _ObservationWriterManager(
                database_path, run_id, trace_namespace
            )
            player_participant_observation_writer = _PlayerParticipantObservationWriter(
                db.connection, run_id
            )
            player_car_setup_observation_writer = _PlayerCarSetupObservationWriter(
                db.connection, run_id
            )
            car_lap_inventory_writer = _CarLapInventoryWriter()
            pipeline = TelemetryPipeline(**IMPORT_CONFIG)
            session_history = SessionHistoryAccumulator()
            sessions: dict[int, int] = {}
            latest_contexts: dict[int, dict[str, object]] = {}
            context_updates: dict[tuple[int, int], dict[str, object]] = {}
            context_invalidations: dict[tuple[int, int], str] = {}
            participant_updates: dict[tuple[int, int, int], tuple[int, dict[str, object]]] = {}
            attempts: list[Any] = []
            lifecycle_events: list[LifecycleEvent] = []
            truncated_lifecycle_sessions: set[int] = set()
            importer_lifecycle_events_dropped = 0
            malformed_packets = 0
            packet_count = 0
            lap_data_error_count = 0
            car_telemetry_error_count = 0
            participant_error_count = 0
            motion_error_count = 0
            car_status_error_count = 0
            car_damage_error_count = 0
            raw_car_damage_packet_count = 0
            raw_car_setup_packet_count = 0
            car_setup_decode_error_count = 0
            session_history_decode_error_count = 0
            capture_metadata: dict[str, object]
            capture_completion: dict[str, object] | None = None
            capture_complete = False

            with CaptureReader(capture_path) as capture:
                capture_metadata = capture.metadata
                if progress_callback is not None:
                    progress_callback("reading_packets", 0, capture.bytes_read, byte_size)
                for raw in capture:
                    packet_count += 1
                    if progress_callback is not None and packet_count % 512 == 0:
                        progress_callback(
                            "reading_packets",
                            packet_count,
                            capture.bytes_read,
                            byte_size,
                        )
                    try:
                        result = pipeline.process(raw)
                    except ProtocolError:
                        malformed_packets += 1
                        continue
                    player_participant_observation_writer.consume(
                        result.player_participant_observations
                    )
                    player_car_setup_observation_writer.consume(
                        result.player_car_setup_observations
                    )
                    car_lap_inventory_writer.consume(
                        result.car_slot_tenures,
                        result.observed_car_lap_attempts,
                    )
                    if result.packet.packet_kind is PacketId.CAR_DAMAGE:
                        raw_car_damage_packet_count += 1
                    if result.packet.packet_kind is PacketId.CAR_SETUPS:
                        raw_car_setup_packet_count += 1
                    attempts.extend(result.lap_attempts)
                    trace_manager.consume(result.car_samples, result.lap_attempts)
                    observation_manager.consume(result.car_observations)
                    for attempt in result.lap_attempts:
                        session_history.register_attempt(attempt)
                    for observation in result.session_history:
                        session_history.observe(observation)
                    session_history_decode_error_count += len(
                        result.session_history_decode_errors
                    )
                    car_setup_decode_error_count += len(
                        result.car_setup_decode_errors
                    )
                    for lifecycle_event in result.lifecycle_events:
                        if len(lifecycle_events) < MAX_STORED_LIFECYCLE_EVENTS:
                            lifecycle_events.append(lifecycle_event)
                        else:
                            truncated_lifecycle_sessions.add(lifecycle_event.session_uid)
                            importer_lifecycle_events_dropped += 1
                    lap_data_error_count += len(result.lap_data_errors)
                    car_telemetry_error_count += len(result.car_telemetry_errors)
                    participant_error_count += int(result.participants_error is not None)
                    motion_error_count += len(pipeline.motion_decode_errors)
                    car_status_error_count += len(pipeline.car_status_decode_errors)
                    car_damage_error_count += len(pipeline.car_damage_decode_errors)
                    pipeline.laps.drain_attempts()
                    pipeline.lap_data_decode_errors.clear()
                    pipeline.car_telemetry_decode_errors.clear()
                    pipeline.participants_decode_errors.clear()
                    pipeline.motion_decode_errors.clear()
                    pipeline.car_status_decode_errors.clear()
                    pipeline.car_damage_decode_errors.clear()
                    uid = result.packet.header.session_uid
                    if (
                        uid != 0
                        and uid == pipeline.sessions.current_session_uid
                        and not pipeline.sessions.is_retired(uid)
                    ):
                        active_format = pipeline.sessions.current_packet_format
                        sessions[uid] = int(active_format or result.packet.packet_format)
                    for event in result.session_events:
                        if event.kind == "session_context_invalidated":
                            latest_contexts.pop(event.session_uid, None)
                            context_invalidations[
                                (event.session_uid, result.packet.header.overall_frame_identifier)
                            ] = "packet_format_changed"
                    for change in result.context_history_changes:
                        key = (change.session_uid, change.frame_identifier)
                        if change.removed:
                            context_updates.pop(key, None)
                            context_invalidations.pop(key, None)
                        elif change.context is None:
                            context_updates.pop(key, None)
                            context_invalidations.setdefault(
                                key, "session_context_unavailable"
                            )
                        else:
                            context_updates[key] = change.context.to_dict()
                            context_invalidations.pop(key, None)
                    if (
                        uid != 0
                        and uid == pipeline.sessions.current_session_uid
                        and not pipeline.sessions.is_retired(uid)
                    ):
                        current = pipeline.sessions.current_context
                        if current is None:
                            latest_contexts.pop(uid, None)
                        else:
                            latest_contexts[uid] = current.to_dict()
                    if (
                        uid != 0
                        and uid == pipeline.sessions.current_session_uid
                        and not pipeline.sessions.is_retired(uid)
                        and result.packet.packet_format
                        is pipeline.sessions.current_packet_format
                        and result.participants is not None
                    ):
                        for car_index, participant in enumerate(result.participants.cars):
                            participant_updates[
                                (uid, car_index, result.packet.header.overall_frame_identifier)
                            ] = (
                                result.participants.active_car_count,
                                asdict(participant),
                            )
                flushed = pipeline.finish_with_outputs()
                player_participant_observation_writer.consume(
                    flushed.player_participant_observations
                )
                player_participant_observation_writer.flush()
                player_car_setup_observation_writer.consume(
                    flushed.player_car_setup_observations
                )
                car_lap_inventory_writer.consume(
                    flushed.car_slot_tenures,
                    flushed.observed_car_lap_attempts,
                )
                player_car_setup_observation_writer.flush()
                attempts.extend(flushed.lap_attempts)
                trace_manager.consume(flushed.car_samples, flushed.lap_attempts)
                observation_manager.consume(flushed.car_observations)
                for attempt in flushed.lap_attempts:
                    session_history.register_attempt(attempt)
                for observation in flushed.session_history:
                    session_history.observe(observation)
                session_history_decode_error_count += len(
                    flushed.session_history_decode_errors
                )
                car_setup_decode_error_count += len(flushed.car_setup_decode_errors)
                for lifecycle_event in flushed.lifecycle_events:
                    if len(lifecycle_events) < MAX_STORED_LIFECYCLE_EVENTS:
                        lifecycle_events.append(lifecycle_event)
                    else:
                        truncated_lifecycle_sessions.add(lifecycle_event.session_uid)
                        importer_lifecycle_events_dropped += 1
                truncated_lifecycle_sessions.update(
                    pipeline.lifecycle_events_truncated_session_uids
                )
                pipeline.laps.drain_attempts()
                lap_data_error_count += len(flushed.lap_data_errors)
                car_telemetry_error_count += len(flushed.car_telemetry_errors)
                motion_error_count += len(pipeline.motion_decode_errors)
                pipeline.motion_decode_errors.clear()
                car_status_error_count += len(pipeline.car_status_decode_errors)
                pipeline.car_status_decode_errors.clear()
                car_damage_error_count += len(pipeline.car_damage_decode_errors)
                pipeline.car_damage_decode_errors.clear()
                trace_manager.finish_all(tuple(attempts))
                observation_chunks = observation_manager.finish_all()
                capture_complete = capture.complete
                capture_completion = capture.completion
                if progress_callback is not None:
                    progress_callback(
                        "writing_traces", packet_count, byte_size, byte_size
                    )

            # A recorder can append a datagram or completion footer while replay
            # runs. Do not associate that changing byte stream with the hash taken
            # before replay.
            _assert_capture_unchanged(capture_path, capture_hash, byte_size)

            (
                attempts,
                lifecycle_links,
                lifecycle_reconciliation_truncated_sessions,
                lifecycle_reconciliation_work,
            ) = reconcile_attempt_lifecycle(
                tuple(attempts),
                tuple(lifecycle_events),
                truncated_session_uids=frozenset(truncated_lifecycle_sessions),
            )
            truncated_lifecycle_sessions.update(
                lifecycle_reconciliation_truncated_sessions
            )
            timing_truncated_sessions = set(
                pipeline.session_history_truncated_session_uids
            )
            timing_truncated_sessions.update(
                pipeline.lifecycle_events_truncated_session_uids
            )
            timing_truncated_sessions.update(truncated_lifecycle_sessions)
            timing_evidence = session_history.reconcile(
                tuple(attempts),
                truncated_session_uids=frozenset(timing_truncated_sessions),
            )
            timing_status_counts: dict[str, int] = {}
            for evidence in timing_evidence.values():
                timing_status_counts[evidence.status] = (
                    timing_status_counts.get(evidence.status, 0) + 1
                )
            timing_truncated_sessions.update(session_history.truncated_session_uids)
            attempt_keys = {
                attempt.attempt_id: _attempt_key(
                    run_id, attempt.session_uid, attempt.car_index, attempt.attempt_number
                )
                for attempt in attempts
            }
            attempts_by_id = {attempt.attempt_id: attempt for attempt in attempts}
            trace_results = trace_manager.results
            sample_count = sum(result[1] for result in trace_results.values())
            missing_samples = sum(
                result[3]["missing_car_telemetry_count"]
                for result in trace_results.values()
            )
            car_observation_count = sum(chunk.row_count for chunk in observation_chunks)
            car_lap_inventory_writer.retain_referentially_complete_attempts()
            car_lap_coverage_counts = [
                {
                    "session_uid": str(session_uid),
                    "car_index": car_index,
                    "reason": reason,
                    "count": count,
                }
                for (session_uid, car_index, reason), count in sorted(
                    pipeline.car_lap_inventory.coverage_counts.items()
                )
            ]
            car_lap_inventory_status = (
                "truncated"
                if car_lap_inventory_writer.dropped_count
                or pipeline.car_lap_inventory.overflowed
                else "assessed"
            )

            missing_capture_frames = tuple(pipeline.missing_car_telemetry_frame_examples)
            capture_quality = {
                "lap_data_packets_decoded": pipeline.lap_data_packets_decoded,
                "car_telemetry_packets_decoded": pipeline.car_telemetry_packets_decoded,
                "car_observation_count": car_observation_count,
                "car_observation_chunk_count": len(observation_chunks),
                "car_lap_inventory": {
                    "version": 1,
                    "status": car_lap_inventory_status,
                    "tenure_count": len(car_lap_inventory_writer.tenures),
                    "attempt_count": len(car_lap_inventory_writer.attempts),
                    "tenures_dropped": car_lap_inventory_writer.tenures_dropped,
                    "attempts_dropped": car_lap_inventory_writer.attempts_dropped,
                    "coverage_count_overflowed": pipeline.car_lap_inventory.overflowed,
                    "unassociated_lap_observation_counts": car_lap_coverage_counts,
                },
                "car_observation_conflict_count": pipeline.car_observation_conflict_frames,
                "car_observation_conflict_examples": [
                    list(frame) for frame in pipeline.car_observation_conflict_examples
                ],
                "missing_car_telemetry_frame_count": pipeline.missing_car_telemetry_frame_count,
                "missing_car_telemetry_frames": [list(frame) for frame in missing_capture_frames],
                "canonical_lap_sample_count": sample_count,
                "missing_car_telemetry_lap_sample_count": missing_samples,
                "motion_packets_decoded": pipeline.motion_packets_decoded,
                "motion_decode_errors": motion_error_count,
                "player_motion_sample_count": pipeline.player_motion_samples,
                "missing_player_motion_sample_count": pipeline.missing_player_motion_samples,
                "car_status_packets_decoded": pipeline.car_status_packets_decoded,
                "car_status_decode_errors": car_status_error_count,
                "player_car_status_sample_count": pipeline.player_car_status_samples,
                "missing_player_car_status_sample_count": pipeline.missing_player_car_status_samples,
                "car_damage_packets_admitted": pipeline.car_damage_packets_admitted,
                "car_damage_packets_raw": raw_car_damage_packet_count,
                "car_damage_packets_decoded": pipeline.car_damage_packets_decoded,
                "car_damage_decode_errors": car_damage_error_count,
                "player_car_damage_sample_count": pipeline.player_car_damage_samples,
                "missing_player_car_damage_sample_count": pipeline.missing_player_car_damage_samples,
                "import_late_packets_ignored": pipeline.frames.late_packets_ignored,
                "import_frame_overflow_packets_dropped": pipeline.frames.overflow_packets_dropped,
                "lifecycle_analysis_version": "rewind-lifecycle-v1",
                "event_packets_decoded": pipeline.event_packets_decoded,
                "event_decode_errors": pipeline.event_decode_error_count,
                "event_code_counts": dict(sorted(pipeline.event_code_counts.items())),
                "lifecycle_event_count": len(lifecycle_events),
                "lifecycle_events_dropped": pipeline.lifecycle_events_dropped
                + importer_lifecycle_events_dropped,
                "lifecycle_evidence_truncated_session_count": len(
                    truncated_lifecycle_sessions
                ),
                "lifecycle_reconciliation_work": lifecycle_reconciliation_work,
                "lifecycle_reconciliation_truncated_session_count": len(
                    lifecycle_reconciliation_truncated_sessions
                ),
                "session_history_analysis_version": "session-history-v1",
                "session_history_packets_admitted": pipeline.session_history_packets_admitted,
                "session_history_packets_decoded": pipeline.session_history_packets_decoded,
                "session_history_non_player_packets": pipeline.session_history_non_player_packets,
                "session_history_player_index_mismatches": pipeline.session_history_player_index_mismatches,
                "session_history_decode_errors": session_history_decode_error_count,
                "session_history_packets_dropped": pipeline.session_history_packets_dropped,
                "session_history_candidate_count": session_history.candidate_count,
                "session_history_association_work": session_history.work,
                "session_history_timing_status_counts": dict(sorted(timing_status_counts.items())),
                "session_history_truncated_session_count": len(timing_truncated_sessions),
                "player_participant_observation_count": (
                    player_participant_observation_writer.stored_count
                    + len(player_participant_observation_writer.truncated_session_uids)
                ),
                "player_participant_observations_dropped": (
                    player_participant_observation_writer.dropped_count
                    + pipeline.player_participant_observations_dropped
                ),
                "player_participant_observation_truncated_session_count": len(
                    player_participant_observation_writer.truncated_session_uids
                    | pipeline.player_participant_observation_truncated_session_uids
                ),
                "player_participant_observation_run_truncated": bool(
                    player_participant_observation_writer.truncation_marker_overflowed
                    or pipeline.player_participant_observation_truncation_marker_overflowed
                ),
                "car_setup_packets_raw": raw_car_setup_packet_count,
                "car_setup_packets_decoded": pipeline.car_setups_packets_decoded,
                "car_setup_decode_errors": car_setup_decode_error_count,
                "player_car_setup_observation_count": (
                    player_car_setup_observation_writer.stored_count
                    + len(player_car_setup_observation_writer.truncated_session_uids)
                ),
                "player_car_setup_observations_dropped": (
                    player_car_setup_observation_writer.dropped_count
                    + pipeline.player_car_setup_observations_dropped
                ),
                "player_car_setup_observation_truncated_session_count": len(
                    player_car_setup_observation_writer.truncated_session_uids
                    | pipeline.player_car_setup_observation_truncated_session_uids
                ),
                "player_car_setup_observation_run_truncated": bool(
                    player_car_setup_observation_writer.truncation_marker_overflowed
                    or pipeline.player_car_setup_observation_truncation_marker_overflowed
                ),
            }

            import_summary = ImportSummary(
                run_id=run_id,
                capture_sha256=capture_hash,
                status="complete",
                already_imported=False,
                packet_count=packet_count,
                malformed_packet_count=malformed_packets,
                session_count=len(sessions),
                participant_packets=pipeline.participants_packets_decoded,
                attempts=len(attempts),
                samples=sample_count,
                car_observations=car_observation_count,
                car_observation_chunks=len(observation_chunks),
                car_slot_tenures=len(car_lap_inventory_writer.tenures),
                observed_car_lap_attempts=len(car_lap_inventory_writer.attempts),
                car_lap_inventory_rows_dropped=car_lap_inventory_writer.dropped_count,
                missing_car_telemetry_samples=missing_samples,
                lap_data_packets=pipeline.lap_data_packets_decoded,
                missing_car_telemetry_frames=missing_capture_frames,
                lap_data_errors=lap_data_error_count,
                car_telemetry_errors=car_telemetry_error_count,
                participant_errors=participant_error_count,
                motion_packets=pipeline.motion_packets_decoded,
                motion_decode_errors=motion_error_count,
                player_motion_samples=pipeline.player_motion_samples,
                missing_player_motion_samples=pipeline.missing_player_motion_samples,
                car_status_packets=pipeline.car_status_packets_decoded,
                car_status_decode_errors=car_status_error_count,
                player_car_status_samples=pipeline.player_car_status_samples,
                missing_player_car_status_samples=pipeline.missing_player_car_status_samples,
                car_damage_packets_raw=raw_car_damage_packet_count,
                car_damage_packets_admitted=pipeline.car_damage_packets_admitted,
                car_damage_packets_decoded=pipeline.car_damage_packets_decoded,
                car_damage_decode_errors=car_damage_error_count,
                player_car_damage_samples=pipeline.player_car_damage_samples,
                missing_player_car_damage_samples=pipeline.missing_player_car_damage_samples,
                import_late_packets_ignored=pipeline.frames.late_packets_ignored,
                import_frame_overflow_packets_dropped=pipeline.frames.overflow_packets_dropped,
                event_packets=pipeline.event_packets_decoded,
                event_decode_errors=pipeline.event_decode_error_count,
                lifecycle_events=len(lifecycle_events),
                lifecycle_events_dropped=pipeline.lifecycle_events_dropped
                + importer_lifecycle_events_dropped,
                session_history_packets_admitted=pipeline.session_history_packets_admitted,
                session_history_packets_decoded=pipeline.session_history_packets_decoded,
                session_history_non_player_packets=pipeline.session_history_non_player_packets,
                session_history_player_index_mismatches=pipeline.session_history_player_index_mismatches,
                session_history_decode_errors=session_history_decode_error_count,
                session_history_packets_dropped=pipeline.session_history_packets_dropped,
                session_history_candidates=session_history.candidate_count,
                session_history_association_work=session_history.work,
                session_history_matched_attempts=timing_status_counts.get("matched", 0),
                session_history_ambiguous_attempts=timing_status_counts.get("ambiguous", 0),
                session_history_conflicting_attempts=timing_status_counts.get("conflicting", 0),
                session_history_unavailable_attempts=timing_status_counts.get("unavailable", 0),
                session_history_truncated_attempts=timing_status_counts.get("truncated", 0),
                session_history_truncated_sessions=len(timing_truncated_sessions),
                player_participant_observations=(
                    player_participant_observation_writer.stored_count
                    + len(player_participant_observation_writer.truncated_session_uids)
                ),
                player_participant_observations_dropped=(
                    player_participant_observation_writer.dropped_count
                    + pipeline.player_participant_observations_dropped
                ),
                player_participant_observation_truncated_sessions=len(
                    player_participant_observation_writer.truncated_session_uids
                    | pipeline.player_participant_observation_truncated_session_uids
                ),
                player_participant_observation_run_truncated=bool(
                    player_participant_observation_writer.truncation_marker_overflowed
                    or pipeline.player_participant_observation_truncation_marker_overflowed
                ),
                car_setup_packets_raw=raw_car_setup_packet_count,
                car_setup_packets_decoded=pipeline.car_setups_packets_decoded,
                car_setup_decode_errors=car_setup_decode_error_count,
                player_car_setup_observations=(
                    player_car_setup_observation_writer.stored_count
                    + len(player_car_setup_observation_writer.truncated_session_uids)
                ),
                player_car_setup_observations_dropped=(
                    player_car_setup_observation_writer.dropped_count
                    + pipeline.player_car_setup_observations_dropped
                ),
                player_car_setup_observation_truncated_sessions=len(
                    player_car_setup_observation_writer.truncated_session_uids
                    | pipeline.player_car_setup_observation_truncated_session_uids
                ),
                player_car_setup_observation_run_truncated=bool(
                    player_car_setup_observation_writer.truncation_marker_overflowed
                    or pipeline.player_car_setup_observation_truncation_marker_overflowed
                ),
            )
            metrics_json = _json(
                {"capture_quality": capture_quality, "summary": import_summary.to_dict()}
            )

            connection = db.connection
            with connection:
                connection.execute(
                    "UPDATE captures SET complete=?, completion_json=?, metadata_json=? WHERE capture_sha256=?",
                    (
                        int(capture_complete),
                        _json(capture_completion) if capture_completion is not None else None,
                        _json(capture_metadata),
                        capture_hash,
                    ),
                )
                for uid, packet_format in sessions.items():
                    session_key = f"{run_id}:{uid}"
                    connection.execute(
                        """INSERT INTO sessions(session_key, run_id, session_uid,
                                  packet_format, context_json) VALUES (?, ?, ?, ?, ?)""",
                        (
                            session_key,
                            run_id,
                            str(uid),
                            packet_format,
                            _json(latest_contexts[uid]) if uid in latest_contexts else None,
                        ),
                    )
                car_lap_inventory_writer.persist(connection, run_id)
                for chunk in observation_chunks:
                    session_key = f"{run_id}:{chunk.session_uid}"
                    chunk_key = (
                        f"{run_id}:observation:{chunk.session_uid}:"
                        f"{chunk.packet_format}:{chunk.lifecycle_epoch}:"
                        f"{chunk.chunk_ordinal}"
                    )
                    connection.execute(
                        """INSERT INTO car_observation_chunks(chunk_key,session_key,
                                  packet_format,lifecycle_epoch,chunk_ordinal,relative_path,
                                  schema_version,row_count,sha256,quality_json,ready)
                             VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                        (
                            chunk_key,
                            session_key,
                            chunk.packet_format,
                            chunk.lifecycle_epoch,
                            chunk.chunk_ordinal,
                            chunk.relative_path,
                            OBSERVATION_SCHEMA_VERSION,
                            chunk.row_count,
                            chunk.sha256,
                            _json(chunk.quality),
                        ),
                    )
                    for car_index, raw_metrics in chunk.quality["slots"].items():
                        metrics = {
                            name: int(value)
                            for name, value in raw_metrics.items()
                            if value is not None
                        }
                        connection.execute(
                            """INSERT INTO car_observation_slots(session_key,
                                      packet_format,lifecycle_epoch,car_index,
                                      observation_count,car_telemetry_count,motion_count,
                                      nonzero_speed_count,header_player_count,
                                      first_frame_ordinal,last_frame_ordinal)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?)
                                 ON CONFLICT(session_key,packet_format,lifecycle_epoch,car_index)
                                 DO UPDATE SET
                                   observation_count=observation_count+excluded.observation_count,
                                   car_telemetry_count=car_telemetry_count+excluded.car_telemetry_count,
                                   motion_count=motion_count+excluded.motion_count,
                                   nonzero_speed_count=nonzero_speed_count+excluded.nonzero_speed_count,
                                   header_player_count=header_player_count+excluded.header_player_count,
                                   first_frame_ordinal=min(first_frame_ordinal,excluded.first_frame_ordinal),
                                   last_frame_ordinal=max(last_frame_ordinal,excluded.last_frame_ordinal)""",
                            (
                                session_key,
                                chunk.packet_format,
                                chunk.lifecycle_epoch,
                                int(car_index),
                                metrics["observation_count"],
                                metrics["car_telemetry_count"],
                                metrics["motion_count"],
                                metrics["nonzero_speed_count"],
                                metrics["header_player_count"],
                                metrics["first_frame_ordinal"],
                                metrics["last_frame_ordinal"],
                            ),
                        )
                for (uid, effective_frame), context in sorted(context_updates.items()):
                    connection.execute(
                        "INSERT INTO session_contexts(session_key,effective_frame,context_json) VALUES (?,?,?)",
                        (f"{run_id}:{uid}", effective_frame, _json(context)),
                    )
                for (uid, effective_frame), reason in sorted(context_invalidations.items()):
                    connection.execute(
                        """INSERT INTO session_context_invalidations(
                                  session_key,effective_frame,reason) VALUES (?,?,?)""",
                        (f"{run_id}:{uid}", effective_frame, reason),
                    )
                for (uid, car_index, effective_frame), (active_count, participant) in sorted(
                    participant_updates.items()
                ):
                    connection.execute(
                        """INSERT INTO driver_snapshots(session_key,car_index,effective_frame,
                                  active_car_count,participant_json) VALUES (?,?,?,?,?)""",
                        (
                            f"{run_id}:{uid}",
                            car_index,
                            effective_frame,
                            active_count,
                            _json(participant),
                        ),
                    )
                lifecycle_event_keys: dict[tuple[int, int], str] = {}
                for event in sorted(
                    lifecycle_events,
                    key=lambda item: (item.session_uid, item.event_ordinal),
                ):
                    if event.session_uid not in sessions:
                        continue
                    event_key = f"{run_id}:{event.session_uid}:event:{event.event_ordinal}"
                    lifecycle_event_keys[(event.session_uid, event.event_ordinal)] = event_key
                    connection.execute(
                        """INSERT INTO lifecycle_events(event_key,session_key,event_ordinal,
                                  frame_ordinal,current_frame_identifier,
                                  current_overall_frame_identifier,packet_format,packet_version,
                                  event_code,event_kind,session_time_s,
                                  target_game_frame_identifier,target_session_time_s,
                                  prior_session_time_s,cause,evidence_status,details_hex,
                                  details_length_bytes,details_truncated,duplicate_count)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            event_key,
                            f"{run_id}:{event.session_uid}",
                            event.event_ordinal,
                            event.frame_ordinal,
                            event.current_frame_identifier,
                            event.current_overall_frame_identifier,
                            event.packet_format,
                            event.packet_version,
                            event.event_code,
                            event.event_kind,
                            event.session_time_s,
                            event.target_game_frame_identifier,
                            event.target_session_time_s,
                            event.prior_session_time_s,
                            event.cause,
                            event.evidence_status,
                            event.details_hex,
                            event.details_length_bytes,
                            int(event.details_truncated),
                            event.duplicate_count,
                        ),
                    )
                for attempt in attempts:
                    key = attempt_keys[attempt.attempt_id]
                    attempt_key = key
                    connection.execute(
                        """INSERT INTO lap_attempts(attempt_key,session_key,car_index,
                                  attempt_number,lap_number,disposition,start_frame_identifier,
                                  end_frame_identifier,start_session_time_s,end_session_time_s,
                                  lap_time_ms,game_valid,start_observed,pit_encountered,sample_count,
                                  reference_eligible,exclusion_reasons_json,attempt_json,
                                  start_frame_ordinal,end_frame_ordinal,superseded,lifecycle_assessed)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            attempt_key,
                            f"{run_id}:{attempt.session_uid}",
                            attempt.car_index,
                            attempt.attempt_number,
                            attempt.lap_number,
                            attempt.disposition.value,
                            attempt.start_frame_identifier,
                            attempt.end_frame_identifier,
                            attempt.start_session_time_s,
                            attempt.end_session_time_s,
                            attempt.lap_time_ms,
                            None if attempt.game_valid is None else int(attempt.game_valid),
                            int(attempt.start_observed),
                            int(attempt.pit_encountered),
                            trace_results[attempt_key][1],
                            int(attempt.reference_eligible),
                            _json(attempt.exclusion_reasons),
                            _json(attempt.to_dict()),
                            attempt.start_frame_ordinal,
                            attempt.end_frame_ordinal,
                            None if attempt.superseded is None else int(attempt.superseded),
                            int(attempt.lifecycle_assessed),
                        ),
                    )
                    for ordinal, segment in enumerate(attempt.context_segments):
                        connection.execute(
                            """INSERT INTO lap_context_segments(attempt_key,ordinal,
                                      from_frame_identifier,context_json) VALUES (?,?,?,?)""",
                            (
                                attempt_key,
                                ordinal,
                                segment.from_frame_identifier,
                                _json(segment.context.to_dict()) if segment.context else None,
                            ),
                        )
                    relative, rows, digest, quality = trace_results[attempt_key]
                    connection.execute(
                        """INSERT INTO telemetry_files(attempt_key,relative_path,schema_version,
                                  row_count,sha256,quality_json,ready) VALUES (?,?,?,?,?,?,1)""",
                        (
                            attempt_key,
                            relative,
                            TRACE_SCHEMA_VERSION,
                            rows,
                            digest,
                            _json(quality),
                        ),
                    )
                    timing = timing_evidence.get(
                        attempt.attempt_id,
                        AttemptTimingEvidence(
                            "unavailable", ("timing_evidence_not_reconciled",)
                        ),
                    )
                    timing_payload = timing.to_dict()
                    timing_payload["provenance"] = {
                        "attempt_key": attempt_key,
                        "run_id": run_id,
                        "capture_sha256": capture_hash,
                        "completion_frame_ordinal": attempt.completion_frame_ordinal,
                    }
                    connection.execute(
                        """INSERT INTO attempt_timing_evidence(
                                  attempt_key,status,evidence_json) VALUES (?,?,?)""",
                        (attempt_key, timing.status, _json(timing_payload)),
                    )
                for attempt_id, event_ordinal, relation in lifecycle_links:
                    attempt = attempts_by_id.get(attempt_id)
                    if attempt is None:
                        continue
                    event_key = lifecycle_event_keys.get(
                        (attempt.session_uid, event_ordinal)
                    )
                    if event_key is None:
                        continue
                    connection.execute(
                        """INSERT OR IGNORE INTO attempt_lifecycle_links(
                                  attempt_key,event_key,relation) VALUES (?,?,?)""",
                        (attempt_keys[attempt_id], event_key, relation),
                    )
                connection.execute(
                    """UPDATE processing_runs SET status='complete',
                              finished_at_utc=CURRENT_TIMESTAMP, error=NULL, metrics_json=?
                         WHERE run_id=?""",
                    (metrics_json, run_id),
                )
            return import_summary
        except Exception as exc:
            try:
                db.connection.execute(
                    """UPDATE processing_runs SET status='failed',
                              finished_at_utc=CURRENT_TIMESTAMP, error=? WHERE run_id=?""",
                    (str(exc), run_id),
                )
                db.connection.commit()
            except Exception:
                # Preserve the original failure; cleanup must not mask it.
                pass
            if trace_manager is not None:
                try:
                    trace_manager.abort()
                except OSError:
                    pass
            if observation_manager is not None:
                try:
                    observation_manager.abort()
                except OSError:
                    pass
            raise


def list_sessions(database_path: str | Path = DEFAULT_DATABASE) -> list[dict[str, object]]:
    with Database(database_path, read_only=True) as db:
        rows = db.connection.execute(
            """SELECT s.session_key, s.run_id, s.session_uid, s.packet_format,
                      s.context_json, r.status AS run_status, r.metrics_json,
                      r.capture_sha256, r.pipeline_version, r.started_at_utc,
                      r.finished_at_utc,
                      COUNT(DISTINCT l.attempt_key) AS lap_attempts
                 FROM sessions s JOIN processing_runs r USING(run_id)
                 LEFT JOIN lap_attempts l USING(session_key)
                 GROUP BY s.session_key
                 ORDER BY COALESCE(r.finished_at_utc, r.started_at_utc), s.session_uid"""
        ).fetchall()
        return [
            {
                "session_key": row["session_key"],
                "run_id": row["run_id"],
                "session_uid": row["session_uid"],
                "packet_format": row["packet_format"],
                "context": json.loads(row["context_json"]) if row["context_json"] else None,
                "run_status": row["run_status"],
                "capture_quality": (
                    json.loads(row["metrics_json"]).get("capture_quality")
                    if row["metrics_json"]
                    else None
                ),
                "capture_sha256": row["capture_sha256"],
                "pipeline_version": row["pipeline_version"],
                "started_at_utc": row["started_at_utc"],
                "finished_at_utc": row["finished_at_utc"],
                "lap_attempts": row["lap_attempts"],
            }
            for row in rows
        ]


def list_laps(
    database_path: str | Path = DEFAULT_DATABASE,
    *,
    run_id: str | None = None,
    session_uid: str | None = None,
) -> list[dict[str, object]]:
    clauses = ["t.ready = 1"]
    parameters: list[Any] = []
    if run_id is not None:
        clauses.append("s.run_id = ?")
        parameters.append(run_id)
    if session_uid is not None:
        clauses.append("s.session_uid = ?")
        parameters.append(session_uid)
    with Database(database_path, read_only=True) as db:
        rows = db.connection.execute(
            f"""SELECT l.*, s.session_uid, s.run_id, t.relative_path,
                       t.row_count AS trace_row_count, t.quality_json, t.sha256 AS trace_sha256,
                       t.schema_version AS trace_schema_version,
                       e.evidence_json AS timing_evidence_json,
                       (SELECT c.context_json FROM lap_context_segments c
                          WHERE c.attempt_key = l.attempt_key ORDER BY c.ordinal LIMIT 1)
                          AS initial_context_json
                  FROM lap_attempts l JOIN sessions s USING(session_key)
                  JOIN telemetry_files t USING(attempt_key)
                  LEFT JOIN attempt_timing_evidence e USING(attempt_key)
                  JOIN processing_runs r USING(run_id)
                 WHERE {' AND '.join(clauses)}
                   AND r.status = 'complete'
                 ORDER BY s.run_id, s.session_uid, l.car_index, l.attempt_number""",
            parameters,
        ).fetchall()
        return [
            {
                "attempt_key": row["attempt_key"],
                "run_id": row["run_id"],
                "session_uid": row["session_uid"],
                "car_index": row["car_index"],
                "attempt_number": row["attempt_number"],
                "lap_number": row["lap_number"],
                "disposition": row["disposition"],
                "lap_time_ms": row["lap_time_ms"],
                "game_valid": None if row["game_valid"] is None else bool(row["game_valid"]),
                "reference_eligible": bool(row["reference_eligible"]),
                "start_frame_ordinal": row["start_frame_ordinal"],
                "end_frame_ordinal": row["end_frame_ordinal"],
                "superseded": (
                    None if row["superseded"] is None else bool(row["superseded"])
                ),
                "lifecycle_assessed": bool(row["lifecycle_assessed"]),
                "start_observed": bool(row["start_observed"]),
                "pit_encountered": bool(row["pit_encountered"]),
                "sample_count": row["sample_count"],
                "trace_row_count": row["trace_row_count"],
                "trace_schema_version": row["trace_schema_version"],
                "trace_sha256": row["trace_sha256"],
                "context": (
                    json.loads(row["initial_context_json"])
                    if row["initial_context_json"]
                    else None
                ),
                "quality": json.loads(row["quality_json"]),
                "exclusion_reasons": json.loads(row["exclusion_reasons_json"]),
                "timing_evidence": (
                    json.loads(row["timing_evidence_json"])
                    if row["timing_evidence_json"]
                    else {
                        "status": "unavailable",
                        "reasons": ["not_available_for_legacy_import"],
                    }
                ),
                "player_participant_context": load_attempt_player_participant_context(
                    db.connection, str(row["attempt_key"])
                ),
                "player_car_setup_context": load_attempt_player_car_setup_context(
                    db.connection, str(row["attempt_key"])
                ),
            }
            for row in rows
        ]


def list_lap_attempt_page(
    database_path: str | Path = DEFAULT_DATABASE,
    *,
    run_id: str,
    session_uid: str,
    limit: int = 50,
    offset: int = 0,
    selected_attempt_keys: tuple[str, ...] = (),
) -> dict[str, object]:
    """Return a bounded, session-scoped comparison inventory and exact selections."""
    if not 1 <= limit <= 100:
        raise ValueError("lap attempt page limit must be between 1 and 100")
    if not 0 <= offset <= MAX_LAP_ATTEMPT_PAGE_OFFSET:
        raise ValueError("lap attempt page offset is out of range")
    if len(selected_attempt_keys) > 2 or any(
        not isinstance(key, str) or not key or len(key) > 256
        for key in selected_attempt_keys
    ):
        raise ValueError("at most two valid selected attempt keys are supported")
    selected_keys = tuple(dict.fromkeys(selected_attempt_keys))
    scope_clauses = [
        "s.run_id = ?",
        "s.session_uid = ?",
        "t.ready = 1",
        "r.status = 'complete'",
    ]
    scope_parameters: list[Any] = [run_id, session_uid]
    select_sql = """SELECT l.*, s.session_uid, s.run_id, t.relative_path,
                       t.row_count AS trace_row_count, t.quality_json, t.sha256 AS trace_sha256,
                       t.schema_version AS trace_schema_version,
                       e.evidence_json AS timing_evidence_json,
                       (SELECT c.context_json FROM lap_context_segments c
                          WHERE c.attempt_key = l.attempt_key ORDER BY c.ordinal LIMIT 1)
                          AS initial_context_json
                  FROM lap_attempts l JOIN sessions s USING(session_key)
                  JOIN telemetry_files t USING(attempt_key)
                  LEFT JOIN attempt_timing_evidence e USING(attempt_key)
                  JOIN processing_runs r USING(run_id)"""

    def materialize(db: Database, row: Any) -> dict[str, object]:
        return {
            "attempt_key": row["attempt_key"],
            "run_id": row["run_id"],
            "session_uid": row["session_uid"],
            "car_index": row["car_index"],
            "attempt_number": row["attempt_number"],
            "lap_number": row["lap_number"],
            "disposition": row["disposition"],
            "lap_time_ms": row["lap_time_ms"],
            "game_valid": None if row["game_valid"] is None else bool(row["game_valid"]),
            "reference_eligible": bool(row["reference_eligible"]),
            "start_frame_ordinal": row["start_frame_ordinal"],
            "end_frame_ordinal": row["end_frame_ordinal"],
            "superseded": None if row["superseded"] is None else bool(row["superseded"]),
            "lifecycle_assessed": bool(row["lifecycle_assessed"]),
            "start_observed": bool(row["start_observed"]),
            "pit_encountered": bool(row["pit_encountered"]),
            "sample_count": row["sample_count"],
            "trace_row_count": row["trace_row_count"],
            "trace_schema_version": row["trace_schema_version"],
            "trace_sha256": row["trace_sha256"],
            "context": json.loads(row["initial_context_json"])
            if row["initial_context_json"]
            else None,
            "quality": json.loads(row["quality_json"]),
            "exclusion_reasons": json.loads(row["exclusion_reasons_json"]),
            "timing_evidence": json.loads(row["timing_evidence_json"])
            if row["timing_evidence_json"]
            else {
                "status": "unavailable",
                "reasons": ["not_available_for_legacy_import"],
            },
            "player_participant_context": load_attempt_player_participant_context(
                db.connection, str(row["attempt_key"])
            ),
            "player_car_setup_context": load_attempt_player_car_setup_context(
                db.connection, str(row["attempt_key"])
            ),
        }

    with Database(database_path, read_only=True) as db:
        db.connection.execute("BEGIN")
        total = int(
            db.connection.execute(
                """SELECT COUNT(*)
                     FROM lap_attempts l JOIN sessions s USING(session_key)
                     JOIN telemetry_files t USING(attempt_key)
                     JOIN processing_runs r USING(run_id)
                    """
                f"WHERE {' AND '.join(scope_clauses)}",
                scope_parameters,
            ).fetchone()[0]
        )
        page_rows = db.connection.execute(
            f"""WITH page_keys AS (
                    SELECT l.attempt_key
                      FROM lap_attempts l JOIN sessions s USING(session_key)
                      JOIN telemetry_files t USING(attempt_key)
                      JOIN processing_runs r USING(run_id)
                     WHERE {' AND '.join(scope_clauses)}
                     ORDER BY s.run_id, s.session_uid, l.car_index, l.attempt_number
                     LIMIT ? OFFSET ?
                )
                {select_sql}
                JOIN page_keys p USING(attempt_key)
                WHERE {' AND '.join(scope_clauses)}
                ORDER BY s.run_id, s.session_uid, l.car_index, l.attempt_number""",
            [*scope_parameters, limit, offset, *scope_parameters],
        ).fetchall()
        selected_rows: dict[str, Any] = {}
        if selected_keys:
            placeholders = ",".join("?" for _ in selected_keys)
            selected_rows = {
                str(row["attempt_key"]): row
                for row in db.connection.execute(
                    f"{select_sql} WHERE {' AND '.join(scope_clauses)} "
                    f"AND l.attempt_key IN ({placeholders}) "
                    "ORDER BY s.run_id, s.session_uid, l.car_index, l.attempt_number",
                    [*scope_parameters, *selected_keys],
                ).fetchall()
            }
        items = [materialize(db, row) for row in page_rows]
        selected_attempts = [
            {
                "requested_attempt_key": key,
                "attempt": materialize(db, selected_rows[key])
                if key in selected_rows
                else None,
            }
            for key in selected_keys
        ]
    return {
        "run_id": run_id,
        "session_uid": session_uid,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "selected_attempts": selected_attempts,
    }


def get_lap(database_path: str | Path, attempt_key: str) -> dict[str, object] | None:
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT l.*, s.session_uid, s.run_id, t.relative_path, t.row_count,
                      t.sha256, t.quality_json, t.schema_version,
                      e.evidence_json AS timing_evidence_json
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN telemetry_files t USING(attempt_key)
                 LEFT JOIN attempt_timing_evidence e USING(attempt_key)
                  JOIN processing_runs r USING(run_id)
                WHERE l.attempt_key = ? AND t.ready = 1 AND r.status = 'complete'""",
            (attempt_key,),
        ).fetchone()
        if row is None:
            return None
        from .parquet import read_trace

        relative_path = Path(row["relative_path"])
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("stored trace path is invalid")
        root = Path(database_path).parent.resolve()
        path = (Path(database_path).parent / relative_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("stored trace path escapes the database directory")
        if not path.is_file() or sha256_file(path) != row["sha256"]:
            raise ValueError("trace file is missing or its checksum does not match SQLite")
        metadata, table = read_trace(
            path, expected_schema_version=row["schema_version"]
        )
        player_participant_context = load_attempt_player_participant_context(
            db.connection, attempt_key
        )
        player_car_setup_context = load_attempt_player_car_setup_context(
            db.connection, attempt_key
        )
        return {
            "attempt": json.loads(row["attempt_json"]),
            "attempt_key": row["attempt_key"],
            "run_id": row["run_id"],
            "trace_path": row["relative_path"],
            "trace_row_count": row["row_count"],
            "trace_schema_version": row["schema_version"],
            "trace_checksum_valid": True,
            "parquet_rows": metadata.num_rows,
            "quality": json.loads(row["quality_json"]),
            "timing_evidence": (
                json.loads(row["timing_evidence_json"])
                if row["timing_evidence_json"]
                else {
                    "status": "unavailable",
                    "reasons": ["not_available_for_legacy_import"],
                }
            ),
            "player_participant_context": player_participant_context,
            "player_car_setup_context": player_car_setup_context,
            "first_sample": table.slice(0, min(1, table.num_rows)).to_pylist(),
            "last_sample": table.slice(max(0, table.num_rows - 1), min(1, table.num_rows)).to_pylist(),
        }
