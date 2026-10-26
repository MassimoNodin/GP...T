import io
import asyncio
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from f1_engineer import cli
from f1_engineer.radio import command, diagnostics
from f1_engineer.radio.client import RadioClient
from tests.test_standalone_radio import Client, snapshot, token_file
from tests.test_radio_listening import REQUEST_ID, canonical_wav, response_data


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_correlated_clocks_and_default_output(tmp_path, capsys):
    diagnostics.emit({"event": "before"})
    assert json.loads(capsys.readouterr().out) == {"event": "before"}
    path = tmp_path / "events.jsonl"
    with diagnostics.radio_event_log(path):
        diagnostics.emit({"event": "spoken", "text": "your lap"})
        diagnostics.trace_event({"event": "private_poll"})
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    values = records(path)
    assert len(output) == 3 and len(values) == 4
    assert len({value["trace"]["run_id"] for value in values}) == 1
    assert [value["trace"]["sequence"] for value in values] == [1, 2, 3, 4]
    assert all(value["trace"]["utc"].endswith("+00:00") for value in values)
    assert values[-1]["trace"]["monotonic_ns"] >= values[0]["trace"]["monotonic_ns"]
    assert not diagnostics.trace_enabled()


def test_thread_serialization_and_hook_restore(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    hooks = []
    original = lambda args: hooks.append(args)
    monkeypatch.setattr(threading, "excepthook", original)
    with diagnostics.radio_event_log(path):
        workers = [threading.Thread(target=lambda: [diagnostics.trace_event({"event": "sample"})
                                                   for index in range(20)]) for worker in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        try:
            raise RuntimeError("sensitive exception message")
        except RuntimeError as exception:
            threading.excepthook(SimpleNamespace(thread=threading.current_thread(), exc_value=exception))
    values = records(path)
    assert len(values) == 83
    assert [value["trace"]["sequence"] for value in values] == list(range(1, 84))
    assert hooks and threading.excepthook is original
    assert "sensitive exception message" not in path.read_text()
    assert next(value for value in values if value["event"] == "radio_thread_failure")["exception_chain"][0]["frames"]


def test_process_error_chain_without_messages_or_locals(tmp_path):
    path = tmp_path / "events.jsonl"
    with pytest.raises(ValueError):
        with diagnostics.radio_event_log(path):
            try:
                raise RuntimeError("Bearer SECRET_TOKEN")
            except RuntimeError as exception:
                raise ValueError("private request body") from exception
    failure = next(value for value in records(path) if value["event"] == "radio_process_failure")
    assert [item["type"] for item in failure["exception_chain"]] == ["ValueError", "RuntimeError"]
    assert all(item["frames"] for item in failure["exception_chain"])
    assert "SECRET_TOKEN" not in path.read_text() and "private request body" not in path.read_text()
    assert records(path)[-1]["event"] == "radio_log_closed"


def test_bounded_rotation_retains_valid_json(tmp_path):
    path = tmp_path / "events.jsonl"
    log = diagnostics.RadioEventLog(path, max_bytes=900, backups=2)
    try:
        for index in range(30):
            log.write({"event": "sample", "index": index})
    finally:
        log.close()
    files = list(tmp_path.glob("events.jsonl*"))
    assert len(files) == 3 and all(records(file) for file in files)
    assert records(path)[-1]["index"] == 29


@pytest.mark.parametrize("name", ["credentials", "events.txt"])
def test_invalid_suffix_does_not_create_file(tmp_path, name):
    with pytest.raises(ValueError):
        diagnostics.RadioEventLog(tmp_path / name)
    assert not (tmp_path / name).exists()


def test_redirected_path_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(diagnostics, "_redirected", lambda parent: parent == tmp_path)
    with pytest.raises(ValueError):
        diagnostics.RadioEventLog(path)
    assert not path.exists()


def test_http_logs_correlated_status_not_credentials_or_body(tmp_path):
    path = tmp_path / "events.jsonl"
    token = token_file(tmp_path)
    client = RadioClient("http://localhost", token, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": snapshot()})))
    with diagnostics.radio_event_log(path):
        assert client.fetch()["status"] == "available"
    client.close()
    values = records(path)
    request = next(value for value in values if value["event"] == "radio_http_request")
    response = next(value for value in values if value["event"] == "radio_http_response")
    assert request["http_id"] == response["http_id"]
    assert response["status_code"] == 200 and response["headers_elapsed_s"] >= 0
    assert token.read_text().strip() not in path.read_text()
    assert "Authorization" not in path.read_text() and "drivers" not in path.read_text()


def test_http_failure_sanitized_frames(tmp_path):
    path = tmp_path / "events.jsonl"
    def fail(request):
        raise httpx.ConnectError("sensitive endpoint message", request=request)
    client = RadioClient("http://localhost", token_file(tmp_path), transport=httpx.MockTransport(fail))
    with diagnostics.radio_event_log(path):
        assert client.fetch()["status"] == "unavailable"
    client.close()
    failure = next(value for value in records(path) if value["event"] == "radio_http_failure")
    assert failure["exception_chain"][0]["type"] == "ConnectError"
    assert failure["exception_chain"][0]["frames"]
    assert "sensitive endpoint message" not in path.read_text()


def test_cli_log_without_browser_or_audio(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(command, "RadioClient", Client)
    monkeypatch.setattr(command.sys, "stdin", io.StringIO("status\nquit\n"))
    monkeypatch.setattr(command, "WindowsSpeech", lambda **kwargs: pytest.fail("unexpected audio"))
    args = cli.build_parser().parse_args(["radio", "--dry-run", "--log-file", str(path),
                                         "--input-settings", str(tmp_path / "unbound.json")])
    assert command.run_radio(args) == 0
    values = records(path)
    assert any(value["event"] == "radio_ready" for value in values)
    assert values[-1]["event"] == "radio_log_closed"


def test_transcription_trace_correlates_capture_without_audio_or_transcript(tmp_path):
    path = tmp_path / "events.jsonl"
    wav = canonical_wav(b"\x00\x00" * 320)
    client = RadioClient("http://localhost", token_file(tmp_path), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response_data(wav),
                                       headers={"X-F1-Backend-Request-ID": request.headers["X-F1-Radio-Request-ID"]})))
    with diagnostics.radio_event_log(path):
        assert asyncio.run(client.transcribe(wav, REQUEST_ID, threading.Event()))["status"] == "available"
    client.close()
    values = records(path)
    request = next(value for value in values if value["event"] == "radio_http_request")
    response = next(value for value in values if value["event"] == "radio_http_response")
    assert request["capture_id"] == REQUEST_ID
    assert request["http_id"] == response["http_id"] == response["backend_request_id"]
    assert "How was my lap?" not in path.read_text()
    assert "audio_sha256" not in path.read_text() and "Authorization" not in path.read_text()
