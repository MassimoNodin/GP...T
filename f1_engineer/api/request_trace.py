from __future__ import annotations

import json
import logging
import re
import time
import uuid

from ..diagnostics import exception_trace


logger = logging.getLogger("uvicorn.error")


class RequestTraceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"x-f1-radio-request-id", b"")
        request_id = supplied.decode("ascii") if re.fullmatch(rb"[a-f0-9]{32}", supplied) else uuid.uuid4().hex
        capture = headers.get(b"x-request-id", b"")
        capture_id = capture.decode("ascii") if re.fullmatch(rb"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}", capture) else None
        started = time.monotonic()
        status = None
        failure = None
        async def traced_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []),
                                                   (b"x-f1-backend-request-id", request_id.encode("ascii"))]}
            await send(message)
        try:
            return await self.app(scope, receive, traced_send)
        except BaseException as exception:
            failure = exception_trace(exception)
            raise
        finally:
            logger.info(json.dumps({"event": "engineer_http_trace", "request_id": request_id,
                                   "capture_id": capture_id, "method": scope.get("method"),
                                   "path": scope.get("path"), "status_code": status,
                                   "elapsed_s": time.monotonic() - started,
                                   "exception_chain": failure}, separators=(",", ":")))
