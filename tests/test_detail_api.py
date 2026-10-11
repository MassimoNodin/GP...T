from dataclasses import replace

from fastapi.testclient import TestClient

from f1_engineer import cli
from f1_engineer.api import app as api_module
from f1_engineer.processing.detail_demand import DetailCommand, DetailDemandQueue


SESSION = "b" * 32
BINDING = "c" * 64
TOKEN = "api-test-control-token-0123456789abcdef"


class FakeRuntime:
    def __init__(self, store, *, host, port, queue_size, detail_profile):
        self.detail_profile = detail_profile
        self.state = "listening"
        self.queue = DetailDemandQueue()
        self.queue.publish_targets(SESSION, ({"binding_id": BINDING, "car_index": 7,
                                             "epoch": 3, "packet_format": 2025,
                                             "is_player": False},))

    def start(self):
        pass

    def close(self):
        pass

    def status(self):
        active, queued = self.queue.counts()
        return {"state": self.state, "detail_processing": {
            "profile": self.detail_profile, "policy_version": "demand-v1",
            "session_id": SESSION, "epoch": 3,
            "selected": [{"car_index": 7, "reasons": ["task"]}],
            "active_tasks": active, "queued_commands": queued,
            "observation_rows_created": 3,
            "observation_rows_skipped_by_policy": 19,
        }}

    def detail_targets(self, session_id):
        return self.queue.targets(session_id)

    def detail_task(self, task_id):
        return self.queue.get(task_id)

    def enqueue_detail_command(self, command):
        return self.queue.enqueue(command)


def app(monkeypatch, tmp_path, *, profile="demand_v1", automatic=True, require_auth=False):
    monkeypatch.setattr(api_module, "LiveSessionRuntime", FakeRuntime)
    return api_module.create_app(
        tmp_path / "archive.sqlite3", evidence_database_path=tmp_path / "evidence.sqlite3", control_token=TOKEN,
        recordings_root=tmp_path / "recordings",
        detail_profile=profile, automatic_acquisition=automatic,
        require_auth=require_auth,
    )


def test_A01_cli_profile_default_explicit_and_evidence_path_separation(monkeypatch, tmp_path):
    parser = cli.build_parser()
    assert parser.parse_args(["api"]).detail_profile == "full"
    assert parser.parse_args(["api", "--detail-profile", "demand_v1"]).detail_profile == "demand_v1"
    captured = []
    class Store:
        def __init__(self, path):
            self.path = path
            captured.append(str(path))
    monkeypatch.setattr(api_module, "EvidenceStore", Store)
    base = tmp_path / "archive.sqlite3"
    api_module.create_app(base, automatic_acquisition=False)
    api_module.create_app(base, automatic_acquisition=False, detail_profile="demand_v1")
    api_module.create_app(base, automatic_acquisition=False, detail_profile="demand_v1",
                          evidence_database_path=tmp_path / "explicit.sqlite3")
    assert captured[0].endswith("archive-evidence.sqlite3")
    assert captured[1].endswith("archive-demand-evidence.sqlite3")
    assert captured[2].endswith("explicit.sqlite3")
    import uvicorn
    forwarded = {}
    monkeypatch.setattr(api_module, "create_app", lambda *args, **kwargs: forwarded.update(kwargs) or object())
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr("f1_engineer.api.security.load_or_create_control_token", lambda _path: TOKEN)
    cli._api(parser.parse_args(["api", "--detail-profile", "demand_v1"]))
    assert forwarded["detail_profile"] == "demand_v1"


def test_A02_post_delete_control_auth_and_app_wide_get_auth(monkeypatch, tmp_path):
    with TestClient(app(monkeypatch, tmp_path)) as client:
        body = {"task_id": "task-a", "session_id": SESSION, "binding_id": BINDING}
        assert client.post("/api/v2/session-evidence/detail-tasks", json=body).status_code == 403
        assert client.delete("/api/v2/session-evidence/detail-tasks/task-a").status_code == 403
        assert client.get(f"/api/v2/session-evidence/detail-targets?session_id={SESSION}").status_code == 200
    with TestClient(app(monkeypatch, tmp_path, require_auth=True)) as client:
        assert client.get("/api/v2/session-evidence/status").status_code == 403
        assert client.get(f"/api/v2/session-evidence/detail-targets?session_id={SESSION}",
                          headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_A03_invalid_ids_unexpected_json_and_bare_slot_or_name_rejected(monkeypatch, tmp_path):
    with TestClient(app(monkeypatch, tmp_path)) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        invalid = [
            {"task_id": "bad space", "session_id": SESSION, "binding_id": BINDING},
            {"task_id": "x", "session_id": "SESSION", "binding_id": BINDING},
            {"task_id": "x", "session_id": SESSION, "binding_id": "7"},
            {"task_id": "x", "session_id": SESSION, "binding_id": BINDING, "ttl": 20},
            {"task_id": "x", "session_id": SESSION, "driver_name": "Driver 7"},
            {"task_id": "x", "session_id": SESSION, "car_index": 7},
        ]
        for body in invalid:
            response = client.post("/api/v2/session-evidence/detail-tasks", json=body, headers=headers)
            assert response.status_code == 422
            assert response.json()["reason"] == "detail_task_request_invalid"
        response = client.post("/api/v2/session-evidence/detail-tasks", data="not-json", headers=headers)
        assert response.status_code == 422


def test_A04_targets_are_current_bounded_and_tasks_report_queue_then_owner_state(monkeypatch, tmp_path):
    with TestClient(app(monkeypatch, tmp_path)) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        targets = client.get(f"/api/v2/session-evidence/detail-targets?session_id={SESSION}").json()["data"]
        assert targets == [{"binding_id": BINDING, "car_index": 7, "epoch": 3,
                            "packet_format": 2025, "is_player": False}]
        assert all("name" not in target for target in targets)
        body = {"task_id": "task-a", "session_id": SESSION, "binding_id": BINDING}
        queued = client.post("/api/v2/session-evidence/detail-tasks", json=body, headers=headers)
        assert queued.status_code == 202 and queued.json()["data"]["state"] == "queued"
        assert client.get("/api/v2/session-evidence/detail-tasks/task-a").json()["data"]["state"] == "queued"
        runtime = client.app.state.live_session_runtime
        active = runtime.queue.apply(runtime.queue.pop(), effective_sequence=4, target_current=True)
        assert active.state == "active" and active.effective_sequence == 4
        assert client.get("/api/v2/session-evidence/detail-tasks/task-a").json()["data"]["state"] == "active"
        conflict = client.post("/api/v2/session-evidence/detail-tasks",
            json={**body, "binding_id": "d" * 64}, headers=headers)
        assert conflict.status_code == 409 and conflict.json()["reason"] == "detail_target_not_current"
        player_binding = "d" * 64
        runtime.queue.publish_targets(SESSION, ({"binding_id": player_binding, "car_index": 0,
            "epoch": 3, "packet_format": 2025, "is_player": True},))
        player_body = {"task_id": "player-task", "session_id": SESSION,
                       "binding_id": player_binding}
        assert client.post("/api/v2/session-evidence/detail-tasks", json=player_body,
                           headers=headers).status_code == 202
        rejected = runtime.queue.apply(runtime.queue.pop(), effective_sequence=5,
                                       target_current=True, is_player=True)
        assert rejected.state == "rejected" and rejected.reason == "detail_target_is_player"


def test_A05_unavailable_unknown_and_terminal_release_behaviors(monkeypatch, tmp_path):
    with TestClient(app(monkeypatch, tmp_path, profile="full")) as client:
        assert client.get(f"/api/v2/session-evidence/detail-targets?session_id={SESSION}").status_code == 409
    with TestClient(app(monkeypatch, tmp_path, automatic=False)) as client:
        assert client.get(f"/api/v2/session-evidence/detail-targets?session_id={SESSION}").status_code == 409
    with TestClient(app(monkeypatch, tmp_path)) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        assert client.get("/api/v2/session-evidence/detail-tasks/missing").status_code == 404
        assert client.delete("/api/v2/session-evidence/detail-tasks/missing", headers=headers).status_code == 404
        body = {"task_id": "terminal", "session_id": SESSION, "binding_id": BINDING}
        client.post("/api/v2/session-evidence/detail-tasks", json=body, headers=headers)
        runtime = client.app.state.live_session_runtime
        runtime.queue.apply(runtime.queue.pop(), effective_sequence=1, target_current=True)
        first = client.delete("/api/v2/session-evidence/detail-tasks/terminal", headers=headers)
        assert first.status_code == 202 and first.json()["data"]["command_state"] == "queued"
        runtime.queue.apply(runtime.queue.pop(), effective_sequence=2, target_current=True)
        repeat = client.delete("/api/v2/session-evidence/detail-tasks/terminal", headers=headers)
        assert repeat.status_code == 202 and repeat.json()["data"]["state"] == "released"
        for index in range(16):
            status, _ = runtime.queue.enqueue(
                DetailCommand("post", f"full_{index}", SESSION, BINDING)
            )
            assert status == 202
        saturated = client.post("/api/v2/session-evidence/detail-tasks",
            json={"task_id": "overflow", "session_id": SESSION, "binding_id": BINDING},
            headers=headers)
        assert saturated.status_code == 409
        assert saturated.json()["reason"] == "detail_command_budget_busy"


def test_A06_deferred_evidence_uses_existing_422_envelope(monkeypatch, tmp_path):
    class Store:
        def __init__(self, path):
            self.path = path
        def evidence(self, attempt_id, *, session=None):
            from f1_engineer.processing.evidence import EvidenceUnavailable
            raise EvidenceUnavailable("trace_not_selected")
        def report(self, report_id):
            return {}
        def sessions(self, **kwargs):
            return []
        def attempts(self, *args, **kwargs):
            return []
    monkeypatch.setattr(api_module, "EvidenceStore", Store)
    with TestClient(app(monkeypatch, tmp_path)) as client:
        response = client.get("/api/v2/session-evidence/sessions/s/attempts/a")
        assert response.status_code == 422
        assert response.json() == {"api_version": "v1", "status": "unavailable",
                                   "data": None, "reason": "trace_not_selected"}


def test_A07_runtime_status_has_bounded_counts_and_policy_row_meanings(monkeypatch, tmp_path):
    with TestClient(app(monkeypatch, tmp_path)) as client:
        detail = client.get("/api/v2/session-evidence/status").json()["detail_processing"]
        assert detail["profile"] == "demand_v1"
        assert detail["selected"] == [{"car_index": 7, "reasons": ["task"]}]
        assert detail["observation_rows_created"] == 3
        assert detail["observation_rows_skipped_by_policy"] == 19
        assert "received" not in detail and "kernel_dropped" not in detail
