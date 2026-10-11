from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Callable

from ..processing.evidence import EvidenceUnavailable, digest, encode


_WAIT_TIMEOUT_SECONDS = 30.0
_MAX_INFLIGHT = 2
_MAX_ENTRIES = 32
_MAX_BYTES = 16 * 1024 * 1024


@dataclass
class _Flight:
    generation: int
    done: Event = field(default_factory=Event)
    result: dict | None = None
    error: BaseException | None = None


class EvidenceCache:
    """Bounded LRU for immutable measurements, not live reference selection.

    Misses count every nonresident lookup, including duplicates and rejections.
    Clear resets entries and counters without cancelling active work: existing
    callers receive its outcome without repopulating the cleared cache.
    Byte accounting covers canonical JSON payloads, not Python object overhead.
    """

    def __init__(self, max_entries: int = 32, max_bytes: int = 16 * 1024 * 1024) -> None:
        for name, value, ceiling in (("max_entries", max_entries, _MAX_ENTRIES),
                                     ("max_bytes", max_bytes, _MAX_BYTES)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
            if value > ceiling:
                raise ValueError(f"{name} must be <= {ceiling}")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, tuple[dict, int]] = OrderedDict()
        self._flights: dict[str, _Flight] = {}
        self._lock = Lock()
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._generation = 0

    def get_or_compute(self, key: str, compute: Callable[[], dict]) -> dict:
        if not isinstance(key, str):
            raise TypeError("measurement cache key must be a string")
        with self._lock:
            if key in self._entries:
                value, _ = self._entries[key]
                self._entries.move_to_end(key)
                self._hits += 1
                return deepcopy(value)
            self._misses += 1
            flight = self._flights.get(key)
            owner = flight is None
            if owner:
                if len(self._flights) >= _MAX_INFLIGHT:
                    raise EvidenceUnavailable("measurement_cache_busy")
                flight = _Flight(self._generation)
                self._flights[key] = flight

        if not owner:
            if not flight.done.wait(_WAIT_TIMEOUT_SECONDS):
                raise EvidenceUnavailable("measurement_cache_wait_timeout")
            if flight.error is not None:
                raise flight.error
            return deepcopy(flight.result)

        try:
            computed = compute()
            if not isinstance(computed, dict):
                raise TypeError("measurement cache compute must return a dict")
            value = deepcopy(computed)
            size = len(encode(value).encode("utf-8"))
            if size > self._max_bytes:
                raise EvidenceUnavailable("measurement_cache_entry_over_budget")
            result = deepcopy(value)
            with self._lock:
                if flight.generation == self._generation:
                    while self._entries and (
                        len(self._entries) >= self._max_entries
                        or self._bytes + size > self._max_bytes
                    ):
                        _, (_, removed_size) = self._entries.popitem(last=False)
                        self._bytes -= removed_size
                    self._entries[key] = (value, size)
                    self._bytes += size
                flight.result = value
            return result
        except BaseException as error:
            flight.error = error
            raise
        finally:
            with self._lock:
                self._flights.pop(key, None)
                flight.done.set()

    def status(self) -> dict:
        with self._lock:
            return {"entries": len(self._entries), "bytes": self._bytes,
                    "hits": self._hits, "misses": self._misses,
                    "inflight": len(self._flights)}

    def clear(self) -> None:
        with self._lock:
            self._generation += 1
            self._entries.clear()
            self._bytes = 0
            self._hits = 0
            self._misses = 0


def measurement_cache_key(
    target: dict, reference: dict, *, policy: str, analysis_version: str
) -> str:
    """Hash ordered immutable identities, manifests and measurement parameters.

    Immutable payload content and explicit source/materialization provenance
    are included; live readiness and candidate sets are not. Callers must
    version all algorithm parameters.
    """
    def identity(attempt: dict) -> dict:
        result = {"id": attempt["id"], "session": attempt["session"],
                  "driver": attempt["driver"],
                  "manifest_hash": digest(encode(attempt["manifest"])),
                  "payload_hash": digest(encode(attempt.get("payload")))}
        for name in ("source", "source_provenance", "provenance", "generation",
                     "evidence_generation", "materialization"):
            if name in attempt:
                result[name] = attempt[name]
        return result

    return digest(encode({"target": identity(target), "reference": identity(reference),
                          "policy": policy, "analysis_version": analysis_version}))
