from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from scripts.control_panel import (
    Component,
    Host,
    Panel,
    branch_options,
    checkout_blockers,
    make_handler,
    parse_status_line,
    split_results,
    systemd_status,
)


def snapshot(*, statuses=("stopped",), dirty=0, reachable=True, branch="main"):
    return {
        "reachable": reachable, "error": None if reachable else "Ubuntu unreachable", "dirty": dirty,
        "branch": branch, "branches": branch_options(["main", "feat/a"], ["origin/main", "origin/feat/b", "origin/HEAD"]),
        "components": [
            {"id": f"c{i}", "label": f"C{i}", "uses_checkout": uses, "status": status, "detail": "", "note": ""}
            for i, (status, uses) in enumerate(
                [(s, True) if isinstance(s, str) else s for s in statuses])],
    }


def test_checkout_is_free_only_when_idle_clean_and_stopped():
    assert checkout_blockers(snapshot(), busy=False) == []
    assert checkout_blockers(snapshot(statuses=("running",)), busy=False) == ["stop first: C0"]
    assert checkout_blockers(snapshot(statuses=("external",)), busy=False) == ["stop first: C0"]
    assert "2 uncommitted" in checkout_blockers(snapshot(dirty=2), busy=False)[0]
    assert checkout_blockers(snapshot(), busy=True) == ["another operation is in progress"]
    assert checkout_blockers(snapshot(reachable=False), busy=False) == ["Ubuntu unreachable"]


def test_components_outside_the_checkout_do_not_block_it():
    assert checkout_blockers(snapshot(statuses=(("running", False),)), busy=False) == []


def test_parse_status_line_and_branch_options():
    assert parse_status_line("## main...origin/main [ahead 8]") == ("main", "origin/main [ahead 8]")
    assert parse_status_line("## feat/x") == ("feat/x", "")
    assert parse_status_line("## HEAD (no branch)") == (None, "")
    options = branch_options(["main", "feat/a"], ["origin/main", "origin/feat/b", "origin/HEAD", "origin"])
    assert options == [{"name": "feat/a", "remote_only": False}, {"name": "main", "remote_only": False},
                       {"name": "feat/b", "remote_only": True}]


def test_split_results_and_systemd_status():
    assert split_results("a\n@@rc=0\nb\nc\n@@rc=3\n", 2) == [(0, "a"), (3, "b\nc")]
    assert split_results("", 1) == [(255, "")]
    assert [systemd_status(s) for s in ("active", "inactive", "failed", "activating", "weird")] == [
        "running", "stopped", "failed", "starting", "unknown"]


class FakeHost(Host):
    key, label = "fake", "Fake"

    def __init__(self, current):
        super().__init__([Component("c0", "C0", "")])
        self.current, self.calls = current, []

    def collect(self):
        return self.current

    def run(self, argv, cwd=None, timeout=600):
        self.calls.append(argv)
        return 0, ""

    def component_action(self, component, action, log):
        self.calls.append((action, component.id))

    def sync_steps(self):
        return []


def finish(host):
    for _ in range(200):
        if not host.job.running:
            return
        threading.Event().wait(0.01)
    raise AssertionError("job did not finish")


def test_switch_and_update_are_refused_while_running_or_dirty():
    for current in (snapshot(statuses=("running",)), snapshot(dirty=1)):
        host = FakeHost(current)
        assert host.request({"action": "switch", "branch": "feat/a"})[0] == 409
        assert host.request({"action": "update"})[0] == 409
        assert host.calls == []


def test_switch_validates_branch_and_picks_the_right_command():
    host = FakeHost(snapshot())
    assert host.request({"action": "switch", "branch": "nope"})[0] == 400
    assert host.request({"action": "switch", "branch": "--detach"})[0] == 400
    assert host.request({"action": "switch", "branch": "main"})[0] == 409
    assert host.request({"action": "switch", "branch": "feat/a"})[0] == 202
    finish(host)
    assert host.calls == [["git", "switch", "feat/a"]]
    host.calls.clear()
    assert host.request({"action": "switch", "branch": "feat/b"})[0] == 202
    finish(host)
    assert host.calls == [["git", "switch", "--track", "origin/feat/b"]]


def test_component_actions_follow_status():
    host = FakeHost(snapshot(statuses=("stopped",)))
    assert host.request({"action": "stop", "component": "c0"})[0] == 409
    assert host.request({"action": "start", "component": "c0"})[0] == 202
    finish(host)
    assert host.calls == [("start", "c0")]
    assert host.request({"action": "start", "component": "missing"})[0] == 400


def test_http_rejects_foreign_requests_and_serves_state():
    host = FakeHost(snapshot())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Panel([host]), 0))
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(Panel([host]), port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def call(method, path, headers=None, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()

        status, body = call("GET", "/api/state")
        assert status == 200 and json.loads(body)["hosts"][0]["key"] == "fake"
        assert call("GET", "/api/state", {"Host": "evil.example"})[0] == 403
        payload = json.dumps({"host": "fake", "action": "update"})
        assert call("POST", "/api/action", {"Content-Type": "application/json"}, payload)[0] == 403
        headers = {"Content-Type": "application/json", "X-Control-Panel": "1", "Origin": "http://evil.example"}
        assert call("POST", "/api/action", headers, payload)[0] == 403
        headers["Origin"] = f"http://127.0.0.1:{port}"
        assert call("POST", "/api/action", headers, payload)[0] == 202
        assert call("POST", "/api/action", headers, "not json")[0] == 400
    finally:
        server.shutdown()
