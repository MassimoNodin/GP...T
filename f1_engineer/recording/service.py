from __future__ import annotations

import asyncio
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..errors import ProtocolError
from ..pipeline import PipelineResult, TelemetryPipeline
from ..sessions.context import SessionContext
from ..sessions.manager import SessionTracker
from ..telemetry.frames import FrameAssembler
from ..udp.decoder import PacketDecoder
from ..udp.models import PacketFrame, RawDatagram
from ..udp.session_context import SessionContextDecoder
from ..udp.source import UDPSource
from .capture import CaptureWriter


@dataclass(frozen=True, slots=True)
class RecordingSnapshot:
    state: str
    elapsed_ms: int
    received: int
    queued: int
    recorded: int
    queue_dropped: int
    socket_errors: int
    latest_context: dict[str, object] | None

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "elapsed_ms": self.elapsed_ms,
            "received": self.received,
            "queued": self.queued,
            "recorded": self.recorded,
            "queue_dropped": self.queue_dropped,
            "socket_errors": self.socket_errors,
            "latest_context": self.latest_context,
        }


@dataclass(frozen=True, slots=True)
class RecordingResult:
    output: Path
    summary: dict[str, int]
    session_contexts: tuple[dict[str, object], ...]
    lap_attempts: tuple[dict[str, object], ...]
    capture_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "capture": str(self.output),
            "summary": dict(self.summary),
            "session_contexts": list(self.session_contexts),
            "lap_attempts": list(self.lap_attempts),
        }


class _AcquisitionObserver:
    """Bounded packet and session observer for long-running managed captures."""

    def __init__(self) -> None:
        self.decoder = PacketDecoder()
        self.session_context_decoder = SessionContextDecoder()
        self.sessions = SessionTracker(retain_context_history=False)
        self.frames = FrameAssembler()
        self.counts: Counter[str] = Counter(completed_frames=0)
        self.latest_context: dict[str, object] | None = None
        self._finished = False

    def process(self, raw: RawDatagram) -> None:
        try:
            packet = self.decoder.decode(raw)
        except ProtocolError:
            self.counts["unrecognized_or_malformed"] += 1
            return

        self.counts[f"format_{packet.packet_format.value}"] += 1
        session_events = self.sessions.observe(packet)
        context_result = self.session_context_decoder.decode(packet)
        if context_result.error is not None:
            self.counts["session_context_decode_errors"] += 1
        if context_result.context is not None and self.sessions.update_context(
            context_result.context, packet.header.overall_frame_identifier
        ):
            self.counts["session_context_updates"] += 1
        if any(
            event.kind in {"session_started", "session_context_invalidated"}
            for event in session_events
        ):
            self.latest_context = None
        if context_result.context is not None:
            current = self.sessions.current_context
            self.latest_context = current.to_dict() if current is not None else None

        for event in session_events:
            if event.kind == "session_ended":
                self._count_completed(self.frames.retire_session(event.session_uid))
            elif event.kind == "session_context_invalidated":
                self.counts["session_context_invalidations"] += 1
                self._count_completed(self.frames.flush_session(event.session_uid))

        obsolete_format = (
            packet.header.session_uid == self.sessions.current_session_uid
            and packet.packet_format is not self.sessions.current_packet_format
        )
        if self.sessions.is_retired(packet.header.session_uid) or obsolete_format:
            self.frames.late_packets_ignored += 1
            return
        self._count_completed(self.frames.add(packet))

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        frames = self.frames.flush()
        self._count_completed(frames)
        self.counts["open_frames_flushed"] = len(frames)
        self.counts["duplicate_envelopes_ignored"] = self.frames.duplicates_ignored
        self.counts["late_packets_ignored"] = self.frames.late_packets_ignored
        self.counts["frame_overflow_packets_dropped"] = (
            self.frames.overflow_packets_dropped
        )

    def _count_completed(self, frames: tuple[object, ...]) -> None:
        self.counts["completed_frames"] += len(frames)


async def record_udp_capture(
    output: str | Path,
    *,
    host: str = "0.0.0.0",
    port: int = 20777,
    queue_size: int = 8192,
    duration: float | None = None,
    overwrite: bool = False,
    collect_inventory: bool = True,
    on_snapshot: Callable[[RecordingSnapshot], None] | None = None,
    on_event: Callable[[dict[str, object]], None] | None = None,
) -> RecordingResult:
    """Capture raw UDP first, then process a copy through the shared pipeline."""
    output_path = Path(output)
    source = UDPSource(host=host, port=port, queue_size=queue_size)
    pipeline = TelemetryPipeline() if collect_inventory else None
    observer = None if collect_inventory else _AcquisitionObserver()
    counts: Counter[str] = Counter(completed_frames=0)
    session_contexts: dict[int, SessionContext] = {}
    latest_context: dict[str, object] | None = None
    started = time.monotonic()
    writer: CaptureWriter | None = None
    writer_executor: ThreadPoolExecutor | None = None
    operation_error: Exception | None = None
    capture_status: str | None = None
    state = "starting"
    loop = asyncio.get_running_loop()
    deadline = started + duration if duration is not None else None

    def snapshot() -> RecordingSnapshot:
        return RecordingSnapshot(
            state=state,
            elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
            received=source.stats.received,
            queued=source.stats.queued,
            recorded=counts["recorded"],
            queue_dropped=source.stats.dropped,
            socket_errors=source.stats.socket_errors,
            latest_context=latest_context,
        )

    def notify_snapshot() -> None:
        if on_snapshot is not None:
            try:
                on_snapshot(snapshot())
            except Exception:
                # Status reporting must never interrupt raw capture.
                pass

    def notify_event(event: dict[str, object]) -> None:
        if on_event is not None:
            try:
                on_event(event)
            except Exception:
                pass

    def process(raw: RawDatagram) -> None:
        nonlocal latest_context
        if observer is not None:
            observer.process(raw)
            latest_context = observer.latest_context
            return
        assert pipeline is not None
        try:
            result = pipeline.process(raw)
        except ProtocolError:
            counts["unrecognized_or_malformed"] += 1
            return
        counts[f"format_{result.packet.packet_format.value}"] += 1
        counts["completed_frames"] += len(result.completed_frames)
        if any(
            event.kind in {"session_started", "session_context_invalidated"}
            for event in result.session_events
        ):
            latest_context = None
        _report_session_events(result, notify_event)
        _collect_session_context(counts, session_contexts, result)
        if result.session_context is not None:
            latest_context = result.session_context.to_dict()
            notify_event(
                {
                    "event": "session_context_updated",
                    "context": result.session_context.to_dict(),
                }
            )

    async def persist(raw: RawDatagram) -> None:
        assert writer is not None and writer_executor is not None
        pending_write = loop.run_in_executor(writer_executor, writer.write, raw)
        try:
            await asyncio.shield(pending_write)
        except asyncio.CancelledError:
            # Do not lose a datagram already removed from the receive queue.
            await pending_write
            counts["recorded"] += 1
            notify_snapshot()
            raise
        counts["recorded"] += 1
        notify_snapshot()

    try:
        await source.open()
        writer = CaptureWriter(
            output_path,
            metadata={
                "application": "f1-race-engineer",
                "application_version": __version__,
                "bind_host": host,
                "bind_port": port,
            },
            overwrite=overwrite,
        )
        writer_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="f1-capture"
        )
        state = "recording"
        notify_snapshot()
        packet_iterator = source.packets().__aiter__()
        while True:
            try:
                if deadline is None:
                    raw = await packet_iterator.__anext__()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    raw = await asyncio.wait_for(
                        packet_iterator.__anext__(), timeout=remaining
                    )
            except (StopAsyncIteration, asyncio.TimeoutError):
                break

            # Persist before decoding so malformed or unsupported datagrams survive.
            await persist(raw)
            process(raw)
            notify_snapshot()
    except asyncio.CancelledError:
        state = "stopping"
        notify_snapshot()
    except Exception as exc:
        operation_error = exc
        state = "stopping"
        notify_snapshot()
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
                process(raw)
                notify_snapshot()
            counts["unpersisted_on_shutdown"] = max(
                0, source.stats.queued - counts["recorded"]
            )
            counts.setdefault("recorded", 0)
            counts["received"] = source.stats.received
            counts["queue_dropped"] = source.stats.dropped
            counts["socket_errors"] = source.stats.socket_errors
            if pipeline is not None:
                counts["open_frames_flushed"] = len(pipeline.finish())
                _add_frame_stats(counts, pipeline)
                _add_lap_stats(counts, pipeline)
            else:
                assert observer is not None
                observer.finish()
                _copy_counts(counts, observer.counts)
            counts["elapsed_ms"] = max(0, int((time.monotonic() - started) * 1000))
            capture_status = (
                "complete"
                if operation_error is None
                and counts["unpersisted_on_shutdown"] == 0
                else "incomplete"
            )
            footer = {"status": capture_status, **dict(counts)}
            try:
                pending_close = loop.run_in_executor(
                    writer_executor, writer.close, footer
                )
                await asyncio.shield(pending_close)
            except Exception as exc:
                operation_error = operation_error or exc
            finally:
                writer_executor.shutdown(wait=True)
        elif writer is not None:
            try:
                writer.close(
                    {"status": "incomplete", "reason": "capture worker unavailable"}
                )
            except Exception as exc:
                operation_error = operation_error or exc

    counts["received"] = source.stats.received
    counts.setdefault("recorded", 0)
    counts.setdefault("queue_dropped", source.stats.dropped)
    counts.setdefault("socket_errors", source.stats.socket_errors)
    counts.setdefault("unpersisted_on_shutdown", 0)
    if pipeline is not None:
        counts.setdefault("open_frames_flushed", len(pipeline.finish()))
        _add_frame_stats(counts, pipeline)
        _add_lap_stats(counts, pipeline)
    elif observer is not None:
        observer.finish()
        _copy_counts(counts, observer.counts)
    counts.setdefault("elapsed_ms", max(0, int((time.monotonic() - started) * 1000)))
    state = (
        "complete"
        if operation_error is None and capture_status == "complete"
        else "failed"
    )
    notify_snapshot()
    if operation_error is not None:
        raise operation_error
    return RecordingResult(
        output=output_path,
        summary=dict(counts),
        session_contexts=tuple(
            session_contexts[uid].to_dict() for uid in sorted(session_contexts)
        ),
        lap_attempts=(
            tuple(attempt.to_dict() for attempt in pipeline.laps.attempts)
            if pipeline is not None
            else ()
        ),
        capture_status=capture_status or "incomplete",
    )


def _report_session_events(
    result: PipelineResult, callback: Callable[[dict[str, object]], None]
) -> None:
    for event in result.session_events:
        callback(
            {
                "event": event.kind,
                "session_uid": event.session_uid,
                "previous_session_uid": event.previous_session_uid,
            }
        )


def _copy_counts(destination: Counter[str], source: Counter[str]) -> None:
    for key, value in source.items():
        destination[key] = value


def _collect_session_context(
    counts: Counter[str],
    contexts: dict[int, SessionContext],
    result: PipelineResult,
) -> None:
    for event in result.session_events:
        if event.kind == "session_context_invalidated":
            counts["session_context_invalidations"] += 1
            contexts.pop(event.session_uid, None)
    if result.session_context_error is not None:
        counts["session_context_decode_errors"] += 1
    if result.session_context is not None:
        counts["session_context_updates"] += 1
        contexts[result.session_context.session_uid] = result.session_context


def _add_frame_stats(counts: Counter[str], pipeline: TelemetryPipeline) -> None:
    counts["duplicate_envelopes_ignored"] = pipeline.frames.duplicates_ignored
    counts["late_packets_ignored"] = pipeline.frames.late_packets_ignored
    counts["frame_overflow_packets_dropped"] = pipeline.frames.overflow_packets_dropped


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
