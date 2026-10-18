"""Local control panel: start, stop and update the app on Windows and Ubuntu.

Run it on Windows with scripts/start-control-panel.ps1 (or
``python -m scripts.control_panel``). It serves one page on loopback only.
Windows components run as detached processes tracked in data/control-panel/;
Ubuntu components are systemd user services driven over the existing SSH key.

Branch changes and updates are refused while any component that runs from the
checkout is running, while the checkout has uncommitted tracked changes, and
while another operation is in progress. The server enforces this; the page only
reflects it. Standard library only, so it can run outside the project venv
(which ``uv sync`` replaces during an update).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PANEL_DIR = REPO / "data" / "control-panel"
PAGE = Path(__file__).with_suffix(".html")

# Statuses in which a component is (or may be) holding the checkout.
ACTIVE = frozenset({"running", "starting", "stopping", "external"})
STARTABLE = frozenset({"stopped", "exited", "failed", "unknown"})
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


@dataclass(frozen=True)
class Component:
    id: str
    label: str
    detail: str
    uses_checkout: bool = True
    command: tuple[str, ...] = ()  # Windows: argv started in the repository
    port: int | None = None  # Windows: loopback port that shows it is ready
    unit: str = ""  # Ubuntu: systemd user unit


def windows_components() -> list[Component]:
    uv = shutil.which("uv") or "uv"
    powershell = shutil.which("powershell") or "powershell"
    script = ("-NoProfile", "-ExecutionPolicy", "Bypass", "-File")
    return [
        Component(
            "backend", "Local backend", "API on 127.0.0.1:8765, game UDP 20777",
            command=(uv, "run", "--extra", "app", "f1-engineer", "api",
                     "--database", "data/f1-engineer.sqlite3", "--recordings-root", "recordings",
                     "--control-token-file", "data/.f1-engineer-control-token"),
            port=8765),
        Component(
            "dashboard", "Dashboard + Ubuntu tunnel", "http://127.0.0.1:3000 against the Ubuntu backend",
            command=(powershell, *script, str(REPO / "scripts" / "start-ubuntu-companion.ps1")),
            port=3000),
        Component(
            "radio", "Radio", "Wheel button, microphone and speech; start after the dashboard",
            command=(powershell, *script, str(REPO / "scripts" / "start-standalone-radio.ps1"))),
    ]


def ubuntu_components() -> list[Component]:
    return [
        Component("backend", "Backend", "f1-engineer.service: API 127.0.0.1:8765, game UDP 20777",
                  unit="f1-engineer"),
        Component("ollama", "Private model", "f1-engineer-ollama.service on 127.0.0.1:11435",
                  uses_checkout=False, unit="f1-engineer-ollama"),
    ]


def checkout_blockers(snapshot: dict, busy: bool) -> list[str]:
    """Reasons the checkout may not be changed right now; empty means it may."""
    if busy:
        return ["another operation is in progress"]
    if not snapshot.get("reachable"):
        return [snapshot.get("error") or "host is unreachable"]
    reasons = []
    running = [c["label"] for c in snapshot["components"] if c["uses_checkout"] and c["status"] in ACTIVE]
    if running:
        reasons.append("stop first: " + ", ".join(running))
    if snapshot["dirty"]:
        reasons.append(f"{snapshot['dirty']} uncommitted change(s); commit or stash them")
    return reasons


def parse_status_line(line: str) -> tuple[str | None, str]:
    """Return (branch or None when detached, upstream tracking summary)."""
    text = line.removeprefix("## ").strip()
    if text.startswith("HEAD (no branch)"):
        return None, ""
    if text.startswith("No commits yet on "):
        return text.removeprefix("No commits yet on "), ""
    branch, _, rest = text.partition("...")
    return branch, rest.strip()


def branch_options(local: list[str], remote: list[str]) -> list[dict]:
    """Local branches, then branches that exist only on origin."""
    names = sorted(set(local))
    remote_only = sorted(r.removeprefix("origin/") for r in remote
                         if r.startswith("origin/") and r not in ("origin/HEAD",)
                         and r.removeprefix("origin/") not in names)
    return ([{"name": n, "remote_only": False} for n in names]
            + [{"name": n, "remote_only": True} for n in remote_only])


def split_results(output: str, count: int) -> list[tuple[int, str]]:
    """Split a combined run of ``count`` commands on their @@rc= markers."""
    results, current = [], []
    for line in output.splitlines():
        match = re.fullmatch(r"@@rc=(\d+)", line)
        if match:
            results.append((int(match.group(1)), "\n".join(current)))
            current = []
        else:
            current.append(line)
    while len(results) < count:
        results.append((255, "\n".join(current)))
        current = []
    return results


# ---------------------------------------------------------------------------
# Windows process tracking
# ---------------------------------------------------------------------------

def _kernel32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    return k32


def process_started_at(pid: int) -> int | None:
    """Creation time (FILETIME) of a live process, else None. Guards against PID reuse."""
    import ctypes
    from ctypes import wintypes
    k32 = _kernel32()
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return None
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(handle, *map(ctypes.byref, (created, exited, kernel, user))):
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        k32.CloseHandle(handle)


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def tail(path: Path, lines: int = 4, width: int = 200) -> str:
    try:
        recent = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return ""
    return "\n".join(line if len(line) <= width else line[:width] + "..." for line in recent)


class ProcessTable:
    """PIDs of components this panel started, persisted so a restart can still stop them."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, key: str) -> dict | None:
        with self.lock:
            return self._read().get(key)

    def put(self, key: str, entry: dict | None) -> None:
        with self.lock:
            data = self._read()
            if entry is None:
                data.pop(key, None)
            else:
                data[key] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(temp, self.path)


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------

class Job:
    def __init__(self) -> None:
        self.title = ""
        self.running = False
        self.ok: bool | None = None
        self.output = ""

    def view(self) -> dict:
        return {"title": self.title, "running": self.running, "ok": self.ok, "output": self.output[-6000:]}


class Host:
    key = ""
    label = ""
    where = ""

    def __init__(self, components: list[Component]) -> None:
        self.components = components
        self.job = Job()
        self.lock = threading.Lock()  # guards job and snapshot
        self.snapshot: dict = {"reachable": False, "error": "not checked yet", "components": [],
                               "dirty": 0, "branches": [], "branch": None}
        self.snapshot_at = 0.0
        self.op_lock = threading.Lock()

    # -- subclass hooks
    def run(self, argv: list[str], cwd: str | None = None, timeout: int = 600) -> tuple[int, str]:
        raise NotImplementedError

    def collect(self) -> dict:
        raise NotImplementedError

    def component_action(self, component: Component, action: str, log) -> None:
        raise NotImplementedError

    def sync_steps(self) -> list[tuple[list[str], str | None]]:
        raise NotImplementedError

    # -- shared
    def refresh(self) -> dict:
        snapshot = self.collect()
        with self.lock:
            self.snapshot, self.snapshot_at = snapshot, time.time()
        return snapshot

    def view(self) -> dict:
        with self.lock:
            snapshot, job = dict(self.snapshot), self.job.view()
        busy = job["running"]
        snapshot["blockers"] = checkout_blockers(snapshot, busy)
        for component in snapshot["components"]:
            status = component["status"]
            idle = not busy and snapshot.get("reachable", False)
            component["can_start"] = idle and status in STARTABLE
            component["can_stop"] = idle and status in {"running", "starting"}
            component["can_restart"] = idle and status == "running"
        return {"key": self.key, "label": self.label, "where": self.where, "job": job, **snapshot}

    def begin(self, title: str, work) -> str | None:
        """Start ``work(log)`` on a thread unless another operation is running."""
        if not self.op_lock.acquire(blocking=False):
            return "another operation is in progress"
        with self.lock:
            self.job = Job()
            self.job.title, self.job.running = title, True

        def log(text: str) -> None:
            with self.lock:
                self.job.output += text if text.endswith("\n") else text + "\n"

        def runner() -> None:
            ok = False
            try:
                work(log)
                ok = True
            except Exception as error:  # surfaced in the job output, not swallowed
                log(f"FAILED: {error}")
            finally:
                with self.lock:
                    self.job.running, self.job.ok = False, ok
                self.op_lock.release()
                self.refresh()

        threading.Thread(target=runner, daemon=True).start()
        return None

    def step(self, log, argv: list[str], cwd: str | None = None, timeout: int = 600) -> str:
        log("$ " + " ".join(argv))
        code, output = self.run(argv, cwd, timeout)
        if output.strip():
            log(output.strip())
        if code != 0:
            raise RuntimeError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''} exited with {code}".strip())
        return output

    def request(self, payload: dict) -> tuple[int, str]:
        """Validate and start an action. Returns (http status, message)."""
        action = payload.get("action")
        if self.job.running:
            return 409, "another operation is in progress"
        if action in ("start", "stop", "restart"):
            component = next((c for c in self.components if c.id == payload.get("component")), None)
            if component is None:
                return 400, "unknown component"
            current = next((c for c in self.refresh()["components"] if c["id"] == component.id), None)
            if current is None:
                return 409, self.snapshot.get("error") or "host is unreachable"
            status = current["status"]
            allowed = {"start": STARTABLE, "stop": {"running", "starting"}, "restart": {"running"}}[action]
            if status not in allowed:
                return 409, f"cannot {action} while {component.label} is {status}"
            error = self.begin(f"{action} {component.label}",
                               lambda log: self.component_action(component, action, log))
            return (409, error) if error else (202, "started")
        if action in ("update", "switch"):
            snapshot = self.refresh()
            reasons = checkout_blockers(snapshot, False)
            if reasons:
                return 409, "; ".join(reasons)
            if action == "update":
                if not snapshot["branch"]:
                    return 409, "the checkout is on a detached HEAD"
                error = self.begin("update", self.update)
            else:
                branch = str(payload.get("branch", ""))
                known = {b["name"]: b for b in snapshot["branches"]}
                if not BRANCH_PATTERN.fullmatch(branch) or branch not in known:
                    return 400, "unknown branch"
                if branch == snapshot["branch"]:
                    return 409, f"already on {branch}"
                error = self.begin(f"switch to {branch}", lambda log: self.switch(branch, known[branch]["remote_only"], log))
            return (409, error) if error else (202, "started")
        return 400, "unknown action"

    def update(self, log) -> None:
        self.step(log, ["git", "fetch", "--prune", "origin"], timeout=120)
        self.step(log, ["git", "pull", "--ff-only"], timeout=120)
        self.sync(log)
        log("Update complete. Start the components again.")

    def switch(self, branch: str, remote_only: bool, log) -> None:
        self.step(log, ["git", "switch", "--track", f"origin/{branch}"] if remote_only else ["git", "switch", branch],
                  timeout=120)
        self.sync(log)
        log(f"Now on {branch}.")

    def sync(self, log) -> None:
        for argv, cwd in self.sync_steps():
            self.step(log, argv, cwd, timeout=900)


class WindowsHost(Host):
    key, label = "windows", "Windows"

    def __init__(self) -> None:
        super().__init__(windows_components())
        self.where = str(REPO)
        self.table = ProcessTable(PANEL_DIR / "processes.json")

    def run(self, argv, cwd=None, timeout=600):
        try:
            done = subprocess.run(argv, cwd=cwd or REPO, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout, creationflags=0x08000000)  # CREATE_NO_WINDOW
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout}s"
        except OSError as error:
            return 127, str(error)
        return done.returncode, done.stdout

    def component_status(self, component: Component) -> dict:
        entry = self.table.get(component.id)
        alive = bool(entry) and process_started_at(entry["pid"]) == entry["started"]
        listening = component.port is not None and port_open(component.port)
        note = ""
        if alive:
            status = "starting" if component.port is not None and not listening else "running"
        elif entry:
            status, note = "exited", tail(PANEL_DIR / "logs" / f"{component.id}.log")
        elif listening:
            status, note = "external", "Running but not started from this panel; stop it where it was started."
        else:
            status = "stopped"
        return {"id": component.id, "label": component.label, "detail": component.detail,
                "uses_checkout": component.uses_checkout, "status": status, "note": note}

    def collect(self) -> dict:
        snapshot = {"reachable": True, "error": None,
                    "components": [self.component_status(c) for c in self.components]}
        snapshot.update(git_view(self))
        return snapshot

    def component_action(self, component: Component, action: str, log) -> None:
        if action in ("stop", "restart"):
            self.stop(component, log)
        if action in ("start", "restart"):
            self.start(component, log)

    def start(self, component: Component, log) -> None:
        logs = PANEL_DIR / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / f"{component.id}.log").open("wb") as sink:
            process = subprocess.Popen(
                list(component.command), cwd=REPO, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                creationflags=0x08000000 | 0x00000200)  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        started = None
        for _ in range(20):
            started = process_started_at(process.pid)
            if started:
                break
            time.sleep(0.05)
        self.table.put(component.id, {"pid": process.pid, "started": started or 0})
        log(f"Started {component.label} (pid {process.pid}). Output: {logs / (component.id + '.log')}")

    def stop(self, component: Component, log) -> None:
        entry = self.table.get(component.id)
        if entry and process_started_at(entry["pid"]) == entry["started"]:
            self.step(log, ["taskkill", "/PID", str(entry["pid"]), "/T", "/F"], timeout=30)
            for _ in range(100):
                if process_started_at(entry["pid"]) != entry["started"]:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError(f"{component.label} did not exit")
        self.table.put(component.id, None)
        log(f"Stopped {component.label}.")

    def sync_steps(self):
        steps: list[tuple[list[str], str | None]] = [
            ([shutil.which("uv") or "uv", "sync", "--frozen", "--extra", "app", "--extra", "dev", "--extra", "radio"], None)]
        web = REPO / "web"
        lock = web / "package-lock.json"
        digest = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else ""
        marker = PANEL_DIR / "npm-ci.sha256"
        installed = marker.read_text().strip() if marker.exists() else ""
        if digest != installed or not (web / "node_modules").exists():
            steps.append(([shutil.which("npm.cmd") or "npm", "ci"], str(web)))
            steps.append(([sys.executable, "-c",
                           f"import pathlib;p=pathlib.Path({str(marker)!r});p.parent.mkdir(parents=True,exist_ok=True);p.write_text({digest!r})"],
                          None))
        return steps


class UbuntuHost(Host):
    key, label = "ubuntu", "Ubuntu"

    def __init__(self, target: str, identity: Path, repository: str) -> None:
        super().__init__(ubuntu_components())
        self.target, self.identity, self.repository = target, identity, repository
        self.where = f"{target}:{repository}"
        self.uv = f"{repository.rsplit('/', 1)[0]}/.local/bin/uv"

    def ssh(self, script: str, timeout: int) -> tuple[int, str]:
        argv = [shutil.which("ssh") or "ssh", "-i", str(self.identity), "-o", "IdentitiesOnly=yes",
                "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", self.target, "bash", "-s"]
        try:
            done = subprocess.run(argv, input=script, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout, creationflags=0x08000000)
        except subprocess.TimeoutExpired:
            return 124, f"ssh timed out after {timeout}s"
        except OSError as error:
            return 127, str(error)
        return done.returncode, done.stdout

    def run_many(self, commands: list[tuple[list[str], str | None]], timeout: int) -> tuple[int, list[tuple[int, str]]]:
        lines = [f"cd {shlex.quote(self.repository)} || exit 97"]
        for argv, cwd in commands:
            prefix = f"cd {shlex.quote(self.repository + '/' + cwd)} && " if cwd else ""
            lines.append(f"( {prefix}{shlex.join(argv)} ) 2>&1; echo \"@@rc=$?\"")
        code, output = self.ssh("\n".join(lines) + "\n", timeout)
        if code != 0:
            return code, [(code, output)] * len(commands)
        return 0, split_results(output, len(commands))

    def run(self, argv, cwd=None, timeout=600):
        code, results = self.run_many([(argv, cwd)], timeout)
        return results[0] if code == 0 else (code, results[0][1])

    def collect(self) -> dict:
        units = [c.unit for c in self.components]
        code, results = self.run_many([
            (["git", "status", "--porcelain=v1", "--branch", "--untracked-files=no"], None),
            (["git", "rev-parse", "--short", "HEAD"], None),
            (["git", "log", "-1", "--format=%s"], None),
            (["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"], None),
            (["git", "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"], None),
            (["systemctl", "--user", "is-active", *units], None),
        ], timeout=20)
        if code != 0:
            error = results[0][1].strip().splitlines()[-1:] or ["ssh failed"]
            return {"reachable": False, "error": f"Ubuntu unreachable: {error[0]}", "components": [],
                    "dirty": 0, "branches": [], "branch": None}
        status, rev, subject, local, remote, active = results
        snapshot = {"reachable": True, "error": None}
        snapshot.update(git_fields(status, rev, subject, local, remote))
        states = active[1].split()
        snapshot["components"] = [
            {"id": c.id, "label": c.label, "detail": c.detail, "uses_checkout": c.uses_checkout, "note": "",
             "status": systemd_status(states[i] if i < len(states) else "unknown")}
            for i, c in enumerate(self.components)]
        return snapshot

    def component_action(self, component: Component, action: str, log) -> None:
        self.step(log, ["systemctl", "--user", action, component.unit], timeout=90)
        log(f"{action.capitalize()} {component.label}: done.")

    def sync_steps(self):
        return [([self.uv, "sync", "--frozen", "--extra", "app", "--extra", "dev"], None)]


def systemd_status(state: str) -> str:
    return {"active": "running", "activating": "starting", "reloading": "running", "deactivating": "stopping",
            "inactive": "stopped", "failed": "failed"}.get(state, "unknown")


def git_fields(status, rev, subject, local, remote) -> dict:
    lines = status[1].splitlines()
    branch, tracking = parse_status_line(lines[0]) if lines else (None, "")
    return {"branch": branch, "tracking": tracking, "rev": rev[1].strip(), "subject": subject[1].strip(),
            "dirty": max(0, len(lines) - 1),
            "branches": branch_options(local[1].split(), remote[1].split())}


def git_view(host: Host) -> dict:
    results = [host.run(argv) for argv in (
        ["git", "status", "--porcelain=v1", "--branch", "--untracked-files=no"],
        ["git", "rev-parse", "--short", "HEAD"],
        ["git", "log", "-1", "--format=%s"],
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"])]
    return git_fields(*results)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Panel:
    def __init__(self, hosts: list[Host]) -> None:
        self.hosts = {h.key: h for h in hosts}

    def start_polling(self) -> None:
        for host, interval in zip(self.hosts.values(), (2.0, 5.0)):
            threading.Thread(target=self._poll, args=(host, interval), daemon=True).start()

    @staticmethod
    def _poll(host: Host, interval: float) -> None:
        while True:
            try:
                host.refresh()
            except Exception as error:
                with host.lock:
                    host.snapshot = {**host.snapshot, "reachable": False, "error": f"status failed: {error}"}
            time.sleep(interval)

    def state(self) -> dict:
        return {"hosts": [h.view() for h in self.hosts.values()]}


def make_handler(panel: Panel, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ControlPanel"

        def log_message(self, *_args) -> None:
            pass

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _local(self) -> bool:
            # Blocks DNS-rebinding and cross-site requests to a page that can start processes.
            origin = self.headers.get("Origin")
            return self.headers.get("Host") in allowed_hosts and (
                origin is None or origin.removeprefix("http://") in allowed_hosts)

        def do_GET(self) -> None:
            if not self._local():
                return self._json(403, {"ok": False, "message": "forbidden"})
            if self.path == "/":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._json(200, panel.state())
            self._json(404, {"ok": False, "message": "not found"})

        def do_POST(self) -> None:
            if not self._local() or self.headers.get("X-Control-Panel") != "1":
                return self._json(403, {"ok": False, "message": "forbidden"})
            if self.path != "/api/action":
                return self._json(404, {"ok": False, "message": "not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length)) if 0 < length <= 4096 else None
            except ValueError:
                payload = None
            host = panel.hosts.get(payload.get("host")) if isinstance(payload, dict) else None
            if host is None:
                return self._json(400, {"ok": False, "message": "invalid request"})
            status, message = host.request(payload)
            self._json(status, {"ok": status < 300, "message": message})

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--ssh-target", default="massimo-nodin@192.168.1.115")
    parser.add_argument("--identity-file", type=Path, default=Path.home() / ".ssh" / "id_ed25519_ubuntu")
    parser.add_argument("--remote-repository", default="/home/massimo-nodin/GP...T")
    args = parser.parse_args(argv)
    panel = Panel([WindowsHost(), UbuntuHost(args.ssh_target, args.identity_file, args.remote_repository)])
    panel.start_polling()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(panel, args.port))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}"
    print(f"Control panel: {url}  (Ctrl+C stops the panel, not the components)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
