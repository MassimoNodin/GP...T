from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from ..errors import ProtocolError
from ..pipeline import TelemetryPipeline
from ..recording.capture import CaptureReader
from ..telemetry.canonical import CarSample
from .database import Database
from .lock import ImportRunLock
from .parquet import ParquetTraceWriter, TRACE_SCHEMA_VERSION, sha256_file


PIPELINE_VERSION = "player-traces-v8-reference-quality"
DEFAULT_DATABASE = Path("data") / "f1-engineer.sqlite3"
IMPORT_CONFIG = {"max_open_frames": 256, "reorder_window_frames": 3}


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
    attempts: int = 0
    samples: int = 0
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
    import_late_packets_ignored: int = 0
    import_frame_overflow_packets_dropped: int = 0

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
        self.results: dict[str, tuple[str, int, str, dict[str, int]]] = {}
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
            pipeline = TelemetryPipeline(**IMPORT_CONFIG)
            sessions: dict[int, int] = {}
            latest_contexts: dict[int, dict[str, object]] = {}
            context_updates: dict[tuple[int, int], dict[str, object]] = {}
            context_invalidations: dict[tuple[int, int], str] = {}
            participant_updates: dict[tuple[int, int, int], tuple[int, dict[str, object]]] = {}
            attempts: list[Any] = []
            malformed_packets = 0
            packet_count = 0
            lap_data_error_count = 0
            car_telemetry_error_count = 0
            participant_error_count = 0
            motion_error_count = 0
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
                    attempts.extend(result.lap_attempts)
                    trace_manager.consume(result.car_samples, result.lap_attempts)
                    lap_data_error_count += len(result.lap_data_errors)
                    car_telemetry_error_count += len(result.car_telemetry_errors)
                    participant_error_count += int(result.participants_error is not None)
                    motion_error_count += len(pipeline.motion_decode_errors)
                    pipeline.laps.drain_attempts()
                    pipeline.lap_data_decode_errors.clear()
                    pipeline.car_telemetry_decode_errors.clear()
                    pipeline.participants_decode_errors.clear()
                    pipeline.motion_decode_errors.clear()
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
                attempts.extend(flushed.lap_attempts)
                trace_manager.consume(flushed.car_samples, flushed.lap_attempts)
                pipeline.laps.drain_attempts()
                lap_data_error_count += len(flushed.lap_data_errors)
                car_telemetry_error_count += len(flushed.car_telemetry_errors)
                motion_error_count += len(pipeline.motion_decode_errors)
                pipeline.motion_decode_errors.clear()
                trace_manager.finish_all(tuple(attempts))
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

            attempts = tuple(attempts)
            attempt_keys = {
                attempt.attempt_id: _attempt_key(
                    run_id, attempt.session_uid, attempt.car_index, attempt.attempt_number
                )
                for attempt in attempts
            }
            trace_results = trace_manager.results
            sample_count = sum(result[1] for result in trace_results.values())
            missing_samples = sum(
                result[3]["missing_car_telemetry_count"]
                for result in trace_results.values()
            )

            missing_capture_frames = tuple(pipeline.missing_car_telemetry_frame_examples)
            capture_quality = {
                "lap_data_packets_decoded": pipeline.lap_data_packets_decoded,
                "car_telemetry_packets_decoded": pipeline.car_telemetry_packets_decoded,
                "missing_car_telemetry_frame_count": pipeline.missing_car_telemetry_frame_count,
                "missing_car_telemetry_frames": [list(frame) for frame in missing_capture_frames],
                "canonical_lap_sample_count": sample_count,
                "missing_car_telemetry_lap_sample_count": missing_samples,
                "motion_packets_decoded": pipeline.motion_packets_decoded,
                "motion_decode_errors": motion_error_count,
                "player_motion_sample_count": pipeline.player_motion_samples,
                "missing_player_motion_sample_count": pipeline.missing_player_motion_samples,
                "import_late_packets_ignored": pipeline.frames.late_packets_ignored,
                "import_frame_overflow_packets_dropped": pipeline.frames.overflow_packets_dropped,
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
                import_late_packets_ignored=pipeline.frames.late_packets_ignored,
                import_frame_overflow_packets_dropped=pipeline.frames.overflow_packets_dropped,
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
                for attempt in attempts:
                    key = attempt_keys[attempt.attempt_id]
                    attempt_key = key
                    connection.execute(
                        """INSERT INTO lap_attempts(attempt_key,session_key,car_index,
                                  attempt_number,lap_number,disposition,start_frame_identifier,
                                  end_frame_identifier,start_session_time_s,end_session_time_s,
                                  lap_time_ms,game_valid,start_observed,pit_encountered,sample_count,
                                  reference_eligible,exclusion_reasons_json,attempt_json)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                connection.execute(
                    """UPDATE processing_runs SET status='complete',
                              finished_at_utc=CURRENT_TIMESTAMP, error=NULL, metrics_json=?
                         WHERE run_id=?""",
                    (metrics_json, run_id),
                )
            return import_summary
        except Exception as exc:
            if trace_manager is not None:
                trace_manager.abort()
            db.connection.execute(
                """UPDATE processing_runs SET status='failed',
                          finished_at_utc=CURRENT_TIMESTAMP, error=? WHERE run_id=?""",
                (str(exc), run_id),
            )
            db.connection.commit()
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
                       (SELECT c.context_json FROM lap_context_segments c
                          WHERE c.attempt_key = l.attempt_key ORDER BY c.ordinal LIMIT 1)
                          AS initial_context_json
                  FROM lap_attempts l JOIN sessions s USING(session_key)
                  JOIN telemetry_files t USING(attempt_key)
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
            }
            for row in rows
        ]


def get_lap(database_path: str | Path, attempt_key: str) -> dict[str, object] | None:
    with Database(database_path, read_only=True) as db:
        row = db.connection.execute(
            """SELECT l.*, s.session_uid, s.run_id, t.relative_path, t.row_count,
                      t.sha256, t.quality_json, t.schema_version
                 FROM lap_attempts l JOIN sessions s USING(session_key)
                 JOIN telemetry_files t USING(attempt_key)
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
            "first_sample": table.slice(0, min(1, table.num_rows)).to_pylist(),
            "last_sample": table.slice(max(0, table.num_rows - 1), min(1, table.num_rows)).to_pylist(),
        }
