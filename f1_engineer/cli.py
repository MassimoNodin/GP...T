from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__
from .errors import F1EngineerError, ProtocolError
from .pipeline import PipelineResult, TelemetryPipeline
from .recording.capture import CaptureReader
from .recording.service import record_udp_capture
from .sessions.context import SessionContext
from .analysis.resampling import ResamplingConfig
from .analysis.comparison_window import optional_distance_window
from .analysis.service import ComparisonPolicy, compare_attempts
from .analysis.reference_selection import (
    ReferenceKind,
    ReferenceSelectionStatus,
    ReferenceRequest,
    select_reference,
)
from .analysis.quality import inspect_attempt_quality
from .analysis.region_service import load_attempt_region_report
from .analysis.trajectory_service import load_observed_trajectory
from .analysis.trace_chart_service import load_attempt_trace_chart_preview
from .storage.importer import (
    DEFAULT_DATABASE,
    get_lap,
    import_capture,
    list_laps,
    list_sessions,
)
from .storage.run_summaries import list_processing_run_lifecycle_events
from .udp.models import DecodedPacket
from .udp.source import ReplaySource, UDPSource
from .tracks.registry import resolve_track_model


def _json_line(value: dict[str, Any]) -> None:
    print(json.dumps(value, separators=(",", ":"), sort_keys=True))


def _write_json_document(output: Path, document: dict[str, Any], *, overwrite: bool) -> None:
    """Atomically publish JSON without clobbering an unapproved destination."""
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(document, separators=(",", ":"), sort_keys=True))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, output)
        else:
            # A same-directory hard link atomically fails if the destination exists.
            os.link(temporary, output)
            temporary.unlink()
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


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
    print(
        f"Recording UDP {args.host}:{args.port} to {args.output}. Press Ctrl+C to stop."
    )
    result = await record_udp_capture(
        args.output,
        host=args.host,
        port=args.port,
        queue_size=args.queue_size,
        collect_inventory=True,
        duration=args.duration,
        overwrite=args.overwrite,
        on_event=_json_line,
    )
    _json_line(result.to_dict())
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


def _lifecycle(args: argparse.Namespace) -> int:
    result = list_processing_run_lifecycle_events(
        args.database,
        args.run_id,
        limit=args.limit,
        offset=args.offset,
    )
    if result is None:
        _json_line({"status": "unavailable", "reason": "processing_run_unavailable"})
        return 1
    _json_line(result)
    return 0


def _lap(args: argparse.Namespace) -> int:
    result = get_lap(args.database, args.attempt_key)
    if result is None:
        print("error: lap attempt not found or its trace is not ready", file=sys.stderr)
        return 2
    _json_line(result)
    return 0


def _quality(args: argparse.Namespace) -> int:
    result = inspect_attempt_quality(args.database, args.attempt_key)
    if result is None:
        print("error: attempt not found or its trace is not ready", file=sys.stderr)
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
            policy=ComparisonPolicy(args.comparison_policy),
            distance_window=optional_distance_window(
                args.window_start_m, args.window_end_m
            ),
        )
    )
    return 0


def _reference(args: argparse.Namespace) -> int:
    selection = select_reference(
        args.database,
        ReferenceRequest(
            target_attempt_key=args.target_attempt_key,
            reference_kind=ReferenceKind.SESSION_BEST,
        ),
    )
    document = selection.to_dict()
    if selection.selected_reference is not None:
        reference_attempt_key = selection.selected_reference["attempt_key"]
        try:
            comparison = compare_attempts(
                args.database,
                args.target_attempt_key,
                str(reference_attempt_key),
            )
        except ValueError as exc:
            document["comparison"] = {"status": "unavailable", "reason": str(exc)}
        else:
            document["comparison"] = {"status": "available", "result": comparison}
    _json_line(document)
    return (
        2
        if selection.status
        in {
            ReferenceSelectionStatus.TARGET_UNAVAILABLE,
            ReferenceSelectionStatus.TARGET_NOT_COMPLETED,
        }
        else 0
    )


def _api(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("error: install the app extra with `uv sync --extra app`", file=sys.stderr)
        return 2

    from .api.app import create_app
    from .api.security import load_or_create_control_token

    uvicorn.run(
        create_app(
            args.database,
            recordings_root=args.recordings_root,
            control_token=load_or_create_control_token(args.control_token_file),
            recording_host=args.udp_host,
            recording_port=args.udp_port,
            recording_queue_size=args.udp_queue_size,
        ),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
    )
    return 0


def _trajectory(args: argparse.Namespace) -> int:
    document = load_observed_trajectory(args.database, args.attempt_key)
    if document is None:
        print("error: lap attempt not found or its trace is not ready", file=sys.stderr)
        return 2
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        print(f"error: output already exists: {output} (use --overwrite)", file=sys.stderr)
        return 2
    try:
        _write_json_document(output, document, overwrite=args.overwrite)
    except FileExistsError:
        print(f"error: output already exists: {output} (use --overwrite)", file=sys.stderr)
        return 2
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


def _traces(args: argparse.Namespace) -> int:
    document = load_attempt_trace_chart_preview(args.database, args.attempt_key)
    if document is None:
        print("error: lap attempt not found or its trace is not ready", file=sys.stderr)
        return 2
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        print(f"error: output already exists: {output} (use --overwrite)", file=sys.stderr)
        return 2
    try:
        _write_json_document(output, document, overwrite=args.overwrite)
    except FileExistsError:
        print(f"error: output already exists: {output} (use --overwrite)", file=sys.stderr)
        return 2
    _json_line(
        {
            "output": str(output),
            "report_version": document["report_version"],
            "artifact_kind": document["artifact_kind"],
            "diagnostic_only": document["diagnostic_only"],
            "channels": {
                identifier: {
                    "observed_sample_count": channel["observed_sample_count"],
                    "rendered_point_count": channel["rendered_point_count"],
                    "source_run_count": channel["source_run_count"],
                }
                for identifier, channel in document["channels"].items()
            },
        }
    )
    return 0


def _regions(args: argparse.Namespace) -> int:
    model = resolve_track_model(args.track_model_id, args.track_model_revision)
    document = load_attempt_region_report(
        args.database,
        args.attempt_key,
        model,
    )
    if document is None:
        print("error: lap attempt not found or its trace is not ready", file=sys.stderr)
        return 2
    _json_line(document)
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

    lifecycle = commands.add_parser(
        "lifecycle", help="list persisted session and flashback lifecycle evidence"
    )
    lifecycle.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    lifecycle.add_argument("--run-id", required=True, help="processing run ID")
    lifecycle.add_argument("--limit", type=int, default=50, help="page size (1-100)")
    lifecycle.add_argument("--offset", type=int, default=0, help="event ordinal page offset")
    lifecycle.set_defaults(handler=_lifecycle)

    lap = commands.add_parser("lap", help="inspect a lap attempt and its trace")
    lap.add_argument("attempt_key", help="attempt key printed by the laps command")
    lap.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    lap.set_defaults(handler=_lap)

    quality = commands.add_parser(
        "quality", help="inspect standalone telemetry quality for a lap attempt"
    )
    quality.add_argument("attempt_key", help="attempt key printed by the laps command")
    quality.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    quality.set_defaults(handler=_quality)

    compare = commands.add_parser(
        "compare", help="compare two explicitly selected completed laps by distance"
    )
    compare.add_argument("target_attempt_key", help="target attempt key from the laps command")
    compare.add_argument("reference_attempt_key", help="reference attempt key from the laps command")
    compare.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    compare.add_argument(
        "--comparison-policy",
        choices=[policy.value for policy in ComparisonPolicy],
        default=ComparisonPolicy.TIME_TRIAL.value,
        help="explicit comparison ruleset (default: Time Trial)",
    )
    compare.add_argument("--grid-step-m", type=float, default=1.0, help="distance grid step (default: %(default)s)")
    compare.add_argument("--max-gap-s", type=float, default=0.1, help="maximum interpolation time gap in seconds")
    compare.add_argument("--max-gap-m", type=float, default=25.0, help="maximum interpolation distance gap in metres")
    compare.add_argument(
        "--window-start-m",
        type=float,
        help="optional diagnostic comparison interval start distance in metres",
    )
    compare.add_argument(
        "--window-end-m",
        type=float,
        help="optional diagnostic comparison interval end distance in metres",
    )
    compare.add_argument(
        "--track-model",
        help="versioned JSON track model; adds diagnostic corner-region analysis",
    )
    compare.set_defaults(handler=_compare)

    reference = commands.add_parser(
        "reference", help="select the best eligible prior Time Trial lap and compare it"
    )
    reference.add_argument("target_attempt_key", help="target attempt from the laps command")
    reference.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    reference.set_defaults(handler=_reference)

    api = commands.add_parser("api", help="serve the local telemetry API and import inbox")
    api.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    api.add_argument("--recordings-root", default="recordings", help="folder containing local .f1ecap files")
    api.add_argument(
        "--control-token-file",
        default=str(Path("data") / ".f1-engineer-control-token"),
        help="server-only authorization token file for local import actions",
    )
    api.add_argument(
        "--udp-host",
        default="0.0.0.0",
        help="local address for app-managed F1 telemetry recording",
    )
    api.add_argument(
        "--udp-port",
        type=int,
        default=20777,
        help="local UDP telemetry port for app-managed recording",
    )
    api.add_argument(
        "--udp-queue-size",
        type=int,
        default=8192,
        help="bounded UDP receive queue size for app-managed recording",
    )
    api.add_argument("--port", type=int, default=8765, help="loopback port (default: 8765)")
    api.set_defaults(handler=_api)

    trajectory = commands.add_parser(
        "trajectory", help="export an observed world-space lap trajectory"
    )
    trajectory.add_argument("attempt_key", help="attempt key printed by the laps command")
    trajectory.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    trajectory.add_argument("--output", required=True, help="destination versioned JSON path")
    trajectory.add_argument("--overwrite", action="store_true", help="replace an existing output file")
    trajectory.set_defaults(handler=_trajectory)

    traces = commands.add_parser(
        "traces", help="export bounded standalone speed and control traces for an attempt"
    )
    traces.add_argument("attempt_key", help="attempt key printed by the laps command")
    traces.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    traces.add_argument("--output", required=True, help="destination versioned JSON path")
    traces.add_argument("--overwrite", action="store_true", help="replace an existing output file")
    traces.set_defaults(handler=_traces)

    regions = commands.add_parser(
        "regions", help="inspect one attempt against a packaged diagnostic distance-region model"
    )
    regions.add_argument("attempt_key", help="attempt key printed by the laps command")
    regions.add_argument("--database", default=str(DEFAULT_DATABASE), help="SQLite database path")
    regions.add_argument("--track-model-id", required=True, help="registered track model ID")
    regions.add_argument("--track-model-revision", type=int, required=True, help="registered model revision")
    regions.set_defaults(handler=_regions)
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
