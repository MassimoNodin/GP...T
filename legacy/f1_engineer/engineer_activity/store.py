from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
MAX_ENTRIES = 5_000
MAX_TEXT_CHARS = 2_000
MAX_DETAIL_BYTES = 4_096
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
BUSY_TIMEOUT_S = 2.0

KINDS = ("proactive", "question", "response", "issue")
SOURCES = ("v1_ask", "v2_ask", "live_rules", "system", "radio")
TOPICS = ("gap", "tyres", "lap_time", "track_limits", "pit", "telemetry", "general")
PRIORITIES = ("low", "normal", "high")

_COLUMNS = (
    "id, created_at_utc, kind, topic, text, session_id, lap, source, related_id, priority, detail"
)


class EngineerActivityUnavailable(RuntimeError):
    """The activity file cannot be opened or has an unsupported schema."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _bounded_detail(detail: dict[str, Any] | None) -> str | None:
    if detail is None:
        return None
    try:
        encoded = json.dumps(detail, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return json.dumps({"truncated": True, "reason": "detail_not_serialisable"})
    if len(encoded.encode("utf-8")) > MAX_DETAIL_BYTES:
        return json.dumps({"truncated": True, "reason": "detail_too_large"})
    return encoded


class EngineerActivityStore:
    """Append-only engineer activity log in its own SQLite file.

    Rows are never updated; the only deletion is bounded retention of the oldest
    entries. The file is opened lazily so a bad file never prevents the API from
    starting; callers see `EngineerActivityUnavailable` or `sqlite3.Error`.
    """

    def __init__(self, path: str | Path, *, max_entries: int = MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.path = Path(path).expanduser().resolve()
        self.max_entries = max_entries
        self._ready = False
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self._ensure_schema()
        return self._open()

    def _open(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA synchronous=NORMAL")
        return database

    def _ensure_schema(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                database = self._open()
            except (OSError, sqlite3.Error) as exception:
                raise EngineerActivityUnavailable("engineer_activity_unavailable") from exception
            try:
                version = database.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1, SCHEMA_VERSION):
                    raise EngineerActivityUnavailable("unsupported_engineer_activity_schema")
                if version == 0:
                    database.execute("PRAGMA journal_mode=WAL")
                    database.executescript(f"""
                        CREATE TABLE IF NOT EXISTS engineer_activity (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            created_at_utc TEXT NOT NULL,
                            kind TEXT NOT NULL CHECK (kind IN ('proactive','question','response','issue')),
                            topic TEXT NOT NULL,
                            text TEXT NOT NULL,
                            session_id TEXT,
                            lap INTEGER,
                            source TEXT NOT NULL CHECK (source IN ('v1_ask','v2_ask','live_rules','system')),
                            related_id INTEGER,
                            priority TEXT NOT NULL,
                            detail TEXT,
                            dedupe_key TEXT UNIQUE
                        );
                        CREATE INDEX IF NOT EXISTS engineer_activity_session
                            ON engineer_activity(session_id, id);
                        CREATE TABLE IF NOT EXISTS engineer_activity_meta (
                            key TEXT PRIMARY KEY, value TEXT NOT NULL
                        );
                        CREATE TRIGGER IF NOT EXISTS engineer_activity_append_only
                            BEFORE UPDATE ON engineer_activity
                            BEGIN SELECT RAISE(ABORT, 'append-only engineer activity'); END;
                        PRAGMA user_version=1;
                    """)
                    database.commit()
                with database:
                    database.execute("BEGIN IMMEDIATE")
                    if database.execute("PRAGMA user_version").fetchone()[0] == 1:
                        schema = database.execute(
                            "SELECT sql FROM sqlite_master WHERE type='table' AND name='engineer_activity'"
                        ).fetchone()[0]
                        objects = database.execute(
                            "SELECT sql FROM sqlite_master WHERE tbl_name='engineer_activity' "
                            "AND type IN ('index','trigger') AND sql IS NOT NULL"
                        ).fetchall()
                        sequence = database.execute(
                            "SELECT seq FROM sqlite_sequence WHERE name='engineer_activity'"
                        ).fetchone()
                        database.execute(schema.replace("engineer_activity", "engineer_activity_new", 1)
                                         .replace("'live_rules','system'", "'live_rules','system','radio'"))
                        database.execute("INSERT INTO engineer_activity_new SELECT * FROM engineer_activity")
                        database.execute("DROP TABLE engineer_activity")
                        database.execute("ALTER TABLE engineer_activity_new RENAME TO engineer_activity")
                        for row in objects:
                            database.execute(row[0])
                        if sequence is not None:
                            database.execute("UPDATE sqlite_sequence SET seq=? WHERE name='engineer_activity'",
                                             (sequence[0],))
                        database.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            except EngineerActivityUnavailable:
                raise
            except sqlite3.Error as exception:
                raise EngineerActivityUnavailable("engineer_activity_unavailable") from exception
            finally:
                database.close()
            self._ready = True

    def append(
        self,
        *,
        kind: str,
        text: str,
        source: str,
        topic: str = "general",
        session_id: str | None = None,
        lap: int | None = None,
        related_id: int | None = None,
        priority: str = "normal",
        detail: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> int | None:
        """Append one entry and return its id, or None when `dedupe_key` already exists."""
        if kind not in KINDS or source not in SOURCES or priority not in PRIORITIES:
            raise ValueError("invalid engineer activity entry")
        if topic not in TOPICS:
            topic = "general"
        text = text[:MAX_TEXT_CHARS]
        database = self._connect()
        try:
            with database:
                if dedupe_key is not None and database.execute(
                    "SELECT 1 FROM engineer_activity WHERE dedupe_key=?", (dedupe_key,)
                ).fetchone():
                    return None
                cursor = database.execute(
                    "INSERT OR IGNORE INTO engineer_activity "
                    "(created_at_utc, kind, topic, text, session_id, lap, source, related_id,"
                    " priority, detail, dedupe_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (utc_now(), kind, topic, text, session_id, lap, source, related_id,
                     priority, _bounded_detail(detail), dedupe_key),
                )
                if cursor.rowcount == 0:
                    return None
                entry_id = int(cursor.lastrowid)
                database.execute(
                    "DELETE FROM engineer_activity WHERE id<=?", (entry_id - self.max_entries,)
                )
                return entry_id
        finally:
            database.close()

    def events(
        self,
        *,
        after: int | None = None,
        before: int | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        session_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (events oldest-first, next_after).

        With 'before', return the newest older entries, still oldest first.
        The forward and backward cursors are mutually exclusive.
        With 'after', entries with a larger id are returned oldest first. Without it,
        the newest `limit` entries are returned. Global system/live notices are
        included for every session filter, but unbound questions are not.
        `next_after` is the largest returned id,
        or `after` (0 when absent) when nothing was returned.
        """
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("engineer activity page exceeds budget")
        if after is not None and before is not None:
            raise ValueError("engineer activity cursors are mutually exclusive")
        for cursor in (after, before):
            if cursor is not None and (isinstance(cursor, bool) or not isinstance(cursor, int)
                                       or not 0 <= cursor <= 2**62):
                raise ValueError("invalid engineer activity cursor")
        clauses: list[str] = []
        parameters: list[Any] = []
        if session_id is not None:
            clauses.append("(session_id=? OR (session_id IS NULL AND source IN ('system','live_rules')))")
            parameters.append(session_id)
        if before is not None:
            clauses.append("id<?")
            parameters.append(before)
        database = self._connect()
        try:
            if after is None:
                where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                rows = database.execute(
                    f"SELECT {_COLUMNS} FROM (SELECT {_COLUMNS} FROM engineer_activity {where} "
                    "ORDER BY id DESC LIMIT ?) ORDER BY id",
                    (*parameters, limit),
                ).fetchall()
            else:
                clauses.insert(0, "id>?")
                parameters.insert(0, after)
                rows = database.execute(
                    f"SELECT {_COLUMNS} FROM engineer_activity WHERE {' AND '.join(clauses)} "
                    "ORDER BY id LIMIT ?",
                    (*parameters, limit),
                ).fetchall()
        finally:
            database.close()
        events = [_event(row) for row in rows]
        next_after = events[-1]["id"] if events else (after or 0)
        return events, next_after

    def get_meta(self, key: str) -> str | None:
        database = self._connect()
        try:
            row = database.execute(
                "SELECT value FROM engineer_activity_meta WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else None
        finally:
            database.close()

    def set_meta(self, key: str, value: str) -> None:
        database = self._connect()
        try:
            with database:
                database.execute(
                    "INSERT INTO engineer_activity_meta(key,value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
        finally:
            database.close()


def _event(row: sqlite3.Row) -> dict[str, Any]:
    detail = None
    if row["detail"] is not None:
        try:
            detail = json.loads(row["detail"])
        except ValueError:
            detail = None
    return {
        "id": row["id"],
        "created_at_utc": row["created_at_utc"],
        "kind": row["kind"],
        "topic": row["topic"],
        "text": row["text"],
        "session_id": row["session_id"],
        "lap": row["lap"],
        "source": row["source"],
        "related_id": row["related_id"],
        "priority": row["priority"],
        "detail": detail,
    }
