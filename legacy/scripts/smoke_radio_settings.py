"""Run a real API and dry-run radio and measure PUT-to-applied JSONL latency."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx


def wait_for(check, seconds=300):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result is not None:
            return result
        time.sleep(0.025)
    raise TimeoutError("radio settings smoke timed out")


def main():
    folder = Path(tempfile.mkdtemp(prefix="f1-radio-settings-smoke-"))
    token = folder / "control-token"
    log = folder / "radio.jsonl"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    processes = []
    with (folder / "processes.log").open("w", encoding="utf8") as output:
        try:
            processes.append(subprocess.Popen([
                sys.executable, "-m", "f1_engineer", "api",
                "--database", str(folder / "telemetry.sqlite"), "--recordings-root", str(folder / "recordings"),
                "--control-token-file", str(token), "--port", str(port), "--manual-acquisition",
            ], stdout=output, stderr=output, stdin=subprocess.DEVNULL))
            with httpx.Client(base_url=base, timeout=2, trust_env=False) as client:
                def ready():
                    try:
                        response = client.get("/api/v2/engineer/radio/settings")
                        return response.json()["data"] if response.status_code == 200 else None
                    except (httpx.RequestError, ValueError, KeyError):
                        return None
                saved = wait_for(ready)
                processes.append(subprocess.Popen([
                    sys.executable, "-m", "f1_engineer", "radio",
                    "--api-url", base, "--token-file", str(token), "--dry-run", "--log-file", str(log),
                ], stdout=output, stderr=output, stdin=subprocess.PIPE, text=True))

                def event_for(revision):
                    if not log.exists():
                        return None
                    for line in log.read_text(encoding="utf8").splitlines():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("event") == "radio_settings_applied" and event.get("revision") == revision:
                            return event
                    return None
                wait_for(lambda: event_for("0:0"))
                headers = {"Authorization": "Bearer " + token.read_text().strip()}
                measurements = []
                for rate in (2, 3, 4):
                    revision = saved.pop("revision")
                    saved.pop("schema_version")
                    saved["rate"] = rate
                    response = client.put("/api/v2/engineer/radio/settings", json=saved,
                                          headers={**headers, "If-Match": f'"{revision}"'})
                    returned = time.monotonic()
                    response.raise_for_status()
                    saved = response.json()["data"]
                    event = wait_for(lambda: event_for(f"0:{saved['revision']}"))
                    latency = event["trace"]["monotonic_ns"] / 1e9 - returned
                    assert latency >= 0, "application preceded HTTP return; repeat for post-response latency"
                    measurements.append(round(latency * 1000, 2))
                print(json.dumps({"latency_ms": measurements, "max_ms": max(measurements), "artifacts": str(folder)}))
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    else:
                        process.terminate()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
