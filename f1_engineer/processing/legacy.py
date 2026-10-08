from __future__ import annotations

import json
from pathlib import Path

from ..storage.database import Database


def archived_session_page(path: Path, *, limit: int = 100, after: str = "") -> list[dict[str, object]]:
    if not 1 <= limit <= 100:
        raise ValueError("legacy session page exceeds budget")
    with Database(path, read_only=True) as database:
        rows = database.connection.execute("""
            SELECT s.session_key,s.run_id,s.session_uid,s.context_json,r.capture_sha256
            FROM sessions s JOIN processing_runs r USING(run_id)
            JOIN captures c USING(capture_sha256)
            WHERE r.status='complete' AND c.complete=1 AND s.session_key>?
            ORDER BY s.session_key LIMIT ?
        """, (after, limit)).fetchall()
        return [{"id": f"archive:{row['session_key']}", "legacy_session_key": row["session_key"],
                 "generation": row["run_id"], "uid": row["session_uid"], "lifecycle": "ended",
                 "acquisition": "archived", "context": json.loads(row["context_json"]) if row["context_json"] else None,
                 "capture_sha256": row["capture_sha256"], "live_revision": None,
                 "limitations": ["legacy_run_identity_not_merged_with_live_sessions", "use_legacy_integrity_checked_analysis"]}
                for row in rows]
