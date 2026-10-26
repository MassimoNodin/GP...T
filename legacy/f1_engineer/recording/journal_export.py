from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .. import __version__
from ..udp.header import HEADER_SIZE
from ..udp.models import RawDatagram
from .capture import CaptureWriter

# Bytes 8-15 (1-based SQLite offsets) of the common F1 header hold the little-endian session UID.
_UID_OFFSET, _UID_BYTES = 8, 8


def _connect(path: str | Path) -> sqlite3.Connection:
    database = Path(path).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"evidence database not found: {database}")
    return sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=30)


def _uid(blob: bytes) -> int:
    return int.from_bytes(blob, "little")


def _contexts(connection: sqlite3.Connection) -> dict[str, list[dict]]:
    contexts: dict[str, list[dict]] = {}
    try:
        rows = connection.execute("SELECT uid, context FROM sessions").fetchall()
    except sqlite3.Error:
        return contexts
    for uid, context in rows:
        try:
            value = json.loads(context) if isinstance(context, str) else {}
        except ValueError:
            value = {}
        contexts.setdefault(str(uid), []).append(value if isinstance(value, dict) else {})
    return contexts


def list_journal_sessions(path: str | Path) -> list[dict[str, object]]:
    """Summarize journaled datagrams per game session UID, longest recorded span first."""
    with _connect(path) as connection:
        groups = connection.execute(
            f"""SELECT substr(payload, {_UID_OFFSET}, {_UID_BYTES}) AS uid, generation, count(*),
                       min(sequence), max(sequence)
                FROM journal WHERE kind = 'datagram' AND length(payload) >= {HEADER_SIZE}
                GROUP BY uid, generation"""
        ).fetchall()
        contexts = _contexts(connection)
        sessions = []
        for uid_blob, generation, count, first, last in groups:
            uid = _uid(uid_blob)
            if uid == 0:
                continue
            times = [json.loads(connection.execute(
                "SELECT metadata FROM journal WHERE generation = ? AND sequence = ?", (generation, sequence)
            ).fetchone()[0]) for sequence in (first, last)]
            context = next(iter(contexts.get(str(uid), [])), {})
            sessions.append({
                "session_uid": str(uid), "generation": generation, "datagrams": count,
                "first_sequence": first, "last_sequence": last,
                "duration_s": round((times[1]["monotonic_ns"] - times[0]["monotonic_ns"]) / 1e9, 3),
                "started_at_ns": times[0]["captured_at_ns"],
                "track": context.get("track_name"), "session_type": context.get("session_type"),
                "total_laps": context.get("total_laps"),
            })
    return sorted(sessions, key=lambda session: session["duration_s"], reverse=True)


def export_journal_session(path: str | Path, session_uid: int | str, output: str | Path, *,
                           max_gap_s: float | None = None, overwrite: bool = False) -> dict[str, object]:
    """Write one journaled session back out as a replayable F1ECAP capture.

    Every payload is checked against its stored SHA-256. With ``max_gap_s``, idle gaps longer than that
    (pause menus, flashbacks) are shortened in the capture timeline; packet bytes and order never change.
    """
    target = int(session_uid)
    if not 0 < target < 2**64:
        raise ValueError("session UID must be a non-zero unsigned 64-bit integer")
    if max_gap_s is not None and not 0.05 <= max_gap_s <= 3600:
        raise ValueError("max gap must be between 0.05 and 3600 seconds")
    uid_blob = target.to_bytes(_UID_BYTES, "little")
    sessions = [session for session in list_journal_sessions(path) if session["session_uid"] == str(target)]
    if not sessions:
        raise LookupError(f"no journaled datagrams for session UID {target}")
    session = max(sessions, key=lambda item: item["datagrams"])
    limit_ns = int(max_gap_s * 1e9) if max_gap_s is not None else None
    written = shortened = 0
    removed_ns = 0
    previous = None
    metadata = {
        "application": "f1-race-engineer", "application_version": __version__,
        "source": "evidence_journal_export", "source_database": Path(path).name,
        "source_generation": session["generation"], "session_uid": str(target),
        "track": session["track"], "session_type": session["session_type"],
        "max_gap_s": max_gap_s,
    }
    with _connect(path) as connection, CaptureWriter(output, metadata, overwrite=overwrite) as writer:
        rows = connection.execute(
            f"""SELECT sequence, payload, metadata FROM journal
                WHERE generation = ? AND kind = 'datagram' AND sequence BETWEEN ? AND ?
                  AND substr(payload, {_UID_OFFSET}, {_UID_BYTES}) = ?
                ORDER BY sequence""",
            (session["generation"], session["first_sequence"], session["last_sequence"], uid_blob))
        for sequence, payload, encoded in rows:
            details = json.loads(encoded)
            if details.get("payload_sha256") != hashlib.sha256(payload).hexdigest():
                raise ValueError(f"journal checksum mismatch at sequence {sequence}")
            monotonic = details["monotonic_ns"]
            if previous is not None:
                gap = monotonic - previous
                if gap < 0:
                    raise ValueError(f"journal clock moved backwards at sequence {sequence}")
                if limit_ns is not None and gap > limit_ns:
                    removed_ns += gap - limit_ns
                    shortened += 1
            previous = monotonic
            writer.write(RawDatagram(
                sequence=written + 1, captured_at_ns=details["captured_at_ns"] - removed_ns,
                monotonic_ns=monotonic - removed_ns, source_host=details.get("source_host") or "journal",
                source_port=int(details.get("source_port") or 0), payload=bytes(payload)))
            written += 1
        summary = {"datagrams": written, "source_duration_s": session["duration_s"],
                   "replay_duration_s": round(session["duration_s"] - removed_ns / 1e9, 3),
                   "gaps_shortened": shortened, "removed_idle_s": round(removed_ns / 1e9, 3)}
        writer.close(summary)
    return {"output": str(Path(output)), **metadata, **summary}
