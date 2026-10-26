import json
import queue
import sqlite3
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from f1_engineer.api.app import create_app
from f1_engineer.engineer_activity.store import EngineerActivityStore
from f1_engineer.radio.activity_reporter import ActivityReporter
from f1_engineer.radio.command import dispatch_submission, start_typed_request
from tests.test_radio_intents import TOKEN, StubRuntime, context, intent
from tests.test_standalone_radio import message, scheduler

HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def test_migrate_old_schema_preserves_rows_indexes_sequence_and_dedupe(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as database:
        database.executescript("""
            CREATE TABLE engineer_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at_utc TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('proactive','question','response','issue')),
                topic TEXT NOT NULL, text TEXT NOT NULL, session_id TEXT, lap INTEGER,
                source TEXT NOT NULL CHECK (source IN ('v1_ask','v2_ask','live_rules','system')),
                related_id INTEGER, priority TEXT NOT NULL, detail TEXT, dedupe_key TEXT UNIQUE
            );
            CREATE INDEX engineer_activity_session ON engineer_activity(session_id,id);
            CREATE INDEX custom_topic ON engineer_activity(topic);
            CREATE TRIGGER engineer_activity_append_only BEFORE UPDATE ON engineer_activity
                BEGIN SELECT RAISE(ABORT, 'append-only engineer activity'); END;
            CREATE TABLE engineer_activity_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO engineer_activity VALUES
                (7,'2026-10-16T00:00:00Z','question','gap','Gap?','session',3,'v2_ask',NULL,'normal','{}','old'),
                (99,'2026-10-16T00:00:01Z','response','gap','Two seconds','session',3,'v2_ask',7,'normal',NULL,NULL);
            DELETE FROM engineer_activity WHERE id=99;
            PRAGMA user_version=1;
        """)
    store = EngineerActivityStore(path)
    before = store.events()[0]
    assert before[0]["id"] == 7 and before[0]["text"] == "Gap?"
    assert store.append(kind="question", source="radio", text="Next?", dedupe_key="new") == 100
    assert store.append(kind="question", source="radio", text="Duplicate", dedupe_key="old") is None
    assert EngineerActivityStore(path).events()[0] == store.events()[0]
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 2
        assert {row[1] for row in database.execute("PRAGMA index_list(engineer_activity)")} >= {
            "custom_topic", "engineer_activity_session"}
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            database.execute("UPDATE engineer_activity SET text='changed' WHERE id=7")


def test_failed_migration_rolls_back_original_table(tmp_path):
    from f1_engineer.engineer_activity.store import EngineerActivityUnavailable

    path = tmp_path / "rollback.sqlite3"
    with sqlite3.connect(path) as database:
        database.executescript("""
            CREATE TABLE engineer_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT CHECK (source IN ('v1_ask','v2_ask','live_rules','system'))
            );
            INSERT INTO engineer_activity VALUES (7, 'v2_ask');
            CREATE VIEW dependent_activity AS SELECT * FROM engineer_activity;
            PRAGMA user_version=1;
        """)
    with pytest.raises(EngineerActivityUnavailable):
        EngineerActivityStore(path).events()
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 1
        assert database.execute("SELECT * FROM engineer_activity").fetchall() == [(7, "v2_ask")]
        assert database.execute("SELECT * FROM dependent_activity").fetchall() == [(7, "v2_ask")]
        assert database.execute("SELECT name FROM sqlite_master WHERE name='engineer_activity_new'").fetchall() == []


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path / "a.sqlite3", recordings_root=tmp_path / "r", control_token=TOKEN,
                     automatic_acquisition=False, engineer_runtime=StubRuntime(result=intent()))
    with TestClient(app) as client:
        yield client


def spoken(**changes):
    return {"client_event_id": "radio-client-1", "kind": "answer", "owner": "automatic_gaps",
            "text": "Gap ahead two seconds.", "session_id": "session", "lap": 3,
            "started_at": "2026-10-16T01:00:00Z", **changes}


def test_spoken_endpoint_requires_auth_validates_whole_batch_and_deduplicates(api):
    endpoint = "/api/v2/engineer/radio/events"
    assert api.post(endpoint, json={"events": [spoken()]}).status_code == 401
    assert api.post(endpoint, headers=HEADERS, json={"events": [spoken(), spoken(kind="issue")]}).status_code == 422
    assert api.get("/api/v2/engineer/events").json()["data"]["events"] == []
    first = api.post(endpoint, headers=HEADERS, json={"events": [spoken()]}).json()["data"]["event_ids"]
    assert first[0] is not None
    assert api.post(endpoint, headers=HEADERS, json={"events": [spoken()]}).json()["data"]["event_ids"] == [None]
    events = api.get("/api/v2/engineer/events", params={"session_id": "session"}).json()["data"]["events"]
    assert len(events) == 1
    assert events[0]["topic"] == "gap" and events[0]["source"] == "radio"
    assert events[0]["detail"]["spoken"] is True


@pytest.mark.parametrize("changes", [
    {"lap": True}, {"related_id": -1}, {"owner": "x" * 129}, {"text": " "},
    {"text": "x" * 2001}, {"started_at": "2026-10-16"}, {"extra": 1}, {"client_event_id": ""},
])
def test_spoken_endpoint_rejects_invalid_events(api, changes):
    assert api.post("/api/v2/engineer/radio/events", headers=HEADERS,
                    json={"events": [spoken(**changes)]}).status_code == 422


def test_spoken_endpoint_limits_request(api):
    endpoint = "/api/v2/engineer/radio/events"
    assert api.post(endpoint, headers=HEADERS, json={"events": []}).status_code == 422
    assert api.post(endpoint, headers=HEADERS, json={"events": [spoken()] * 17}).status_code == 422
    assert api.post(endpoint, headers={**HEADERS, "Content-Type": "application/json"},
                    content=b" " * 32769).status_code == 413


def test_intent_question_links_only_actual_playback(api):
    result = api.post("/api/v2/engineer/radio-intent", headers=HEADERS,
                      json={"transcript": "what is my gap", "context": context()})
    assert result.status_code == 200
    related = result.json()["data"]["question_event_id"]
    assert [event["kind"] for event in api.get("/api/v2/engineer/events").json()["data"]["events"]] == ["question"]
    api.post("/api/v2/engineer/radio/events", headers=HEADERS,
             json={"events": [spoken(related_id=related)]})
    events = api.get("/api/v2/engineer/events").json()["data"]["events"]
    assert events[1]["related_id"] == events[0]["id"]


def test_intent_failure_records_issue(tmp_path):
    from f1_engineer.analysis.engineer_runtime import EngineerRuntimeUnavailable

    app = create_app(tmp_path / "a.sqlite3", recordings_root=tmp_path / "r", control_token=TOKEN,
                     automatic_acquisition=False,
                     engineer_runtime=StubRuntime(error=EngineerRuntimeUnavailable("codex_request_timeout")))
    with TestClient(app) as client:
        assert client.post("/api/v2/engineer/radio-intent", headers=HEADERS,
                           json={"transcript": "gap?", "context": context()}).status_code == 503
        events = client.get("/api/v2/engineer/events").json()["data"]["events"]
    assert [event["kind"] for event in events] == ["question", "issue"]
    assert events[1]["related_id"] == events[0]["id"]


def test_reporter_logs_only_playback_and_retries_same_id():
    sent = []
    delivered = threading.Event()

    def send(events):
        sent.append(events)
        if len(sent) == 1:
            return False
        delivered.set()
        return True

    reporter = ActivityReporter(send, retry_delay=0.001)
    radio, playback, notifications = scheduler()
    radio.notify = reporter.observe
    radio.related_question_id = 42
    radio.submit(replace(message(kind="answer"), scope="race"), now=0)
    radio.submit(message("expired", expires=1), now=0)
    radio.tick(now=2)
    assert delivered.wait(2)
    playback.playing = False
    radio.tick(now=3)
    assert radio.repeat(now=4)
    radio.tick(now=4)
    reporter.close()
    assert sent[0][0]["client_event_id"] == sent[1][0]["client_event_id"]
    assert sent[1][0]["related_id"] == 42
    assert len(sent) == 3 and sent[2][0]["repeat"] is True
    assert sent[2][0]["client_event_id"] != sent[1][0]["client_event_id"]


def test_reporter_overflow_never_waits_for_network_and_retains_newest():
    blocked = threading.Event()
    release = threading.Event()
    sent = []
    notices = []

    def send(events):
        sent.extend(events)
        blocked.set()
        release.wait(2)
        return True

    reporter = ActivityReporter(send, capacity=2, notify=notices.append)
    event = {"event": "radio_started", "kind": "proactive", "text": "First", "scope": json.dumps(["g", "s"])}
    reporter.observe(event)
    assert blocked.wait(1)
    started = time.monotonic()
    for index in range(10):
        reporter.observe({**event, "text": str(index)})
    assert time.monotonic() - started < 0.5
    assert notices and all(item["reason"] == "queue_full" for item in notices)
    release.set()
    reporter.close()
    assert [item["text"] for item in sent][-2:] == ["8", "9"]
    assert sent[-1]["session_id"] == "s"


def test_typed_interpretation_carries_question_link_to_scheduled_reply():
    from tests.test_radio_questions import live

    controller, radio, events, value = live()

    class Client:
        async def interpret(self, *args):
            return {"status": "available", "question_event_id": 123,
                    "intent": intent(action="unsupported", unsupported_topic="strategy")}

    results = queue.Queue()
    start_typed_request("what is my gap", Client(), controller, {}, results, interpret=True)
    submission, voice = results.get(timeout=2)
    dispatch_submission(submission, controller, voice, now=10)
    assert radio.pending[-1].related_id == 123
    assert radio.related_question_id is None


def test_voice_interpretation_preserves_question_id():
    from types import SimpleNamespace
    from f1_engineer.radio.listening import VoiceInput

    async def interpret(*args):
        return {"status": "available", "intent": intent(), "question_event_id": 321}

    voice = SimpleNamespace(_clock=lambda: 10, _put=lambda *args, **kwargs: None,
                            _interpret=interpret, intent_context=context())
    result = VoiceInput._interpretation(voice, "capture", "gap ahead", 9, threading.Event())
    assert result["question_event_id"] == 321


def test_reporter_retry_budget_is_bounded():
    attempts = []
    dropped = threading.Event()

    def notify(event):
        if event.get("reason") == "retry_limit":
            dropped.set()

    reporter = ActivityReporter(lambda events: attempts.append(events) or False,
                                notify=notify, retry_delay=0.001, max_attempts=3)
    reporter.observe({"event": "radio_started", "kind": "answer", "text": "Say again."})
    assert dropped.wait(2)
    reporter.close()
    assert len(attempts) == 3
