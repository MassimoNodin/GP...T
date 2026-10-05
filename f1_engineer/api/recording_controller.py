from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..recording.capture import CaptureReader
from ..recording.service import (
    LIVE_CAR_DAMAGE_FRESHNESS_LIMIT_MS,
    LIVE_CAR_SETUP_FRESHNESS_LIMIT_MS,
    LIVE_MOTION_FRESHNESS_LIMIT_MS,
    LIVE_SESSION_CONDITIONS_FRESHNESS_LIMIT_MS,
    LIVE_CAR_STATUS_FRESHNESS_LIMIT_MS,
    LIVE_LAP_TIMING_FRESHNESS_LIMIT_MS,
    LIVE_TELEMETRY_FRESHNESS_LIMIT_MS,
    RecordingSnapshot,
    record_udp_capture,
)
from ..storage.import_jobs import list_recording_sources
from ..storage.recordings import (
    create_recording_job,
    get_current_recording_job,
    get_recording_job,
    get_recording_job_paths,
    list_active_recording_job_paths,
    recover_abandoned_recordings,
    update_recording_job,
)
from .import_controller import ImportController


logger = logging.getLogger(__name__)
_ACTIVE = {"starting", "recording", "stopping"}


class RecordingController:
    """Own one durable, mode-independent UDP capture through the local API."""

    def __init__(
        self,
        database_path: str | Path,
        recordings_root: str | Path,
        operation_controller: ImportController,
        *,
        host: str = "0.0.0.0",
        port: int = 20777,
        queue_size: int = 8192,
    ) -> None:
        if not host.strip():
            raise ValueError("recording bind host must not be empty")
        if not 1 <= port <= 65535:
            raise ValueError("recording UDP port must be between 1 and 65535")
        if queue_size < 1:
            raise ValueError("recording queue size must be at least 1")
        self.database_path = Path(database_path).expanduser().resolve()
        self.recordings_root = Path(recordings_root).expanduser().resolve()
        self.operation_controller = operation_controller
        self.host = host
        self.port = port
        self.queue_size = queue_size
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._recording_id: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._stop_requested = False
        self._cancel_sent = False
        self._snapshots: dict[str, dict[str, object]] = {}
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready and self.operation_controller.ready

    def start(self) -> None:
        if not self.operation_controller.ready:
            return
        self.recordings_root.mkdir(parents=True, exist_ok=True)
        self._reconcile_published_files()
        recover_abandoned_recordings(self.database_path)
        self._ready = True

    def close(self) -> None:
        with self._lock:
            recording_id = self._recording_id
            thread = self._thread
        if recording_id is not None:
            try:
                self.stop_recording(recording_id)
            except ValueError:
                logger.exception("Could not request graceful recording shutdown")
        if thread is not None:
            thread.join()
        self._ready = False

    def start_recording(self) -> dict[str, Any]:
        self._ensure_ready()
        with self._lock:
            self._reconcile_published_files()
            current = get_current_recording_job(self.database_path)
            if current is not None and current["status"] in _ACTIVE:
                if current["recording_id"] == self._recording_id:
                    return self._with_progress(current)
                raise ValueError("recording_controller_busy")
            if not self.operation_controller.reserve_operation("recording"):
                raise ValueError("another_local_operation_is_in_progress")

            recording_id = uuid.uuid4().hex
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            staging_name = f".f1e-recording-{recording_id}.part"
            final_name = f"f1e-{timestamp}-{recording_id[:8]}.f1ecap"
            try:
                job, created = create_recording_job(
                    self.database_path,
                    recording_id=recording_id,
                    staging_relative_path=staging_name,
                    final_relative_path=final_name,
                    bind_host=self.host,
                    bind_port=self.port,
                )
                if not created:
                    self.operation_controller.release_operation("recording")
                    return self._with_progress(job)
                staging_path = self._safe_path(staging_name)
                self._recording_id = recording_id
                self._stop_requested = False
                self._cancel_sent = False
                self._snapshots[recording_id] = {
                    "state": "starting",
                    "elapsed_ms": 0,
                    "received": 0,
                    "queued": 0,
                    "recorded": 0,
                    "queue_dropped": 0,
                    "socket_errors": 0,
                    "latest_context": None,
                    "live_telemetry": {
                        "status": "waiting",
                        "reason": None,
                        "age_ms": None,
                    },
                    "live_car_status": {
                        "status": "waiting",
                        "reason": None,
                        "age_ms": None,
                    },
                    "live_lap_timing": {
                        "status": "waiting",
                        "reason": None,
                        "age_ms": None,
                    },
                    "live_car_damage": {
                        "status": "waiting",
                        "reason": None,
                        "age_ms": None,
                        "observation_count": 0,
                    },
                    "live_car_setup": {
                        "status": "waiting",
                        "reason": None,
                        "age_ms": None,
                        "observation_count": 0,
                    },
                    "live_session_conditions": {
                        "status": "waiting",
                        "reason": None,
                        "age_ms": None,
                        "observation_count": 0,
                    },
                    "live_motion": {
                        "status": "waiting",
                        "reason": None,
                        "age_ms": None,
                        "observation_count": 0,
                    },
                }
                self._thread = threading.Thread(
                    target=self._run_recording,
                    args=(recording_id, staging_path),
                    name="f1-managed-udp-recording",
                    daemon=True,
                )
                self._thread.start()
            except Exception:
                self.operation_controller.release_operation("recording")
                update_recording_job(
                    self.database_path,
                    recording_id,
                    status="failed",
                    failure_reason="recording_start_failed",
                    finished=True,
                )
                self._recording_id = None
                self._snapshots.pop(recording_id, None)
                self._thread = None
                raise
        job = get_recording_job(self.database_path, recording_id)
        assert job is not None
        return self._with_progress(job)

    def stop_recording(self, recording_id: str) -> dict[str, Any]:
        self._ensure_ready()
        with self._lock:
            self._reconcile_published_files()
            # Re-read while holding the lifecycle lock. The recording worker uses
            # this same lock for terminal writes, so a late/repeated Stop cannot
            # move a completed job back to `stopping`.
            job = get_recording_job(self.database_path, recording_id)
            if job is None:
                raise ValueError("recording_unavailable")
            if job["status"] not in _ACTIVE:
                return job
            if self._recording_id != recording_id:
                raise ValueError("recording_unavailable")
            self._stop_requested = True
            update_recording_job(
                self.database_path, recording_id, status="stopping"
            )
            loop = self._loop
            task = self._task
            snapshot = self._snapshots.get(recording_id, {})
            if (
                loop is not None
                and task is not None
                and snapshot.get("state") != "starting"
                and not self._cancel_sent
            ):
                self._cancel_sent = True
                loop.call_soon_threadsafe(task.cancel)
        updated = get_recording_job(self.database_path, recording_id)
        assert updated is not None
        return self._with_progress(updated)

    def current(self) -> dict[str, Any] | None:
        self._ensure_ready()
        with self._lock:
            self._reconcile_published_files()
            job = get_current_recording_job(self.database_path)
        return self._with_progress(job) if job is not None else None

    def get(self, recording_id: str) -> dict[str, Any] | None:
        self._ensure_ready()
        job = get_recording_job(self.database_path, recording_id)
        return self._with_progress(job) if job is not None else None

    def _run_recording(self, recording_id: str, staging_path: Path) -> None:
        persisted_state = "starting"
        publication_committed = False
        final_path: Path | None = None

        def on_snapshot(snapshot: RecordingSnapshot) -> None:
            nonlocal persisted_state
            value = snapshot.to_dict()
            latest_context = value.get("latest_context")
            if isinstance(latest_context, dict) and latest_context.get("session_uid") is not None:
                latest_context = dict(latest_context)
                latest_context["session_uid"] = str(latest_context["session_uid"])
                value["latest_context"] = latest_context
            with self._lock:
                self._snapshots[recording_id] = value
                stop_requested = self._stop_requested
                if (
                    snapshot.state in {"recording", "stopping"}
                    and snapshot.state != persisted_state
                    and not (stop_requested and snapshot.state == "recording")
                ):
                    update_recording_job(
                        self.database_path,
                        recording_id,
                        status=snapshot.state,
                        starting=snapshot.state == "recording",
                    )
                    persisted_state = snapshot.state
                should_cancel = (
                    self._stop_requested
                    and snapshot.state == "recording"
                    and not self._cancel_sent
                )
                if should_cancel:
                    self._cancel_sent = True
                    loop = self._loop
                    task = self._task
                else:
                    loop = None
                    task = None
            if loop is not None and task is not None:
                loop.call_soon_threadsafe(task.cancel)

        async def run_capture():
            loop = asyncio.get_running_loop()
            task = asyncio.create_task(
                record_udp_capture(
                    staging_path,
                    host=self.host,
                    port=self.port,
                    queue_size=self.queue_size,
                    collect_inventory=False,
                    on_snapshot=on_snapshot,
                )
            )
            with self._lock:
                self._loop = loop
                self._task = task
            return await task

        try:
            result = asyncio.run(run_capture())
            if result.capture_status != "complete":
                raise RuntimeError("recording_incomplete")
            with self._lock:
                update_recording_job(
                    self.database_path, recording_id, status="stopping"
                )
                final_path = self._recording_final_path(recording_id)
                self._publish_without_replacement(staging_path, final_path)
                publication_committed = True
                update_recording_job(
                    self.database_path,
                    recording_id,
                    status="complete",
                    summary=result.summary,
                    finished=True,
                )
            try:
                list_recording_sources(self.database_path, self.recordings_root)
            except Exception:
                # The next inbox refresh discovers this already-published capture.
                logger.exception("Could not immediately register the finished capture")
        except Exception as exc:
            logger.exception("Managed UDP recording %s failed", recording_id)
            if publication_committed and final_path is not None:
                # A finalized inbox file is the publication commit. If the job
                # status write failed, leave it recoverable and retry from its
                # durable footer instead of claiming the recording was unpublished.
                try:
                    completion = self._read_complete_footer(final_path)
                except Exception:
                    completion = None
                    logger.exception(
                        "Could not read the published recording footer for %s",
                        recording_id,
                    )
                if completion is not None:
                    try:
                        with self._lock:
                            update_recording_job(
                                self.database_path,
                                recording_id,
                                status="complete",
                                summary={
                                    key: value
                                    for key, value in completion.items()
                                    if key != "status"
                                },
                                finished=True,
                            )
                        return
                    except Exception:
                        logger.exception(
                            "Published recording %s awaits completion-state reconciliation",
                            recording_id,
                        )
                else:
                    logger.error(
                        "Published recording %s has no valid completion footer",
                        recording_id,
                    )
                # Keep its existing `stopping` state active. current() and a
                # later Start both retry reconciliation; restart also recovers it.
                return
            failure_reason = (
                "capture_publication_collision"
                if isinstance(exc, FileExistsError)
                else "recording_incomplete"
                if isinstance(exc, RuntimeError) and str(exc) == "recording_incomplete"
                else "recording_failed"
            )
            snapshot = self._snapshots.get(recording_id)
            with self._lock:
                update_recording_job(
                    self.database_path,
                    recording_id,
                    status="failed",
                    summary=snapshot,
                    failure_reason=failure_reason,
                    finished=True,
                )
        finally:
            with self._lock:
                self._loop = None
                self._task = None
                self._recording_id = None
                self._cancel_sent = False
                self._snapshots.pop(recording_id, None)
                self._thread = None
            self.operation_controller.release_operation("recording")

    def _publish_without_replacement(self, staging_path: Path, final_path: Path) -> None:
        self._safe_path(staging_path.name)
        self._safe_path(final_path.name)
        if staging_path.is_symlink() or not staging_path.is_file():
            raise OSError("recording staging file is unavailable")
        # A same-directory hard link is atomic and fails if the destination exists.
        # Successful linking is the publication commit. The private staging name
        # is outside the inbox scan, so cleanup failure must not undo that commit.
        os.link(staging_path, final_path)
        try:
            staging_path.unlink()
        except OSError:
            logger.warning(
                "Capture was published, but staging cleanup failed for %s",
                staging_path,
                exc_info=True,
            )

    def _recording_final_path(self, recording_id: str) -> Path:
        paths = get_recording_job_paths(self.database_path, recording_id)
        if paths is None:
            raise ValueError("recording_unavailable")
        return self._safe_path(paths["final_relative_path"])

    def _safe_path(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("recording_path_invalid")
        candidate = self.recordings_root / relative
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.recordings_root):
            raise ValueError("recording_path_invalid")
        if candidate.parent.resolve(strict=False) != self.recordings_root:
            raise ValueError("recording_path_invalid")
        return candidate

    def _reconcile_published_files(self) -> None:
        for row in list_active_recording_job_paths(self.database_path):
            if row["recording_id"] == self._recording_id:
                # A live worker has not committed its publication yet. An
                # unrelated file at the reserved destination is a collision,
                # not evidence that this recording completed.
                continue
            try:
                final_path = self._safe_path(row["final_relative_path"])
                completion = self._read_complete_footer(final_path)
                if completion is not None:
                    update_recording_job(
                        self.database_path,
                        row["recording_id"],
                        status="complete",
                        summary={
                            key: value
                            for key, value in completion.items()
                            if key != "status"
                        },
                        finished=True,
                    )
                    continue
            except Exception:
                logger.exception(
                    "Could not reconcile recording %s",
                    row["recording_id"],
                )
        # Unpublished staging files remain untouched for inspection.

    @staticmethod
    def _read_complete_footer(path: Path) -> dict[str, Any] | None:
        if path.is_symlink() or not path.is_file():
            return None
        with CaptureReader(path) as reader:
            for _ in reader:
                pass
            if reader.complete and reader.completion is not None:
                return reader.completion
        return None

    def _with_progress(self, job: dict[str, Any]) -> dict[str, Any]:
        result = dict(job)
        with self._lock:
            progress = self._snapshots.get(str(job["recording_id"]))
        if progress is None or job["status"] not in _ACTIVE:
            result["progress"] = None
            return result

        progress_response = dict(progress)
        freshness_limits = {
            "live_telemetry": LIVE_TELEMETRY_FRESHNESS_LIMIT_MS,
            "live_car_status": LIVE_CAR_STATUS_FRESHNESS_LIMIT_MS,
            "live_lap_timing": LIVE_LAP_TIMING_FRESHNESS_LIMIT_MS,
            "live_car_damage": LIVE_CAR_DAMAGE_FRESHNESS_LIMIT_MS,
            "live_car_setup": LIVE_CAR_SETUP_FRESHNESS_LIMIT_MS,
            "live_session_conditions": LIVE_SESSION_CONDITIONS_FRESHNESS_LIMIT_MS,
            "live_motion": LIVE_MOTION_FRESHNESS_LIMIT_MS,
        }
        for name, freshness_limit_ms in freshness_limits.items():
            live = progress.get(name)
            if not isinstance(live, dict):
                continue
            live_response = dict(live)
            observed_ns = live_response.pop("_observed_monotonic_ns", None)
            age_ms = (
                max(0, (time.monotonic_ns() - observed_ns) // 1_000_000)
                if isinstance(observed_ns, int) and not isinstance(observed_ns, bool)
                else None
            )
            live_response["age_ms"] = age_ms
            if (
                age_ms is not None
                and age_ms > freshness_limit_ms
                and live_response.get("status") in {"fresh", "unavailable"}
            ):
                live_response["status"] = "stale"
            progress_response[name] = live_response
        result["progress"] = progress_response
        return result

    def _ensure_ready(self) -> None:
        if not self.ready:
            raise ValueError("recording_controller_unavailable")
