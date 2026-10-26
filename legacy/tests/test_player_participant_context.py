from __future__ import annotations

import struct
from dataclasses import replace

import pytest

from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.sessions.participant_context import PlayerParticipantObservation
from f1_engineer.storage import importer as importer_module
from f1_engineer.storage.importer import _PlayerParticipantObservationWriter
from tests.helpers import make_datagram


_PARTICIPANT_RECORD = struct.Struct("<7B32s2BH14B")
_SESSION_UID = 9_900_100


def _participants_body(
    *,
    active_count: int = 22,
    driver_id: int = 7,
    name: str = "Player",
    show_online_names: int = 1,
    colour_seed: int = 0,
) -> bytes:
    records = []
    for index in range(22):
        colours = tuple((colour_seed + value) % 256 for value in range(12))
        records.append(
            _PARTICIPANT_RECORD.pack(
                0,
                driver_id if index == 0 else index,
                0,
                3,
                1,
                44,
                8,
                name.encode("utf-8").ljust(32, b"\0")
                if index == 0
                else bytes(32),
                1,
                show_online_names if index == 0 else 1,
                99,
                0,
                3,
                *colours,
            )
        )
    return bytes((active_count,)) + b"".join(records)


def _participant_packet(
    frame: int,
    sequence: int,
    *,
    player_car_index: int = 0,
    body: bytes | None = None,
):
    return make_datagram(
        packet_id=4,
        session_uid=_SESSION_UID,
        frame=frame,
        session_time=frame / 60,
        player_car_index=player_car_index,
        body=body if body is not None else _participants_body(),
        sequence=sequence,
    )


def _observations(packets, *, reorder_window_frames: int = 1):
    pipeline = TelemetryPipeline(reorder_window_frames=reorder_window_frames)
    result = []
    for packet in packets:
        result.extend(pipeline.process(packet).player_participant_observations)
    result.extend(
        pipeline.finish_with_outputs().player_participant_observations
    )
    return result


def test_admitted_player_participant_evidence_uses_frame_order_across_u32_wrap() -> None:
    observations = _observations(
        [
            _participant_packet(0xFFFF_FFFE, 1, body=_participants_body(driver_id=1)),
            _participant_packet(0xFFFF_FFFF, 2, body=_participants_body(driver_id=2)),
            _participant_packet(0, 3, body=_participants_body(driver_id=3)),
        ],
        reorder_window_frames=3,
    )

    assert [item.overall_frame_identifier for item in observations] == [
        0xFFFF_FFFE,
        0xFFFF_FFFF,
        0,
    ]
    assert [item.frame_ordinal for item in observations] == [1, 2, 3]
    assert [item.participant.driver_id for item in observations] == [1, 2, 3]


def test_late_player_participant_packet_does_not_update_admitted_timeline() -> None:
    pipeline = TelemetryPipeline(reorder_window_frames=1)
    packets = [
        _participant_packet(10, 1, body=_participants_body(driver_id=10)),
        _participant_packet(12, 2, body=_participants_body(driver_id=12)),
        _participant_packet(11, 3, body=_participants_body(driver_id=11)),
    ]
    observations = []
    results = [pipeline.process(packet) for packet in packets]
    for result in results:
        observations.extend(result.player_participant_observations)
    observations.extend(
        pipeline.finish_with_outputs().player_participant_observations
    )

    assert [item.overall_frame_identifier for item in observations] == [10, 12]
    assert [item.participant.driver_id for item in observations] == [10, 12]
    assert pipeline.frames.late_packets_ignored == 1


def test_same_frame_cosmetic_participant_differences_do_not_conflict() -> None:
    observations = _observations(
        [
            _participant_packet(20, 1, body=_participants_body(driver_id=7)),
            _participant_packet(
                20,
                2,
                body=_participants_body(
                    driver_id=7, show_online_names=0, colour_seed=40
                ),
            ),
            _participant_packet(21, 3),
        ]
    )

    assert len(observations) == 2
    first = observations[0]
    assert first.status == "observed"
    assert first.participant is not None
    assert first.participant.driver_id == 7
    assert first.source_packet_count == 2


def test_same_frame_other_car_count_change_does_not_conflict() -> None:
    observations = _observations(
        [
            _participant_packet(22, 1, body=_participants_body(active_count=21)),
            _participant_packet(22, 2, body=_participants_body(active_count=22)),
            _participant_packet(23, 3),
        ]
    )

    assert observations[0].status == "observed"
    assert observations[0].participant is not None
    assert observations[0].participant.driver_id == 7
    assert observations[0].active_car_count in {21, 22}
    assert observations[0].source_packet_count == 2


def test_same_frame_player_identity_conflict_is_unavailable() -> None:
    observations = _observations(
        [
            _participant_packet(30, 1, body=_participants_body(driver_id=7)),
            _participant_packet(30, 2, body=_participants_body(driver_id=8)),
            _participant_packet(31, 3),
        ]
    )

    assert observations[0].status == "unavailable"
    assert observations[0].reason == "conflicting_player_participant_evidence"
    assert observations[0].participant is None


def test_valid_selected_record_supplies_provenance_after_malformed_variant() -> None:
    malformed = _participant_packet(
        33, 1, body=_participants_body()[:-1]
    )
    malformed_payload = bytearray(malformed.payload)
    struct.pack_into("<f", malformed_payload, 15, 0.5)
    struct.pack_into("<I", malformed_payload, 19, 999)
    malformed = replace(malformed, payload=bytes(malformed_payload))

    observations = _observations(
        [
            malformed,
            _participant_packet(33, 2, body=_participants_body(driver_id=44)),
            _participant_packet(34, 3),
        ]
    )

    assert observations[0].status == "observed"
    assert observations[0].frame_identifier == 33
    assert observations[0].session_time_s == pytest.approx(33 / 60)
    assert observations[0].participant is not None
    assert observations[0].participant.driver_id == 44


def test_lifecycle_boundary_participants_cannot_seed_the_new_epoch() -> None:
    boundary_event = make_datagram(
        packet_id=3,
        session_uid=_SESSION_UID,
        frame=51,
        session_time=1.0,
        body=b"FLBK" + struct.pack("<If", 10, 0.5) + b"\0" * 4,
        sequence=2,
    )
    observations = _observations(
        [
            _participant_packet(50, 1, body=_participants_body(driver_id=10)),
            boundary_event,
            _participant_packet(51, 3, body=_participants_body(driver_id=11)),
            _participant_packet(52, 4, body=_participants_body(driver_id=12)),
        ]
    )

    assert [item.overall_frame_identifier for item in observations] == [50, 51, 52]
    assert observations[0].status == "observed"
    assert observations[1].status == "unavailable"
    assert observations[1].reason == "lifecycle_boundary"
    assert observations[1].association_epoch == observations[2].association_epoch
    assert observations[2].status == "observed"
    assert observations[2].participant is not None
    assert observations[2].participant.driver_id == 12


def test_inactive_player_slot_is_an_unavailable_fence() -> None:
    observations = _observations(
        [
            _participant_packet(40, 1, body=_participants_body(driver_id=7)),
            _participant_packet(41, 2, body=_participants_body(active_count=0)),
            _participant_packet(42, 3, body=_participants_body(driver_id=8)),
        ]
    )

    assert [item.status for item in observations] == [
        "observed",
        "unavailable",
        "observed",
    ]
    assert observations[1].reason == "player_slot_inactive"


@pytest.mark.parametrize("header_player", (0, 1))
def test_conflicting_frame_player_headers_emit_scope_fence(header_player: int) -> None:
    observations = _observations(
        [
            _participant_packet(50, 1, player_car_index=0),
            _participant_packet(50, 2, player_car_index=header_player),
            _participant_packet(51, 3, player_car_index=header_player),
        ]
    )

    if header_player == 0:
        # Identical wire packets are collapsed by the assembler.
        assert observations[0].status == "observed"
    else:
        assert observations[0].status == "unavailable"
        assert observations[0].reason == "frame_player_scope_conflict"
        assert observations[0].player_car_index is None


class _RecordingConnection:
    def __init__(self) -> None:
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def executemany(self, _sql, rows) -> None:
        self.rows.extend(rows)


def test_import_writer_bounds_observations_and_truncation_fences(monkeypatch) -> None:
    monkeypatch.setattr(
        importer_module, "MAX_STORED_PLAYER_PARTICIPANT_OBSERVATIONS", 2
    )
    monkeypatch.setattr(
        importer_module, "MAX_STORED_PLAYER_PARTICIPANT_TRUNCATION_FENCES", 1
    )
    connection = _RecordingConnection()
    writer = _PlayerParticipantObservationWriter(connection, "run")
    observations = tuple(
        PlayerParticipantObservation(
            session_uid=session_uid,
            frame_ordinal=1,
            frame_identifier=1,
            overall_frame_identifier=1,
            packet_format=2025,
            association_epoch=0,
            association_scope_assessable=True,
            player_car_index=0,
            session_time_s=1.0,
            status="unavailable",
            reason="participant_packet_unavailable",
            active_car_count=None,
            participant=None,
            source_packet_count=0,
        )
        for session_uid in (11, 12, 13, 14, 14)
    )

    writer.consume(observations)
    writer.flush()

    assert writer.stored_count == 2
    assert len(writer.truncated_session_uids) == 1
    assert writer.truncation_marker_overflowed is True
    assert len(connection.rows) == 3
    assert [row[10] for row in connection.rows].count("truncated") == 1
