from __future__ import annotations

import asyncio
import copy
import concurrent.futures
import logging
import math
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..recording.service import (
    LIVE_CAR_DAMAGE_FRESHNESS_LIMIT_MS,
    LIVE_CAR_STATUS_FRESHNESS_LIMIT_MS,
    LIVE_LAP_TIMING_FRESHNESS_LIMIT_MS,
    LIVE_TELEMETRY_FRESHNESS_LIMIT_MS,
    AcquisitionObserver,
)
from ..storage.import_jobs import resolve_recording_source
from ..udp.source import ReplaySource
from .import_controller import ImportController


logger = logging.getLogger(__name__)
ALLOWED_REPLAY_SPEEDS = (0.5, 1.0, 2.0, 4.0)
ACTIVE_REPLAY_STATES = {
    "starting",
    "playing",
    "pausing",
    "paused",
    "resuming",
    "stepping",
    "stopping",
}


class ReplayController:
    """Own one ephemeral diagnostic replay through the local API."""

    def __init__(
        self,
        database_path: str | Path,
        recordings_root: str | Path,
        operation_controller: ImportController,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.recordings_root = Path(recordings_root).expanduser().resolve()
        self.operation_controller = operation_controller
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._source: ReplaySource | None = None
        self._playback_id: str | None = None
        self._snapshot: dict[str, Any] | None = None
        self._stop_requested = False
        self._cancel_sent = False
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready and self.operation_controller.ready

    def start(self) -> None:
        self._ready = self.operation_controller.ready

    def close(self) -> None:
        with self._lock:
            snapshot = copy.deepcopy(self._snapshot)
            thread = self._thread
        if snapshot is not None and snapshot.get("state") in ACTIVE_REPLAY_STATES:
            playback_id = snapshot.get("playback_id")
            if isinstance(playback_id, str):
                try:
                    self.stop_replay(playback_id)
                except ValueError:
                    logger.exception("Could not request diagnostic replay shutdown")
        if thread is not None:
            thread.join()
        self._ready = False

    def start_replay(self, capture_id: str, speed: float = 1.0) -> dict[str, Any]:
        self._ensure_ready()
        if (
            isinstance(speed, bool)
            or not isinstance(speed, (int, float))
            or not math.isfinite(speed)
            or float(speed) not in ALLOWED_REPLAY_SPEEDS
        ):
            raise ValueError("replay_speed_unsupported")
        speed = float(speed)
        source_path = resolve_recording_source(
            self.database_path, self.recordings_root, capture_id
        )
        if source_path is None:
            raise ValueError("capture_id_unavailable")
        try:
            source_identity = _file_identity(source_path)
        except OSError as exc:
            raise ValueError("capture_id_unavailable") from exc

        with self._lock:
            if self._snapshot is not None and self._snapshot.get("state") in ACTIVE_REPLAY_STATES:
                if (
                    self._snapshot.get("capture_id") == capture_id
                    and self._snapshot.get("speed") == speed
                ):
                    return self.current() or copy.deepcopy(self._snapshot)
                raise ValueError("another_local_operation_is_in_progress")
            if not self.operation_controller.reserve_operation("replay"):
                raise ValueError("another_local_operation_is_in_progress")

            playback_id = uuid.uuid4().hex
            self._playback_id = playback_id
            self._stop_requested = False
            self._cancel_sent = False
            self._snapshot = {
                "source_kind": "replay",
                "playback_id": playback_id,
                "capture_id": capture_id,
                "capture_name": source_path.name,
                "speed": speed,
                "state": "starting",
                "elapsed_ms": 0,
                "datagrams_delivered": 0,
                "capture_complete": None,
                "capture_completion": None,
                "source_stable": None,
                "latest_context": None,
                "live_telemetry": _waiting_monitor(),
                "live_car_status": _waiting_monitor(),
                "live_lap_timing": _waiting_monitor(),
                "live_car_damage": {
                    **_waiting_monitor(),
                    "observation_count": 0,
                },
                "failure_reason": None,
                "progress_updated_monotonic_ns": time.monotonic_ns(),
            }
            self._thread = threading.Thread(
                target=self._run_replay,
                args=(playback_id, capture_id, source_path, source_identity, speed),
                name="f1-managed-diagnostic-replay",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._thread = None
                self._playback_id = None
                self._snapshot = None
                self.operation_controller.release_operation("replay")
                raise
        return self.current() or {}

    def stop_replay(self, playback_id: str) -> dict[str, Any]:
        self._ensure_ready()
        with self._lock:
            if playback_id != self._playback_id or self._snapshot is None:
                raise ValueError("playback_unavailable")
            state = self._snapshot.get("state")
            if state not in ACTIVE_REPLAY_STATES:
                return self.current() or copy.deepcopy(self._snapshot)
            self._stop_requested = True
            self._snapshot["state"] = "stopping"
            loop = self._loop
            task = self._task
            if loop is not None and task is not None and not self._cancel_sent:
                self._cancel_sent = True
                loop.call_soon_threadsafe(task.cancel)
        return self.current() or {}

    def pause_replay(self, playback_id: str) -> dict[str, Any]:
        return self._control_replay(playback_id, "pause")

    def resume_replay(self, playback_id: str) -> dict[str, Any]:
        return self._control_replay(playback_id, "resume")

    def step_replay(self, playback_id: str) -> dict[str, Any]:
        return self._control_replay(playback_id, "step")

    def _control_replay(self, playback_id: str, action: str) -> dict[str, Any]:
        self._ensure_ready()
        with self._lock:
            if playback_id != self._playback_id or self._snapshot is None:
                raise ValueError("playback_unavailable")
            state = self._snapshot.get("state")
            if state not in ACTIVE_REPLAY_STATES:
                raise ValueError("playback_unavailable")

            if action == "pause":
                if state in {"paused", "pausing"}:
                    return self.current() or {}
                if state != "playing":
                    raise ValueError("replay_not_playing")
                transitional_state = "pausing"
            elif action == "resume":
                if state in {"playing", "resuming"}:
                    return self.current() or {}
                if state != "paused":
                    raise ValueError("replay_not_paused")
                transitional_state = "resuming"
            elif action == "step":
                if state == "stepping":
                    raise ValueError("replay_step_in_progress")
                if state != "paused":
                    raise ValueError("replay_not_paused")
                transitional_state = "stepping"
            else:
                raise ValueError("replay_control_unsupported")

            loop = self._loop
            source = self._source
            if loop is None or source is None:
                raise ValueError("replay_not_ready")
            self._snapshot["state"] = transitional_state

        async def apply_control() -> None:
            with self._lock:
                if self._playback_id != playback_id or self._stop_requested:
                    raise ValueError("playback_unavailable")
            await getattr(source, action)()

        control_coro = apply_control()
        try:
            future = asyncio.run_coroutine_threadsafe(control_coro, loop)
        except RuntimeError as exc:
            control_coro.close()
            with self._lock:
                if (
                    self._playback_id == playback_id
                    and self._snapshot is not None
                    and self._snapshot.get("state") == transitional_state
                ):
                    self._snapshot["state"] = state
            raise ValueError("replay_controller_unavailable") from exc
        try:
            future.result(timeout=2.0)
        except ValueError:
            with self._lock:
                if (
                    self._playback_id == playback_id
                    and self._snapshot is not None
                    and self._snapshot.get("state") == transitional_state
                ):
                    self._snapshot["state"] = state
            raise
        except TimeoutError as exc:
            future.cancel()
            with self._lock:
                if (
                    self._playback_id == playback_id
                    and self._snapshot is not None
                    and self._snapshot.get("state") == transitional_state
                ):
                    self._snapshot["state"] = state
            raise ValueError("replay_controller_unavailable") from exc
        except concurrent.futures.CancelledError as exc:
            with self._lock:
                if (
                    self._playback_id == playback_id
                    and self._snapshot is not None
                    and self._snapshot.get("state") == transitional_state
                ):
                    self._snapshot["state"] = state
            raise ValueError("replay_controller_unavailable") from exc
        return self.current() or {}

    def current(self) -> dict[str, Any] | None:
        self._ensure_ready()
        with self._lock:
            result = copy.deepcopy(self._snapshot)
        if result is None:
            return None
        updated_at_ns = result.pop("progress_updated_monotonic_ns", None)
        if isinstance(updated_at_ns, int):
            started_at_ns = result.pop("started_monotonic_ns", updated_at_ns)
            result["elapsed_ms"] = max(
                0, (time.monotonic_ns() - started_at_ns) // 1_000_000
            )
        else:
            result.pop("started_monotonic_ns", None)

        freshness_limits = {
            "live_telemetry": LIVE_TELEMETRY_FRESHNESS_LIMIT_MS,
            "live_car_status": LIVE_CAR_STATUS_FRESHNESS_LIMIT_MS,
            "live_lap_timing": LIVE_LAP_TIMING_FRESHNESS_LIMIT_MS,
            "live_car_damage": LIVE_CAR_DAMAGE_FRESHNESS_LIMIT_MS,
        }
        for name, limit_ms in freshness_limits.items():
            monitor = result.get(name)
            if not isinstance(monitor, dict):
                continue
            observed_ns = monitor.pop("_observed_monotonic_ns", None)
            age_ms = (
                max(0, (time.monotonic_ns() - observed_ns) // 1_000_000)
                if isinstance(observed_ns, int) and not isinstance(observed_ns, bool)
                else None
            )
            monitor["age_ms"] = age_ms
            if (
                age_ms is not None
                and age_ms > limit_ms
                and monitor.get("status") in {"fresh", "unavailable"}
            ):
                monitor["status"] = "stale"
        if result.get("state") in {"stopped", "completed", "failed"}:
            result["live_car_damage"] = {
                "status": "unavailable",
                "reason": "operation_ended",
                "age_ms": None,
                "observation_count": 0,
            }
        result.pop("started_monotonic_ns", None)
        return result

    def _run_replay(
        self,
        playback_id: str,
        capture_id: str,
        source_path: Path,
        expected_identity: tuple[int, int, int, int],
        speed: float,
    ) -> None:
        observer = AcquisitionObserver()
        started_ns = time.monotonic_ns()
        delivered = 0
        latest_context: dict[str, object] | None = None
        state = "failed"
        failure_reason: str | None = None
        source_stable: bool | None = None
        eof = False
        last_publish_ns = 0
        source: ReplaySource | None = None

        def publish(current_state: str) -> None:
            nonlocal latest_context
            if latest_context is not None:
                latest_context = dict(latest_context)
                if latest_context.get("session_uid") is not None:
                    latest_context["session_uid"] = str(latest_context["session_uid"])
            snapshot = {
                "source_kind": "replay",
                "playback_id": playback_id,
                "capture_id": capture_id,
                "capture_name": source_path.name,
                "speed": speed,
                "state": current_state,
                "elapsed_ms": max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
                "datagrams_delivered": delivered,
                "capture_complete": source.complete if eof and source is not None else None,
                "capture_completion": (
                    copy.deepcopy(source.completion)
                    if eof and source is not None
                    else None
                ),
                "source_stable": source_stable,
                "latest_context": latest_context,
                "live_telemetry": observer.live_telemetry_snapshot(),
                "live_car_status": observer.live_car_status_snapshot(),
                "live_lap_timing": observer.live_lap_timing_snapshot(),
                "live_car_damage": observer.live_car_damage_snapshot(),
                "failure_reason": failure_reason,
                "started_monotonic_ns": started_ns,
                "progress_updated_monotonic_ns": time.monotonic_ns(),
            }
            with self._lock:
                if self._playback_id == playback_id:
                    if (
                        self._stop_requested
                        and current_state not in {"stopped", "failed"}
                    ):
                        if current_state == "completed":
                            snapshot["state"] = "stopped"
                        else:
                            return
                    self._snapshot = snapshot

        def on_control_state(current_state: str) -> None:
            nonlocal state
            state = current_state
            publish(current_state)

        async def run() -> None:
            nonlocal delivered, latest_context, state, failure_reason, source_stable, eof, last_publish_ns, source
            loop = asyncio.get_running_loop()
            task = asyncio.current_task()
            stop_before_start = False
            with self._lock:
                if self._playback_id == playback_id:
                    self._loop = loop
                    self._task = task
                    stop_before_start = self._stop_requested
            try:
                if stop_before_start:
                    observer.finish()
                    state = "stopped"
                    return
                if _file_identity(source_path) != expected_identity:
                    raise ValueError("capture_changed_before_replay")
                source = ReplaySource(
                    source_path,
                    speed=speed,
                    start_paused=True,
                    on_control_state=on_control_state,
                )
                with self._lock:
                    if self._playback_id == playback_id:
                        self._source = source
                iterator = source.packets()
                try:
                    async for raw in iterator:
                        delivery_ns = time.monotonic_ns()
                        observer.process(raw, delivery_monotonic_ns=delivery_ns)
                        delivered += 1
                        latest_context = observer.latest_context
                        if delivered == 1 or delivered % 256 == 0 or delivery_ns - last_publish_ns >= 100_000_000:
                            publish(state)
                            last_publish_ns = delivery_ns
                        # Provide cancellation points even when the requested
                        # playback speed is faster than packet processing.
                        if delivered % 256 == 0:
                            await asyncio.sleep(0)
                    eof = True
                finally:
                    await iterator.aclose()
                observer.finish()
                latest_context = observer.latest_context
                if _file_identity(source_path) != expected_identity:
                    source_stable = False
                    failure_reason = "capture_changed_during_replay"
                    state = "failed"
                else:
                    source_stable = True
                    state = "completed"
            except asyncio.CancelledError:
                observer.finish()
                latest_context = observer.latest_context
                try:
                    source_stable = _file_identity(source_path) == expected_identity
                except OSError:
                    source_stable = False
                if not source_stable:
                    state = "failed"
                    failure_reason = "capture_changed_during_replay"
                else:
                    state = "stopped"
            except Exception as exc:
                observer.finish()
                latest_context = observer.latest_context
                try:
                    source_stable = _file_identity(source_path) == expected_identity
                except OSError:
                    source_stable = False
                if isinstance(exc, ValueError) and str(exc) == "capture_changed_before_replay":
                    failure_reason = str(exc)
                elif not source_stable:
                    failure_reason = "capture_changed_during_replay"
                else:
                    failure_reason = "capture_read_failed"
                state = "failed"
                logger.info("Diagnostic replay %s ended: %s", playback_id, exc)
            finally:
                publish(state)

        try:
            asyncio.run(run())
        finally:
            with self._lock:
                if self._playback_id == playback_id:
                    self._loop = None
                    self._task = None
                    self._source = None
                    self._thread = None
                    self._stop_requested = False
                    self._cancel_sent = False
            self.operation_controller.release_operation("replay")

    def _ensure_ready(self) -> None:
        if not self.ready:
            raise ValueError("replay_controller_unavailable")


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    if path.is_symlink() or not path.is_file():
        raise OSError("capture source is no longer a regular file")
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _waiting_monitor() -> dict[str, object]:
    return {"status": "waiting", "reason": None, "age_ms": None}
