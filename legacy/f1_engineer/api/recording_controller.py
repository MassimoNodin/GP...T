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
    LIVE_SESSION_HISTORY_FRESHNESS_LIMIT_MS,
    LIVE_SESSION_CONDITIONS_FRESHNESS_LIMIT_MS,
    LIVE_CAR_STATUS_FRESHNESS_LIMIT_MS,
    LIVE_LAP_TIMING_FRESHNESS_LIMIT_MS,
    LIVE_TELEMETRY_FRESHNESS_LIMIT_MS,
    RecordingSnapshot,
    record_udp_capture,
)
from ..storage.import_jobs import list_recording_sources
from ..storage.recordings import (
    create_recording_group,
    create_recording_group_segment,
    get_current_recording_group,
    get_current_recording_job,
    get_recording_group,
    get_recording_job,
    get_recording_job_paths,
    list_active_recording_job_paths,
    list_recording_group_events,
    list_recording_group_segments,
    mark_recording_segment_started,
    recover_abandoned_recordings,
    recover_abandoned_recording_groups,
    request_recording_group_transition,
    settle_recording_segment_complete,
    settle_recording_segment_failed,
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
        self._group_id: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._stop_requested = False
        self._cancel_sent = False
        self._snapshots: dict[str, dict[str, object]] = {}
        self._ready = False
        self._closing = threading.Event()
        self._settlement_thread: threading.Thread | None = None
        self._settlement_pending: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return self._ready and self.operation_controller.ready

    def start(self) -> None:
        if not self.operation_controller.ready:
            return
        self.recordings_root.mkdir(parents=True, exist_ok=True)
        self._reconcile_published_files()
        recover_abandoned_recording_groups(self.database_path)
        recover_abandoned_recordings(self.database_path)
        self._ready = True

    def close(self) -> None:
        with self._lock:
            group_id = self._group_id
            thread = self._thread
        self._closing.set()
        if group_id is not None:
            try:
                self.stop_recording_group(group_id)
            except ValueError:
                logger.exception("Could not request graceful recording-group shutdown")
        if thread is not None:
            thread.join()
        settlement_thread = self._settlement_thread
        if settlement_thread is not None:
            settlement_thread.join(timeout=2)
        self._ready = False

    def start_recording(self) -> dict[str, Any]:
        group = self.start_recording_group()
        recording_id = group.get("current_recording_id") or group.get("last_recording_id")
        job = get_recording_job(self.database_path, recording_id) if recording_id else None
        if job is None:
            raise ValueError("recording_unavailable")
        return self._with_progress(job)

    def start_recording_group(self) -> dict[str, Any]:
        self._ensure_ready()
        with self._lock:
            if self._group_id is not None:
                current_group = get_recording_group(self.database_path, self._group_id)
                if current_group and current_group["status"] in {
                    "starting", "recording", "pausing", "paused", "resuming", "stopping"
                }:
                    if current_group["status"] in {"starting", "recording"}:
                        return current_group
                    raise ValueError("recording_group_transition_conflict")
            current = get_current_recording_group(self.database_path)
            if current is not None and current["status"] in {
                "starting", "recording", "pausing", "paused", "resuming", "stopping"
            }:
                raise ValueError("recording_controller_busy")
            if not self.operation_controller.reserve_operation("recording"):
                raise ValueError("another_local_operation_is_in_progress")

            group_id = uuid.uuid4().hex
            recording_id = uuid.uuid4().hex
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            staging_name = f".f1e-recording-{recording_id}.part"
            final_name = f"f1e-{timestamp}-{recording_id[:8]}.f1ecap"
            created = False
            try:
                group, job, created = create_recording_group(
                    self.database_path,
                    group_id=group_id,
                    recording_id=recording_id,
                    staging_relative_path=staging_name,
                    final_relative_path=final_name,
                    bind_host=self.host,
                    bind_port=self.port,
                )
                if not created:
                    self.operation_controller.release_operation("recording")
                    raise ValueError("recording_controller_busy")
                self._group_id = group_id
                self._recording_id = recording_id
                self._stop_requested = False
                self._cancel_sent = False
                self._launch_segment_worker(group_id, job)
            except Exception:
                if created:
                    self._settle_unstarted_segment_failure(
                        group_id,
                        recording_id,
                        int(job["segment_ordinal"]),
                        "recording_start_failed",
                    )
                else:
                    self.operation_controller.release_operation("recording")
                    self._recording_id = None
                    self._group_id = None
                    self._snapshots.pop(recording_id, None)
                    self._thread = None
                raise
        return get_recording_group(self.database_path, group_id) or group

    def current_group(self) -> dict[str, Any] | None:
        self._ensure_ready()
        return get_current_recording_group(self.database_path)

    def group(self, group_id: str) -> dict[str, Any] | None:
        self._ensure_ready()
        return get_recording_group(self.database_path, group_id)

    def group_segments(
        self, group_id: str, *, limit: int, offset: int
    ) -> dict[str, Any]:
        self._ensure_ready()
        if get_recording_group(self.database_path, group_id) is None:
            raise ValueError("recording_group_unavailable")
        return list_recording_group_segments(
            self.database_path, group_id, limit=limit, offset=offset
        )

    def group_events(self, group_id: str) -> list[dict[str, Any]]:
        self._ensure_ready()
        if get_recording_group(self.database_path, group_id) is None:
            raise ValueError("recording_group_unavailable")
        return list_recording_group_events(self.database_path, group_id)

    def pause_recording_group(
        self,
        group_id: str,
        *,
        expected_revision: int,
        expected_recording_id: str,
    ) -> dict[str, Any]:
        self._ensure_ready()
        with self._lock:
            if self._group_id != group_id or self._recording_id != expected_recording_id:
                raise ValueError("recording_group_transition_conflict")
            group = request_recording_group_transition(
                self.database_path,
                group_id,
                target_status="pausing",
                event_kind="pause_requested",
                expected_status="recording",
                expected_revision=expected_revision,
                expected_recording_id=expected_recording_id,
            )
            self._stop_requested = True
            self._cancel_current_segment()
            return group

    def resume_recording_group(
        self,
        group_id: str,
        *,
        expected_revision: int,
        expected_recording_id: str,
    ) -> dict[str, Any]:
        self._ensure_ready()
        prior_worker: threading.Thread | None = None
        with self._lock:
            if self._group_id != group_id:
                raise ValueError("recording_group_transition_conflict")
            current_group = get_recording_group(self.database_path, group_id)
            if (
                current_group is None
                or current_group["status"] != "paused"
                or current_group["transition_revision"] != expected_revision
                or current_group["last_recording_id"] != expected_recording_id
            ):
                raise ValueError("recording_group_transition_conflict")
            if self._thread is not None:
                # Pause is durable before the segment worker's final bookkeeping
                # releases its thread handle. Wait outside the lifecycle lock so
                # an immediate Resume after the pause acknowledgement is reliable.
                prior_worker = self._thread
            else:
                if self._recording_id is not None:
                    raise ValueError("recording_group_transition_conflict")
                recording_id = uuid.uuid4().hex
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                staging_name = f".f1e-recording-{recording_id}.part"
                final_name = f"f1e-{timestamp}-{recording_id[:8]}.f1ecap"
                group, segment = create_recording_group_segment(
                    self.database_path,
                    group_id=group_id,
                    expected_revision=expected_revision,
                    expected_recording_id=expected_recording_id,
                    recording_id=recording_id,
                    staging_relative_path=staging_name,
                    final_relative_path=final_name,
                )
                self._recording_id = recording_id
                self._stop_requested = False
                self._cancel_sent = False
                try:
                    self._launch_segment_worker(group_id, segment)
                except Exception:
                    self._settle_unstarted_segment_failure(
                        group_id,
                        recording_id,
                        int(segment["segment_ordinal"]),
                        "recording_resume_start_failed",
                    )
                    raise
        if prior_worker is not None:
            prior_worker.join(timeout=5)
            if prior_worker.is_alive():
                raise ValueError("recording_group_transition_conflict")
            return self.resume_recording_group(
                group_id,
                expected_revision=expected_revision,
                expected_recording_id=expected_recording_id,
            )
        return get_recording_group(self.database_path, group_id) or group

    def stop_recording_group(self, group_id: str) -> dict[str, Any]:
        self._ensure_ready()
        paused_worker: threading.Thread | None = None
        release_paused_reservation = False
        with self._lock:
            current = get_recording_group(self.database_path, group_id)
            if current is None:
                raise ValueError("recording_group_unavailable")
            if current["status"] in {"complete", "failed", "interrupted"}:
                return current
            if self._group_id != group_id:
                raise ValueError("recording_group_transition_conflict")
            if current["status"] == "stopping":
                self._stop_requested = True
                self._cancel_current_segment()
                return current
            previous_status = current["status"]
            target = "complete" if previous_status == "paused" else "stopping"
            group = request_recording_group_transition(
                self.database_path,
                group_id,
                target_status=target,
                event_kind="complete" if target == "complete" else "stop_requested",
                expected_status=previous_status,
            )
            if target == "complete":
                if self._group_id == group_id:
                    if self._thread is not None:
                        paused_worker = self._thread
                    else:
                        self._group_id = None
                        self._recording_id = None
                        release_paused_reservation = True
            else:
                self._stop_requested = True
                self._cancel_current_segment()
        if paused_worker is not None:
            paused_worker.join(timeout=5)
        elif release_paused_reservation:
            self.operation_controller.release_operation("recording")
        return group

    def _settle_unstarted_segment_failure(
        self,
        group_id: str,
        recording_id: str,
        segment_ordinal: int,
        failure_reason: str,
    ) -> None:
        self._snapshots.pop(recording_id, None)
        self._recording_id = None
        self._thread = None
        try:
            _, group = settle_recording_segment_failed(
                self.database_path,
                recording_id,
                summary=None,
                failure_reason=failure_reason,
            )
            if group is None:
                raise ValueError("recording_group_settlement_unavailable")
        except Exception:
            logger.exception("Recording launch failure settlement is pending")
            self._group_id = group_id
            self._schedule_settlement(
                {
                    "kind": "failed",
                    "group_id": group_id,
                    "recording_id": recording_id,
                    "segment_ordinal": segment_ordinal,
                    "failure_reason": failure_reason,
                    "summary": None,
                }
            )
            return
        self._group_id = None
        self.operation_controller.release_operation("recording")

    def _cancel_current_segment(self) -> None:
        recording_id = self._recording_id
        snapshot = self._snapshots.get(recording_id, {}) if recording_id else {}
        if snapshot.get("state") == "starting":
            return
        loop = self._loop
        task = self._task
        if loop is not None and task is not None and not self._cancel_sent:
            self._cancel_sent = True
            loop.call_soon_threadsafe(task.cancel)

    def _launch_segment_worker(
        self, group_id: str, job: dict[str, Any]
    ) -> None:
        recording_id = str(job["recording_id"])
        paths = get_recording_job_paths(self.database_path, recording_id)
        if paths is None:
            raise ValueError("recording_unavailable")
        staging_path = self._safe_path(paths["staging_relative_path"])
        self._snapshots[recording_id] = self._empty_snapshot()
        self._thread = threading.Thread(
            target=self._run_recording,
            args=(group_id, recording_id, int(job["segment_ordinal"]), staging_path),
            name="f1-managed-udp-recording-segment",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _empty_snapshot() -> dict[str, Any]:
        return {
            "state": "starting",
            "elapsed_ms": 0,
            "received": 0,
            "queued": 0,
            "recorded": 0,
            "queue_dropped": 0,
            "socket_errors": 0,
            "latest_context": None,
            "live_telemetry": {"status": "waiting", "reason": None, "age_ms": None},
            "live_car_status": {"status": "waiting", "reason": None, "age_ms": None},
            "live_lap_timing": {"status": "waiting", "reason": None, "age_ms": None},
            "live_car_damage": {"status": "waiting", "reason": None, "age_ms": None, "observation_count": 0},
            "live_car_setup": {"status": "waiting", "reason": None, "age_ms": None, "observation_count": 0},
            "live_session_conditions": {"status": "waiting", "reason": None, "age_ms": None, "observation_count": 0},
            "live_motion": {"status": "waiting", "reason": None, "age_ms": None, "observation_count": 0},
            "live_session_history": {"status": "waiting", "reason": None, "age_ms": None, "observation_count": 0},
        }

    def stop_recording(self, recording_id: str) -> dict[str, Any]:
        self._ensure_ready()
        with self._lock:
            job = get_recording_job(self.database_path, recording_id)
            if job is None:
                raise ValueError("recording_unavailable")
            if job.get("group_id"):
                group = get_recording_group(self.database_path, job["group_id"])
                if group is not None and (
                    group.get("current_recording_id") == recording_id
                    or (
                        group.get("status") == "paused"
                        and group.get("last_recording_id") == recording_id
                    )
                ):
                    self.stop_recording_group(job["group_id"])
                    updated = get_recording_job(self.database_path, recording_id)
                    return self._with_progress(updated or job)
                return job
            if job["status"] not in _ACTIVE:
                return job
            if self._recording_id != recording_id:
                raise ValueError("recording_unavailable")
            self._stop_requested = True
            update_recording_job(
                self.database_path, recording_id, status="stopping"
            )
            snapshot = self._snapshots.get(recording_id, {})
            if snapshot.get("state") != "starting":
                self._cancel_current_segment()
        updated = get_recording_job(self.database_path, recording_id)
        assert updated is not None
        return self._with_progress(updated)

    def current(self) -> dict[str, Any] | None:
        self._ensure_ready()
        job = get_current_recording_job(self.database_path)
        return self._with_progress(job) if job is not None else None

    def get(self, recording_id: str) -> dict[str, Any] | None:
        self._ensure_ready()
        job = get_recording_job(self.database_path, recording_id)
        return self._with_progress(job) if job is not None else None

    def _run_recording(
        self,
        group_id: str,
        recording_id: str,
        segment_ordinal: int,
        staging_path: Path,
    ) -> None:
        worker_thread = threading.current_thread()
        persisted_state = "starting"
        publication_committed = False
        final_path: Path | None = None
        summary: dict[str, Any] | None = None

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
                if snapshot.state == "recording" and persisted_state != "recording":
                    mark_recording_segment_started(self.database_path, recording_id)
                    persisted_state = "recording"
                elif snapshot.state == "stopping" and persisted_state != "stopping":
                    update_recording_job(
                        self.database_path, recording_id, status="stopping"
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
                    recording_group_id=group_id,
                    recording_id=recording_id,
                    segment_ordinal=segment_ordinal,
                    on_snapshot=on_snapshot,
                )
            )
            with self._lock:
                self._loop = loop
                self._task = task
            return await task

        try:
            result = asyncio.run(run_capture())
            summary = result.summary
            if result.capture_status != "complete":
                raise RuntimeError("recording_incomplete")
            with self._lock:
                update_recording_job(
                    self.database_path, recording_id, status="stopping"
                )
                final_path = self._recording_final_path(recording_id)
                self._publish_without_replacement(staging_path, final_path)
                publication_committed = True
                segment_record, group_record = settle_recording_segment_complete(
                    self.database_path, recording_id, summary=result.summary
                )
                if segment_record is None or group_record is None:
                    raise RuntimeError("recording_group_settlement_unavailable")
            try:
                list_recording_sources(self.database_path, self.recordings_root)
            except Exception:
                # The next inbox refresh discovers this already-published capture.
                logger.exception("Could not immediately register the finished capture")
        except Exception as exc:
            logger.exception("Managed UDP recording %s failed", recording_id)
            if publication_committed and final_path is not None:
                try:
                    completion = self._read_matching_group_capture(
                        final_path, group_id, recording_id, segment_ordinal
                    )
                except Exception:
                    completion = None
                    logger.exception(
                        "Could not verify the published segment footer for %s",
                        recording_id,
                    )
                if completion is not None:
                    try:
                        segment_record, group_record = settle_recording_segment_complete(
                            self.database_path,
                            recording_id,
                            summary={key: value for key, value in completion.items() if key != "status"},
                        )
                        if segment_record is not None and group_record is not None:
                            summary = {
                                key: value for key, value in completion.items() if key != "status"
                            }
                        else:
                            raise RuntimeError("recording_group_settlement_unavailable")
                    except Exception:
                        logger.exception(
                            "Published segment %s awaits completion-state reconciliation",
                            recording_id,
                        )
                        self._schedule_settlement(
                            {
                                "kind": "complete",
                                "group_id": group_id,
                                "recording_id": recording_id,
                                "segment_ordinal": segment_ordinal,
                                "final_path": final_path,
                                "summary": {
                                    key: value
                                    for key, value in completion.items()
                                    if key != "status"
                                },
                            }
                        )
                else:
                    self._schedule_settlement(
                        {
                            "kind": "failed",
                            "group_id": group_id,
                            "recording_id": recording_id,
                            "segment_ordinal": segment_ordinal,
                            "failure_reason": "published_capture_identity_unavailable",
                            "summary": self._snapshots.get(recording_id),
                        }
                    )
            else:
                failure_reason = (
                    "capture_publication_collision"
                    if isinstance(exc, FileExistsError)
                    else "recording_incomplete"
                    if isinstance(exc, RuntimeError) and str(exc) == "recording_incomplete"
                    else "recording_failed"
                )
                try:
                    settle_recording_segment_failed(
                        self.database_path,
                        recording_id,
                        summary=self._snapshots.get(recording_id),
                        failure_reason=failure_reason,
                    )
                except Exception:
                    logger.exception("Recording-group failure settlement is pending")
                    self._schedule_settlement(
                        {
                            "kind": "failed",
                            "group_id": group_id,
                            "recording_id": recording_id,
                            "segment_ordinal": segment_ordinal,
                            "failure_reason": failure_reason,
                            "summary": self._snapshots.get(recording_id),
                        }
                    )
        finally:
            with self._lock:
                self._snapshots.pop(recording_id, None)
                owns_current_worker = (
                    self._thread is worker_thread
                    and self._recording_id == recording_id
                    and self._group_id == group_id
                )
                if owns_current_worker:
                    self._loop = None
                    self._task = None
                    self._recording_id = None
                    self._cancel_sent = False
                    self._stop_requested = False
                    self._thread = None
                pending = self._settlement_pending is not None
                group = None
                try:
                    group = get_recording_group(self.database_path, group_id)
                except Exception:
                    pending = True
                if group is None:
                    pending = True
                if pending and self._settlement_pending is None:
                    self._schedule_settlement(
                        {"kind": "ownership", "group_id": group_id}
                    )
                    if self._settlement_pending is None:
                        self._schedule_settlement(
                            {"kind": "ownership", "group_id": group_id}
                        )
                if owns_current_worker:
                    if pending or group is None or group["status"] not in {"complete", "failed", "interrupted"}:
                        self._group_id = group_id
                    else:
                        self._group_id = None
                        self.operation_controller.release_operation("recording")

    def _schedule_settlement(self, settlement: dict[str, Any]) -> None:
        with self._lock:
            settlement = {**settlement, "settlement_id": uuid.uuid4().hex}
            self._settlement_pending = settlement
            if self._settlement_thread is not None and self._settlement_thread.is_alive():
                return
            self._settlement_thread = threading.Thread(
                target=self._retry_settlement,
                name="f1-recording-group-settlement",
                daemon=True,
            )
            self._settlement_thread.start()

    def _retry_settlement(self) -> None:
        while not self._closing.wait(1.0):
            with self._lock:
                if self._settlement_pending is None:
                    if self._settlement_thread is threading.current_thread():
                        self._settlement_thread = None
                    return
                settlement = dict(self._settlement_pending)
            try:
                if settlement.get("kind") == "ownership":
                    group = get_recording_group(
                        self.database_path, str(settlement["group_id"])
                    )
                    if group is None:
                        raise ValueError("recording_group_unavailable")
                elif settlement.get("kind") == "complete":
                    final_path = settlement.get("final_path")
                    completion = self._read_matching_group_capture(
                        Path(final_path),
                        str(settlement["group_id"]),
                        str(settlement["recording_id"]),
                        int(settlement["segment_ordinal"]),
                    )
                    if completion is None:
                        raise ValueError("published_capture_identity_unavailable")
                    _, group = settle_recording_segment_complete(
                        self.database_path,
                        str(settlement["recording_id"]),
                        summary={key: value for key, value in completion.items() if key != "status"},
                    )
                    if group is None:
                        raise ValueError("recording_group_settlement_unavailable")
                else:
                    _, group = settle_recording_segment_failed(
                        self.database_path,
                        str(settlement["recording_id"]),
                        summary=settlement.get("summary"),
                        failure_reason=str(settlement.get("failure_reason", "recording_failed")),
                    )
                    if group is None:
                        raise ValueError("recording_group_settlement_unavailable")
                with self._lock:
                    pending = self._settlement_pending
                    if (
                        pending is None
                        or pending.get("settlement_id")
                        != settlement.get("settlement_id")
                    ):
                        continue
                    # A newer segment may have advanced or even completed this
                    # group while an older settlement was being retried. Use
                    # the committed database state, not the stale result from
                    # before that transition.
                    current_group = get_recording_group(
                        self.database_path, str(settlement["group_id"])
                    )
                    if current_group is None:
                        raise ValueError("recording_group_unavailable")
                    self._settlement_pending = None
                    if self._group_id == str(current_group["group_id"]):
                        if current_group["status"] in {"complete", "failed", "interrupted"}:
                            worker_still_owns_group = (
                                self._thread is not None
                                and self._thread.is_alive()
                                and self._group_id == str(current_group["group_id"])
                            )
                            if not worker_still_owns_group:
                                self._group_id = None
                                self.operation_controller.release_operation("recording")
                        else:
                            self._group_id = str(current_group["group_id"])
                    if self._settlement_thread is threading.current_thread():
                        self._settlement_thread = None
                return
            except Exception:
                logger.warning("Recording-group settlement is still unavailable", exc_info=True)

    @staticmethod
    def _read_matching_group_capture(
        path: Path,
        group_id: str,
        recording_id: str,
        segment_ordinal: int,
    ) -> dict[str, Any] | None:
        if path.is_symlink() or not path.is_file():
            return None
        with CaptureReader(path) as reader:
            for _ in reader:
                pass
            if (
                reader.complete
                and reader.completion is not None
                and reader.metadata.get("recording_group_id") == group_id
                and reader.metadata.get("recording_id") == recording_id
                and reader.metadata.get("segment_ordinal") == segment_ordinal
            ):
                return reader.completion
        return None

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
                if row.get("group_id"):
                    completion = self._read_matching_group_capture(
                        final_path,
                        row["group_id"],
                        row["recording_id"],
                        int(row["segment_ordinal"]),
                    )
                else:
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
            "live_session_history": LIVE_SESSION_HISTORY_FRESHNESS_LIMIT_MS,
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
