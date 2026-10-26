from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

from .ai_admission import AIGateLease
from .local_speech import LocalSpeechRuntime, LocalSpeechUnavailable, LocalTranscription
from .openai_speech import OpenAISpeechRuntime
from .speech_settings import DEVICES, MODELS, SpeechSettingsStore


class SpeechRouter:
    def __init__(self, directory: Path, *, provider: Literal["local", "openai"] = "local",
                 key_file: str | Path | None = None) -> None:
        self.directory = directory
        self.legacy_path = directory / ".f1-engineer-whisper-pin.json"
        self.legacy = LocalSpeechRuntime(self.legacy_path, directory / ".f1-engineer-speech")
        self.locals = {
            (model, device): LocalSpeechRuntime(
                directory / f".f1-engineer-whisper-pin.{model}.{device}.json",
                directory / ".f1-engineer-speech",
            ) for model in MODELS for device in DEVICES
        }
        self.cloud = OpenAISpeechRuntime(key_file or directory / ".openai-api-key")
        model, device = self._legacy_variant() or ("tiny.en", "cpu")
        self.store = SpeechSettingsStore(directory / ".f1-engineer-speech-settings.json", {
            "provider": provider, "local": {"model": model, "device": device},
        })
        self._lifecycle_lock = asyncio.Lock()
        self._cloud_active = 0
        self._closing = False

    def _legacy_variant(self) -> tuple[str, str] | None:
        try:
            with self.legacy_path.open("rb") as handle:
                document = json.loads(handle.read(16385))
            model, device = document.get("model_name"), document.get("device", "cpu")
            if model in MODELS and device in DEVICES:
                return model, device
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def local_runtime(self, model: str, device: str) -> LocalSpeechRuntime:
        runtime = self.locals[(model, device)]
        if not runtime._pin_path.exists() and not runtime._pin_path.is_symlink():
            if self._legacy_variant() == (model, device):
                return self.legacy
        return runtime

    def resolve(self) -> LocalSpeechRuntime | OpenAISpeechRuntime:
        settings = self.store.load()["settings"]
        if settings["provider"] == "openai":
            return self.cloud
        model, device = settings["local"]["model"], settings["local"]["device"]
        runtime = self.local_runtime(model, device)
        status = runtime.status()
        if status["status"] == "ready" and (
                status["model_name"] != model or status["placement"] != device.upper()):
            raise LocalSpeechUnavailable("runtime_variant_mismatch")
        return runtime

    def status(self) -> dict[str, object]:
        try:
            return self.resolve().status()
        except LocalSpeechUnavailable as exc:
            return {"status": "unavailable", "reason": exc.reason}
        except (OSError, ValueError):
            return {"status": "unavailable", "reason": "speech_settings_unreadable"}

    def options(self) -> list[dict[str, object]]:
        options = []
        for model in MODELS:
            for device in DEVICES:
                runtime = self.local_runtime(model, device)
                status = runtime.status()
                ready = (status["status"] == "ready" and status["model_name"] == model
                         and status["placement"] == device.upper())
                reason = status.get("reason") or (None if ready else "runtime_variant_mismatch")
                installed = runtime._pin_path.exists() or runtime._pin_path.is_symlink()
                options.append({"provider": "local", "model": model, "device": device,
                                "status": "ready" if ready else (
                                    "invalid" if installed else "not_installed"), "reason": reason})
        cloud = self.cloud.status()
        options.append({"provider": "openai", "status": cloud["status"], "reason": cloud["reason"]})
        return options

    def document(self) -> dict[str, Any]:
        return {**self.store.load(), "options": self.options(), "selected_status": self.status()}

    def _cloud_selected(self) -> bool:
        try:
            return self.store.load()["settings"]["provider"] == "openai"
        except (OSError, ValueError):
            return False

    async def sync_selection(self) -> None:
        async with self._lifecycle_lock:
            if not self._closing and self._cloud_selected() and self.cloud.status()["status"] == "ready":
                await self.cloud.start()
            else:
                await self.cloud.stop_keepalive()
                if not self._cloud_active:
                    await self.cloud.close()

    async def start(self) -> None:
        self._closing = False
        await self.sync_selection()

    async def close(self) -> None:
        async with self._lifecycle_lock:
            self._closing = True
            await self.cloud.stop_keepalive()
            if not self._cloud_active:
                await self.cloud.close()

    def cleanup_stale_temporary_files(self) -> None:
        self.legacy.cleanup_stale_temporary_files()

    async def transcribe(self, wav: bytes, *, gate_lease: AIGateLease | None = None,
                         hint: str | None = None,
                         runtime: LocalSpeechRuntime | OpenAISpeechRuntime | None = None) -> LocalTranscription:
        selected = runtime if runtime is not None else self.resolve()
        if selected is not self.cloud:
            return await selected.transcribe(wav, gate_lease=gate_lease, hint=hint)
        async with self._lifecycle_lock:
            await self.cloud.start()
            if not self._cloud_selected():
                await self.cloud.stop_keepalive()
            self._cloud_active += 1
        try:
            return await selected.transcribe(wav, hint=hint)
        finally:
            async with self._lifecycle_lock:
                self._cloud_active -= 1
                if not self._cloud_active and (self._closing or not self._cloud_selected()):
                    await self.cloud.close()
