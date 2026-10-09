from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from f1_engineer.analysis.local_speech import LocalSpeechRuntime, _runtime_identity

RUNTIME_VERSION = "1.9.3"
SOURCE_COMMIT = "371b5a7561823ab2bb32142d2751e35e7534727b"
MODEL_SHA1 = "c78c86eb1a8faa21b369bcd33207cc90d64ae9df"
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin"
MAX_MODEL_BYTES = 128 * 1024 * 1024


def file_digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(model: Path) -> None:
    if model.exists():
        if model.is_symlink() or file_digest(model, "sha1") != MODEL_SHA1:
            raise RuntimeError("Existing tiny.en model does not match the pinned checksum")
        return
    with tempfile.NamedTemporaryFile(dir=model.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_MODEL_BYTES:
                        raise RuntimeError("Model download exceeds the runtime size limit")
                    stream.write(chunk)
            stream.close()
            if file_digest(temporary, "sha1") != MODEL_SHA1:
                raise RuntimeError("Downloaded tiny.en model does not match the pinned checksum")
            temporary.replace(model)
        finally:
            stream.close()
            temporary.unlink(missing_ok=True)


def write_pin(pin: Path, executable: Path, model: Path, accept_update: bool) -> str:
    executable_digest = file_digest(executable)
    model_digest = file_digest(model)
    runtime_id = _runtime_identity(RUNTIME_VERSION, executable_digest, model_digest, ())
    manifest = {
        "schema_version": 1,
        "runtime": "whisper.cpp",
        "runtime_version": RUNTIME_VERSION,
        "executable_path": str(executable.resolve()),
        "executable_sha256": executable_digest,
        "model_name": "tiny.en",
        "model_path": str(model.resolve()),
        "model_sha256": model_digest,
        "dependencies": [],
        "runtime_id": runtime_id,
    }
    if pin.is_symlink():
        raise RuntimeError("Refusing to replace a symlinked runtime pin")
    if pin.exists() and not accept_update:
        existing = json.loads(pin.read_text(encoding="utf-8"))
        if existing != manifest:
            raise RuntimeError("Runtime pin changed; review before using --accept-runtime-update")
    pin.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=pin.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(manifest, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            status = LocalSpeechRuntime(temporary, pin.parent / ".speech-tmp").status()
            if status["status"] != "ready":
                raise RuntimeError(f"Built runtime verification failed: {status['reason']}")
            temporary.replace(pin)
        finally:
            stream.close()
            temporary.unlink(missing_ok=True)
    return runtime_id


def restore_generated_version(path: Path, original: bytes) -> None:
    current = path.read_bytes()
    if current == original:
        return
    expected = original.replace(b'"version": "1.9.3"', b'"version": "1.9.3-dev"', 1)
    if current != expected:
        raise RuntimeError("Unexpected source changes during CMake configuration; preserved for review")
    path.write_bytes(original)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and pin the Ubuntu CPU speech runtime without sudo")
    parser.add_argument("--database", type=Path, default=Path("data/f1-engineer.sqlite3"))
    parser.add_argument("--install-root", type=Path, default=Path.home() / ".local/share/GP...T/speech-runtime/v1.9.3")
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=2)
    parser.add_argument("--accept-runtime-update", action="store_true")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Use setup_local_speech_runtime.ps1 on Windows")
    for command in ("git", "g++", args.cmake):
        if not shutil.which(command):
            parser.error(f"Missing build tool: {command}")
    root = args.install_root.expanduser().absolute()
    if root.is_symlink():
        parser.error("Refusing a symlinked install root")
    root.mkdir(parents=True, exist_ok=True)
    source = root / f"source-{SOURCE_COMMIT[:12]}"
    build = root / f"build-{SOURCE_COMMIT[:12]}"
    model_root = root / "models"
    for directory in (source, build, model_root):
        if directory.is_symlink():
            parser.error(f"Refusing a symlinked runtime directory: {directory}")
    if not source.exists():
        subprocess.run(["git", "init", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "remote", "add", "origin", "https://github.com/ggml-org/whisper.cpp.git"], check=True)
        subprocess.run(["git", "-C", str(source), "fetch", "--depth=1", "origin", SOURCE_COMMIT], check=True)
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"], check=True)
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    changes = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"], text=True).strip()
    if actual != SOURCE_COMMIT or changes:
        raise RuntimeError("Speech source must be the exact pinned commit with a clean checkout")
    generated_package = source / "bindings/javascript/package.json"
    original_package = generated_package.read_bytes()
    try:
        subprocess.run([
            args.cmake, "-S", str(source), "-B", str(build),
            "-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_BUILD_EXAMPLES=ON", "-DWHISPER_FFMPEG=OFF",
            "-DGGML_NATIVE=OFF", "-DGGML_CUDA=OFF", "-DGGML_OPENCL=OFF", "-DGGML_VULKAN=OFF",
            "-DGGML_OPENMP=OFF", "-DBUILD_SHARED_LIBS=OFF", "-DCMAKE_BUILD_TYPE=Release",
        ], check=True)
    finally:
        restore_generated_version(generated_package, original_package)
    subprocess.run([args.cmake, "--build", str(build), "--target", "whisper-cli", "--parallel", str(args.jobs)], check=True)
    executable = build / "bin/whisper-cli"
    model_root.mkdir(exist_ok=True)
    model = model_root / "ggml-tiny.en.bin"
    download_model(model)
    pin = args.database.expanduser().absolute().parent / ".f1-engineer-whisper-pin.json"
    runtime_id = write_pin(pin, executable, model, args.accept_runtime_update)
    print(f"Pinned CPU speech runtime: {pin}\nRuntime ID: {runtime_id}")


if __name__ == "__main__":
    main()
