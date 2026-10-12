from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from ..engineer_activity import EngineerActivity, EngineerActivityUnavailable
from ..engineer_activity.store import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE


def register_engineer_activity_routes(app: FastAPI, activity: EngineerActivity) -> None:
    """Expose the engineer activity feed and publish the bundle on `app.state`."""
    app.state.engineer_activity = activity

    @app.get("/api/v2/engineer/events")
    def engineer_events(
        after: int | None = Query(default=None, ge=0, le=2**62),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        session_id: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> Any:
        try:
            events, next_after = activity.store.events(after=after, limit=limit, session_id=session_id)
        except (EngineerActivityUnavailable, sqlite3.Error):
            return JSONResponse(
                status_code=503,
                content={"api_version": "v1", "status": "unavailable", "data": None,
                         "reason": "engineer_activity_unavailable"},
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            content={"data": {"events": events, "next_after": next_after}},
            headers={"Cache-Control": "no-store"},
        )
