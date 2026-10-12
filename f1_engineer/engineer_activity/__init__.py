"""Durable engineer activity log, ask recording and deterministic in-session calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .ask_log import AskActivityLog, question_topic
from .calls import (
    ALWAYS_ALLOWED_TOPICS,
    AttemptSource,
    EngineerCallObserver,
    EvidenceAttemptSource,
    TopicProvider,
)
from .store import (
    MAX_ENTRIES,
    EngineerActivityStore,
    EngineerActivityUnavailable,
)

ACTIVITY_DATABASE_SUFFIX = "-engineer-activity.sqlite3"

__all__ = [
    "ACTIVITY_DATABASE_SUFFIX",
    "ALWAYS_ALLOWED_TOPICS",
    "AskActivityLog",
    "AttemptSource",
    "EngineerActivity",
    "EngineerActivityStore",
    "EngineerActivityUnavailable",
    "EngineerCallObserver",
    "EvidenceAttemptSource",
    "MAX_ENTRIES",
    "build_engineer_activity",
    "question_topic",
]


class EngineerActivity:
    """Wiring bundle: store, ask recorder and (live acquisition only) call observer."""

    def __init__(
        self,
        store: EngineerActivityStore,
        ask_log: AskActivityLog,
        observer: EngineerCallObserver | None,
    ) -> None:
        self.store = store
        self.ask_log = ask_log
        self.observer = observer
        self._topics: TopicProvider | None = None
        if observer is not None:
            observer.set_topics(self.current_topics)

    def set_topics(self, provider: TopicProvider | None) -> None:
        """Install the assistance-topic filter: a callable returning enabled topics or None."""
        self._topics = provider

    def current_topics(self) -> set[str] | frozenset[str] | None:
        provider = self._topics
        return provider() if provider is not None else None

    def start(self) -> None:
        if self.observer is not None:
            self.observer.start()

    def stop(self) -> None:
        if self.observer is not None:
            self.observer.stop()


def build_engineer_activity(
    database_path: str | Path,
    evidence_store: Any,
    live_runtime: Any | None,
) -> EngineerActivity:
    """Create the activity bundle beside `database_path`; no file is touched until first use."""
    path = Path(database_path)
    store = EngineerActivityStore(path.with_name(path.stem + ACTIVITY_DATABASE_SUFFIX))
    observer = (
        EngineerCallObserver(store, live_runtime, EvidenceAttemptSource(evidence_store))
        if live_runtime is not None else None
    )
    return EngineerActivity(
        store, AskActivityLog(store, evidence_store, legacy_database_path=path), observer,
    )
