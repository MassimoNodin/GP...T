from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from f1_engineer.cli import _inspect
from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.recording.capture import CaptureWriter
from f1_engineer.sessions.context import GameMode, RuleSet, SessionType
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.models import RawDatagram
from f1_engineer.udp.session_context import SessionContextDecoder
from tests.helpers import make_datagram


FIXTURE = Path(__file__).parent / "fixtures" / "f1_25_session_packet_v1.bin"
RECORDED_SESSION_UID = 14237356543050158953


def _recorded_packet() -> RawDatagram:
    return RawDatagram(
        sequence=0,
        captured_at_ns=1,
        monotonic_ns=1,
        source_host="127.0.0.1",
        source_port=20777,
        payload=FIXTURE.read_bytes(),
    )


def _packet_with_body(body: bytes, *, frame: int = 47, version: int = 1) -> RawDatagram:
    return make_datagram(
        packet_format=2025,
        packet_id=1,
        packet_version=version,
        session_uid=RECORDED_SESSION_UID,
        frame=frame,
        body=body,
    )


def test_session_context_decoder_reads_the_real_recorded_packet() -> None:
    packet = PacketDecoder().decode(_recorded_packet())
    result = SessionContextDecoder().decode(packet)

    assert result.error is None
    assert result.context is not None
    assert result.context.session_uid == RECORDED_SESSION_UID
    assert result.context.packet_version == 1
    assert result.context.session_type_id == 18
    assert result.context.session_type is SessionType.TIME_TRIAL
    assert result.context.game_mode_id == 5
    assert result.context.game_mode is GameMode.TIME_TRIAL
    assert result.context.rule_set_id == 2
    assert result.context.rule_set is RuleSet.TIME_TRIAL
    assert result.context.track_id == 0
    assert result.context.track_name == "Melbourne"
    assert result.context.track_length_m == 5276
    assert result.context.total_laps == 1
    assert result.context.weather_name == "clear"


def test_unknown_mode_identifiers_are_preserved_without_guessing() -> None:
    body = bytearray(FIXTURE.read_bytes()[29:])
    body[6] = 250
    body[665] = 251
    body[666] = 252
    packet = PacketDecoder().decode(_packet_with_body(bytes(body)))

    result = SessionContextDecoder().decode(packet)

    assert result.error is None
    assert result.context is not None
    assert result.context.session_type_id == 250
    assert result.context.session_type is None
    assert result.context.game_mode_id == 251
    assert result.context.game_mode is None
    assert result.context.rule_set_id == 252
    assert result.context.rule_set is None


def test_race_session_type_is_independent_of_career_game_mode() -> None:
    body = bytearray(FIXTURE.read_bytes()[29:])
    body[6] = 15
    body[665] = 27
    body[666] = 1
    packet = PacketDecoder().decode(_packet_with_body(bytes(body)))

    result = SessionContextDecoder().decode(packet)

    assert result.context is not None
    assert result.context.session_type is SessionType.RACE
    assert result.context.game_mode.value == "my_team_career_25"
    assert result.context.rule_set is RuleSet.RACE


def test_unsupported_or_malformed_session_packets_have_no_context() -> None:
    body = FIXTURE.read_bytes()[29:]
    decoder = SessionContextDecoder()
    packet_decoder = PacketDecoder()

    malformed = decoder.decode(
        packet_decoder.decode(_packet_with_body(body[:-1]))
    )
    unsupported_version = decoder.decode(
        packet_decoder.decode(_packet_with_body(body, version=2))
    )
    unsupported_format = decoder.decode(
        packet_decoder.decode(
            make_datagram(
                packet_format=2026,
                packet_id=1,
                session_uid=RECORDED_SESSION_UID,
                body=body,
            )
        )
    )

    assert malformed.context is None
    assert "must be 724 bytes" in malformed.error
    assert unsupported_version.context is None
    assert "unsupported session packet adapter" in unsupported_version.error
    assert unsupported_format.context is None
    assert "unsupported session packet adapter" in unsupported_format.error


def test_pipeline_emits_only_new_non_stale_session_context() -> None:
    pipeline = TelemetryPipeline()
    original = _recorded_packet()

    first = pipeline.process(original)
    repeated = pipeline.process(_packet_with_body(original.payload[29:], frame=48))
    stale_body = bytearray(original.payload[29:])
    stale_body[0] = 1
    stale = pipeline.process(_packet_with_body(bytes(stale_body), frame=46))
    newer = pipeline.process(_packet_with_body(bytes(stale_body), frame=49))

    assert first.session_context is not None
    assert first.session_context.session_type is SessionType.TIME_TRIAL
    assert repeated.session_context is None
    assert stale.session_context is None
    assert pipeline.sessions.current_context is not None
    assert pipeline.sessions.current_context.weather_id == 1
    assert newer.session_context is not None
    assert pipeline.sessions.current_context.weather_id == 1


def test_session_context_timeline_returns_context_at_the_observed_frame() -> None:
    pipeline = TelemetryPipeline()
    first = pipeline.process(_recorded_packet())
    changed_body = bytearray(_recorded_packet().payload[29:])
    changed_body[0] = 1
    changed = pipeline.process(_packet_with_body(bytes(changed_body), frame=49))

    old_context, old_frame = pipeline.sessions.context_at(
        47, session_uid=RECORDED_SESSION_UID
    )
    new_context, new_frame = pipeline.sessions.context_at(
        49, session_uid=RECORDED_SESSION_UID
    )

    assert first.session_context is not None
    assert changed.session_context is not None
    assert old_context == first.session_context
    assert old_frame == 47
    assert new_context == changed.session_context
    assert new_context.weather_id == 1
    assert new_frame == 49


def test_first_session_context_can_arrive_before_a_later_session_start_packet() -> None:
    pipeline = TelemetryPipeline()
    pipeline.process(
        make_datagram(
            packet_id=0,
            session_uid=RECORDED_SESSION_UID,
            frame=51,
            sequence=1,
        )
    )
    context_packet = _recorded_packet()
    packet = PacketDecoder().decode(context_packet)
    delayed_context = _packet_with_body(
        packet.body,
        frame=50,
    )
    result = pipeline.process(delayed_context)

    context, context_frame = pipeline.sessions.context_at(
        50, session_uid=RECORDED_SESSION_UID
    )
    context_after_session_start, context_after_frame = pipeline.sessions.context_at(
        51, session_uid=RECORDED_SESSION_UID
    )

    assert result.session_context is not None
    assert context == result.session_context
    assert context_frame == 50
    assert context_after_session_start == result.session_context
    assert context_after_frame == 50
    assert [(change.frame_identifier, change.removed) for change in result.context_history_changes] == [
        (51, True),
        (50, False),
    ]


def test_pipeline_emits_only_new_context_history_changes() -> None:
    pipeline = TelemetryPipeline()
    first = pipeline.process(_recorded_packet())
    repeated = pipeline.process(_packet_with_body(_recorded_packet().payload[29:], frame=48))

    assert [(change.frame_identifier, change.context) for change in first.context_history_changes] == [
        (47, None),
        (47, first.session_context),
    ]
    assert len(repeated.context_history_changes) == 1
    assert repeated.context_history_changes[0].frame_identifier == 48
    assert repeated.context_history_changes[0].context == first.session_context


def test_reordered_session_update_can_follow_a_newer_motion_packet() -> None:
    pipeline = TelemetryPipeline()
    body = bytearray(FIXTURE.read_bytes()[29:])
    pipeline.process(_packet_with_body(bytes(body), frame=47))

    pipeline.process(
        make_datagram(
            packet_id=0,
            session_uid=RECORDED_SESSION_UID,
            frame=51,
            sequence=1,
        )
    )
    body[0] = 1
    reordered_session = pipeline.process(
        _packet_with_body(bytes(body), frame=50)
    )

    assert reordered_session.session_context is not None
    assert pipeline.sessions.current_context is not None
    assert pipeline.sessions.current_context.weather_id == 1


def test_reordered_historical_session_context_keeps_the_newer_state() -> None:
    pipeline = TelemetryPipeline()
    original = pipeline.process(_recorded_packet()).session_context
    assert original is not None

    pipeline.process(_packet_with_body(_recorded_packet().payload[29:], frame=61))
    unknown_body = bytearray(_recorded_packet().payload[29:])
    unknown_body[665] = 250
    delayed = pipeline.process(_packet_with_body(bytes(unknown_body), frame=60))

    context_at_60, _ = pipeline.sessions.context_at(
        60, session_uid=RECORDED_SESSION_UID
    )
    context_at_61, _ = pipeline.sessions.context_at(
        61, session_uid=RECORDED_SESSION_UID
    )
    timeline = pipeline.sessions.context_timeline(
        50, 61, session_uid=RECORDED_SESSION_UID
    )

    assert delayed.session_context is None
    assert context_at_60 is not None
    assert context_at_60.game_mode is None
    assert context_at_61 == original
    assert [frame for frame, _ in timeline] == [50, 60, 61]


def test_context_reordering_uses_the_overall_frame_watermark() -> None:
    pipeline = TelemetryPipeline()
    original = pipeline.process(_recorded_packet()).session_context
    assert original is not None
    pipeline.process(_packet_with_body(_recorded_packet().payload[29:], frame=61))
    pipeline.process(
        make_datagram(
            packet_id=0,
            session_uid=RECORDED_SESSION_UID,
            frame=63,
            sequence=63,
        )
    )

    unknown_body = bytearray(_recorded_packet().payload[29:])
    unknown_body[665] = 250
    pipeline.process(_packet_with_body(bytes(unknown_body), frame=60))

    context_at_60, _ = pipeline.sessions.context_at(
        60, session_uid=RECORDED_SESSION_UID
    )
    assert context_at_60 == original
    assert pipeline.sessions.current_context == original
    assert pipeline.frames.late_packets_ignored == 1


def test_session_transition_clears_context_and_ignores_retired_updates() -> None:
    pipeline = TelemetryPipeline()
    recorded = _recorded_packet()
    initial = pipeline.process(recorded)
    transition = pipeline.process(
        make_datagram(packet_id=0, session_uid=200, frame=48, sequence=1)
    )
    delayed = pipeline.process(recorded)

    assert initial.session_context is not None
    assert [event.kind for event in transition.session_events] == [
        "session_ended",
        "session_started",
    ]
    assert pipeline.sessions.current_context is None
    assert delayed.session_context is None
    assert pipeline.sessions.current_session_uid == 200


def test_newer_wire_format_invalidates_context_but_delayed_format_does_not() -> None:
    pipeline = TelemetryPipeline()
    original = pipeline.process(_recorded_packet())
    delayed_format_packet = make_datagram(
        packet_format=2026,
        packet_id=0,
        session_uid=RECORDED_SESSION_UID,
        frame=46,
        sequence=1,
    )
    delayed = pipeline.process(delayed_format_packet)

    assert original.session_context is not None
    assert delayed.session_events == ()
    assert pipeline.sessions.current_context == original.session_context

    current_format_packet = make_datagram(
        packet_format=2026,
        packet_id=0,
        session_uid=RECORDED_SESSION_UID,
        frame=48,
        sequence=2,
    )
    changed = pipeline.process(current_format_packet)

    assert [event.kind for event in changed.session_events] == [
        "session_context_invalidated"
    ]
    assert pipeline.sessions.current_packet_format.value == 2026
    assert pipeline.sessions.current_context is None


def test_inspect_reports_mode_context_from_recorded_packets(tmp_path, capsys) -> None:
    capture_path = tmp_path / "time-trial.f1ecap"
    with CaptureWriter(capture_path) as writer:
        writer.write(_recorded_packet())

    assert _inspect(Namespace(capture=str(capture_path))) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["session_contexts"][0]["session_type"] == "time_trial"
    assert report["session_contexts"][0]["game_mode"] == "time_trial"
    assert report["session_context_missing_uids"] == []


def test_inspect_does_not_report_old_context_after_format_change(tmp_path, capsys) -> None:
    capture_path = tmp_path / "format-change.f1ecap"
    with CaptureWriter(capture_path) as writer:
        writer.write(_recorded_packet())
        writer.write(
            make_datagram(
                packet_format=2026,
                packet_id=0,
                session_uid=RECORDED_SESSION_UID,
                frame=48,
                sequence=1,
            )
        )

    assert _inspect(Namespace(capture=str(capture_path))) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["session_contexts"] == []
    assert report["session_context_missing_uids"] == [RECORDED_SESSION_UID]
    assert report["summary"]["session_context_invalidations"] == 1
