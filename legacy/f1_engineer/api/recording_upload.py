from __future__ import annotations

import asyncio

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from ..storage.recording_upload import (
    MAX_RECORDING_UPLOAD_DEADLINE_SECONDS,
    MAX_RECORDING_UPLOAD_STALL_SECONDS,
    RecordingUpload,
)


_DRAINING_UPLOADS: set[asyncio.Task[None]] = set()


class RecordingUploadTransferError(RuntimeError):
    def __init__(self, reason: str, status_code: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


async def receive_recording_upload(
    request: Request, upload: RecordingUpload
) -> dict[str, object]:
    worker_task: asyncio.Task[object] | None = None
    try:
        iterator = request.stream().__aiter__()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + MAX_RECORDING_UPLOAD_DEADLINE_SECONDS
        async with asyncio.timeout_at(deadline):
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        anext(iterator), timeout=MAX_RECORDING_UPLOAD_STALL_SECONDS
                    )
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    raise RecordingUploadTransferError(
                        "recording_upload_stalled", 408
                    ) from exc
                if chunk:
                    worker_task = asyncio.create_task(
                        run_in_threadpool(upload.write_chunk, chunk)
                    )
                    await asyncio.shield(worker_task)
            worker_task = asyncio.create_task(run_in_threadpool(upload.finish))
            return await asyncio.shield(worker_task)
    except RecordingUploadTransferError:
        await _cleanup_after_request(upload, worker_task)
        raise
    except TimeoutError as exc:
        await _cleanup_after_request(upload, worker_task)
        raise RecordingUploadTransferError(
            "recording_upload_deadline_exceeded", 504
        ) from exc
    except asyncio.CancelledError:
        await _cleanup_after_request(upload, worker_task)
        raise
    except BaseException:
        await _cleanup_after_request(upload, worker_task)
        raise


async def _cleanup_after_request(
    upload: RecordingUpload, worker_task: asyncio.Task[object] | None
) -> None:
    upload.request_cancel()
    if worker_task is None or worker_task.done():
        await run_in_threadpool(upload.abort)
        return

    task = asyncio.create_task(_drain_worker_then_abort(upload, worker_task))
    _DRAINING_UPLOADS.add(task)
    task.add_done_callback(_DRAINING_UPLOADS.discard)


async def _drain_worker_then_abort(
    upload: RecordingUpload, worker_task: asyncio.Task[object]
) -> None:
    try:
        await worker_task
    except BaseException:
        pass
    await run_in_threadpool(upload.abort)
