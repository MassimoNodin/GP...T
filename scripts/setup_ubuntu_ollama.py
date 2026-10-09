from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

VERSION = "0.40.2"
ARCHIVE_SHA256 = "726bee78706c281b0eeef00746efe51a044d71c592c3f0b195820707f31fdf04"
ARCHIVE_URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-linux-amd64.tar.zst"
ENDPOINT = "http://127.0.0.1:11435"
MODEL = "qwen3:4b"
MAX_ARCHIVE_BYTES = 2 * 1024**3


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise RuntimeError("Refusing to replace a symlinked metadata file")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            temporary.replace(path)
        finally:
            stream.close()
            temporary.unlink(missing_ok=True)


def install_binary(root: Path) -> Path:
    executable = root / "bin/ollama"
    marker = root / ".install.json"
    if root.is_symlink() or marker.is_symlink():
        raise RuntimeError("Refusing redirected installation paths")
    if marker.exists():
        identity = json.loads(marker.read_text())
        if (identity.get("archive_sha256") != ARCHIVE_SHA256
                or executable.is_symlink()
                or digest_file(executable) != identity.get("executable_sha256")):
            raise RuntimeError("Installed Ollama identity changed; preserved for review")
        return executable
    if root.exists():
        raise RuntimeError("Unverified install directory already exists; preserved for review")
    root.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root.parent).free < 12 * 1024**3:
        raise RuntimeError("At least 12 GiB free is required for initial runtime/model provisioning")
    with tempfile.TemporaryDirectory(prefix="ollama-install-", dir=root.parent) as temporary:
        staging = Path(temporary)
        archive = staging / "release.tar.zst"
        print(f"Downloading checksum-pinned Ollama {VERSION}", flush=True)
        digest = hashlib.sha256()
        total = 0
        with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response, archive.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("Runtime archive exceeds its size limit")
                digest.update(chunk)
                stream.write(chunk)
        if digest.hexdigest() != ARCHIVE_SHA256:
            raise RuntimeError("Ollama release archive checksum mismatch")
        unpacked = staging / "runtime"
        unpacked.mkdir()
        process = subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as bundle:
                bundle.extractall(unpacked, filter="data")
        finally:
            process.stdout.close()
            process.wait()
        if process.returncode != 0:
            raise RuntimeError("Runtime archive decompression failed")
        binary = unpacked / "bin/ollama"
        if not binary.is_file() or binary.is_symlink():
            raise RuntimeError("Release does not contain the expected regular executable")
        atomic_json(unpacked / ".install.json", {
            "version": VERSION, "archive_sha256": ARCHIVE_SHA256,
            "executable_sha256": digest_file(binary),
        })
        unpacked.rename(root)
    return executable


def systemd_quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("Systemd values must not contain newlines")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_text(executable: Path, models: Path) -> str:
    return "\n".join([
        "[Unit]", "Description=GP...T private CPU model runtime", "After=network.target", "",
        "[Service]", "Type=simple", f"ExecStart={systemd_quote(str(executable))} serve",
        f"Environment={systemd_quote('OLLAMA_MODELS=' + str(models))}",
        "Environment=OLLAMA_HOST=127.0.0.1:11435", "Environment=OLLAMA_NO_CLOUD=1",
        "Environment=OLLAMA_NUM_PARALLEL=1", "Environment=OLLAMA_MAX_LOADED_MODELS=1",
        "Environment=OLLAMA_MAX_QUEUE=1", "Environment=OLLAMA_KEEP_ALIVE=0",
        "Restart=on-failure", "RestartSec=3", "TimeoutStopSec=30", "UMask=0077",
        "NoNewPrivileges=true", "", "[Install]", "WantedBy=default.target", "",
    ])


def get_json(path: str) -> dict:
    with urllib.request.urlopen(ENDPOINT + path, timeout=5) as response:
        body = response.read(8193)
    if len(body) > 8192:
        raise RuntimeError("Runtime status response exceeds its size limit")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError("Unexpected runtime status response")
    return value


def pin_model(pin: Path, model: dict, accept_update: bool) -> None:
    digest = model.get("digest", "")
    if (model.get("name") != MODEL or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or model.get("remote_host") or model.get("remote_model")):
        raise RuntimeError("Only the exact locally downloaded model may be pinned")
    value = {
        "schema_version": 1, "profile": "gp-dot-t-private-v1", "endpoint": ENDPOINT,
        "no_cloud_requested": True, "model": MODEL, "digest": "sha256:" + digest,
    }
    if pin.exists() and not accept_update and json.loads(pin.read_text()) != value:
        raise RuntimeError("Model pin changed; review before using --accept-model-update")
    atomic_json(pin, value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision a pinned, user-local private Ollama service")
    parser.add_argument("--database", type=Path, default=Path("data/f1-engineer.sqlite3"))
    parser.add_argument("--accept-model-update", action="store_true")
    args = parser.parse_args()
    if not sys.platform.startswith("linux") or platform.machine() != "x86_64":
        parser.error("This pinned release supports Ubuntu x86_64 only")
    for command in ("zstd", "systemctl"):
        if not shutil.which(command):
            parser.error(f"Missing prerequisite: {command}")
    root = Path.home() / f".local/share/GP...T/ollama/v{VERSION}"
    models = Path.home() / ".local/share/GP...T/ollama/models"
    unit = Path.home() / ".config/systemd/user/f1-engineer-ollama.service"
    expected_unit = service_text(root / "bin/ollama", models)
    if unit.is_symlink() or (unit.exists() and unit.read_text() != expected_unit):
        raise RuntimeError("Existing Ollama user unit differs; preserved for review")
    with socket.socket() as probe:
        occupied = probe.connect_ex(("127.0.0.1", 11435)) == 0
    if occupied and (not unit.exists() or subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", "f1-engineer-ollama"]).returncode != 0):
        raise RuntimeError("Private model port is owned by another service")
    executable = install_binary(root)
    models.mkdir(parents=True, exist_ok=True)
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(expected_unit)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "f1-engineer-ollama"], check=True)
    deadline = time.monotonic() + 60
    while True:
        try:
            if get_json("/api/version").get("version") != VERSION:
                raise RuntimeError("Unexpected private runtime version")
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Private runtime did not become ready")
            time.sleep(0.5)
    pin = args.database.expanduser().absolute().parent / ".f1-engineer-ollama-model.json"
    installed = next((entry for entry in get_json("/api/tags").get("models", []) if entry.get("name") == MODEL), None)
    if installed is None or args.accept_model_update:
        environment = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11435", "OLLAMA_NO_CLOUD": "1"}
        subprocess.run([str(executable), "pull", MODEL], env=environment, check=True, timeout=1800)
        installed = next((entry for entry in get_json("/api/tags").get("models", []) if entry.get("name") == MODEL), None)
    if installed is None:
        raise RuntimeError("Downloaded model is unavailable")
    pin_model(pin, installed, args.accept_model_update)
    print(f"Private Ollama {VERSION} ready; model pinned beside {args.database}")


if __name__ == "__main__":
    main()
