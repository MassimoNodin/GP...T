from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..storage.database import Database
from ..storage.import_jobs import (
    create_import_job,
    get_import_job,
    recover_abandoned_import_jobs,
    resolve_recording_source,
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
        self.unavailable_reason: str | None = None

    @property
    def ready(self) -> bool:
        return self._owns_lock and self._executor is not None

    @property
    def current_operation_reservation(self) -> str | None:
        """Return the current exclusive operation without changing ownership."""
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

    def close(self) -> None:
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        if self._owns_lock:
            self._owns_lock = False
            self._lock.__exit__(None, None, None)

    def submit(self, capture_id: str) -> dict[str, Any]:
        self._ensure_ready()
        source = resolve_recording_source(
            self.database_path, self.recordings_root, capture_id
        )
        if source is None:
            raise ValueError("capture_id_unavailable")
        with self._operation_lock:
            if self._active_operation == "import":
                active_job = (
                    get_import_job(self.database_path, self._active_import_job_id)
                    if self._active_import_job_id is not None
                    else None
                )
                if (
                    active_job is not None
                    and active_job["status"] in {"queued", "running"}
                    and active_job["capture_id"] == capture_id
                ):
                    return self.get(active_job["job_id"])
                raise ValueError("another_local_operation_is_in_progress")
            if self._active_operation is not None:
                raise ValueError("another_local_operation_is_in_progress")
            self._active_operation = "import"
            try:
                job, created = create_import_job(self.database_path, capture_id)
                if created:
                    self._active_import_job_id = job["job_id"]
                    self._submit_worker(job["job_id"], capture_id, source)
                else:
                    self._active_operation = None
                    self._active_import_job_id = None
            except Exception:
                self._active_operation = None
                self._active_import_job_id = None
                raise
        return self.get(job["job_id"])

    def retry(self, job_id: str) -> dict[str, Any]:
        self._ensure_ready()
        job = get_import_job(self.database_path, job_id)
        if job is None:
            raise ValueError("import_job_unavailable")
        source = resolve_recording_source(
            self.database_path, self.recordings_root, job["capture_id"]
        )
        if source is None:
            raise ValueError("capture_id_unavailable")
        with self._operation_lock:
            if self._active_operation is not None:
                raise ValueError("another_local_operation_is_in_progress")
            self._active_operation = "import"
            self._active_import_job_id = job_id
            try:
                queued = retry_import_job(self.database_path, job_id)
                self._submit_worker(job_id, job["capture_id"], source)
            except Exception:
                self._active_operation = None
                self._active_import_job_id = None
                raise
        return self.get(queued["job_id"])

    def reserve_operation(self, operation: str) -> bool:
        """Reserve the local controller for one exclusive local operation."""
        self._ensure_ready()
        if operation not in {"import", "recording", "replay", "upload"}:
            raise ValueError("unsupported_local_operation")
        with self._operation_lock:
            if self._active_operation is not None:
                return False
            self._active_operation = operation
            return True

    def release_operation(self, operation: str) -> None:
        with self._operation_lock:
            if self._active_operation == operation:
                self._active_operation = None
                if operation == "import":
                    self._active_import_job_id = None

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

    def _submit_worker(self, job_id: str, capture_id: str, source: Path) -> None:
        executor = self._executor
        if executor is None:
            raise ValueError("import_controller_unavailable")
        try:
            total_bytes = source.stat().st_size
        except OSError as exc:
            update_import_job(
                self.database_path,
                job_id,
                status="failed",
                phase="failed",
                failure_reason="capture_unavailable_retry_after_refresh",
                finished=True,
            )
            raise ValueError("capture_id_unavailable") from exc
        with self._progress_lock:
            self._progress[job_id] = {
                "phase": "hashing_capture",
                "packets_processed": 0,
                "bytes_read": 0,
                "total_bytes": total_bytes,
            }
        try:
            executor.submit(self._run_import, job_id, capture_id, total_bytes)
        except RuntimeError as exc:
            with self._progress_lock:
                self._progress.pop(job_id, None)
            update_import_job(
                self.database_path,
                job_id,
                status="failed",
                phase="failed",
                failure_reason="import_controller_unavailable",
                finished=True,
            )
            raise ValueError("import_controller_unavailable") from exc

    def _run_import(self, job_id: str, capture_id: str, total_bytes: int) -> None:
        persisted_phase = "queued"

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
            source = resolve_recording_source(
                self.database_path, self.recordings_root, capture_id
            )
            if source is None:
                raise ValueError("capture_id_unavailable")
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
                starting=True,
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
        except Exception:
            logger.exception("Capture import job %s failed", job_id)
            with self._progress_lock:
                self._progress.pop(job_id, None)
            try:
                update_import_job(
                    self.database_path,
                    job_id,
                    status="failed",
                    phase="failed",
                    failure_reason="capture_import_failed_retry_available",
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
