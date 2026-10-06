from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..storage.database import Database
from ..storage.import_jobs import (
    active_import_job_for_capture,
    cancel_queued_import_job,
    claim_oldest_import_job,
    enqueue_import_job,
    get_import_job,
    has_queued_import_jobs,
    import_queue_snapshot,
    recover_abandoned_import_jobs,
    recording_source_pin,
    resolve_pinned_recording_source,
    retry_import_job,
    update_import_job,
)
from ..storage.importer import import_capture
from ..storage.lock import ImportRunLock


logger = logging.getLogger(__name__)


class ImportController:
    def __init__(self, database_path: str | Path, recordings_root: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.recordings_root = Path(recordings_root).expanduser().resolve()
        lock_path = (
            self.database_path.parent
            / f".{self.database_path.name}.locks"
            / "import-controller.lock"
        )
        self._lock = ImportRunLock(lock_path)
        self._owns_lock = False
        self._executor: ThreadPoolExecutor | None = None
        self._progress_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._active_operation: str | None = None
        self._active_import_job_id: str | None = None
        self._progress: dict[str, dict[str, Any]] = {}
        self._dispatch_enabled = False
        self._closing = False
        self._dispatch_event = threading.Event()
        self._dispatch_thread: threading.Thread | None = None
        self.unavailable_reason: str | None = None

    @property
    def ready(self) -> bool:
        return self._owns_lock and self._executor is not None

    @property
    def current_operation_reservation(self) -> str | None:
        """Return the current exclusive local operation without changing ownership."""
        with self._operation_lock:
            return self._active_operation

    def start(self) -> None:
        try:
            self._lock.__enter__()
        except ValueError:
            self.unavailable_reason = "another_api_owns_the_import_controller"
            return
        self._owns_lock = True
        try:
            self.recordings_root.mkdir(parents=True, exist_ok=True)
            with Database(self.database_path):
                pass
            recover_abandoned_import_jobs(self.database_path)
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="f1-capture-import"
            )
        except Exception:
            logger.exception("Could not start the local recording import controller")
            self.unavailable_reason = "import_controller_start_failed"
            self.close()

    def enable_dispatch(self) -> None:
        """Begin draining durable waiters after every local controller recovered."""
        # Import ownership is process-wide. Another API may own it while this
        # instance still serves read-only catalog and telemetry routes.
        if not self.ready:
            return
        with self._operation_lock:
            self._closing = False
            if self._dispatch_enabled:
                return
            self._dispatch_enabled = True
            self._dispatch_thread = threading.Thread(
                target=self._dispatch_loop,
                name="f1-import-queue-dispatch",
                daemon=True,
            )
            self._dispatch_thread.start()
        self._dispatch_event.set()

    def stop_dispatch(self) -> None:
        """Prevent new claims before other controllers release their reservations."""
        with self._operation_lock:
            self._closing = True
            self._dispatch_enabled = False
            dispatch_thread = self._dispatch_thread
            self._dispatch_thread = None
        self._dispatch_event.set()
        if dispatch_thread is not None and dispatch_thread is not threading.current_thread():
            dispatch_thread.join()

    def close(self) -> None:
        self.stop_dispatch()
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        if self._owns_lock:
            self._owns_lock = False
            self._lock.__exit__(None, None, None)

    def submit(self, capture_id: str, *, queue_if_busy: bool = False) -> dict[str, Any]:
        self._ensure_ready()
        with self._operation_lock:
            if self._closing:
                raise ValueError("import_controller_shutting_down")
            active_job = active_import_job_for_capture(self.database_path, capture_id)
            if active_job is not None:
                return self.get(active_job["job_id"])

        source_pin = recording_source_pin(
            self.database_path, self.recordings_root, capture_id
        )
        if source_pin is None:
            raise ValueError("capture_id_unavailable")

        claimed: dict[str, Any] | None = None
        should_dispatch = False
        with self._operation_lock:
            if self._closing:
                raise ValueError("import_controller_shutting_down")
            active_job = active_import_job_for_capture(self.database_path, capture_id)
            if active_job is not None:
                return self.get(active_job["job_id"])
            queued_exists = has_queued_import_jobs(self.database_path)
            busy = self._active_operation is not None or queued_exists
            if busy and not queue_if_busy:
                raise ValueError("another_local_operation_is_in_progress")

            if busy:
                job, _created = enqueue_import_job(
                    self.database_path,
                    capture_id,
                    source_pin["root_namespace"],
                    source_pin["metadata_version"],
                )
                should_dispatch = self._active_operation is None
            else:
                self._active_operation = "import"
                try:
                    job, _created = enqueue_import_job(
                        self.database_path,
                        capture_id,
                        source_pin["root_namespace"],
                        source_pin["metadata_version"],
                    )
                    claimed = claim_oldest_import_job(self.database_path)
                    if claimed is None:
                        raise ValueError("import_queue_claim_failed")
                    self._active_import_job_id = claimed["job_id"]
                except Exception:
                    self._active_operation = None
                    self._active_import_job_id = None
                    raise

        if should_dispatch:
            self._dispatch_event.set()
        if claimed is not None:
            self._start_claimed_job(claimed)
        return self.get(job["job_id"])

    def retry(self, job_id: str) -> dict[str, Any]:
        self._ensure_ready()
        job = get_import_job(self.database_path, job_id)
        if job is None:
            raise ValueError("import_job_unavailable")
        source_pin = recording_source_pin(
            self.database_path, self.recordings_root, job["capture_id"]
        )
        if source_pin is None:
            raise ValueError("capture_id_unavailable")

        claimed: dict[str, Any] | None = None
        should_dispatch = False
        with self._operation_lock:
            if self._closing:
                raise ValueError("import_controller_shutting_down")
            queued_exists = has_queued_import_jobs(self.database_path)
            busy = self._active_operation is not None or queued_exists
            if busy:
                retried = retry_import_job(
                    self.database_path,
                    job_id,
                    source_pin["root_namespace"],
                    source_pin["metadata_version"],
                )
                should_dispatch = self._active_operation is None
            else:
                self._active_operation = "import"
                try:
                    retried = retry_import_job(
                        self.database_path,
                        job_id,
                        source_pin["root_namespace"],
                        source_pin["metadata_version"],
                    )
                    claimed = claim_oldest_import_job(self.database_path)
                    if claimed is None:
                        raise ValueError("import_queue_claim_failed")
                    self._active_import_job_id = claimed["job_id"]
                except Exception:
                    self._active_operation = None
                    self._active_import_job_id = None
                    raise
        if should_dispatch:
            self._dispatch_event.set()
        if claimed is not None:
            self._start_claimed_job(claimed)
        return self.get(retried["job_id"])

    def cancel(self, job_id: str) -> tuple[dict[str, Any] | None, bool]:
        self._ensure_ready()
        job, cancelled = cancel_queued_import_job(self.database_path, job_id)
        if cancelled:
            self._dispatch_event.set()
        if job is not None:
            job["progress"] = None
        return job, cancelled

    def queue_snapshot(self) -> dict[str, Any]:
        self._ensure_ready()
        # Keep reservation and SQLite state from describing different moments
        # while a queued job is claimed or another operation is reserved.
        with self._operation_lock:
            return import_queue_snapshot(self.database_path, self._active_operation)

    def reserve_operation(self, operation: str) -> bool:
        """Reserve the service; older queued imports take priority after release."""
        self._ensure_ready()
        if operation not in {"import", "recording", "replay", "upload"}:
            raise ValueError("unsupported_local_operation")
        wake_dispatch = False
        with self._operation_lock:
            if self._closing:
                return False
            if self._active_operation is not None:
                return False
            if operation != "import" and has_queued_import_jobs(self.database_path):
                wake_dispatch = True
                acquired = False
            else:
                self._active_operation = operation
                acquired = True
        if wake_dispatch:
            self._dispatch_event.set()
        return acquired

    def release_operation(self, operation: str) -> None:
        released = False
        with self._operation_lock:
            if self._active_operation == operation:
                self._active_operation = None
                released = True
                if operation == "import":
                    self._active_import_job_id = None
        if released:
            self._dispatch_event.set()

    def get(self, job_id: str) -> dict[str, Any] | None:
        job = get_import_job(self.database_path, job_id)
        if job is None:
            return None
        if job["status"] == "running":
            with self._progress_lock:
                job["progress"] = self._progress.get(job_id)
        else:
            job["progress"] = None
        return job

    def _dispatch_loop(self) -> None:
        while True:
            self._dispatch_event.wait()
            self._dispatch_event.clear()
            with self._operation_lock:
                if not self._dispatch_enabled:
                    return
            try:
                self._dispatch_one()
            except Exception:
                logger.exception("Could not dispatch the next queued capture import")
                if not self._dispatch_event.wait(timeout=0.5):
                    self._dispatch_event.set()

    def _dispatch_one(self) -> bool:
        with self._operation_lock:
            if (
                not self._dispatch_enabled
                or self._closing
                or self._active_operation is not None
            ):
                return False
            self._active_operation = "import"
            try:
                job = claim_oldest_import_job(self.database_path)
            except Exception:
                self._active_operation = None
                self._active_import_job_id = None
                raise
            if job is None:
                self._active_operation = None
                self._active_import_job_id = None
                return False
            self._active_import_job_id = job["job_id"]
        self._start_claimed_job(job)
        return True

    def _start_claimed_job(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        capture_id = job["capture_id"]
        try:
            source = resolve_pinned_recording_source(
                self.database_path,
                self.recordings_root,
                capture_id,
                job.get("source_root_namespace"),
                job.get("source_metadata_version"),
            )
            total_bytes = source.stat().st_size if source is not None else None
        except Exception:
            logger.exception("Could not validate source for import job %s", job_id)
            self._fail_claimed_job(
                job_id, "capture_source_changed_refresh_catalog"
            )
            return
        if total_bytes is None:
            self._fail_claimed_job(
                job_id, "capture_source_changed_refresh_catalog"
            )
            return
        with self._progress_lock:
            self._progress[job_id] = {
                "phase": "hashing_capture",
                "packets_processed": 0,
                "bytes_read": 0,
                "total_bytes": total_bytes,
            }
        executor = self._executor
        if executor is None:
            self._fail_claimed_job(job_id, "import_controller_unavailable")
            return
        try:
            executor.submit(self._run_import, job)
        except RuntimeError:
            self._fail_claimed_job(job_id, "import_controller_unavailable")

    def _fail_claimed_job(self, job_id: str, reason: str) -> None:
        with self._progress_lock:
            self._progress.pop(job_id, None)
        try:
            update_import_job(
                self.database_path,
                job_id,
                status="failed",
                phase="failed",
                failure_reason=reason,
                finished=True,
            )
        finally:
            self.release_operation("import")

    def _run_import(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        capture_id = job["capture_id"]
        persisted_phase = "starting"

        def report_progress(
            phase: str, packets_processed: int, bytes_read: int, total_bytes: int
        ) -> None:
            nonlocal persisted_phase
            with self._progress_lock:
                self._progress[job_id] = {
                    "phase": phase,
                    "packets_processed": packets_processed,
                    "bytes_read": bytes_read,
                    "total_bytes": total_bytes,
                }
            if phase != persisted_phase:
                update_import_job(
                    self.database_path,
                    job_id,
                    status="running",
                    phase=phase,
                )
                persisted_phase = phase

        try:
            source = resolve_pinned_recording_source(
                self.database_path,
                self.recordings_root,
                capture_id,
                job.get("source_root_namespace"),
                job.get("source_metadata_version"),
            )
            if source is None:
                raise ValueError("capture_source_changed_refresh_catalog")
            total_bytes = source.stat().st_size
            with self._progress_lock:
                self._progress[job_id] = {
                    "phase": "hashing_capture",
                    "packets_processed": 0,
                    "bytes_read": 0,
                    "total_bytes": total_bytes,
                }
            update_import_job(
                self.database_path,
                job_id,
                status="running",
                phase="hashing_capture",
            )
            persisted_phase = "hashing_capture"
            summary = import_capture(
                source,
                self.database_path,
                progress_callback=report_progress,
            )
            update_import_job(
                self.database_path,
                job_id,
                status="complete",
                phase="complete",
                result=summary.to_dict(),
                finished=True,
            )
        except Exception as exc:
            logger.exception("Capture import job %s failed", job_id)
            reason = (
                "capture_source_changed_refresh_catalog"
                if isinstance(exc, ValueError)
                and str(exc) == "capture_source_changed_refresh_catalog"
                else "capture_import_failed_retry_available"
            )
            try:
                update_import_job(
                    self.database_path,
                    job_id,
                    status="failed",
                    phase="failed",
                    failure_reason=reason,
                    finished=True,
                )
            except Exception:
                logger.exception("Could not persist failure state for job %s", job_id)
        finally:
            with self._progress_lock:
                self._progress.pop(job_id, None)
            self.release_operation("import")

    def _ensure_ready(self) -> None:
        if not self.ready:
            raise ValueError(
                self.unavailable_reason or "import_controller_unavailable"
            )
