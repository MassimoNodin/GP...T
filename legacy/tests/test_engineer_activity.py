import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from f1_engineer.api.app import create_app
from f1_engineer.engineer_activity import (
    EngineerActivity,
    EngineerActivityStore,
    EngineerActivityUnavailable,
    EngineerCallObserver,
    AskActivityLog,
    build_engineer_activity,
)
from f1_engineer.engineer_activity.calls import (
    PlayerAttempt,
    EvidenceAttemptSource,
    format_delta,
    format_lap_time,
)
from f1_engineer.processing.evidence import EvidenceStore

SESSION = "s" * 32
TOKEN = "engineer-activity-test-token-0123456789abcdef"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class FakeRuntime:
    def __init__(self, state="listening", error=None):
        self.state = state
        self.error = error

    def status(self):
        return {"state": self.state, "error": self.error}


class FakeAttempts:
    def __init__(self, high=0):
        self.items = []
        self.high = high

    def add(self, revision, lap, lap_ms, *, valid=True, pit=False, session=SESSION, disposition="completed"):
        self.high += 1
        self.items.append(PlayerAttempt(self.high, revision, session, {
            "lap_number": lap, "lap_time_ms": lap_ms, "game_valid": valid,
            "pit_encountered": pit, "disposition": disposition,
        }))

    def high_water(self):
        return self.high

    def player_attempts(self, after, limit):
        batch = [item for item in self.items if item.cursor > after][:limit]
        resume = batch[-1].cursor if len(batch) == limit else self.high
        return batch, resume


@pytest.fixture
def store(tmp_path):
    return EngineerActivityStore(tmp_path / "activity.sqlite3")


def observer(store, runtime=None, attempts=None, **kwargs):
    return EngineerCallObserver(store, runtime or FakeRuntime(), attempts or FakeAttempts(), **kwargs)


def texts(store):
    return [item["text"] for item in store.events(limit=200)[0]]


# --- store -------------------------------------------------------------------


def test_store_schema_wal_and_monotonic_ids(store):
    first = store.append(kind="question", topic="gap", text="How big is the gap?", source="v2_ask",
                         session_id=SESSION, lap=3, detail={"a": 1})
    second = store.append(kind="response", topic="gap", text="Four tenths.", source="v2_ask",
                          session_id=SESSION, lap=3, related_id=first)
    assert (first, second) == (1, 2)
    with sqlite3.connect(store.path) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 2
        assert database.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        with pytest.raises(sqlite3.DatabaseError):
            database.execute("UPDATE engineer_activity SET text='x'")
    events, next_after = store.events()
    assert [item["id"] for item in events] == [1, 2]
    assert events[1]["related_id"] == 1 and events[0]["detail"] == {"a": 1}
    assert next_after == 2


def test_store_rejects_invalid_entries_and_coerces_topic(store):
    with pytest.raises(ValueError):
        store.append(kind="nope", text="x", source="system")
    with pytest.raises(ValueError):
        store.append(kind="issue", text="x", source="elsewhere")
    store.append(kind="issue", text="x", source="system", topic="unknown")
    assert store.events()[0][0]["topic"] == "general"


def test_store_bounds_text_detail_and_retention(tmp_path):
    bounded = EngineerActivityStore(tmp_path / "bounded.sqlite3", max_entries=3)
    bounded.append(kind="issue", text="x" * 10_000, source="system", detail={"big": "y" * 10_000})
    entry = bounded.events()[0][0]
    assert len(entry["text"]) == 2_000
    assert entry["detail"] == {"truncated": True, "reason": "detail_too_large"}
    for index in range(5):
        bounded.append(kind="issue", text=f"n{index}", source="system")
    events, _ = bounded.events()
    assert [item["text"] for item in events] == ["n2", "n3", "n4"]
    assert [item["id"] for item in events] == [4, 5, 6]
    assert bounded.append(kind="issue", text="n5", source="system") == 7


def test_store_paging_session_filter_and_dedupe(store):
    store.append(kind="proactive", text="a", source="live_rules", session_id="one", dedupe_key="k")
    assert store.append(kind="proactive", text="dup", source="live_rules", dedupe_key="k") is None
    store.append(kind="proactive", text="b", source="live_rules", session_id="two")
    store.append(kind="issue", text="c", source="system")
    assert [item["text"] for item in store.events(session_id="one")[0]] == ["a", "c"]
    page, next_after = store.events(after=0, limit=2)
    assert [item["text"] for item in page] == ["a", "b"] and next_after == 2
    assert store.events(after=3)[0] == [] and store.events(after=3)[1] == 3
    newest, _ = store.events(limit=2)
    assert [item["text"] for item in newest] == ["b", "c"]
    with pytest.raises(ValueError):
        store.events(limit=201)


def test_store_unsupported_schema_is_reported(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA user_version=9")
    with pytest.raises(EngineerActivityUnavailable):
        EngineerActivityStore(path).events()


# --- deterministic calls -----------------------------------------------------


def test_lap_calls_and_templates(store):
    attempts = FakeAttempts()
    watcher = observer(store, attempts=attempts)
    watcher.poll_once()
    attempts.add("r1", 1, 94_000)
    attempts.add("r2", 2, 93_197)
    attempts.add("r3", 3, 93_193)
    attempts.add("r4", 4, 93_197)
    attempts.add("r5", 5, 95_000, valid=False)
    attempts.add("r6", 6, 110_500, pit=True)
    attempts.add("r7", 7, 93_000, disposition="partial")
    watcher.poll_once()
    events, _ = store.events()
    assert texts(store) == [
        "Lap 1, 1:34.000. First timed lap, that sets the benchmark.",
        "Lap 2, 1:33.197. New personal best, 0.803 faster than before.",
        "Lap 3, 1:33.193. New personal best, 0.004 faster than before.",
        "Lap 4, 1:33.197, +0.004 to your best.",
        "Lap 5, 1:35.000, invalidated. That one will not count.",
        "Lap 6, 1:50.500, included a pit stop.",
    ]
    assert [item["topic"] for item in events] == [
        "lap_time", "lap_time", "lap_time", "lap_time", "track_limits", "pit"]
    assert all(item["kind"] == "proactive" and item["source"] == "live_rules" for item in events)
    assert events[3]["session_id"] == SESSION and events[3]["lap"] == 4
    assert events[3]["detail"]["delta_ms"] == 4 and events[3]["detail"]["rule"] == "lap_completed"
    assert events[4]["priority"] == "high"


def test_history_is_not_announced_and_restart_is_idempotent(tmp_path):
    activity = EngineerActivityStore(tmp_path / "activity.sqlite3")
    attempts = FakeAttempts()
    attempts.add("old", 1, 90_000)
    first = observer(activity, attempts=attempts)
    first.poll_once()
    assert texts(activity) == []
    attempts.add("r2", 2, 91_000)
    first.poll_once()
    first.poll_once()
    assert len(texts(activity)) == 1

    # Restart: a new observer resumes from the persisted cursor and best.
    attempts.add("r3", 3, 91_500)
    second = observer(activity, attempts=attempts)
    second.poll_once()
    assert texts(activity)[-1] == "Lap 3, 1:31.500, +0.500 to your best."
    assert len(texts(activity)) == 2

    # Cursor lost: revisions already logged are not repeated.
    with sqlite3.connect(activity.path) as database:
        database.execute("UPDATE engineer_activity_meta SET value='0' WHERE key='attempt_cursor'")
    third = observer(activity, attempts=attempts)
    third.poll_once()
    # Only the never-logged history revision is new; laps 2 and 3 are not repeated.
    assert len(texts(activity)) == 3


def test_topic_filter_suppresses_calls_but_keeps_state(store):
    attempts = FakeAttempts()
    enabled = {"track_limits"}
    watcher = observer(store, attempts=attempts, topics=lambda: enabled)
    watcher.poll_once()
    attempts.add("a", 1, 94_000)
    attempts.add("b", 2, 95_000, valid=False)
    attempts.add("c", 3, 94_500, pit=True)
    watcher.poll_once()
    assert texts(store) == ["Lap 2, 1:35.000, invalidated. That one will not count."]
    enabled.add("lap_time")
    attempts.add("d", 4, 94_100)
    watcher.poll_once()
    assert texts(store)[-1] == "Lap 4, 1:34.100, +0.100 to your best."


def test_topic_filter_failure_allows_calls_and_telemetry_is_exempt(store):
    def broken():
        raise RuntimeError("boom")

    runtime = FakeRuntime("receiving")
    attempts = FakeAttempts()
    watcher = observer(store, runtime, attempts, topics=broken)
    watcher.poll_once()
    runtime.state = "stale"
    attempts.add("a", 1, 94_000)
    watcher.poll_once()
    assert len(texts(store)) == 2
    watcher.set_topics(lambda: set())
    runtime.state = "receiving"
    attempts.add("b", 2, 94_500)
    watcher.poll_once()
    assert texts(store)[-1] == "Telemetry is back. Data is flowing again."
    assert len(texts(store)) == 3


def test_telemetry_gap_recovery_and_rate_limit(store):
    now = [100.0]
    runtime = FakeRuntime("listening")
    watcher = observer(store, runtime, clock=lambda: now[0])
    watcher.poll_once()
    assert texts(store) == []
    runtime.state = "receiving"
    watcher.poll_once()
    runtime.state = "stale"
    watcher.poll_once()
    runtime.state = "receiving"
    watcher.poll_once()
    runtime.state = "stale"
    now[0] += 1
    watcher.poll_once()  # repeated gap inside the rate-limit window is suppressed
    runtime.state = "failed"
    runtime.error = "OSError: bind"
    watcher.poll_once()
    events, _ = store.events()
    assert [(item["kind"], item["detail"]["rule"]) for item in events] == [
        ("proactive", "connected"), ("issue", "gap"), ("proactive", "recovery"), ("issue", "failed")]
    assert all(item["topic"] == "telemetry" and item["session_id"] is None for item in events)
    assert events[3]["detail"]["error"] == "OSError: bind"


def test_observer_thread_runs_and_stops(store):
    attempts = FakeAttempts()
    watcher = observer(store, attempts=attempts, interval_s=0.01)
    watcher.poll_once()
    attempts.add("a", 1, 94_000)
    watcher.start()
    try:
        for _ in range(200):
            if texts(store):
                break
            watcher._stop.wait(0.01)
    finally:
        watcher.stop()
    assert len(texts(store)) == 1
    assert not watcher._thread.is_alive()


def test_formatting():
    assert format_lap_time(93_193) == "1:33.193"
    assert format_lap_time(59_999) == "0:59.999"
    assert format_delta(4) == "+0.004"
    assert format_delta(-123) == "-0.123"


def test_evidence_attempt_source_reads_new_published_player_attempts(tmp_path):
    evidence = EvidenceStore(tmp_path / "evidence.sqlite3")

    def insert(identifier, role, state, lap):
        payload = json.dumps({"lap_number": lap, "lap_time_ms": 90_000 + lap, "game_valid": True,
                              "pit_encountered": False, "disposition": "completed"})
        with evidence.connect() as database:
            database.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (identifier, SESSION, None, identifier, role, "{}", payload, 0, 0, 1))
            database.execute("INSERT INTO dispositions VALUES (?,?,?,?)", (identifier, 1, state, "x"))

    source = EvidenceAttemptSource(evidence)
    assert source.high_water() == 0
    insert("a", "player", "published", 1)
    insert("b", "opponent", "published", 2)
    insert("c", "player", "quarantined", 3)
    insert("d", "player", "published", 4)
    items, resume = source.player_attempts(0, 25)
    assert [item.revision_id for item in items] == ["a", "d"] and resume == 4
    assert items[0].session_id == SESSION and items[0].payload["lap_number"] == 1
    assert source.player_attempts(resume, 25) == ([], resume)
    items, resume = source.player_attempts(0, 2)
    assert [item.revision_id for item in items] == ["a"] and resume == 3
    before = evidence.path.stat().st_size
    source.player_attempts(0, 25)
    assert evidence.path.stat().st_size == before


# --- ask recording and endpoint ---------------------------------------------


class StubRequest:
    def __init__(self):
        self.state = type("State", (), {})()


def run(coroutine):
    import asyncio

    return asyncio.run(coroutine)


def test_ask_log_records_success_failure_and_unnoted_requests(store):
    from fastapi.responses import JSONResponse

    log = AskActivityLog(store)
    outcome = {"value": {"data": {"message": "unused"}}}

    @log.record("v1_ask")
    async def handler(*, request):
        result = outcome["value"]
        if isinstance(result, BaseException):
            raise result
        return result

    request = StubRequest()
    run(handler(request=request))  # no question noted: nothing recorded
    assert store.events()[0] == []

    log.note_question(request, source="v1_ask", question="How are my tyres wearing?",
                      detail={"selection": {"intent": "attempt_summary"}})
    outcome["value"] = JSONResponse(content={"api_version": "v1", "status": "ok",
                                             "data": {"status": "answered", "message": "Rear left is fading."}})
    run(handler(request=request))
    request = StubRequest()
    log.note_question(request, source="v2_ask", question="Why was that lap slow?", session_id=SESSION)
    outcome["value"] = JSONResponse(status_code=409, content={"api_version": "v1", "status": "unavailable",
                                                              "data": None, "reason": "engineer_ask_busy"})
    run(handler(request=request))
    request = StubRequest()
    log.note_question(request, source="v2_ask", question="Gap to the car ahead?", session_id=SESSION)
    outcome["value"] = {"data": {"status": "available", "message": "Two tenths."}}
    assert run(handler(request=request)) == outcome["value"]
    request = StubRequest()
    log.note_question(request, source="v2_ask", question="Anything?", session_id=SESSION)
    outcome["value"] = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        run(handler(request=request))

    events, _ = store.events()
    assert [(item["kind"], item["topic"], item["related_id"]) for item in events] == [
        ("question", "tyres", None), ("response", "tyres", 1),
        ("question", "general", None), ("issue", "general", 3),
        ("question", "gap", None), ("response", "gap", 5),
        ("question", "general", None), ("issue", "general", 7)]
    assert events[1]["text"] == "Rear left is fading." and events[1]["detail"]["status"] == "answered"
    assert events[3]["detail"] == {"http_status": 409, "reason": "engineer_ask_busy"}
    assert events[3]["priority"] == "high" and events[3]["session_id"] == SESSION
    assert events[7]["detail"]["reason"] == "handler_RuntimeError"


def test_ask_log_never_breaks_the_handler(tmp_path):
    broken = EngineerActivityStore(tmp_path / "missing" / "file.sqlite3")
    broken.path.parent.mkdir()
    broken.path.parent.rmdir()
    broken.path.parent.write_text("not a directory")
    log = AskActivityLog(broken)

    @log.record("v1_ask")
    async def handler(*, request):
        return {"data": {"message": "ok"}}

    request = StubRequest()
    log.note_question(request, source="v1_ask", question="hello")
    assert run(handler(request=request)) == {"data": {"message": "ok"}}


def test_events_endpoint_contract(tmp_path):
    app = create_app(tmp_path / "archive.sqlite3", recordings_root=tmp_path / "recordings",
                     control_token=TOKEN, automatic_acquisition=False)
    activity = app.state.engineer_activity
    assert activity.observer is None
    with TestClient(app) as client:
        empty = client.get("/api/v2/engineer/events")
        assert empty.status_code == 200
        assert empty.json() == {"data": {"events": [], "next_after": 0}}
        for index in range(3):
            activity.store.append(kind="proactive", topic="lap_time", text=f"call {index}",
                                  source="live_rules", session_id=SESSION, lap=index + 1)
        activity.store.append(kind="issue", text="other", source="system", session_id="x" * 32)
        newest = client.get("/api/v2/engineer/events", params={"limit": 2}).json()["data"]
        assert [item["id"] for item in newest["events"]] == [3, 4] and newest["next_after"] == 4
        polled = client.get("/api/v2/engineer/events", params={"after": 1, "limit": 2}).json()["data"]
        assert [item["id"] for item in polled["events"]] == [2, 3] and polled["next_after"] == 3
        assert client.get("/api/v2/engineer/events", params={"after": 4}).json()["data"] == {
            "events": [], "next_after": 4}
        scoped = client.get("/api/v2/engineer/events", params={"session_id": SESSION}).json()["data"]
        assert [item["id"] for item in scoped["events"]] == [1, 2, 3]
        assert set(scoped["events"][0]) == {
            "id", "created_at_utc", "kind", "topic", "text", "session_id", "lap", "source",
            "related_id", "priority", "detail"}
        for bad in ({"limit": 0}, {"limit": 201}, {"after": -1}, {"session_id": ""}):
            assert client.get("/api/v2/engineer/events", params=bad).status_code == 422
    assert (tmp_path / "archive-engineer-activity.sqlite3").exists()


def test_events_endpoint_reports_unavailable_store(tmp_path):
    app = create_app(tmp_path / "archive.sqlite3", recordings_root=tmp_path / "recordings",
                     control_token=TOKEN, automatic_acquisition=False)
    with sqlite3.connect(tmp_path / "archive-engineer-activity.sqlite3") as database:
        database.execute("PRAGMA user_version=9")
    with TestClient(app) as client:
        response = client.get("/api/v2/engineer/events")
        assert response.status_code == 503
        assert response.json()["reason"] == "engineer_activity_unavailable"


def test_v1_ask_endpoint_records_question_and_response(tmp_path):
    app = create_app(tmp_path / "archive.sqlite3", recordings_root=tmp_path / "recordings",
                     control_token=TOKEN, automatic_acquisition=False)
    body = {"question": "Where am I losing tyre life?",
            "selection": {"intent": "attempt_summary", "target_attempt_key": "missing"}}
    with TestClient(app) as client:
        response = client.post("/api/v1/engineer/ask", json=body, headers=HEADERS)
        assert response.status_code == 200
        data = response.json()["data"]
        events = client.get("/api/v2/engineer/events").json()["data"]["events"]
        assert [(item["kind"], item["source"], item["topic"]) for item in events] == [
            ("question", "v1_ask", "tyres"), ("response", "v1_ask", "tyres")]
        assert events[1]["related_id"] == events[0]["id"]
        assert events[1]["text"] == data["message"]
        assert events[1]["detail"]["status"] == data["status"]
        assert events[0]["detail"]["selection"]["target_attempt_key"] == "missing"
        # Unauthorised and invalid requests are not logged.
        assert client.post("/api/v1/engineer/ask", json=body).status_code == 401
        assert client.post("/api/v1/engineer/ask", json={"question": ""}, headers=HEADERS).status_code == 422
        assert len(client.get("/api/v2/engineer/events").json()["data"]["events"]) == 2


def test_v1_ask_endpoint_records_failure_as_issue(tmp_path, monkeypatch):
    from f1_engineer.api import app as module
    from f1_engineer.analysis.engineer_runtime import EngineerRuntimeUnavailable

    async def unavailable(*_args, **_kwargs):
        raise EngineerRuntimeUnavailable("model_offline")

    monkeypatch.setattr(module, "answer_engineer_question", unavailable)
    app = create_app(tmp_path / "archive.sqlite3", recordings_root=tmp_path / "recordings",
                     control_token=TOKEN, automatic_acquisition=False)
    body = {"question": "Are the pits open?",
            "selection": {"intent": "attempt_summary", "target_attempt_key": "missing"}}
    with TestClient(app) as client:
        response = client.post("/api/v1/engineer/ask", json=body, headers=HEADERS)
        assert response.status_code == 503
        events = client.get("/api/v2/engineer/events").json()["data"]["events"]
        assert [(item["kind"], item["topic"]) for item in events] == [("question", "pit"), ("issue", "pit")]
        assert events[1]["related_id"] == events[0]["id"]
        assert events[1]["detail"] == {"http_status": 503, "reason": "engineer_ask_unavailable"}

def test_v2_ask_endpoint_records_question_and_response(tmp_path):
    evidence = EvidenceStore(tmp_path / "evidence.sqlite3")
    app = create_app(tmp_path / "archive.sqlite3", evidence_database_path=evidence.path,
                     recordings_root=tmp_path / "recordings", control_token=TOKEN,
                     automatic_acquisition=False)
    url = f"/api/v2/session-evidence/sessions/{SESSION}/ask"
    with TestClient(app) as client:
        response = client.post(url, headers=HEADERS, json={
            "binding_id": "a" * 64, "question": "What is the gap to the car ahead?"})
        assert response.status_code == 200
        events = client.get("/api/v2/engineer/events", params={"session_id": SESSION}).json()["data"]["events"]
        assert [(item["kind"], item["source"], item["topic"]) for item in events] == [
            ("question", "v2_ask", "gap"), ("response", "v2_ask", "gap")]
        assert events[1]["related_id"] == events[0]["id"]
        assert events[1]["text"] == response.json()["data"]["message"]
        assert events[0]["detail"]["binding_id"] == "a" * 64
        assert client.post(url, json={"question": "x"}).status_code == 403
        assert len(client.get("/api/v2/engineer/events").json()["data"]["events"]) == 2


def test_build_engineer_activity_wires_observer_only_with_live_runtime(tmp_path):
    evidence = EvidenceStore(tmp_path / "evidence.sqlite3")
    without = build_engineer_activity(tmp_path / "archive.sqlite3", evidence, None)
    assert without.observer is None
    assert without.store.path == (tmp_path / "archive-engineer-activity.sqlite3").resolve()
    assert not without.store.path.exists()
    live = build_engineer_activity(tmp_path / "archive.sqlite3", evidence, FakeRuntime())
    assert isinstance(live, EngineerActivity) and live.observer is not None
    assert live.observer.topics() is None
    live.set_topics(lambda: {"gap"})
    assert live.observer.topics() == {"gap"}
    live.stop()
