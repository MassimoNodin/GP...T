from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote_from_bytes

from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..storage.recording_download import OpenRecordingDownload

RECORDING_DOWNLOAD_STALL_TIMEOUT_SECONDS = 60
RECORDING_DOWNLOAD_DEADLINE_SECONDS = 30 * 60


def recording_download_content_disposition(display_name: str) -> str:
    encoded_name = quote_from_bytes(
        display_name.encode("utf-8"), safe="!#$&+-.^_`|~"
    )
    return (
        'attachment; filename="capture.f1ecap"; '
        f"filename*=UTF-8''{encoded_name}"
    )


class RecordingDownloadResponse(StreamingResponse):
    def __init__(
        self,
        download: OpenRecordingDownload,
        *,
        headers: dict[str, str],
    ) -> None:
        self.download = download
        super().__init__(
            self._chunks(),
            status_code=200,
            headers=headers,
            media_type="application/octet-stream",
        )

    async def _chunks(self) -> AsyncIterator[bytes]:
        try:
            while True:
                chunk = await asyncio.wait_for(
                    run_in_threadpool(self.download.read_chunk),
                    timeout=RECORDING_DOWNLOAD_STALL_TIMEOUT_SECONDS,
                )
                if not chunk:
                    return
                yield chunk
        finally:
            self.download.close()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def send_with_stall_timeout(message: Any) -> None:
            await asyncio.wait_for(
                send(message), timeout=RECORDING_DOWNLOAD_STALL_TIMEOUT_SECONDS
            )

        try:
            await asyncio.wait_for(
                super().__call__(scope, receive, send_with_stall_timeout),
                timeout=RECORDING_DOWNLOAD_DEADLINE_SECONDS,
            )
        finally:
            self.download.close()
