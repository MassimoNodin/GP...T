from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import __version__
from .errors import F1EngineerError, ProtocolError
from .pipeline import PipelineResult, TelemetryPipeline
from .recording.capture import CaptureReader, CaptureWriter
from .sessions.context import SessionContext
from .analysis.resampling import ResamplingConfig
from .analysis.service import compare_attempts
from .analysis.trajectory import build_observed_trajectory
from .storage.importer import (
    DEFAULT_DATABASE,
    get_lap,
    import_capture,
    list_laps,
    list_sessions,
)
from .storage.query import TRAJECTORY_TRACE_COLUMNS, load_attempt_trace
from .udp.models import DecodedPacket, RawDatagram
from .udp.source import ReplaySource, UDPSource


def _json_line(value: dict[str, Any]) -> None:
    print(json.dumps(value, separators=(",", ":"), sort_keys=True))


def _packet_summary(packet: DecodedPacket) -> dict[str, Any]:
    header = packet.header
    return {
        "sequence": None,
        "format": packet.packet_format.value,
        "packet_id": header.packet_id,
        "packet_name": packet.packet_kind.name.lower()
        if packet.packet_kind is not None
        else "unknown",
        "packet_version": header.packet_version,
        "session_uid": header.session_uid,
        "session_time": header.session_time,
        "frame_identifier": header.frame_identifier,
        "overall_frame_identifier": header.overall_frame_identifier,
        "body_bytes": len(packet.body),
    }


def _add_frame_stats(counts: Counter[str], pipeline: TelemetryPipeline) -> None:
    counts["duplicate_envelopes_ignored"] = pipeline.frames.duplicates_ignored
    counts["late_packets_ignored"] = pipeline.frames.late_packets_ignored
    counts["frame_overflow_packets_dropped"] = pipeline.frames.overflow_packets_dropped


def _lap_attempts(pipeline: TelemetryPipeline) -> list[dict[str, object]]:
    return [attempt.to_dict() for attempt in pipeline.laps.attempts]


def _add_lap_stats(counts: Counter[str], pipeline: TelemetryPipeline) -> None:
    attempts = pipeline.laps.attempts
    counts["lap_data_packets_decoded"] = pipeline.lap_data_packets_decoded
    counts["lap_data_decode_errors"] = len(pipeline.lap_data_decode_errors)
    counts["lap_attempts"] = len(attempts)
    counts["completed_lap_attempts"] = sum(
        attempt.disposition.value == "completed" for attempt in attempts
    )
    counts["invalid_lap_attempts"] = sum(
        attempt.game_valid is False for attempt in attempts
    )
    counts["partial_lap_attempts"] = sum(
        attempt.disposition.value == "partial" for attempt in attempts
    )
    counts["abandoned_lap_attempts"] = sum(
        attempt.disposition.value == "abandoned" for attempt in attempts
    )
    counts["reference_eligible_laps"] = sum(
        attempt.reference_eligible for attempt in attempts
    )
    counts["motion_packets_decoded"] = pipeline.motion_packets_decoded
    counts["motion_decode_errors"] = len(pipeline.motion_decode_errors)
    counts["player_motion_samples"] = pipeline.player_motion_samples
    counts["missing_player_motion_samples"] = pipeline.missing_player_motion_samples


def _collect_session_context(
    counts: Counter[str],
    contexts: dict[int, SessionContext],
    result: PipelineResult,
) -> SessionContext | None:
    for event in result.session_events:
        if event.kind == "session_context_invalidated":
            counts["session_context_invalidations"] += 1
            contexts.pop(event.session_uid, None)
    if result.session_context_error is not None:
        counts["session_context_decode_errors"] += 1
    if result.session_context is not None:
        counts["session_context_updates"] += 1
        contexts[result.session_context.session_uid] = result.session_context
        return result.session_context
    return None


async def _record(args: argparse.Namespace) -> int:
    output = Path(args.output)
    source = UDPSource(host=args.host, port=args.port, queue_size=args.queue_size)
    pipeline = TelemetryPipeline()
    counts: Counter[str] = Counter()
    counts["completed_frames"] = 0
    session_contexts: dict[int, SessionContext] = {}
    started = time.monotonic()
    writer: CaptureWriter | None = None
    writer_executor: ThreadPoolExecutor | None = None
    operation_error: Exception | None = None
    loop = asyncio.get_running_loop()
    deadline = started + args.duration if args.duration is not None else None

    try:
        await source.open()
        writer = CaptureWriter(
            output,
            metadata={
                "application": "f1-race-engineer",
                "application_version": __version__,
                "bind_host": args.host,
                "bind_port": args.port,
            },
            overwrite=args.overwrite,
        )
        writer_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="f1-capture")
        print(f"Recording UDP {args.host}:{args.port} to {output}. Press Ctrl+C to stop.")

        async def persist(raw: RawDatagram) -> None:
            assert writer is not None and writer_executor is not None
            pending_write = loop.run_in_executor(writer_executor, writer.write, raw)
            try:
                await asyncio.shield(pending_write)
            except asyncio.CancelledError:
                # Do not lose a datagram already removed from the receive queue.
                await pending_write
                counts["recorded"] += 1
                raise
            counts["recorded"] += 1

        packet_iterator = source.packets().__aiter__()
        while True:
            try:
                if deadline is None:
                    raw = await packet_iterator.__anext__()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    raw = await asyncio.wait_for(packet_iterator.__anext__(), timeout=remaining)
            except (StopAsyncIteration, asyncio.TimeoutError):
                break

            # Persist first: malformed or unsupported packets remain available for later decoders.
            await persist(raw)
            try:
                result = pipeline.process(raw)
            except ProtocolError:
                counts["unrecognized_or_malformed"] += 1
                continue
            counts[f"format_{result.packet.packet_format.value}"] += 1
            counts["completed_frames"] += len(result.completed_frames)
            for event in result.session_events:
                _json_line(
                    {
                        "event": event.kind,
                        "session_uid": event.session_uid,
                        "previous_session_uid": event.previous_session_uid,
                    }
                )
            context_update = _collect_session_context(counts, session_contexts, result)
            if context_update is not None:
                _json_line(
                    {
                        "event": "session_context_updated",
                        "context": context_update.to_dict(),
                    }
                )
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        operation_error = exc
    finally:
        source.close()
        pending = source.drain_pending()
        if writer is not None and writer_executor is not None:
            for raw in pending:
                pending_write = loop.run_in_executor(writer_executor, writer.write, raw)
                try:
                    await asyncio.shield(pending_write)
                except Exception as exc:
                    operation_error = operation_error or exc
                    break
                counts["recorded"] += 1
                try:
                    result = pipeline.process(raw)
                except ProtocolError:
                    counts["unrecognized_or_malformed"] += 1
                else:
                    counts[f"format_{result.packet.packet_format.value}"] += 1
                    counts["completed_frames"] += len(result.completed_frames)
                    _collect_session_context(counts, session_contexts, result)
            counts["unpersisted_on_shutdown"] = max(
                0, source.stats.queued - counts["recorded"]
            )
            counts["received"] = source.stats.received
            counts["queue_dropped"] = source.stats.dropped
            counts["socket_errors"] = source.stats.socket_errors
            counts["open_frames_flushed"] = len(pipeline.finish())
            _add_frame_stats(counts, pipeline)
            _add_lap_stats(counts, pipeline)
            counts["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            capture_status = (
                "complete"
                if operation_error is None and counts["unpersisted_on_shutdown"] == 0
                else "incomplete"
            )
            footer = {"status": capture_status, **dict(counts)}
            try:
                pending_close = loop.run_in_executor(writer_executor, writer.close, footer)
                await asyncio.shield(pending_close)
            except Exception as exc:
                operation_error = operation_error or exc
            finally:
                writer_executor.shutdown(wait=True)
        elif writer is not None:
            try:
                writer.close({"status": "incomplete", "reason": "capture worker unavailable"})
            except Exception as exc:
                operation_error = operation_error or exc

    counts["received"] = source.stats.received
    counts.setdefault("queue_dropped", source.stats.dropped)
    counts.setdefault("socket_errors", source.stats.socket_errors)
    counts.setdefault("unpersisted_on_shutdown", 0)
    counts.setdefault("open_frames_flushed", len(pipeline.finish()))
    _add_frame_stats(counts, pipeline)
    _add_lap_stats(counts, pipeline)
    counts.setdefault("elapsed_ms", int((time.monotonic() - started) * 1000))
    _json_line(
        {
            "capture": str(output),
            "summary": dict(counts),
            "session_contexts": [
                session_contexts[uid].to_dict() for uid in sorted(session_contexts)
            ],
            "lap_attempts": _lap_attempts(pipeline),
        }
    )
    if operation_error is not None:
        raise operation_error
    return 0


async def _replay(args: argparse.Namespace) -> int:
    source = ReplaySource(args.capture, speed=args.speed)
    pipeline = TelemetryPipeline()
    counts: Counter[str] = Counter()
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    session_contexts: dict[int, SessionContext] = {}

    async for raw in source.packets():
        counts["datagrams"] += 1
        first_timestamp = raw.captured_at_ns if first_timestamp is None else first_timestamp
        last_timestamp = raw.captured_at_ns
        try:
            result = pipeline.process(raw)
        except ProtocolError as exc:
            counts["unrecognized_or_malformed"] += 1
            _json_line({"sequence": raw.sequence, "decode_error": str(exc)})
            continue
        counts[f"format_{result.packet.packet_format.value}"] += 1
        counts["completed_frames"] += len(result.completed_frames)
        context_update = _collect_session_context(counts, session_contexts, result)
        record = _packet_summary(result.packet)
        record["sequence"] = raw.sequence
        _json_line(record)
        for event in result.session_events:
            _json_line(
                {
                    "event": event.kind,
                    "session_uid": event.session_uid,
                    "previous_session_uid": event.previous_session_uid,
                }
            )
        if context_update is not None:
            _json_line(
                {
                    "event": "session_context_updated",
                    "context": context_update.to_dict(),
                }
            )

    counts["open_frames_flushed"] = len(pipeline.finish())
    _add_frame_stats(counts, pipeline)
    _add_lap_stats(counts, pipeline)
    _json_line(
        {
            "capture": str(args.capture),
            "replay_speed": args.speed,
            "complete": source.complete,
            "completion": source.completion,
            "first_captured_at_ns": first_timestamp,
            "last_captured_at_ns": last_timestamp,
            "summary": dict(counts),
            "session_contexts": [
                session_contexts[uid].to_dict() for uid in sorted(session_contexts)
            ],
            "lap_attempts": _lap_attempts(pipeline),
        }
    )
    return 0


def _inspect(args: argparse.Namespace) -> int:
    counts: Counter[str] = Counter()
    counts["completed_frames"] = 0
    formats: Counter[str] = Counter()
    packet_types: Counter[str] = Counter()
    session_uids: set[int] = set()
    session_contexts: dict[int, SessionContext] = {}
    pipeline = TelemetryPipeline()
    first_timestamp: int | None = None
    last_timestamp: int | None = None

    with CaptureReader(args.capture) as capture:
        metadata = capture.metadata
        for raw in capture:
            counts["datagrams"] += 1
            first_timestamp = raw.captured_at_ns if first_timestamp is None else first_timestamp
            last_timestamp = raw.captured_at_ns
            try:
                result = pipeline.process(raw)
            except ProtocolError:
                counts["unrecognized_or_malformed"] += 1
                continue
            packet = result.packet
            counts["completed_frames"] += len(result.completed_frames)
            _collect_session_context(counts, session_contexts, result)
            formats[str(packet.packet_format.value)] += 1
            name = (
                packet.packet_kind.name.lower()
                if packet.packet_kind is not None
                else "unknown"
            )
            packet_types[f"{packet.packet_format.value}:{packet.header.packet_id}:{name}"] += 1
            if packet.header.session_uid:
                session_uids.add(packet.header.session_uid)

    counts["open_frames_flushed"] = len(pipeline.finish())
    _add_frame_stats(counts, pipeline)
    _add_lap_stats(counts, pipeline)
    _json_line(
        {
            "capture": str(args.capture),
            "metadata": metadata,
            "complete": capture.complete,
            "completion": capture.completion,
            "summary": dict(counts),
            "formats": dict(formats),
            "packet_types": dict(packet_types),
            "session_uids": sorted(session_uids),
            "session_contexts": [
                session_contexts[uid].to_dict() for uid in sorted(session_contexts)
            ],
            "session_context_missing_uids": sorted(session_uids - session_contexts.keys()),
            "lap_attempts": _lap_attempts(pipeline),
            "first_captured_at_ns": first_timestamp,
            "last_captured_at_ns": last_timestamp,
        }
    )
    return 0


def _import_capture(args: argparse.Namespace) -> int:
    result = import_capture(args.capture, args.database)
    _json_line(result.to_dict())
    return 0


def _sessions(args: argparse.Namespace) -> int:
    _json_line({"sessions": list_sessions(args.database)})
    return 0


def _laps(args: argparse.Namespace) -> int:
    _json_line(
        {
            "laps": list_laps(
                args.database,
                run_id=args.run_id,
                session_uid=args.session_uid,
            )
        }
    )
    return 0


def _lap(args: argparse.Namespace) -> int:
    result = get_lap(args.database, args.attempt_key)
    if result is None:
        print("error: lap attempt not found or its trace is not ready", file=sys.stderr)
        return 2
    _json_line(result)
    return 0


def _compare(args: argparse.Namespace) -> int:
    config = ResamplingConfig(
        grid_step_m=args.grid_step_m,
        max_bracket_time_s=args.max_gap_s,
        max_bracket_distance_m=args.max_gap_m,
    )
    _json_line(
        compare_attempts(
            args.database,
            args.target_attempt_key,
            args.reference_attempt_key,
            config=config,
            track_model=args.track_model,
        )
    )
    return 0


def _trajectory(args: argparse.Namespace) -> int:
    attempt = load_attempt_trace(
        args.database,
        args.attempt_key,
        columns=TRAJECTORY_TRACE_COLUMNS,
    )
    if attempt is None:
        print("error: lap attempt not found or its trace is not ready", file=sys.stderr)
        return 2
    document = build_observed_trajectory(
        attempt_key=attempt.attempt_key,
        run_id=attempt.run_id,
        session_uid=attempt.session_uid,
        car_index=attempt.car_index,
        disposition=attempt.disposition,
        lap_time_ms=attempt.lap_time_ms,
        game_valid=attempt.game_valid,
        reference_eligible=attempt.reference_eligible,
        exclusion_reasons=attempt.exclusion_reasons,
        trace_sha256=attempt.trace_sha256,
        trace_schema_version=attempt.trace_schema_version,
        context_segments=attempt.context_segments,
        samples=attempt.samples,
    )
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        print(f"error: output already exists: {output} (use --overwrite)", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(
        json.dumps(document, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    _json_line(
        {
            "output": str(output),
            "schema_version": document["schema_version"],
            "artifact_kind": document["artifact_kind"],
            "diagnostic_only": document["diagnostic_only"],
            "coverage": document["coverage"],
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="f1-engineer", description="F1 telemetry capture and replay")
    parser.add_argument("--version", action="version", version=f"f1-engineer {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    record = commands.add_parser("record", help="capture raw F1 UDP telemetry")
    record.add_argument("--output", required=True, help="destination .f1ecap file")
    record.add_argument("--host", default="0.0.0.0", help="local address to bind (default: %(default)s)")
    record.add_argument("--port", type=int, default=20777, help="UDP port to bind (default: %(default)s)")
    record.add_argument("--queue-size", type=int, default=8192, help="bounded receive queue size")
    record.add_argument("--duration", type=float, help="stop after this many seconds")
    record.add_argument("--overwrite", action="store_true", help="replace an existing capture file")
    record.set_defaults(handler=_record)

    inspect = commands.add_parser("inspect", help="summarize a telemetry capture")
    inspect.add_argument("capture", help="capture file to inspect")
    inspect.set_defaults(handler=_inspect)

    replay = commands.add_parser("replay", help="replay a capture through the decoder and session pipeline")
    replay.add_argument("capture", help="capture file to replay")
    replay.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="relative to recorded timing; use 0 for maximum processing speed",
    )
    replay.set_defaults(handler=_replay)

    import_command = commands.add_parser("import", help="import a capture into SQLite and Parquet")
    import_command.add_argument("capture", help="source .f1ecap capture")
    import_command.add_argument(
        "--database", default=str(DEFAULT_DATABASE), help="SQLite database path"
    )
    import_command.set_defaults(handler=_import_capture)

    sessions = commands.add_parser("sessions", help="list imported sessions")
    sessions.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    sessions.set_defaults(handler=_sessions)

    laps = commands.add_parser("laps", help="list imported lap attempts")
    laps.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    laps.add_argument("--run-id", help="filter to one processing run")
    laps.add_argument("--session-uid", help="filter by EA session UID")
    laps.set_defaults(handler=_laps)

    lap = commands.add_parser("lap", help="inspect a lap attempt and its trace")
    lap.add_argument("attempt_key", help="attempt key printed by the laps command")
    lap.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    lap.set_defaults(handler=_lap)

    compare = commands.add_parser(
        "compare", help="compare two completed Time Trial laps by distance"
    )
    compare.add_argument("target_attempt_key", help="target attempt key from the laps command")
    compare.add_argument("reference_attempt_key", help="reference attempt key from the laps command")
    compare.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    compare.add_argument("--grid-step-m", type=float, default=1.0, help="distance grid step (default: %(default)s)")
    compare.add_argument("--max-gap-s", type=float, default=0.1, help="maximum interpolation time gap in seconds")
    compare.add_argument("--max-gap-m", type=float, default=25.0, help="maximum interpolation distance gap in metres")
    compare.add_argument(
        "--track-model",
        help="versioned JSON track model; adds diagnostic corner-region analysis",
    )
    compare.set_defaults(handler=_compare)

    trajectory = commands.add_parser(
        "trajectory", help="export an observed world-space lap trajectory"
    )
    trajectory.add_argument("attempt_key", help="attempt key printed by the laps command")
    trajectory.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    trajectory.add_argument("--output", required=True, help="destination versioned JSON path")
    trajectory.add_argument("--overwrite", action="store_true", help="replace an existing output file")
    trajectory.set_defaults(handler=_trajectory)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "duration", None) is not None and (
        not math.isfinite(args.duration) or args.duration <= 0
    ):
        parser.error("--duration must be greater than zero")
    if getattr(args, "queue_size", None) is not None and args.queue_size < 1:
        parser.error("--queue-size must be at least one")
    if getattr(args, "speed", None) is not None and (
        not math.isfinite(args.speed) or args.speed < 0
    ):
        parser.error("--speed must be zero or greater")

    try:
        if asyncio.iscoroutinefunction(args.handler):
            code = asyncio.run(args.handler(args))
        else:
            code = args.handler(args)
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        code = 130
    except (F1EngineerError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)
