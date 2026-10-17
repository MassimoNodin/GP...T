from __future__ import annotations

import functools
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..storage.database import Database
from .store import EngineerActivityStore

LOGGER = logging.getLogger(__name__)
_TICKET_ATTRIBUTE = "engineer_activity_ticket"
_MAX_PARSED_BODY_BYTES = 128 * 1024
_TOPIC_PATTERNS = (
    ("track_limits", re.compile(r"track limit|white line|invalid|deleted|corner cut|cutting", re.I)),
    ("tyres", re.compile(r"\btyres?\b|\btires?\b|degrad|\bwear\b|compound|graining", re.I)),
    ("pit", re.compile(r"\bpit\b|\bpits\b|\bbox\b|undercut|overcut|\bstop\b", re.I)),
    ("gap", re.compile(r"\bgap\b|\bdelta\b|\bahead\b|\bbehind\b|interval|\bcatch", re.I)),
    ("lap_time", re.compile(r"lap time|\bpace\b|sector|\bfaster\b|\bslower\b|\bbest\b|compare", re.I)),
)


def question_topic(question: str) -> str:
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(question):
            return topic
    return "general"


@dataclass(frozen=True, slots=True)
class _Ticket:
    question_id: int
    source: str
    topic: str
    session_id: str | None
    lap: int | None


class AskActivityLog:
    """Records engineer ask traffic; every public method swallows its own failures."""

    def __init__(self, store: EngineerActivityStore, evidence_store: Any | None = None,
                 *, legacy_database_path: str | Path | None = None) -> None:
        self.store = store
        self.evidence_store = evidence_store
        self.legacy_database_path = legacy_database_path

    def note_question(
        self,
        request: Any,
        *,
        source: str,
        question: str,
        session_id: str | None = None,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append the driver question and remember it on the request for `record`."""
        try:
            topic = question_topic(question)
            lap = self._lap(session_id, target)
            if source == "v1_ask" and self.legacy_database_path is not None:
                selection = (detail or {}).get("selection")
                if isinstance(selection, dict):
                    session_id, lap = self._legacy_scope(selection.get("target_attempt_key"))
            question_id = self.store.append(
                kind="question", topic=topic, text=question, source=source,
                session_id=session_id, lap=lap, detail=detail,
            )
            if question_id is not None:
                setattr(request.state, _TICKET_ATTRIBUTE,
                        _Ticket(question_id, source, topic, session_id, lap))
        except Exception:
            LOGGER.warning("engineer activity question was not recorded", exc_info=True)

    def record(self, source: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorate an ask handler: append the response, or the failure as an issue."""

        def decorate(handler: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(handler)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                request = kwargs.get("request")
                try:
                    result = await handler(*args, **kwargs)
                except BaseException as exception:
                    self._finish_failure(request, f"handler_{type(exception).__name__}")
                    raise
                self._finish(request, result)
                return result

            return wrapper

        return decorate

    def question_id(self, request: Any) -> int | None:
        ticket = self._ticket(request)
        return ticket.question_id if ticket is not None else None

    def _lap(self, session_id: str | None, target: str | None) -> int | None:
        if self.evidence_store is None or session_id is None or target is None:
            return None
        try:
            lap = self.evidence_store.attempt(target, session=session_id)["payload"]["lap_number"]
            return lap if isinstance(lap, int) and not isinstance(lap, bool) else None
        except Exception:
            return None

    def _legacy_scope(self, target: Any) -> tuple[str | None, int | None]:
        if not isinstance(target, str) or not 1 <= len(target) <= 256:
            return None, None
        try:
            with Database(self.legacy_database_path, read_only=True) as database:
                row = database.connection.execute(
                    "SELECT l.session_key,l.lap_number FROM lap_attempts l "
                    "JOIN sessions s USING(session_key) JOIN processing_runs r USING(run_id) "
                    "WHERE l.attempt_key=? AND r.status='complete'", (target,),
                ).fetchone()
            return (row["session_key"], row["lap_number"]) if row else (None, None)
        except Exception:
            LOGGER.warning("engineer activity session could not be resolved", exc_info=True)
            return None, None

    @staticmethod
    def _ticket(request: Any) -> _Ticket | None:
        try:
            ticket = getattr(request.state, _TICKET_ATTRIBUTE, None)
        except Exception:
            return None
        return ticket if isinstance(ticket, _Ticket) else None

    def _finish(self, request: Any, result: Any) -> None:
        ticket = self._ticket(request)
        if ticket is None:
            return
        try:
            status_code, body = _outcome(result)
            if status_code == 200 and isinstance(body, dict) and isinstance(body.get("data"), dict):
                if ticket.source == "radio":
                    return
                data = body["data"]
                message = data.get("message")
                detail = {key: data[key] for key in ("status", "reason", "route", "focus")
                          if isinstance(data.get(key), (str, int, bool))}
                self.store.append(
                    kind="response",
                    topic=ticket.topic,
                    text=message if isinstance(message, str) and message else "No answer text was returned.",
                    source=ticket.source, session_id=ticket.session_id, lap=ticket.lap,
                    related_id=ticket.question_id, detail=detail or None,
                )
            elif status_code != 200:
                reason = body.get("reason") if isinstance(body, dict) else None
                self._finish_failure(
                    request, reason if isinstance(reason, str) and reason else f"http_{status_code}",
                    status_code=status_code,
                )
        except Exception:
            LOGGER.warning("engineer activity response was not recorded", exc_info=True)

    def _finish_failure(self, request: Any, reason: str, *, status_code: int | None = None) -> None:
        ticket = self._ticket(request)
        if ticket is None:
            return
        try:
            self.store.append(
                kind="issue", topic=ticket.topic, text=f"The engineer could not answer: {reason}",
                source=ticket.source, session_id=ticket.session_id, lap=ticket.lap,
                related_id=ticket.question_id, priority="high",
                detail={"reason": reason, **({"http_status": status_code} if status_code else {})},
            )
        except Exception:
            LOGGER.warning("engineer activity issue was not recorded", exc_info=True)


def _outcome(result: Any) -> tuple[int, Any]:
    """Return (status code, parsed JSON body) for a handler result."""
    if isinstance(result, dict):
        return 200, result
    status_code = getattr(result, "status_code", None)
    raw = getattr(result, "body", None)
    if not isinstance(status_code, int) or not isinstance(raw, (bytes, bytearray)):
        return 200, None
    if len(raw) > _MAX_PARSED_BODY_BYTES:
        return status_code, None
    try:
        return status_code, json.loads(raw)
    except ValueError:
        return status_code, None
