from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ai_admission import AIGateLease

SPEECH_RUNTIME_NAME = "whisper.cpp"
SPEECH_MODEL_NAME = "tiny.en"
SPEECH_SAMPLE_RATE = 16_000
SPEECH_MAX_SECONDS = 12
SPEECH_MAX_SAMPLES = SPEECH_SAMPLE_RATE * SPEECH_MAX_SECONDS
SPEECH_MAX_AUDIO_BYTES = 512 * 1_024
SPEECH_MAX_TRANSCRIPT_BYTES = 4 * 1_024
SPEECH_MAX_RESPONSE_BYTES = 16 * 1_024
SPEECH_PROCESS_TIMEOUT_SECONDS = 55.0
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_TEMP_DIR_PATTERN = re.compile(r"^gp-speech-[a-f0-9]{32}$")
_TRANSCRIPTION_TEXT_LIMIT = 4 * 1_024
_STDOUT_LIMIT = 4 * 1_024
_STDERR_LIMIT = 16 * 1_024
_TEMPORARY_CLEANUP_RETRY_SECONDS = 5.0


class LocalSpeechUnavailable(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class WhisperPin:
    runtime_version: str
    executable: Path
    executable_sha256: str
    model: Path
    model_sha256: str
    dependencies: tuple[tuple[Path, str], ...]
    runtime_id: str


@dataclass(frozen=True, slots=True)
class LocalTranscription:
    transcript: str
    audio_sha256: str
    runtime_id: str
    runtime_version: str
    model_name: str
    model_sha256: str


class LocalSpeechRuntime:
    """Pinned, local-only whisper.cpp CLI with bounded temporary inputs/outputs."""

    def __init__(self, pin_path: str | Path, temporary_root: str | Path) -> None:
        self._pin_path = Path(os.path.abspath(Path(pin_path).expanduser()))
        self._temporary_root = Path(os.path.abspath(Path(temporary_root).expanduser()))

    def status(self) -> dict[str, object]:
        try:
            pin = self._load_verified_pin()
        except LocalSpeechUnavailable as exc:
            return self._unavailable(exc.reason)
        return {
            "status": "ready",
            "reason": None,
            "runtime": SPEECH_RUNTIME_NAME,
            "runtime_version": pin.runtime_version,
            "model_name": SPEECH_MODEL_NAME,
            "model_sha256": pin.model_sha256,
            "runtime_id": pin.runtime_id,
            "language": "en",
            "placement": "CPU",
            "maximum_clip_seconds": SPEECH_MAX_SECONDS,
            "audio_persisted": False,
        }

    async def transcribe(
        self, wav: bytes, *, gate_lease: AIGateLease | None = None
    ) -> LocalTranscription:
        _validate_canonical_wav(wav)
        pin_verification = asyncio.create_task(
            asyncio.to_thread(self._load_verified_pin)
        )
        try:
            pin = await asyncio.shield(pin_verification)
        except asyncio.CancelledError:
            if gate_lease is not None:
                gate_lease.retain_until(pin_verification)
            else:
                await _wait_for_task_cleanup(pin_verification)
            raise
        audio_sha256 = hashlib.sha256(wav).hexdigest()
        try:
            self._temporary_root.mkdir(parents=True, exist_ok=True)
            temporary_root_info = self._temporary_root.lstat()
        except OSError as exc:
            raise LocalSpeechUnavailable("temporary_directory_unavailable") from exc
        if not stat.S_ISDIR(temporary_root_info.st_mode) or self._temporary_root.is_symlink():
            raise LocalSpeechUnavailable("temporary_directory_unavailable")
        directory = self._temporary_root / f"gp-speech-{uuid.uuid4().hex}"
        directory.mkdir(mode=0o700)
        wav_path = directory / "clip.wav"
        output_prefix = directory / "transcript"
        transcript_path = directory / "transcript.txt"
        process: asyncio.subprocess.Process | None = None
        stdout_task: asyncio.Task[tuple[bytes, bool]] | None = None
        stderr_task: asyncio.Task[tuple[bytes, bool]] | None = None
        reap_task: asyncio.Task[None] | None = None
        try:
            wav_path.write_bytes(wav)
            arguments = [
                str(pin.executable),
                "-m",
                str(pin.model),
                "-f",
                str(wav_path),
                "-l",
                "en",
                "-t",
                "2",
                "-ng",
                "-nt",
                "-np",
                "-otxt",
                "-of",
                str(output_prefix),
            ]
            try:
                spawn = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        *arguments,
                        cwd=str(pin.executable.parent),
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                )
                try:
                    process = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    spawn_cleanup = asyncio.create_task(_reap_cancelled_spawn(spawn))
                    await _wait_for_task_cleanup(spawn_cleanup)
                    raise
            except (OSError, ValueError) as exc:
                raise LocalSpeechUnavailable("runtime_launch_failed") from exc
            assert process.stdout is not None and process.stderr is not None
            stdout_task = asyncio.create_task(_read_process_stream(process.stdout, _STDOUT_LIMIT))
            stderr_task = asyncio.create_task(_read_process_stream(process.stderr, _STDERR_LIMIT))
            try:
                async with asyncio.timeout(SPEECH_PROCESS_TIMEOUT_SECONDS):
                    await process.wait()
                    stdout_result, stderr_result = await asyncio.gather(stdout_task, stderr_task)
            except TimeoutError as exc:
                reap_task = asyncio.create_task(
                    _terminate_and_reap(process, stdout_task, stderr_task)
                )
                if await _wait_for_task_cleanup(reap_task):
                    raise asyncio.CancelledError
                raise LocalSpeechUnavailable("transcription_timeout") from exc
            if process.returncode != 0:
                raise LocalSpeechUnavailable("transcription_failed")
            if stdout_result[1] or stderr_result[1]:
                raise LocalSpeechUnavailable("runtime_output_limit_exceeded")
            try:
                output_info = transcript_path.lstat()
                if (
                    not stat.S_ISREG(output_info.st_mode)
                    or transcript_path.is_symlink()
                    or output_info.st_size > _TRANSCRIPTION_TEXT_LIMIT
                ):
                    raise LocalSpeechUnavailable("transcript_output_invalid")
                transcript_bytes = transcript_path.read_bytes()
            except OSError as exc:
                raise LocalSpeechUnavailable("transcript_output_missing") from exc
            if len(transcript_bytes) > _TRANSCRIPTION_TEXT_LIMIT:
                raise LocalSpeechUnavailable("transcript_output_limit_exceeded")
            try:
                transcript = transcript_bytes.decode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise LocalSpeechUnavailable("transcript_encoding_invalid") from exc
            transcript = transcript.lstrip("\ufeff").strip()
            encoded_transcript = transcript.encode("utf-8", errors="strict")
            if len(encoded_transcript) > SPEECH_MAX_TRANSCRIPT_BYTES:
                raise LocalSpeechUnavailable("transcript_output_limit_exceeded")
            if not transcript:
                raise LocalSpeechUnavailable("transcript_empty")
            return LocalTranscription(
                transcript=transcript,
                audio_sha256=audio_sha256,
                runtime_id=pin.runtime_id,
                runtime_version=pin.runtime_version,
                model_name=SPEECH_MODEL_NAME,
                model_sha256=pin.model_sha256,
            )
        except asyncio.CancelledError:
            if process is not None:
                if reap_task is None:
                    reap_task = asyncio.create_task(
                        _terminate_and_reap(process, stdout_task, stderr_task)
                    )
                await _wait_for_task_cleanup(reap_task)
            raise
        finally:
            try:
                shutil.rmtree(directory)
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_task = asyncio.create_task(
                    _retry_remove_directory(directory)
                )
                if gate_lease is not None:
                    gate_lease.retain_until(cleanup_task)
                raise LocalSpeechUnavailable("temporary_cleanup_failed") from exc

    def cleanup_stale_temporary_files(self, *, older_than_seconds: float = 3_600.0) -> int:
        try:
            self._temporary_root.mkdir(parents=True, exist_ok=True)
            root_info = self._temporary_root.lstat()
            if not stat.S_ISDIR(root_info.st_mode) or self._temporary_root.is_symlink():
                return 0
            root = self._temporary_root.resolve(strict=True)
            cutoff = time.time() - older_than_seconds
            removed = 0
            for entry in root.iterdir():
                try:
                    info = entry.lstat()
                    if (
                        not _TEMP_DIR_PATTERN.fullmatch(entry.name)
                        or not stat.S_ISDIR(info.st_mode)
                        or entry.is_symlink()
                        or info.st_mtime > cutoff
                        or entry.resolve(strict=True).parent != root
                    ):
                        continue
                    shutil.rmtree(entry)
                    removed += 1
                except OSError:
                    continue
            return removed
        except OSError:
            return 0

    def _load_verified_pin(self) -> WhisperPin:
        try:
            info = self._pin_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or self._pin_path.is_symlink()
                or info.st_size > 8 * 1_024
            ):
                raise LocalSpeechUnavailable("runtime_pin_invalid")
            raw = self._pin_path.read_bytes()
            if len(raw) > 8 * 1_024:
                raise LocalSpeechUnavailable("runtime_pin_invalid")
            value = json.loads(raw.decode("utf-8", errors="strict"))
        except LocalSpeechUnavailable:
            raise
        except FileNotFoundError as exc:
            raise LocalSpeechUnavailable("runtime_not_configured") from exc
        except (OSError, UnicodeError, ValueError, RecursionError, OverflowError) as exc:
            raise LocalSpeechUnavailable("runtime_pin_invalid") from exc
        required = {
            "schema_version",
            "runtime",
            "runtime_version",
            "executable_path",
            "executable_sha256",
            "model_name",
            "model_path",
            "model_sha256",
            "dependencies",
            "runtime_id",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise LocalSpeechUnavailable("runtime_pin_invalid")
        if (
            value.get("schema_version") != 1
            or value.get("runtime") != SPEECH_RUNTIME_NAME
            or value.get("model_name") != SPEECH_MODEL_NAME
        ):
            raise LocalSpeechUnavailable("runtime_pin_invalid")
        runtime_version = value.get("runtime_version")
        if (
            not isinstance(runtime_version, str)
            or not re.fullmatch(r"1\.9\.3", runtime_version)
        ):
            raise LocalSpeechUnavailable("runtime_version_unsupported")
        executable = _absolute_regular_file(
            value.get("executable_path"), "runtime_executable_invalid", 128 * 1024 * 1024
        )
        model = _absolute_regular_file(
            value.get("model_path"), "runtime_model_invalid", 128 * 1024 * 1024
        )
        executable_digest = _digest(value.get("executable_sha256"))
        model_digest = _digest(value.get("model_sha256"))
        runtime_id = _digest(value.get("runtime_id"))
        dependencies_value = value.get("dependencies")
        if not isinstance(dependencies_value, list) or len(dependencies_value) > 32:
            raise LocalSpeechUnavailable("runtime_pin_invalid")
        dependencies: list[tuple[Path, str]] = []
        dependency_names: set[str] = set()
        for item in dependencies_value:
            if not isinstance(item, dict) or set(item) != {"name", "sha256"}:
                raise LocalSpeechUnavailable("runtime_pin_invalid")
            name = item.get("name")
            digest = _digest(item.get("sha256"))
            if (
                not isinstance(name, str)
                or not name.lower().endswith(".dll")
                or Path(name).name != name
                or name.casefold() in dependency_names
            ):
                raise LocalSpeechUnavailable("runtime_pin_invalid")
            dependency_names.add(name.casefold())
            dependency_path = _absolute_regular_file(
                str(executable.parent / name), "runtime_dependency_invalid", 128 * 1024 * 1024
            )
            dependencies.append((dependency_path, digest))
        if sum(path.stat().st_size for path, _digest_value in dependencies) > 384 * 1024 * 1024:
            raise LocalSpeechUnavailable("runtime_pin_invalid")
        try:
            actual_dependency_names = {
                item.name.casefold()
                for item in executable.parent.iterdir()
                if item.is_file() and item.suffix.casefold() == ".dll"
            }
        except OSError as exc:
            raise LocalSpeechUnavailable("runtime_dependency_unavailable") from exc
        if actual_dependency_names != dependency_names:
            raise LocalSpeechUnavailable("runtime_dependency_set_changed")
        expected_runtime_id = _runtime_identity(
            runtime_version,
            executable_digest,
            model_digest,
            tuple((path.name, digest) for path, digest in dependencies),
        )
        if runtime_id != expected_runtime_id:
            raise LocalSpeechUnavailable("runtime_pin_invalid")
        if _file_sha256(executable) != executable_digest:
            raise LocalSpeechUnavailable("runtime_binary_changed")
        if _file_sha256(model) != model_digest:
            raise LocalSpeechUnavailable("runtime_model_changed")
        for path, expected_digest in dependencies:
            if _file_sha256(path) != expected_digest:
                raise LocalSpeechUnavailable("runtime_dependency_changed")
        return WhisperPin(
            runtime_version=runtime_version,
            executable=executable,
            executable_sha256=executable_digest,
            model=model,
            model_sha256=model_digest,
            dependencies=tuple(dependencies),
            runtime_id=runtime_id,
        )

    @staticmethod
    def _unavailable(reason: str) -> dict[str, object]:
        return {
            "status": "unavailable",
            "reason": reason[:96],
            "runtime": SPEECH_RUNTIME_NAME,
            "runtime_version": "1.9.3",
            "model_name": SPEECH_MODEL_NAME,
            "model_sha256": None,
            "runtime_id": None,
            "language": "en",
            "placement": "CPU",
            "maximum_clip_seconds": SPEECH_MAX_SECONDS,
            "audio_persisted": False,
        }


def validate_canonical_wav(wav: bytes) -> None:
    _validate_canonical_wav(wav)


def _validate_canonical_wav(wav: bytes) -> None:
    if not isinstance(wav, bytes) or len(wav) < 46 or len(wav) > SPEECH_MAX_AUDIO_BYTES:
        raise LocalSpeechUnavailable("audio_size_invalid")
    if wav[0:4] != b"RIFF" or wav[8:16] != b"WAVEfmt ":
        raise LocalSpeechUnavailable("audio_wav_header_invalid")
    riff_size = int.from_bytes(wav[4:8], "little")
    fmt_size = int.from_bytes(wav[16:20], "little")
    audio_format = int.from_bytes(wav[20:22], "little")
    channels = int.from_bytes(wav[22:24], "little")
    sample_rate = int.from_bytes(wav[24:28], "little")
    byte_rate = int.from_bytes(wav[28:32], "little")
    block_align = int.from_bytes(wav[32:34], "little")
    bits_per_sample = int.from_bytes(wav[34:36], "little")
    data_header = wav[36:40]
    data_size = int.from_bytes(wav[40:44], "little")
    if (
        riff_size != len(wav) - 8
        or fmt_size != 16
        or audio_format != 1
        or channels != 1
        or sample_rate != SPEECH_SAMPLE_RATE
        or byte_rate != SPEECH_SAMPLE_RATE * 2
        or block_align != 2
        or bits_per_sample != 16
        or data_header != b"data"
        or data_size <= 0
        or data_size % 2 != 0
        or data_size > SPEECH_MAX_SAMPLES * 2
        or len(wav) != 44 + data_size
    ):
        raise LocalSpeechUnavailable("audio_wav_format_invalid")


async def _read_process_stream(
    stream: asyncio.StreamReader, maximum: int
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    exceeded = False
    while True:
        chunk = await stream.read(1_024)
        if not chunk:
            break
        size += len(chunk)
        if size <= maximum:
            chunks.append(chunk)
        else:
            exceeded = True
    return b"".join(chunks), exceeded


async def _wait_for_task_cleanup(task: asyncio.Task[Any]) -> bool:
    current = asyncio.current_task()
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
            interrupted = True
            if current is not None:
                current.uncancel()
        except BaseException:
            if task.done():
                break
            raise
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            raise error
    return interrupted


async def _reap_cancelled_spawn(
    spawn: asyncio.Task[asyncio.subprocess.Process],
) -> None:
    try:
        process = await spawn
    except (OSError, ValueError):
        return
    if process.stdout is None or process.stderr is None:
        await process.wait()
        return
    stdout_task = asyncio.create_task(
        _read_process_stream(process.stdout, _STDOUT_LIMIT)
    )
    stderr_task = asyncio.create_task(
        _read_process_stream(process.stderr, _STDERR_LIMIT)
    )
    await _terminate_and_reap(process, stdout_task, stderr_task)


async def _retry_remove_directory(directory: Path) -> None:
    while True:
        try:
            await asyncio.to_thread(shutil.rmtree, directory)
            return
        except FileNotFoundError:
            return
        except OSError:
            await asyncio.sleep(_TEMPORARY_CLEANUP_RETRY_SECONDS)


async def _terminate_and_reap(
    process: asyncio.subprocess.Process,
    stdout_task: asyncio.Task[tuple[bytes, bool]] | None,
    stderr_task: asyncio.Task[tuple[bytes, bool]] | None,
) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()
    readers = [task for task in (stdout_task, stderr_task) if task is not None]
    if readers:
        await asyncio.gather(*readers, return_exceptions=True)


def _absolute_regular_file(value: object, reason: str, maximum_bytes: int) -> Path:
    if not isinstance(value, str) or len(value) > 1_024:
        raise LocalSpeechUnavailable("runtime_pin_invalid")
    path = Path(value)
    if not path.is_absolute():
        raise LocalSpeechUnavailable(reason)
    try:
        info = path.lstat()
    except OSError as exc:
        raise LocalSpeechUnavailable(reason) from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or path.is_symlink()
        or info.st_size <= 0
        or info.st_size > maximum_bytes
    ):
        raise LocalSpeechUnavailable(reason)
    return path


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise LocalSpeechUnavailable("runtime_pin_invalid")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise LocalSpeechUnavailable("runtime_file_unavailable") from exc
    return digest.hexdigest()


def _runtime_identity(
    runtime_version: str,
    executable_sha256: str,
    model_sha256: str,
    dependencies: tuple[tuple[str, str], ...],
) -> str:
    identity = {
        "runtime": SPEECH_RUNTIME_NAME,
        "runtime_version": runtime_version,
        "executable_sha256": executable_sha256,
        "model_name": SPEECH_MODEL_NAME,
        "model_sha256": model_sha256,
        "dependencies": [
            {"name": name, "sha256": digest}
            for name, digest in sorted(dependencies, key=lambda entry: entry[0].casefold())
        ],
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
