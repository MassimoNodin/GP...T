from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import threading
from pathlib import Path

import pytest

import f1_engineer.analysis.local_speech as local_speech_module
from f1_engineer.analysis.ai_admission import EngineerAIGate
from f1_engineer.analysis.local_speech import (
    LocalSpeechRuntime,
    LocalSpeechUnavailable,
    SPEECH_MAX_SAMPLES,
    validate_canonical_wav,
    _runtime_identity,
)


def canonical_wav(sample_count: int = 1) -> bytes:
    pcm = b"\x00\x00" * sample_count
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


def write_pin(directory: Path) -> tuple[Path, Path, Path]:
    executable = directory / "whisper-cli.exe"
    model = directory / "ggml-tiny.en.bin"
    dependency = directory / "ggml-base.dll"
    executable.write_bytes(b"pinned test executable")
    model.write_bytes(b"pinned tiny.en model")
    dependency.write_bytes(b"pinned runtime dependency")
    executable_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
    model_sha256 = hashlib.sha256(model.read_bytes()).hexdigest()
    dependency_sha256 = hashlib.sha256(dependency.read_bytes()).hexdigest()
    dependencies = ((dependency.name, dependency_sha256),)
    runtime_id = _runtime_identity(
        "1.9.3", executable_sha256, model_sha256, dependencies
    )
    pin = {
        "schema_version": 1,
        "runtime": "whisper.cpp",
        "runtime_version": "1.9.3",
        "executable_path": str(executable.resolve()),
        "executable_sha256": executable_sha256,
        "model_name": "tiny.en",
        "model_path": str(model.resolve()),
        "model_sha256": model_sha256,
        "dependencies": [{"name": dependency.name, "sha256": dependency_sha256}],
        "runtime_id": runtime_id,
    }
    (directory / "pin.json").write_text(json.dumps(pin), encoding="utf-8")
    return executable, model, dependency


class FakeProcess:
    def __init__(self, *, block: bool = False, stderr: bytes = b"") -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_eof()
        if stderr:
            self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode: int | None = None
        self._block = block
        self._exited = asyncio.Event()
        if not block:
            self.returncode = 0

    async def wait(self) -> int:
        if self._block:
            await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9
        self._exited.set()


def test_canonical_wav_accepts_minimum_and_maximum_payloads() -> None:
    validate_canonical_wav(canonical_wav())
    largest = canonical_wav(SPEECH_MAX_SAMPLES)
    validate_canonical_wav(largest)
    assert len(largest) == 44 + SPEECH_MAX_SAMPLES * 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda wav: wav + b"x",
        lambda wav: wav[:4] + b"\x00\x00\x00\x00" + wav[8:],
        lambda wav: wav[:20] + b"\x03\x00" + wav[22:],
        lambda wav: wav[:22] + b"\x02\x00" + wav[24:],
        lambda wav: wav[:24] + struct.pack("<I", 44_100) + wav[28:],
        lambda wav: wav[:40] + b"\x00\x00\x00\x00" + wav[44:],
    ],
)
def test_canonical_wav_rejects_malformed_or_noncanonical_audio(mutation) -> None:
    with pytest.raises(LocalSpeechUnavailable):
        validate_canonical_wav(mutation(canonical_wav(2)))


def test_runtime_status_checks_executable_model_and_dependency_hashes(tmp_path: Path) -> None:
    executable, model, dependency = write_pin(tmp_path)
    runtime = LocalSpeechRuntime(tmp_path / "pin.json", tmp_path / "temporary")

    status = runtime.status()
    assert status["status"] == "ready"
    assert status["model_name"] == "tiny.en"

    dependency.write_bytes(b"changed DLL")
    changed_status = runtime.status()
    assert changed_status["status"] == "unavailable"
    assert changed_status["reason"] == "runtime_dependency_changed"


def test_runtime_invokes_fixed_cpu_english_cli_and_removes_clip_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, model, _dependency = write_pin(tmp_path)
    temporary_root = tmp_path / "temporary"
    runtime = LocalSpeechRuntime(tmp_path / "pin.json", temporary_root)
    observed: dict[str, object] = {}

    async def create_process(*arguments: str, **kwargs: object) -> FakeProcess:
        observed["arguments"] = arguments
        observed["kwargs"] = kwargs
        output_prefix = Path(arguments[-1])
        Path(f"{output_prefix}.txt").write_text("Ready for your question.", encoding="utf-8")
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    result = asyncio.run(runtime.transcribe(canonical_wav(16_000)))

    arguments = observed["arguments"]
    assert isinstance(arguments, tuple)
    assert arguments[0] == str(executable)
    assert arguments[arguments.index("-m") + 1] == str(model)
    assert arguments[arguments.index("-l") + 1] == "en"
    assert arguments[arguments.index("-t") + 1] == "2"
    assert "-ng" in arguments and "-otxt" in arguments
    assert result.transcript == "Ready for your question."
    assert len(result.audio_sha256) == 64
    assert list(temporary_root.iterdir()) == []


def test_runtime_bounds_child_diagnostics_and_cleans_failed_clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_pin(tmp_path)
    temporary_root = tmp_path / "temporary"
    runtime = LocalSpeechRuntime(tmp_path / "pin.json", temporary_root)

    async def noisy_process(*_arguments: str, **_kwargs: object) -> FakeProcess:
        return FakeProcess(stderr=b"x" * (16 * 1_024 + 1))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", noisy_process)
    with pytest.raises(LocalSpeechUnavailable, match="runtime_output_limit_exceeded"):
        asyncio.run(runtime.transcribe(canonical_wav()))
    assert list(temporary_root.iterdir()) == []


def test_cancelled_transcription_kills_and_reaps_child_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_pin(tmp_path)
    temporary_root = tmp_path / "temporary"
    runtime = LocalSpeechRuntime(tmp_path / "pin.json", temporary_root)
    started = asyncio.Event()
    process: FakeProcess | None = None

    async def blocking_process(*_arguments: str, **_kwargs: object) -> FakeProcess:
        nonlocal process
        process = FakeProcess(block=True)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", blocking_process)

    async def exercise() -> None:
        task = asyncio.create_task(runtime.transcribe(canonical_wav()))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert process is not None and process.returncode == -9
    assert list(temporary_root.iterdir()) == []


def test_cancelled_process_spawn_drains_pipes_before_reaping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_pin(tmp_path)
    temporary_root = tmp_path / "temporary"
    runtime = LocalSpeechRuntime(tmp_path / "pin.json", temporary_root)
    spawn_started = asyncio.Event()
    allow_spawn = asyncio.Event()
    process: FakeProcess | None = None

    async def delayed_process(*_arguments: str, **_kwargs: object) -> FakeProcess:
        nonlocal process
        spawn_started.set()
        await allow_spawn.wait()
        process = FakeProcess(block=True, stderr=b"diagnostic" * 4_096)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_process)

    async def exercise() -> None:
        task = asyncio.create_task(runtime.transcribe(canonical_wav()))
        await asyncio.wait_for(spawn_started.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        allow_spawn.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)

    asyncio.run(exercise())
    assert process is not None and process.returncode == -9
    assert process.stderr is not None and not process.stderr._buffer
    assert list(temporary_root.iterdir()) == []


def test_cancelled_pin_verification_keeps_ai_gate_until_hash_worker_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_pin(tmp_path)
    runtime = LocalSpeechRuntime(tmp_path / "pin.json", tmp_path / "temporary")
    original_load = runtime._load_verified_pin
    worker_started = threading.Event()
    finish_worker = threading.Event()

    def delayed_load():
        worker_started.set()
        finish_worker.wait(timeout=2)
        return original_load()

    monkeypatch.setattr(runtime, "_load_verified_pin", delayed_load)
    gate = EngineerAIGate()
    lease = gate.try_acquire()
    assert lease is not None

    async def exercise() -> None:
        task = asyncio.create_task(
            runtime.transcribe(canonical_wav(), gate_lease=lease)
        )
        assert await asyncio.to_thread(worker_started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        lease.close()
        assert gate.try_acquire() is None
        finish_worker.set()
        async with asyncio.timeout(1):
            while True:
                next_lease = gate.try_acquire()
                if next_lease is not None:
                    next_lease.close()
                    break
                await asyncio.sleep(0.01)

    asyncio.run(exercise())


def test_temporary_cleanup_failure_keeps_gate_until_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_pin(tmp_path)
    temporary_root = tmp_path / "temporary"
    runtime = LocalSpeechRuntime(tmp_path / "pin.json", temporary_root)
    original_rmtree = local_speech_module.shutil.rmtree
    cleanup_started = threading.Event()
    allow_cleanup = threading.Event()
    call_count = 0

    def fail_once(path, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise PermissionError("test cleanup failure")
        cleanup_started.set()
        allow_cleanup.wait(timeout=2)
        return original_rmtree(path, *args, **kwargs)

    async def fake_process(*arguments: str, **_kwargs: object) -> FakeProcess:
        output_prefix = Path(arguments[-1])
        Path(f"{output_prefix}.txt").write_text("Ready.", encoding="utf-8")
        return FakeProcess()

    monkeypatch.setattr(local_speech_module.shutil, "rmtree", fail_once)
    monkeypatch.setattr(local_speech_module.asyncio, "create_subprocess_exec", fake_process)
    monkeypatch.setattr(
        local_speech_module,
        "_TEMPORARY_CLEANUP_RETRY_SECONDS",
        0.01,
    )
    gate = EngineerAIGate()
    lease = gate.try_acquire()
    assert lease is not None

    async def exercise() -> None:
        with pytest.raises(LocalSpeechUnavailable, match="temporary_cleanup_failed"):
            await runtime.transcribe(canonical_wav(), gate_lease=lease)
        lease.close()
        assert gate.try_acquire() is None
        assert await asyncio.to_thread(cleanup_started.wait, 1)
        assert gate.try_acquire() is None
        allow_cleanup.set()
        async with asyncio.timeout(1):
            while True:
                next_lease = gate.try_acquire()
                if next_lease is not None:
                    next_lease.close()
                    break
                await asyncio.sleep(0.01)

    asyncio.run(exercise())


def test_ai_gate_remains_held_for_cancel_detached_worker() -> None:
    gate = EngineerAIGate()
    lease = gate.try_acquire()
    assert lease is not None
    worker_release = threading.Event()

    async def exercise() -> None:
        worker = asyncio.create_task(asyncio.to_thread(worker_release.wait))
        lease.retain_until(worker)
        lease.close()
        assert gate.try_acquire() is None
        worker_release.set()
        await worker
        await asyncio.sleep(0)

    asyncio.run(exercise())
    next_lease = gate.try_acquire()
    assert next_lease is not None
    next_lease.close()
