from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def stop_owned(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def request_json(url: str, headers: dict[str, str]) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=12)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(16385)
        if len(body) > 16384:
            raise RuntimeError("Verification response exceeds its limit")
        return response.status, json.loads(body)


def wait_status(url: str, headers: dict[str, str], expected: int) -> dict:
    deadline = time.monotonic() + 45
    last_status = None
    while time.monotonic() < deadline:
        try:
            last_status, body = request_json(url, headers)
            if last_status == expected:
                return body
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Verification deadline: expected HTTP {expected}, last HTTP {last_status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify split transport failure/reconnect with owned disposable processes")
    parser.add_argument("--ssh-target", default="massimo-nodin@192.168.1.115")
    parser.add_argument("--identity", type=Path, default=Path.home() / ".ssh/id_ed25519_ubuntu")
    parser.add_argument("--tunnel-port", type=int, default=18767)
    parser.add_argument("--dashboard-port", type=int, default=3002)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Run this verification from the Windows editing workspace")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]+", args.ssh_target):
        parser.error("Invalid SSH target")
    for port in (args.tunnel_port, args.dashboard_port):
        if not 1 <= port <= 65535:
            parser.error("Ports must be in 1..65535")
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", port))
    if args.tunnel_port == args.dashboard_port:
        parser.error("Tunnel and dashboard ports must differ")
    repo = Path(__file__).resolve().parent.parent
    web = repo / "web"
    token_file = repo / "data/.f1-engineer-ubuntu-control-token"
    token = token_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise RuntimeError("Companion credentials are unavailable; run its launcher first")
    if not (web / ".next/BUILD_ID").is_file():
        raise RuntimeError("Build the Windows dashboard before verification")
    ssh = shutil.which("ssh")
    node = shutil.which("node")
    if ssh is None or node is None:
        raise RuntimeError("SSH and Node.js are required")
    tunnel_url = f"http://127.0.0.1:{args.tunnel_port}"
    dashboard_url = f"http://127.0.0.1:{args.dashboard_port}"
    authorization = {"Authorization": "Bearer " + token}
    browser_headers = {"Sec-Fetch-Site": "same-origin"}
    tunnel_command = [ssh, "-i", str(args.identity.resolve(strict=True)), "-o", "IdentitiesOnly=yes",
                      "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ExitOnForwardFailure=yes",
                      "-N", "-T", "-L", f"127.0.0.1:{args.tunnel_port}:127.0.0.1:8765", args.ssh_target]
    tunnel = dashboard = None
    with tempfile.TemporaryFile() as logs:
        try:
            tunnel = subprocess.Popen(tunnel_command, stdout=logs, stderr=logs, creationflags=subprocess.CREATE_NO_WINDOW)
            wait_status(tunnel_url + "/api/v2/session-evidence/status", authorization, 200)
            status, _body = request_json(tunnel_url + "/api/v2/session-evidence/status", {})
            assert status == 403, "Anonymous tunnel access was not rejected"
            environment = {
                **os.environ, "F1_ENGINEER_API_URL": tunnel_url,
                "F1_ENGINEER_API_TOKEN_FILE": str(token_file), "F1_ENGINEER_WEB_ORIGIN": dashboard_url,
            }
            dashboard = subprocess.Popen(
                [node, str(web / "node_modules/next/dist/bin/next"), "start", "--hostname", "127.0.0.1", "--port", str(args.dashboard_port)],
                cwd=web, env=environment, stdout=logs, stderr=logs, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            speech = wait_status(dashboard_url + "/api/engineer/transcribe", browser_headers, 200)
            assert speech["data"]["status"] == "ready"
            runtime = wait_status(dashboard_url + "/api/engineer/runtime", browser_headers, 200)
            assert runtime["data"]["status"] == "ready"
            assert token not in json.dumps((speech, runtime))
            stop_owned(tunnel)
            failed = wait_status(dashboard_url + "/api/engineer/runtime", browser_headers, 503)
            assert failed.get("data") is None, "Disconnected transport returned stale successful data"
            tunnel = subprocess.Popen(tunnel_command, stdout=logs, stderr=logs, creationflags=subprocess.CREATE_NO_WINDOW)
            wait_status(tunnel_url + "/api/v2/session-evidence/status", authorization, 200)
            restored = wait_status(dashboard_url + "/api/engineer/runtime", browser_headers, 200)
            assert restored["data"]["status"] == "ready"
            print(json.dumps({"anonymous_rejected": True, "speech_ready": True, "model_ready": True,
                              "disconnect_fail_closed": True, "reconnect_recovered": True,
                              "credentials_not_in_responses": True}))
        finally:
            stop_owned(dashboard)
            stop_owned(tunnel)


if __name__ == "__main__":
    main()
