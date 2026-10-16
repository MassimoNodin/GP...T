from __future__ import annotations

import asyncio
import hmac
import json
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from ..engineer_activity.store import EngineerActivityStore, MAX_TEXT_CHARS

MAX_BODY_BYTES = 32 * 1024
OWNER_TOPICS = {
    "automatic_gaps": "gap", "gap_changes": "gap", "opponent_regions": "gap",
    "lap_time": "lap_time", "pace_sectors": "lap_time", "lap_reports": "lap_time",
    "pit_benchmark": "pit",
}


class SpokenEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    client_event_id: StrictStr = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")
    kind: Literal["proactive", "answer"]
    owner: StrictStr | None = Field(default=None, max_length=128)
    text: StrictStr = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    scope: StrictStr | None = Field(default=None, max_length=4096)
    session_id: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    lap: StrictInt | None = Field(default=None, ge=0, le=1_000_000)
    related_id: StrictInt | None = Field(default=None, ge=1, le=2**62)
    started_at: StrictStr = Field(min_length=1, max_length=64)
    repeat: bool = False


class SpokenBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    events: list[SpokenEvent] = Field(min_length=1, max_length=16)


def register_radio_activity_routes(app: FastAPI, store: EngineerActivityStore, token: str | None) -> None:
    @app.post("/api/v2/engineer/radio/events")
    async def radio_events(request: Request):
        def error(status, reason):
            return JSONResponse(status_code=status, content={"status": "error", "reason": reason})

        authorization = request.headers.get("authorization", "")
        if token is None or not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            return error(401, "radio_events_unauthorized")
        if request.query_params:
            return error(400, "radio_events_query_not_allowed")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return error(415, "radio_events_json_required")
        raw = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > MAX_BODY_BYTES:
                        return error(413, "radio_events_request_limit_exceeded")
            batch = SpokenBatch.model_validate(json.loads(raw))
            for event in batch.events:
                started = datetime.fromisoformat(event.started_at.replace("Z", "+00:00"))
                if started.utcoffset() is None or not event.text.strip():
                    raise ValueError("invalid spoken event")
        except TimeoutError:
            return error(408, "radio_events_deadline_exceeded")
        except (ValidationError, ValueError, TypeError, UnicodeError, RecursionError):
            return error(422, "radio_events_request_invalid")

        def record():
            ids = []
            for event in batch.events:
                ids.append(store.append(
                    kind="response" if event.kind == "answer" else "proactive", source="radio",
                    topic=OWNER_TOPICS.get(event.owner, "general"), text=event.text,
                    session_id=event.session_id, lap=event.lap, related_id=event.related_id,
                    dedupe_key=f"radio:{event.client_event_id}",
                    detail={"owner": event.owner, "scope": event.scope, "started_at": event.started_at,
                            "spoken": True, "repeat": event.repeat, "client_event_id": event.client_event_id},
                ))
            return ids

        try:
            ids = await asyncio.to_thread(record)
        except Exception:
            return error(503, "radio_events_unavailable")
        return JSONResponse(content={"status": "ok", "data": {"event_ids": ids}},
                            headers={"Cache-Control": "no-store"})
