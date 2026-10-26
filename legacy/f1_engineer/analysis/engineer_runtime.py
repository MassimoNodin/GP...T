from __future__ import annotations

from typing import Protocol

from .engineer_ask import EngineerAskRoute


class EngineerRuntimeUnavailable(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EngineerRuntime(Protocol):
    async def status(self) -> dict[str, object]: ...

    async def route_question(
        self, question: str, *, selection_kind: str, allowed_routes: tuple[str, ...]
    ) -> tuple[EngineerAskRoute, dict[str, object]]: ...
