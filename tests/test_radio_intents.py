from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from f1_engineer.analysis import codex_runtime
from f1_engineer.analysis.engineer_runtime import EngineerRuntimeUnavailable
from f1_engineer.api.app import create_app
from f1_engineer.radio import command
from f1_engineer.radio.glossary import DEFAULT_GLOSSARY, load_glossary
from f1_engineer.radio.intents import (LOCAL_CONTROLS, intent_schema, parse_context, parse_intent, render_intent)
from f1_engineer.radio.questions import plan_question
from f1_engineer.radio.spoken_numbers import regional_reply
from f1_engineer.radio.tasks import parse_task_request
from tests.test_codex_runtime import MODEL, completed_events, install_server, successful_server
from tests.test_radio_listening import REQUEST_ID, listening
from tests.test_radio_questions import live

BASE = dict(action="unclear", heard_as="x", corrections=[], targets=[], metrics=[], lap_number=None, laps=None,
            task_id=None, task_kind=None, tolerance_ms=None, region_start_m=None, region_end_m=None,
            region_name=None, compare_previous_region=False, opponent=None, control=None, memory=None,
            unsupported_topic=None)
NAMES = ("charles leclerc", "leclerc")
LECLERC = "Charles Leclerc"


def intent(**fields):
    return parse_intent({**BASE, **fields})


def context(**fields):
    base = {"drivers": [{"name": LECLERC, "race_number": 16, "position": 2, "role": "ahead"}],
            "track": "Bahrain", "regions": ["Turn 5"], "tasks": [{"task_id": 2, "summary": "you: last lap"}],
            "recent": [], "pending_region": None, "glossary": {"terms": {}, "mishearings": {}}}
    return parse_context({**base, **fields})


@pytest.mark.parametrize("fields,targets,metrics", [
    (dict(targets=["self"], metrics=["last_lap"]), ("self",), ("last_lap",)),
    (dict(targets=["self", "leader"], metrics=["last_lap", "position"]), ("self", "leader"), ("last_lap", "position")),
    (dict(targets=["ahead", "behind"], metrics=["last_lap"]), ("ahead", "behind"), ("last_lap",)),
    (dict(targets=["position:3", f"name:{LECLERC}"], metrics=["current_lap", "front_gap"]),
     ("position:3", "name:charles leclerc"), ("current_lap", "front_gap")),
    (dict(targets=["self"], lap_number=4), ("self",), ("lap_number:4",)),
    (dict(targets=["self"], metrics=["track_name"]), ("self",), ("track_name",)),
    (dict(targets=["self"], metrics=["weather", "tyre_age"]), ("self",), ("weather", "tyre_age")),
    (dict(targets=["self"], metrics=["last_sector2"]), ("self",), ("last_sector2",)),
])
def test_timing_questions_render_into_the_existing_planner(fields, targets, metrics):
    rendered = render_intent(intent(action="timing_question", **fields))
    plan = plan_question(rendered.text, names=NAMES)
    assert rendered.kind == "request" and plan.targets == targets and plan.metrics == metrics


@pytest.mark.parametrize("fields,kind,expected", [
    (dict(action="task_list"), "list", {}),
    (dict(action="task_cancel_all"), "cancel_all", {}),
    (dict(action="task_cancel"), "cancel_reference", {}),
    (dict(action="task_cancel", task_id=4), "cancel", {"task_id": 4}),
    (dict(action="task_extend", laps=3), "extend", {"laps": 3, "task_id": None}),
    (dict(action="task_extend", laps=3, task_id=2), "extend", {"laps": 3, "task_id": 2}),
    (dict(action="task_create", task_kind="lap_report", targets=["self"], metrics=["leader_gap"], laps=5),
     "create", {"laps": 5, "question": "my gap to the leader"}),
    (dict(action="task_create", task_kind="lap_report", targets=["self"], metrics=["position"], laps=1),
     "create", {"laps": 1}),
    (dict(action="task_create", task_kind="consistency", laps=5), "create", {"laps": 5}),
    (dict(action="task_create", task_kind="target_pace", tolerance_ms=300, laps=4),
     "create", {"tolerance_ms": 300, "laps": 4}),
    (dict(action="task_create", task_kind="watch", opponent=LECLERC, laps=3), "create", {"laps": 3}),
    (dict(action="task_update", task_id=2, task_kind="watch", opponent=LECLERC), "update", {"task_id": 2}),
    (dict(action="task_create", task_kind="region", region_start_m=100, region_end_m=250.5, laps=3),
     "create", {"region_bounds_m": (100.0, 250.5), "laps": 3}),
    (dict(action="task_create", task_kind="region", region_start_m=100, region_end_m=250,
          compare_previous_region=True, laps=2), "create", {"region_compare": True}),
    (dict(action="task_create", task_kind="region", region_name="Turn 5", laps=2),
     "create", {"region_name": "turn 5", "laps": 2}),
    (dict(action="task_create", task_kind="paired_region", opponent=LECLERC, region_start_m=100,
          region_end_m=250, laps=2), "create", {"region_opponent": "charles leclerc"}),
    (dict(action="task_create", task_kind="paired_region", opponent=LECLERC, region_name="Turn 5", laps=2),
     "create", {"region_opponent": "charles leclerc", "region_name": "turn 5"}),
    (dict(action="task_create", task_kind="paired_region", opponent=LECLERC, laps=2),
     "clarify_region", {"region_opponent": "charles leclerc"}),
    (dict(action="task_create", task_kind="focus"), "focus_corner", {}),
    (dict(action="task_create", task_kind="focus", region_name="Turn 5", laps=2),
     "create", {"focus_coach": True, "region_name": "turn 5"}),
    (dict(action="task_create", task_kind="focus", region_start_m=100, region_end_m=200, laps=2),
     "create", {"focus_coach": True, "region_bounds_m": (100.0, 200.0)}),
])
def test_task_intents_render_into_the_existing_task_grammar(fields, kind, expected):
    action = parse_task_request(render_intent(intent(**fields)).text)
    assert action.kind == kind
    assert all(getattr(action, key) == value for key, value in expected.items())


def test_region_replies_memory_controls_and_replies():
    assert regional_reply(render_intent(intent(action="region_reply", region_start_m=100, region_end_m=200)).text) \
        == ((100.0, 200.0), None)
    assert regional_reply(render_intent(intent(action="region_reply", region_name="Turn 5")).text) == (None, "turn 5")
    assert render_intent(intent(action="memory", memory="last_request")).text == "what was my last request"
    assert LOCAL_CONTROLS[render_intent(intent(action="control", control="quiet")).text] == "quiet"
    for fields in (dict(action="unclear"), dict(action="smalltalk"),
                   dict(action="unsupported", unsupported_topic="strategy")):
        assert render_intent(intent(**fields)).kind == "reply"


@pytest.mark.parametrize("fields", [
    dict(action="launch_missiles"), dict(action="timing_question"), dict(action="timing_question", targets=["self"]),
    dict(action="timing_question", targets=['name:x"y'], metrics=["last_lap"]),
    dict(action="timing_question", targets=["name:__named_0__"], metrics=["last_lap"]),
    dict(action="timing_question", targets=["self"], metrics=["last_lap", "position", "speed", "tyre_age"]),
    dict(action="timing_question", targets=["self"], metrics=["consistency"]),
    dict(action="task_extend"), dict(action="task_update", task_kind="consistency"),
    dict(action="task_create", task_kind="target_pace"), dict(action="task_create", task_kind="watch"),
    dict(action="task_create", task_kind="consistency", laps=21),
    dict(action="task_create", task_kind="region", region_start_m=1), dict(action="region_reply"),
    dict(action="task_create", task_kind="region", region_name="turn with spin"),
    dict(action="unsupported"), dict(action="control", control="self_destruct"),
])
def test_invalid_intents_are_rejected(fields):
    with pytest.raises(ValueError):
        intent(**fields)


def test_intents_must_be_exact_json_objects():
    with pytest.raises(ValueError):
        parse_intent({**BASE, "extra": 1})
    with pytest.raises(ValueError):
        parse_intent("not json")
    with pytest.raises(ValueError):
        parse_intent({key: value for key, value in BASE.items() if key != "laps"})
    assert parse_intent(json.dumps(BASE))["action"] == "unclear"


def test_context_pins_names_and_task_numbers_to_the_live_session():
    known = context()
    intent_ok = {**BASE, "action": "timing_question", "targets": [f"name:{LECLERC.lower()}"], "metrics": ["last_lap"]}
    assert parse_intent(intent_ok, context=known)
    with pytest.raises(ValueError):
        parse_intent({**intent_ok, "targets": ["name:Max Verstappen"]}, context=known)
    with pytest.raises(ValueError):
        parse_intent({**BASE, "action": "task_cancel", "task_id": 9}, context=known)
    with pytest.raises(ValueError):
        parse_intent({**BASE, "action": "task_create", "task_kind": "watch", "opponent": "Max Verstappen"},
                     context=known)
    assert parse_intent({**BASE, "action": "task_cancel", "task_id": 2}, context=known)


def test_schema_limits_names_and_tasks_to_the_roster():
    schema = intent_schema(context())
    assert f"name:{LECLERC}" in schema["properties"]["targets"]["items"]["enum"]
    assert schema["properties"]["opponent"]["enum"] == [LECLERC, None]
    assert schema["properties"]["task_id"]["enum"] == [2, None]
    assert set(schema["required"]) == set(schema["properties"]) and schema["additionalProperties"] is False


def test_context_is_bounded():
    for bad in ({"drivers": [{"name": LECLERC, "race_number": 1, "position": 1, "role": "ahead"}] * 25},
                {"regions": ["x"] * 65}, {"tasks": [{"task_id": 1, "summary": "x"}] * 9},
                {"recent": [{"heard": "a", "reply": "b"}] * 4}, {"track": "x" * 81}):
        with pytest.raises(ValueError):
            context(**bad)


def test_glossary_merges_valid_local_entries_only(tmp_path):
    path = tmp_path / "glossary.toml"
    path.write_text('[mishearings]\n"lecler" = "Leclerc"\n"" = "bad"\n[terms]\nundercut = "pit earlier"\n')
    glossary, problem = load_glossary(path)
    assert problem is None and glossary["mishearings"]["lecler"] == "Leclerc" and "" not in glossary["mishearings"]
    assert glossary["terms"]["undercut"] == "pit earlier" and glossary["mishearings"]["slap"] == "lap"
    assert load_glossary(tmp_path / "missing.toml") == (DEFAULT_GLOSSARY, None)
    path.write_text("not = [valid")
    assert load_glossary(path)[1] == "glossary_file_invalid"


def intent_json(**fields):
    return json.dumps({**BASE, "action": "timing_question", "targets": ["self"], "metrics": ["last_lap"], **fields})


def test_radio_interpretation_uses_luna_with_the_live_schema(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    first = successful_server(completed_events(intent_json()))
    install_server(monkeypatch, runtime, first)
    result, model = asyncio.run(runtime.interpret_radio("what was my last lap", context()))
    assert result["metrics"] == ["last_lap"] and model["model_name"] == MODEL and model["fallback_enabled"] is False
    calls = dict(first.calls)
    assert calls["turn/start"]["effort"] == "low" and calls["thread/start"]["ephemeral"] is True
    assert "name:Charles Leclerc" in json.dumps(calls["turn/start"]["outputSchema"])


def test_radio_server_is_kept_between_requests_and_dropped_after_a_failure(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    entered, exited = [], []
    server = successful_server()

    @asynccontextmanager
    async def open_server():
        entered.append(1)
        try:
            yield server
        finally:
            exited.append(1)

    monkeypatch.setattr(runtime, "_open", open_server)

    async def scenario():
        server.events = completed_events(intent_json())
        await runtime.interpret_radio("one", context())
        server.events = completed_events(intent_json(metrics=["position"]))
        await runtime.interpret_radio("two", context())
        assert (len(entered), len(exited)) == (1, 0)
        server.events = completed_events('{"action":"nonsense"}')
        with pytest.raises(EngineerRuntimeUnavailable):
            await runtime.interpret_radio("three", context())
        await runtime.close()

    asyncio.run(scenario())
    assert len(entered) == 1 and len(exited) == 1


def test_effort_option_is_dropped_when_the_codex_build_rejects_it(monkeypatch, tmp_path):
    runtime = codex_runtime.CodexRuntime(tmp_path)
    server = successful_server(completed_events(intent_json()))
    original = server.rpc
    seen = []

    async def rpc(method, params):
        if method == "turn/start":
            seen.append("effort" in params)
            if "effort" in params:
                raise EngineerRuntimeUnavailable("codex_request_failed")
        return await original(method, params)

    server.rpc = rpc
    install_server(monkeypatch, runtime, server)
    asyncio.run(runtime.interpret_radio("what was my last lap", context()))
    assert seen == [True, False] and runtime._radio_effort is None


TOKEN = "radio-intent-test-token-0123456789abcdef"


class StubRuntime:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def status(self):
        return {}

    async def interpret_radio(self, transcript, ctx):
        self.calls.append((transcript, ctx))
        if self.error:
            raise self.error
        return self.result, {"model_name": MODEL, "latency_ms": 12}


def post(tmp_path, runtime, body=None, token=TOKEN):
    app = create_app(tmp_path / "a.sqlite3", recordings_root=tmp_path / "r", control_token=TOKEN,
                     automatic_acquisition=False, engineer_runtime=runtime)
    with TestClient(app) as client:
        return client.post("/api/v2/engineer/radio-intent",
                           json=body or {"transcript": "last lap", "context": context()},
                           headers={"Authorization": f"Bearer {token}"})


def test_endpoint_returns_the_interpreted_intent(tmp_path):
    runtime = StubRuntime(intent(action="timing_question", targets=["self"], metrics=["last_lap"]))
    response = post(tmp_path, runtime)
    assert response.status_code == 200 and response.json()["data"]["intent"]["metrics"] == ["last_lap"]
    assert runtime.calls[0][0] == "last lap" and runtime.calls[0][1]["track"] == "Bahrain"


@pytest.mark.parametrize("kwargs,status,reason", [
    (dict(token="wrong-token-wrong-token-wrong-token"), 401, "radio_intent_unauthorized"),
    (dict(body={"transcript": "", "context": context()}), 422, "radio_intent_request_invalid"),
    (dict(body={"transcript": "hi", "context": {"drivers": []}}), 422, "radio_intent_request_invalid"),
    (dict(body={"transcript": "hi", "context": context(), "extra": 1}), 422, "radio_intent_request_invalid"),
])
def test_endpoint_rejects_bad_requests(tmp_path, kwargs, status, reason):
    response = post(tmp_path, StubRuntime(), **kwargs)
    assert response.status_code == status and response.json()["reason"] == reason


def test_endpoint_reports_runtime_failures_without_leaking_detail(tmp_path):
    response = post(tmp_path, StubRuntime(error=EngineerRuntimeUnavailable("codex_request_timeout")))
    assert response.status_code == 503 and response.json()["reason"] == "codex_request_timeout"


def test_endpoint_is_unavailable_for_runtimes_without_interpretation(tmp_path):
    class NoIntents:
        async def status(self):
            return {}

    assert post(tmp_path, NoIntents()).json()["reason"] == "radio_intent_runtime_unsupported"


def test_voice_input_attaches_the_interpretation_to_the_draft():
    seen = []

    async def transcribe(wav, request_id, cancelled):
        return {"status": "available", "transcript": "What was my latest time?"}

    async def interpret(transcript, ctx, request_id, cancelled):
        seen.append((transcript, ctx))
        return {"status": "available", "intent": intent(action="timing_question", targets=["self"],
                                                          metrics=["last_lap"])}

    voice, hardware, events, clock = listening(transcribe=transcribe)
    voice._interpret, voice.intent_context = interpret, {"drivers": []}
    from tests.test_radio_listening import run_capture
    run_capture(voice)
    voice.tick(scope="session")
    assert seen == [("What was my latest time?", {"drivers": []})]
    assert voice._draft == "What was my latest time?"
    assert voice._draft_interpretation["intent"]["metrics"] == ["last_lap"]
    assert any(event["event"] == "radio_transcribed" for event in events)


def test_exact_local_controls_skip_the_interpreter():
    async def transcribe(wav, request_id, cancelled):
        return {"status": "available", "transcript": "Stop speaking."}

    async def interpret(*args):
        raise AssertionError("local controls must not wait on the network")

    voice, hardware, events, clock = listening(transcribe=transcribe)
    voice._interpret = interpret
    from tests.test_radio_listening import run_capture
    run_capture(voice)
    voice.tick(scope="session")
    assert voice._draft_interpretation == {"status": "skipped", "reason": "local_control", "intent": None}


def submission(interpretation, question="What was my latest time?"):
    return {"question": question, "capture_id": REQUEST_ID, "source": "wheel", "interpretation": interpretation}


def test_dispatch_uses_the_interpreted_request(monkeypatch):
    controller, radio, events, value = live()
    sent = []
    monkeypatch.setattr(command, "dispatch_voice_request", lambda question, *a, **k: sent.append(question))
    monkeypatch.setattr(command, "emit", lambda event: sent.append(event["event"]))
    command.dispatch_submission(submission({"status": "available", "intent": intent(
        action="timing_question", targets=["self"], metrics=["last_lap"])}), controller, None, now=10)
    assert sent == ["radio_intent_dispatch", "my last lap time"]


def test_dispatch_speaks_replies_and_falls_back_to_raw_words(monkeypatch):
    controller, radio, events, value = live()
    sent = []
    monkeypatch.setattr(command, "dispatch_voice_request", lambda question, *a, **k: sent.append(question))
    monkeypatch.setattr(command, "emit", lambda event: sent.append(event["event"]))
    command.dispatch_submission(submission({"status": "available", "intent": intent(
        action="unsupported", unsupported_topic="strategy")}), controller, None, now=10)
    assert sent == ["radio_intent_dispatch"] and "strategy" in radio.pending[-1].text
    sent.clear()
    command.dispatch_submission(submission({"status": "unavailable", "reason": "intent_timeout", "intent": None}),
                                controller, None, now=10)
    assert sent == ["radio_intent_fallback", "What was my latest time?"]
    sent.clear()
    command.dispatch_submission(submission(None), controller, None, now=10)
    assert sent == ["What was my latest time?"]


def test_session_facts_answer_without_a_driver_label():
    controller, radio, events, value = live()
    value["context"] = {"status": "available", "reason": None, "value": {
        "session_uid": 99, "packet_format": 2025, "track_name": "Bahrain", "weather_name": "Clear",
        "air_temperature_c": 28, "track_temperature_c": 35}}
    controller.feed(value, now=10)
    controller.answer_question("my track name and weather", now=10)
    assert radio.pending[-1].text == "track Bahrain, weather clear, air 28 degrees, track 35 degrees."


def test_speech_hint_lists_the_session_vocabulary_in_plain_ascii_within_the_limit():
    from f1_engineer.radio.glossary import speech_hint
    from f1_engineer.analysis.local_speech import valid_speech_hint
    ctx = context(drivers=[{"name": "Nico Hülkenberg", "race_number": 27, "position": 1, "role": "leader"},
                           {"name": LECLERC, "race_number": 16, "position": 2, "role": "ahead"}])
    hint = speech_hint(ctx)
    assert "Nico Hulkenberg, Charles Leclerc" in hint and "Corners: Turn 5." in hint and valid_speech_hint(hint)
    crowded = context(drivers=[{"name": f"Driver Number {chr(65 + n)}{chr(65 + n)}{chr(65 + n)}", "race_number": n,
                                "position": n + 1, "role": "other"} for n in range(24)])
    assert valid_speech_hint(speech_hint(crowded)) and len(speech_hint(crowded).encode()) <= 300
    assert speech_hint(None) is None


def test_voice_input_sends_the_hint_to_the_transcriber_only_when_enabled():
    from tests.test_radio_listening import run_capture
    received = []

    async def transcribe(wav, request_id, cancelled, *hint):
        received.append(hint)
        return {"status": "available", "transcript": "Hello"}

    for enabled, expected in ((True, 1), (False, 0)):
        voice, hardware, events, clock = listening(transcribe=transcribe)
        voice._speech_hints, voice.intent_context = enabled, context()
        run_capture(voice)
        assert len(received[-1]) == expected


def test_transcription_endpoint_forwards_a_valid_hint_and_rejects_an_unsafe_one(tmp_path, monkeypatch):
    import hashlib
    from tests.test_api import _canonical_speech_wav
    app = create_app(tmp_path / "a.sqlite3", recordings_root=tmp_path / "r", control_token=TOKEN,
                     automatic_acquisition=False, engineer_runtime=StubRuntime())
    audio = _canonical_speech_wav()
    seen = []

    async def transcribe(wav, *, gate_lease=None, hint=None):
        seen.append(hint)
        raise EngineerRuntimeUnavailable("stop_here")

    def headers(hint):
        return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "audio/wav", "X-Speech-Hint": hint,
                "X-Request-ID": "12345678-1234-4abc-8abc-123456789abc",
                "X-Audio-SHA256": hashlib.sha256(audio).hexdigest()}

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.local_speech_runtime, "transcribe", transcribe)
        client.post("/api/v1/engineer/transcribe", headers=headers("Formula 1 race radio. Drivers: Max."), content=audio)
        bad = client.post("/api/v1/engineer/transcribe", headers=headers("-m evil"), content=audio)
    assert seen == ["Formula 1 race radio. Drivers: Max."]
    assert bad.status_code == 422 and bad.json()["reason"] == "engineer_speech_hint_invalid"


def test_typed_say_requests_keep_case_and_other_commands_are_lowercased():
    assert command._command_text("say What's my GAP to Leclerc?\n") == "say What's my GAP to Leclerc?"
    assert command._command_text("/ASK Last lap\n") == "ask Last lap"
    assert command._command_text("STATUS\n") == "status"


def test_command_file_is_tailed_without_holding_it_open(tmp_path):
    import queue
    import threading
    import time
    path = tmp_path / "commands.txt"
    path.write_text("say ignored because it existed before start\n")
    inbox, stop = queue.Queue(), threading.Event()
    worker = threading.Thread(target=command._tail_commands, args=(inbox, path, stop), daemon=True)
    worker.start()
    time.sleep(0.3)
    with path.open("a") as stream:
        stream.write("say What is my position?\nstatus\n")
    path.rename(tmp_path / "renamed.txt")  # would fail on Windows if the reader held the file open
    (tmp_path / "renamed.txt").rename(path)
    deadline = time.monotonic() + 3
    seen = []
    while time.monotonic() < deadline and len(seen) < 2:
        try:
            seen.append(inbox.get(timeout=0.2))
        except queue.Empty:
            pass
    stop.set()
    assert seen == ["say What is my position?", "status"]


def test_typed_request_uses_the_interpreter_with_live_contexts(monkeypatch):
    import queue
    controller, radio, events, value = live()
    emitted = []
    monkeypatch.setattr(command, "emit", emitted.append)

    class Client:
        async def interpret(self, text, ctx, request_id, cancelled):
            assert text == "What was my latest time?" and ctx["glossary"] == {"terms": {}, "mishearings": {}}
            return {"status": "available", "intent": intent(action="timing_question", targets=["self"],
                                                              metrics=["last_lap"])}

    results = queue.Queue()
    command.start_typed_request("What was my latest time?", Client(), controller,
                                {"terms": {}, "mishearings": {}}, results, interpret=True)
    submission, voice_like = results.get(timeout=5)
    assert submission["interpretation"]["intent"]["metrics"] == ["last_lap"]
    assert voice_like.task_context is not None and voice_like.question_context is None  # nothing asked yet
    command.dispatch_submission(submission, controller, voice_like, now=10)
    assert radio.pending[-1].text.startswith("You:")
    kinds = [event["event"] for event in emitted]
    assert kinds[:2] == ["radio_typed_request", "radio_transcript"] and "radio_intent_dispatch" in kinds
