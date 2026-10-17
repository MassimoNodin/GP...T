from __future__ import annotations

import hashlib
import json
import sqlite3
import struct

import pytest

from f1_engineer.cli import build_parser
from f1_engineer.recording.capture import CaptureReader
from f1_engineer.recording.journal_export import export_journal_session, list_journal_sessions
from f1_engineer.recording.udp_replay import replay_udp_capture

HEADER = struct.Struct("<HBBBBBQfIIBB")
GENERATION = "a" * 32


def packet(uid: int, frame: int, body: bytes = b"telemetry") -> bytes:
    return HEADER.pack(2025, 25, 1, 0, 1, 2, uid, frame / 60, frame, frame, 0, 255) + body


def database(tmp_path, rows, *, corrupt=None):
    path = tmp_path / "evidence.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE journal (generation, sequence, kind, payload, metadata)")
    connection.execute("CREATE TABLE sessions (id, generation, uid, lifecycle, acquisition, context, occurrence)")
    connection.execute("INSERT INTO sessions VALUES ('s', ?, '42', NULL, NULL, ?, NULL)",
                       (GENERATION, json.dumps({"track_name": "Miami", "session_type": "race", "total_laps": 5})))
    for sequence, (uid, seconds) in enumerate(rows, start=1):
        payload = packet(uid, sequence)
        digest = hashlib.sha256(b"tampered" if sequence == corrupt else payload).hexdigest()
        metadata = {"captured_at_ns": 1_700_000_000_000_000_000 + int(seconds * 1e9),
                    "monotonic_ns": 5_000_000_000 + int(seconds * 1e9), "sequence": sequence,
                    "source_host": "192.168.1.111", "source_port": 50367, "payload_sha256": digest}
        connection.execute("INSERT INTO journal VALUES (?,?,?,?,?)",
                           (GENERATION, sequence, "datagram", payload, json.dumps(metadata)))
    connection.execute("INSERT INTO journal VALUES (?,?,?,?,?)",
                       (GENERATION, len(rows) + 1, "gap", b"", json.dumps({"reason": "telemetry_silence"})))
    connection.commit()
    connection.close()
    return path


ROWS = [(42, 0.0), (42, 1.0), (0, 1.1), (42, 1.5), (42, 301.5), (42, 302.0), (7, 303.0), (7, 304.0), (7, 309.0)]


def test_lists_sessions_longest_first_with_context(tmp_path):
    sessions = list_journal_sessions(database(tmp_path, ROWS))
    assert [session["session_uid"] for session in sessions] == ["42", "7"]
    longest = sessions[0]
    assert longest["datagrams"] == 5 and longest["duration_s"] == 302.0
    assert (longest["track"], longest["session_type"], longest["total_laps"]) == ("Miami", "race", 5)


def test_export_preserves_bytes_order_and_timing_and_replays(tmp_path):
    path = database(tmp_path, ROWS)
    output = tmp_path / "miami.f1ecap"
    result = export_journal_session(path, 42, output)
    assert result["datagrams"] == 5 and result["replay_duration_s"] == 302.0 and result["gaps_shortened"] == 0
    with CaptureReader(output) as reader:
        packets = list(reader)
        assert reader.complete and reader.metadata["session_uid"] == "42"
    assert [raw.payload for raw in packets] == [packet(42, n) for n in (1, 2, 4, 5, 6)]
    assert [raw.sequence for raw in packets] == [1, 2, 3, 4, 5]
    assert packets[-1].monotonic_ns - packets[0].monotonic_ns == 302_000_000_000
    assert packets[0].source_host == "192.168.1.111" and packets[0].source_port == 50367
    import asyncio
    summary = asyncio.run(replay_udp_capture(output, "127.0.0.1", 20999, dry_run=True))
    assert summary["validated_packets"] == 5


def test_idle_gaps_can_be_shortened_without_touching_packets(tmp_path):
    output = tmp_path / "short.f1ecap"
    result = export_journal_session(database(tmp_path, ROWS), "42", output, max_gap_s=2)
    assert result["gaps_shortened"] == 1 and result["removed_idle_s"] == 298.0
    assert result["replay_duration_s"] == 4.0
    with CaptureReader(output) as reader:
        packets = list(reader)
    assert packets[-1].monotonic_ns - packets[0].monotonic_ns == 4_000_000_000
    assert [raw.payload for raw in packets] == [packet(42, n) for n in (1, 2, 4, 5, 6)]


def test_checksum_mismatch_stops_export_and_marks_capture_incomplete(tmp_path):
    output = tmp_path / "bad.f1ecap"
    with pytest.raises(ValueError, match="checksum mismatch at sequence 4"):
        export_journal_session(database(tmp_path, ROWS, corrupt=4), 42, output)
    with CaptureReader(output) as reader:
        list(reader)
        assert not reader.complete


@pytest.mark.parametrize("uid,error", [(99, LookupError), (0, ValueError)])
def test_unknown_or_invalid_session_uid(tmp_path, uid, error):
    with pytest.raises(error):
        export_journal_session(database(tmp_path, ROWS), uid, tmp_path / "x.f1ecap")


def test_refuses_to_overwrite_and_opens_database_read_only(tmp_path):
    path = database(tmp_path, ROWS)
    output = tmp_path / "miami.f1ecap"
    export_journal_session(path, 42, output)
    with pytest.raises(FileExistsError):
        export_journal_session(path, 42, output)
    before = path.read_bytes()
    export_journal_session(path, 42, output, overwrite=True)
    assert path.read_bytes() == before


def test_cli_lists_and_exports(tmp_path, capsys):
    path = database(tmp_path, ROWS)
    parser = build_parser()
    args = parser.parse_args(["export-journal", "--database", str(path), "--list"])
    assert args.handler(args) == 0
    listed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert listed[0]["session_uid"] == "42"
    args = parser.parse_args(["export-journal", "--database", str(path), "--session-uid", "42",
                              "--output", str(tmp_path / "cli.f1ecap"), "--max-gap", "5"])
    assert args.handler(args) == 0
    assert json.loads(capsys.readouterr().out)["gaps_shortened"] == 1
