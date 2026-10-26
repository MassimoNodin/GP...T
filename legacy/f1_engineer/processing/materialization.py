from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from .coordinator import SessionCoordinator
from .evidence import MAX_READ_BYTES, MAX_READ_ROWS, PROCESSOR_VERSION, EvidenceStore, EvidenceUnavailable, digest, encode


MATERIALIZER_VERSION = "historical-detail-v1"


class MaterializedEvidenceProvider:
    def __init__(self, base: Any) -> None:
        self.base = base
        self._derived: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}

    def add(self, metadata: dict[str, Any], records: list[dict[str, Any]]) -> None:
        original = self.base.attempt(metadata["id"], session=metadata["session"])
        if metadata["id"] not in self._derived and len(self._derived) >= 2:
            raise EvidenceUnavailable("materialization_overlay_budget_exceeded")
        if len(records) > MAX_READ_ROWS or len(encode([metadata, records]).encode()) > MAX_READ_BYTES:
            raise EvidenceUnavailable("analysis_read_budget_exceeded")
        provenance = metadata.get("materialization") or {}
        if any(metadata[key] != original[key] for key in ("id", "session", "driver", "original_id", "role", "payload")) or (
            provenance.get("source_revision") != original["id"]
            or provenance.get("source_session") != original["session"]
            or provenance.get("source_manifest_hash") != digest(encode(original["manifest"]))
            or any(metadata["manifest"].get(key) != original["manifest"].get(key)
                   for key in ("driver", "epoch", "format", "start_frame", "end_frame"))
        ):
            raise EvidenceUnavailable("materialization_overlay_identity_mismatch")
        if (metadata.get("readiness") or {}).get("state") != "published":
            raise EvidenceUnavailable("materialization_coverage_unavailable")
        self._derived[metadata["id"]] = (copy.deepcopy(metadata), copy.deepcopy(records))

    def attempt(self, attempt_id: str, *, session: str | None = None) -> dict[str, Any]:
        if attempt_id not in self._derived:
            return self.base.attempt(attempt_id, session=session)
        metadata = self._derived[attempt_id][0]
        if session is not None and metadata["session"] != session:
            raise EvidenceUnavailable("attempt_not_in_session")
        return copy.deepcopy(metadata)

    def attempts(self, session: str, *, limit: int = 100, after: str = "") -> list[dict[str, Any]]:
        return [self.attempt(item["id"], session=session)
                for item in self.base.attempts(session, limit=limit, after=after)]

    def evidence(self, attempt_id: str, *, session: str | None = None
                 ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if attempt_id not in self._derived:
            return self.base.evidence(attempt_id, session=session)
        return self.attempt(attempt_id, session=session), copy.deepcopy(self._derived[attempt_id][1])


@dataclass(frozen=True)
class MaterializationLimits:
    journal_entries: int = 100_000
    source_bytes: int = 128 * 1024 * 1024
    temporary_bytes: int = 256 * 1024 * 1024
    duration_s: float = 30.0

    def __post_init__(self) -> None:
        maxima = (100_000, 128 * 1024 * 1024, 256 * 1024 * 1024, 30.0)
        values = (self.journal_entries, self.source_bytes, self.temporary_bytes, self.duration_s)
        if any(not isinstance(value, int) for value in values[:3]) or any(
            not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= maximum
               for value, maximum in zip(values, maxima)):
            raise ValueError("invalid materialization limits")


class HistoricalMaterializer:
    def __init__(self, store: EvidenceStore, *, limits: MaterializationLimits | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.limits = limits or MaterializationLimits()
        self.clock = clock
        self._admission = threading.Lock()
        self._status_lock = threading.Lock()
        self._completed = 0
        self._failed = 0
        self._active = False

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            return {"version": MATERIALIZER_VERSION, "active": self._active,
                    "completed": self._completed, "failed": self._failed,
                    "persistence": "request_scoped_ephemeral"}

    def materialize(self, attempt_id: str, *, session: str,
                    cancelled: Callable[[], bool] | None = None
                    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not self._admission.acquire(blocking=False):
            raise EvidenceUnavailable("materialization_busy")
        with self._status_lock:
            self._active = True
        try:
            result = self._materialize(attempt_id, session, cancelled)
            with self._status_lock:
                self._completed += 1
            return result
        except BaseException:
            with self._status_lock:
                self._failed += 1
            raise
        finally:
            with self._status_lock:
                self._active = False
            self._admission.release()

    def _check(self, started: float, cancelled: Callable[[], bool] | None,
               directory: Path | None = None) -> None:
        if cancelled is not None and cancelled():
            raise EvidenceUnavailable("materialization_cancelled")
        if self.clock() - started >= self.limits.duration_s:
            raise EvidenceUnavailable("materialization_time_budget_exceeded")
        if directory is not None:
            size = sum(item.stat().st_size for item in directory.iterdir() if item.is_file())
            if size > self.limits.temporary_bytes:
                raise EvidenceUnavailable("materialization_storage_budget_exceeded")

    def _materialize(self, attempt_id: str, session: str,
                     cancelled: Callable[[], bool] | None
                     ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        started = self.clock()
        self._check(started, cancelled)
        with self.store.connect() as source:
            source.execute("BEGIN")
            row = source.execute("SELECT * FROM attempts WHERE id=? AND session=?",
                                 (attempt_id, session)).fetchone()
            if row is None:
                raise EvidenceUnavailable("attempt_not_in_session")
            original = self.store._attempt(source, row)
            state = (original.get("readiness") or {}).get("state")
            if state == "published":
                return self.store.evidence(attempt_id, session=session)
            if state != "deferred" or not original["driver"]:
                raise EvidenceUnavailable("materialization_attempt_not_deferred")
            session_row = source.execute("SELECT * FROM sessions WHERE id=?", (session,)).fetchone()
            if session_row is None:
                raise EvidenceUnavailable("materialization_session_unavailable")
            generation = source.execute("SELECT * FROM generations WHERE id=?",
                                        (session_row["generation"],)).fetchone()
            if generation is None or generation["version"] != PROCESSOR_VERSION:
                raise EvidenceUnavailable("materialization_processor_version_unavailable")
            prefix = original["published_sequence"]
            if prefix > generation["committed_sequence"] or prefix < 1:
                raise EvidenceUnavailable("materialization_source_not_committed")
            if prefix > self.limits.journal_entries:
                raise EvidenceUnavailable("materialization_source_budget_exceeded")
            binding = source.execute("SELECT * FROM bindings WHERE id=? AND session=?",
                                     (original["driver"], session)).fetchone()
            if binding is None:
                raise EvidenceUnavailable("materialization_binding_unavailable")
            budget = source.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(payload)+length(CAST(metadata AS BLOB))),0),"
                "MIN(sequence),MAX(sequence) FROM journal WHERE generation=? AND sequence<=?",
                (generation["id"], prefix),
            ).fetchone()
            if budget[0] > self.limits.journal_entries or budget[1] > self.limits.source_bytes:
                raise EvidenceUnavailable("materialization_source_budget_exceeded")
            if budget[0] != prefix or budget[2] != 1 or budget[3] != prefix:
                raise EvidenceUnavailable("materialization_journal_prefix_incomplete")
            with TemporaryDirectory(prefix="f1-detail-") as temporary:
                directory = Path(temporary)
                derived_store = EvidenceStore(directory / "derived.sqlite3")
                coordinator = SessionCoordinator(derived_store, "historical-materialization", recover=False)
                journal_hash = hashlib.sha256()
                try:
                    cursor = source.execute(
                        "SELECT * FROM journal WHERE generation=? AND sequence<=? ORDER BY sequence",
                        (generation["id"], prefix),
                    )
                    for entry in cursor:
                        self._check(started, cancelled, directory)
                        if entry["kind"] not in {"datagram", "gap", "finish", "detail_demand"}:
                            raise EvidenceUnavailable("materialization_unknown_journal_kind")
                        try:
                            metadata = json.loads(entry["metadata"])
                        except (TypeError, ValueError) as exception:
                            raise EvidenceUnavailable("materialization_journal_metadata_invalid") from exception
                        if not isinstance(metadata, dict):
                            raise EvidenceUnavailable("materialization_journal_metadata_invalid")
                        payload = bytes(entry["payload"])
                        if metadata.get("payload_sha256") != hashlib.sha256(payload).hexdigest():
                            raise EvidenceUnavailable("materialization_journal_checksum_mismatch")
                        journal_hash.update(encode({"sequence": entry["sequence"], "kind": entry["kind"],
                                                   "metadata": metadata}).encode())
                        coordinator._admit(entry["kind"], payload, metadata)
                    self._check(started, cancelled, directory)
                    return self._extract(derived_store, original, session_row, binding,
                                         coordinator.generation, prefix, journal_hash.hexdigest())
                finally:
                    coordinator.close()

    def _extract(self, derived_store: EvidenceStore, original: dict[str, Any],
                 session_row: Any, binding: Any, generation: str, prefix: int,
                 journal_hash: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with derived_store.connect() as database:
            derived_session = database.execute(
                "SELECT id FROM sessions WHERE generation=? AND uid=? AND occurrence=?",
                (generation, session_row["uid"], session_row["occurrence"]),
            ).fetchone()
            if derived_session is None:
                raise EvidenceUnavailable("materialization_session_unavailable")
            matches = database.execute(
                "SELECT * FROM attempts WHERE session=? AND original_id=? AND role=?",
                (derived_session["id"], original["original_id"], original["role"]),
            ).fetchmany(2)
            if len(matches) != 1:
                raise EvidenceUnavailable("materialization_attempt_unavailable")
            derived = derived_store._attempt(database, matches[0])
            derived_binding = database.execute("SELECT * FROM bindings WHERE id=?", (derived["driver"],)).fetchone()
            if derived_binding is None or any(derived_binding[key] != binding[key]
                                             for key in ("epoch", "format", "car", "tenure", "fingerprint", "start_frame")):
                raise EvidenceUnavailable("materialization_identity_mismatch")
            if derived["payload"] != original["payload"] or any(
                derived["manifest"][key] != original["manifest"][key]
                for key in ("epoch", "format", "start_frame", "end_frame")
            ):
                raise EvidenceUnavailable("materialization_attempt_mismatch")
            if derived["readiness"]["state"] != "published" or set(derived["manifest"]["qualifications"]) & {
                "missing_observation_rows", "acquisition_gap", "lap_start_unobserved"
            }:
                raise EvidenceUnavailable("materialization_coverage_unavailable")
        derived, records = derived_store.evidence(derived["id"], session=derived_session["id"])
        projected = copy.deepcopy(derived)
        projected.update({"id": original["id"], "session": original["session"],
                          "driver": original["driver"], "published_sequence": original["published_sequence"]})
        projected["manifest"]["driver"] = original["driver"]
        projected["detail"] = {"profile": MATERIALIZER_VERSION, "state": "available", "reason": None}
        manifest_hash = digest(encode(projected["manifest"]))
        derived_revision = digest(encode({"version": MATERIALIZER_VERSION,
                                          "source_revision": original["id"],
                                          "journal_hash": journal_hash,
                                          "manifest_hash": manifest_hash}))
        projected["materialization"] = {
            "version": MATERIALIZER_VERSION, "processor_version": PROCESSOR_VERSION,
            "source_generation": session_row["generation"], "source_session": original["session"],
            "source_revision": original["id"], "source_manifest_hash": digest(encode(original["manifest"])),
            "journal_start": 1, "journal_end": prefix, "journal_hash": journal_hash,
            "derived_revision": derived_revision, "derived_manifest_hash": manifest_hash,
            "persistence": "request_scoped_ephemeral",
        }
        return projected, records
