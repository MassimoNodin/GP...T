import io
import json
import queue
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from f1_engineer import cli
from f1_engineer.radio import client as client_module, command, windows_speech
from f1_engineer.radio.client import RadioClient, read_control_token
from f1_engineer.radio.controller import RadioController
from f1_engineer.radio.scheduler import RadioMessage, RadioScheduler


class Playback:
    def __init__(self):
        self.playing = False
        self.calls = []

    def start(self, text):
        assert not self.playing
        self.playing = True
        self.calls.append(("start", text))

    def busy(self):
        return self.playing

    def cancel(self):
        self.playing = False
        self.calls.append(("cancel", None))

    def close(self):
        self.calls.append(("close", None))


def message(key="lap:1", *, kind="proactive", expires=20, scope="race"):
    return RadioMessage(key, scope, "Lap 1: 43.500 seconds.", kind, expires)


def scheduler(*, limit=16):
    playback = Playback()
    events = []
    radio = RadioScheduler(playback, queue_limit=limit, notify=events.append)
    radio.set_scope("race")
    return radio, playback, events


def snapshot(*, cursor=0, events=None, age=0.1, floor=0):
    identity = {"car_index": 0, "tenure_ordinal": 1, "participant_identity_fingerprint": "a" * 64}
    return {"schema_version": 1, "status": "available", "generation": "1" * 32,
            "session_id": "2" * 32, "scope": {"session_uid": 99, "packet_format": 2025, "epoch": 2},
            "player_car_index": 0, "received_monotonic_ns": 90_000_000_000_000,
            "age_s": age, "event_cursor": cursor, "event_floor": floor,
            "drivers": [{"car_index": 0, "identity": identity,
                         "lap": {"current_lap_number": 3, "last_lap_time_ms": 103_500}}],
            "events": events or []}


def lap_event(identifier, *, age_s=0, invalid=0):
    value = snapshot()
    return {"id": identifier, "scope": value["scope"], "driver": value["drivers"][0]["identity"],
            "kind": "lap_completed", "qualification": "observed_lap_transition",
            "lap_number": 2, "lap_time_ms": 103_500, "previous_observed_invalid": invalid,
            "received_monotonic_ns": value["received_monotonic_ns"] - int(age_s * 1e9)}


def controller():
    radio, playback, events = scheduler()
    return RadioController(radio, notify=events.append), radio, playback, events


def test_scheduler_deduplicates_and_never_overlaps_playback():
    radio, playback, events = scheduler()
    assert radio.submit(message(), now=0)
    assert not radio.submit(message(), now=0)
    radio.tick(now=0)
    radio.submit(message("lap:2"), now=1)
    radio.tick(now=1)
    assert len(playback.calls) == 1
    playback.playing = False
    radio.tick(now=2)
    assert len(playback.calls) == 2
    assert any(event.get("reason") == "duplicate" for event in events)


def test_answer_interrupts_proactive_speech_without_requeueing_it():
    radio, playback, events = scheduler()
    radio.submit(message(), now=0)
    radio.tick(now=0)
    radio.submit(message("answer:1", kind="answer"), now=1)
    radio.tick(now=1)
    assert [item[0] for item in playback.calls] == ["start", "cancel", "start"]
    assert radio.active.kind == "answer"
    assert not radio.pending


def test_quiet_cancels_old_output_but_explicit_answers_still_work():
    radio, playback, events = scheduler()
    radio.submit(message(), now=0)
    radio.tick(now=0)
    radio.quiet()
    assert not playback.playing
    assert not radio.submit(message("lap:2"), now=1)
    assert radio.submit(message("answer:1", kind="answer"), now=1)
    radio.tick(now=1)
    assert radio.active.kind == "answer"


def test_scope_change_clears_active_pending_repeat_and_deduplication():
    radio, playback, events = scheduler()
    radio.submit(message(), now=0)
    radio.tick(now=0)
    radio.submit(message("lap:2"), now=1)
    radio.set_scope("new-race")
    assert not playback.playing and not radio.pending
    assert not radio.repeat(now=1)
    assert not radio.submit(message(), now=1)
    assert radio.submit(message(scope="new-race"), now=1)


def test_queue_capacity_prefers_answers_and_latest_routine_report():
    radio, playback, events = scheduler(limit=1)
    radio.submit(message(), now=0)
    radio.submit(message("lap:2"), now=0)
    assert radio.pending[0].key == "lap:2"
    radio.submit(message("answer:1", kind="answer"), now=0)
    assert not radio.submit(message("lap:3"), now=0)
    assert radio.pending[0].key == "answer:1"


def test_expired_pending_and_active_speech_are_discarded():
    radio, playback, events = scheduler()
    assert not radio.submit(message(expires=0), now=0)
    radio.submit(message(expires=1), now=0)
    radio.tick(now=2)
    assert not playback.calls
    radio.submit(message("lap:2", expires=3), now=2)
    radio.tick(now=2)
    radio.tick(now=3)
    assert not playback.playing
    assert radio.active is None


def test_repeat_uses_same_scope_and_new_expiry_then_shutdown_cleans_up():
    radio, playback, events = scheduler()
    radio.submit(message(), now=0)
    radio.tick(now=0)
    assert radio.repeat(now=1)
    radio.tick(now=1)
    assert radio.active.kind == "answer"
    assert radio.active.expires_at == 16
    radio.close()
    assert playback.calls[-1][0] == "close"


def test_dispatch_reports_queue_wait_and_relative_observation_age():
    radio, playback, events = scheduler()
    radio.submit(replace(message(), observation_age_s=1.5), now=0)
    radio.tick(now=2)
    assert events[-1]["queue_wait_s"] == 2
    assert events[-1]["observation_to_dispatch_s"] == 3.5


def test_long_operator_line_is_discarded_without_executing_its_tail():
    commands = queue.Queue()
    command._commands(commands, io.StringIO("x" * 1025 + "quit\nstatus\n"))
    assert [commands.get_nowait() for _ in range(commands.qsize())] == ["invalid", "status", "quit"]


@pytest.mark.parametrize("invalid", [
    replace(message(), text=""), replace(message(), text="a" * 1025),
    replace(message(), kind="bad"), replace(message(), expires_at=float("nan")),
])
def test_invalid_radio_messages_are_rejected(invalid):
    radio, playback, events = scheduler()
    with pytest.raises(ValueError):
        radio.submit(invalid, now=0)


def test_controller_seeds_cursor_without_announcing_history_then_reports_new_lap_once():
    engine, radio, playback, events = controller()
    engine.feed(snapshot(cursor=5, events=[lap_event(5)]), now=1000)
    assert not radio.pending
    engine.feed(snapshot(cursor=6, events=[lap_event(5), lap_event(6)]), now=1001)
    engine.feed(snapshot(cursor=6, events=[lap_event(6)]), now=1001)
    assert len(radio.pending) == 1
    radio.tick(now=1001)
    assert playback.calls[0][1] == "Lap 2: 1 minute 43.500 seconds."


def test_controller_uses_server_relative_age_not_windows_vs_ubuntu_monotonic_clocks():
    engine, radio, playback, events = controller()
    engine.feed(snapshot(), now=1000)
    engine.feed(snapshot(cursor=1, events=[lap_event(1, age_s=1)]), now=1001, transport_age_s=0.2)
    assert radio.pending[0].expires_at == pytest.approx(1009.7)


@pytest.mark.parametrize("field,value", [
    ("age_s", -1), ("age_s", float("nan")), ("age_s", 10**1000),
    ("player_car_index", False), ("schema_version", True), ("event_floor", 1),
    ("session_id", "bad"), ("events", [{}] * 129),
], ids=["negative-age", "nan-age", "huge-age", "boolean-player", "boolean-version",
        "invalid-floor", "invalid-session", "too-many-events"])
def test_invalid_or_unbounded_snapshots_cannot_create_speech(field, value):
    engine, radio, playback, events = controller()
    data = snapshot()
    data[field] = value
    engine.feed(data, now=0)
    assert engine.status()["backend"] == "unavailable"
    assert not radio.pending


def test_old_future_missing_or_nonplayer_lap_events_are_not_spoken():
    engine, radio, playback, events = controller()
    engine.feed(snapshot(), now=0)
    missing = lap_event(2)
    missing["lap_time_ms"] = None
    other = lap_event(3)
    other["driver"] = {**other["driver"], "car_index": 1}
    engine.feed(snapshot(cursor=4, events=[lap_event(1, age_s=20), missing, other,
                                          lap_event(4, age_s=-1)]), now=1)
    assert not radio.pending


def test_observed_invalidity_is_qualified_not_silently_presented_as_valid():
    engine, radio, playback, events = controller()
    engine.feed(snapshot(), now=0)
    engine.feed(snapshot(cursor=1, events=[lap_event(1, invalid=1)]), now=1)
    assert radio.pending[0].text.endswith("Observed invalid.")


def test_history_loss_resynchronises_and_does_not_replay_a_backlog():
    engine, radio, playback, events = controller()
    engine.feed(snapshot(), now=0)
    engine.feed(snapshot(cursor=3, floor=2, events=[lap_event(3)]), now=1)
    assert not radio.pending
    assert any(event.get("event") == "radio_resynchronised" for event in events)
    engine.feed(snapshot(cursor=4, floor=2, events=[lap_event(4)]), now=2)
    assert len(radio.pending) == 1


def test_disconnect_or_local_age_expiry_stops_old_speech_and_reconnect_seeds_history():
    engine, radio, playback, events = controller()
    engine.feed(snapshot(), now=0)
    engine.answer_last_lap(now=0)
    radio.tick(now=0)
    engine.check_freshness(now=3)
    assert not playback.playing
    engine.feed(snapshot(cursor=1, events=[lap_event(1)]), now=4)
    assert not radio.pending
    engine.feed({"status": "unavailable", "reason": "runtime_stale"}, now=5)
    assert radio.scope is None


def test_explicit_last_lap_answer_and_missing_data_response():
    engine, radio, playback, events = controller()
    engine.feed(snapshot(), now=0)
    engine.answer_last_lap(now=0)
    assert radio.pending[0].text == "Lap 2: 1 minute 43.500 seconds."
    engine.feed({"status": "unavailable", "reason": "runtime_stale"}, now=1)
    engine.answer_last_lap(now=1)
    assert radio.pending[0].text == "No fresh player telemetry is available."


def test_changed_player_identity_cancels_old_output_and_seeds_new_baseline():
    engine, radio, playback, events = controller()
    engine.feed(snapshot(), now=0)
    engine.answer_last_lap(now=0)
    radio.tick(now=0)
    changed = snapshot(cursor=1, events=[lap_event(1)])
    changed["drivers"][0]["identity"]["participant_identity_fingerprint"] = "b" * 64
    engine.feed(changed, now=1)
    assert not playback.playing and not radio.pending


def test_round_trip_age_is_included_in_freshness():
    engine, radio, playback, events = controller()
    engine.feed(snapshot(age=2), now=0, transport_age_s=1.1)
    assert engine.status()["reason"] == "lap_observations_stale"


def token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("a" * 32, encoding="utf-8")
    return path


@pytest.mark.parametrize("url", [
    "https://127.0.0.1", "http://example.com", "http://user:secret@localhost",
    "http://localhost/path", "http://localhost?token=secret", "http://localhost/#secret",
])
def test_radio_transport_refuses_remote_or_credential_bearing_urls(tmp_path, url):
    with pytest.raises(ValueError):
        RadioClient(url, token_file(tmp_path))


def test_radio_transport_authenticates_only_fixed_loopback_route_and_never_follows_redirects(tmp_path):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://example.com"})
    client = RadioClient("http://127.0.0.1:8765", token_file(tmp_path), transport=httpx.MockTransport(respond))
    try:
        assert client.fetch()["status"] == "unavailable"
        assert len(requests) == 1
        assert requests[0].url.path == "/api/v2/engineer/live"
        assert requests[0].headers["Authorization"] == "Bearer " + "a" * 32
    finally:
        client.close()


@pytest.mark.parametrize("code,body", [(200, b"bad json"), (403, b"secret"),
                                      (200, b"x" * 1_048_577)],
                         ids=["invalid-json", "unauthorized", "oversized"])
def test_backend_errors_or_oversized_responses_do_not_leak_body(tmp_path, code, body):
    client = RadioClient("http://localhost", token_file(tmp_path),
                         transport=httpx.MockTransport(lambda request: httpx.Response(code, content=body)))
    try:
        result = client.fetch()
        assert result["status"] == "unavailable"
        assert "secret" not in json.dumps(result)
    finally:
        client.close()


def test_transport_returns_valid_snapshot(tmp_path):
    client = RadioClient("http://localhost", token_file(tmp_path),
                         transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": snapshot()})))
    try:
        assert client.fetch() == snapshot()
    finally:
        client.close()


def test_slow_backend_body_is_abandoned_without_returning_stale_data(tmp_path, monkeypatch):
    clock = iter((0.0, 6.0))
    monkeypatch.setattr(client_module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    client = RadioClient("http://localhost", token_file(tmp_path),
                         transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": snapshot()})))
    try:
        assert client.fetch()["status"] == "unavailable"
    finally:
        client.close()


@pytest.mark.parametrize("raw", [b"short", b"x" * 1025, b"bad\xfftoken", b"a" * 32 + b"\nInjected"],
                         ids=["short", "oversized", "invalid-utf8", "injected"])
def test_invalid_tokens_are_rejected_without_logging_credentials(tmp_path, raw):
    path = token_file(tmp_path)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="radio_token_file_invalid"):
        read_control_token(path)


class Client:
    def __init__(self, *args):
        self.closed = False

    def fetch(self):
        return snapshot()

    def close(self):
        self.closed = True


def test_cli_once_checks_backend_without_initialising_audio(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(command, "RadioClient", Client)
    monkeypatch.setattr(command, "WindowsSpeech", lambda **kwargs: pytest.fail("audio must not initialise"))
    args = cli.build_parser().parse_args(["radio", "--once"])
    assert cli._radio(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "available"


@pytest.mark.parametrize("arguments", [["--poll-interval", "nan"], ["--poll-interval", "0"],
                                      ["--rate", "11"], ["--volume", "101"]])
def test_cli_rejects_invalid_settings_before_network_or_audio(arguments, monkeypatch):
    monkeypatch.setattr(command, "RadioClient", lambda *args: pytest.fail("network must not initialise"))
    args = cli.build_parser().parse_args(["radio", *arguments])
    with pytest.raises(ValueError, match="invalid radio settings"):
        cli._radio(args)


def test_cli_dry_run_answers_and_handles_controls_without_a_browser(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(command, "RadioClient", Client)
    monkeypatch.setattr(command.sys, "stdin", io.StringIO("lap\nstatus\nquiet\nresume\nrepeat\ncancel\nquit\n"))
    monkeypatch.setattr(command, "WindowsSpeech", lambda **kwargs: pytest.fail("audio must not initialise"))
    args = cli.build_parser().parse_args(["radio", "--dry-run", "--input-settings", str(tmp_path / "unbound.json")])
    assert cli._radio(args) == 0
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    spoken = [event for event in output if event["event"] == "radio_started"]
    assert spoken[0]["text"] == "Lap 2: 1 minute 43.500 seconds."
    assert len(spoken) == 2


def test_windows_helper_uses_hidden_encoded_code_and_json_text_not_shell_interpolation(monkeypatch):
    replies = [{"ok": True, "voices": ["Local Voice"]}, {"ok": True, "voice": "Local Voice"},
               {"ok": True}, {"ok": True, "busy": True}, {"ok": True}]
    process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(
        "\n".join(json.dumps(reply) for reply in replies) + "\n"),
        wait=lambda timeout: 0, kill=lambda: None)
    spawned = {}
    def launch(arguments, **kwargs):
        spawned.update(arguments=arguments, **kwargs)
        return process
    monkeypatch.setattr(windows_speech.sys, "platform", "win32")
    monkeypatch.setattr(windows_speech.subprocess, "CREATE_NO_WINDOW", 123, raising=False)
    monkeypatch.setattr(windows_speech.subprocess, "Popen", launch)
    adapter = windows_speech.WindowsSpeech(voice="Local Voice")
    try:
        text = "Do not execute '; Get-Process; ' as code."
        adapter.start(text)
        assert adapter.busy()
        adapter.cancel()
        assert json.loads(process.stdin.getvalue().splitlines()[1])["text"] == text
        assert "-EncodedCommand" in spawned["arguments"]
        assert "Hidden" in spawned["arguments"]
        assert spawned["creationflags"] == 123
        assert text not in " ".join(spawned["arguments"])
    finally:
        adapter.close()


def test_windows_adapter_rejects_other_platforms_before_spawning(monkeypatch):
    monkeypatch.setattr(windows_speech.sys, "platform", "linux")
    with pytest.raises(windows_speech.WindowsSpeechUnavailable, match="requires_windows"):
        windows_speech.WindowsSpeech()


@pytest.mark.parametrize("reply", [{"ok": True}, {"ok": False}], ids=["invalid-status", "failed-status"])
def test_windows_adapter_fails_closed_on_unknown_playback_state(monkeypatch, reply):
    replies = [{"ok": True, "voices": ["Local Voice"]}, {"ok": True, "voice": "Local Voice"}, reply]
    process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(
        "\n".join(json.dumps(value) for value in replies) + "\n"),
        wait=lambda timeout: 0, kill=lambda: None)
    monkeypatch.setattr(windows_speech.sys, "platform", "win32")
    monkeypatch.setattr(windows_speech.subprocess, "CREATE_NO_WINDOW", 123, raising=False)
    monkeypatch.setattr(windows_speech.subprocess, "Popen", lambda *args, **kwargs: process)
    adapter = windows_speech.WindowsSpeech()
    with pytest.raises(windows_speech.WindowsSpeechUnavailable):
        adapter.busy()
    assert process.stdin.closed and process.stdout.closed
