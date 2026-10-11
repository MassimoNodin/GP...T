import struct
from dataclasses import replace

import pytest

import f1_engineer.pipeline as pipeline_module
from f1_engineer.pipeline import TelemetryPipeline
from f1_engineer.sessions.car_lap_inventory import CarSlotTenure
from f1_engineer.sessions.context import GameMode, RuleSet, SessionContext, SessionType
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.models import PacketFormat, PacketFrame
from tests.helpers import make_datagram
from tests.test_car_lap_inventory import _participants_packet


SESSION = 919191
RECORD = struct.Struct("<IIHBHBHBHBfff15BHHBfB")


def _context(kind=SessionType.RACE, length=5000, packet_format=PacketFormat.F1_25):
    return SessionContext(SESSION, packet_format, 1, 0, None, 25, 20, 20, length,
                          0, kind, 1, "Track", 0, 0, 0,
                          GameMode.GRAND_PRIX_23,
                          1, RuleSet.RACE if kind in {SessionType.RACE, SessionType.RACE_2,
                          SessionType.RACE_3} else RuleSet.PRACTICE_QUALIFYING,
                          0, 0, 0, 0)


def _lap_packet(frame, *, count=22, packet_format=2025, positions=None,
                distances=None, player=0, session_time=None):
    positions = positions or list(range(1, count + 1))
    distances = distances or [float(index * 150) for index in range(count)]
    rows = []
    for index in range(count):
        fields = (0, 1000, 0, 0, 0, 0, 0, 0, 0, 0, distances[index],
                  distances[index], 0.0, positions[index], 1, 0, 0, 0, 0, 0,
                  0, 0, 0, 0, 1, 1, 2, False, 0, 0, False, 0.0, 0)
        rows.append(RECORD.pack(*fields))
    body = b"".join(rows) + bytes((0, 255))
    return PacketDecoder().decode(make_datagram(
        packet_format=packet_format, packet_id=2, session_uid=SESSION, frame=frame,
        session_time=frame / 60 if session_time is None else session_time,
        player_car_index=player, body=body, sequence=frame,
    ))


def _roster_packet(frame, *, count=22, packet_format=2025, driver_ids=None):
    roster = _participants_packet(frame, packet_format=packet_format, active_count=count,
                                  slot_count=count, driver_ids=driver_ids)
    return PacketDecoder().decode(make_datagram(
        packet_format=packet_format, packet_id=4, session_uid=SESSION, frame=frame,
        body=roster.body, sequence=frame,
    ))


def _pipeline_frame(pipeline, frame, *, profile="demand_v1", kind=SessionType.RACE,
                    count=22, packet_format=2025, positions=None, distances=None,
                    player=0, driver_ids=None, duplicate=False):
    pipeline.detail_profile = profile
    roster = _roster_packet(frame, count=count, packet_format=packet_format, driver_ids=driver_ids)
    lap = _lap_packet(frame, count=count, packet_format=packet_format, positions=positions,
                      distances=distances, player=player)
    context = _context(kind, packet_format=PacketFormat(packet_format))
    if pipeline.sessions.current_session_uid is None:
        pipeline.sessions.observe(roster)
        pipeline.sessions.update_context(context, frame)
    packets = (roster, lap)
    if duplicate:
        conflicting_positions = list(positions or range(1, count + 1))
        conflicting_positions[2] += 1
        packets += (_lap_packet(frame, count=count, packet_format=packet_format,
                                positions=conflicting_positions, distances=distances,
                                player=player),)
    return pipeline._process_frames((PacketFrame(SESSION, frame, packets),))


def _rows(result):
    return result[3]


def test_P11_full_profile_preserves_all_car_rows_and_player_sample_records():
    pipeline = TelemetryPipeline(detail_profile="full")
    output = _pipeline_frame(pipeline, 1, profile="full")
    assert tuple(row.car_index for row in _rows(output)) == tuple(range(22))
    assert pipeline.detail_profile == "full"
    assert pipeline.observation_rows_created == 22
    assert output[2] and output[2][0].car_index == 0


def test_P12_demand_profile_selects_three_and_keeps_all_field_tenures():
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    positions = [12] + list(range(1, 12)) + list(range(13, 23))
    output = _pipeline_frame(pipeline, 1, positions=positions)
    assert tuple(row.car_index for row in _rows(output)) == (0, 11, 12)
    assert len(pipeline.car_lap_inventory.active_tenures({SESSION: 1})) == 22
    assert pipeline.observation_rows_skipped_by_policy == 19


def test_P13_suppressed_car_observations_are_never_constructed(monkeypatch):
    calls = []
    original = pipeline_module.make_car_observation
    def spy(**kwargs):
        calls.append(kwargs["car_index"])
        return original(**kwargs)
    monkeypatch.setattr(pipeline_module, "make_car_observation", spy)
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    _pipeline_frame(pipeline, 1, positions=[12] + list(range(1, 12)) + list(range(13, 23)))
    assert calls == [0, 11, 12]


def test_P14_current_roster_identity_replacement_invalidates_old_task_binding():
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    _pipeline_frame(pipeline, 1, kind=SessionType.TIME_TRIAL)
    old = next(item for item in pipeline.car_lap_inventory.active_tenures({SESSION: 1})
               if item.car_index == 5)
    from f1_engineer.processing.detail_policy import DetailDemandTarget
    pipeline.set_detail_demands((DetailDemandTarget("task", old),))
    replaced = {5: 99}
    output = _pipeline_frame(pipeline, 2, kind=SessionType.TIME_TRIAL, driver_ids=replaced)
    assert tuple(row.car_index for row in _rows(output)) == (0,)
    current = next(item for item in pipeline.car_lap_inventory.active_tenures({SESSION: 2})
                   if item.car_index == 5)
    assert current.participant_identity_fingerprint != old.participant_identity_fingerprint


def test_P15_player_record_parity_between_full_and_demand_profiles():
    full = TelemetryPipeline(detail_profile="full")
    demand = TelemetryPipeline(detail_profile="demand_v1")
    a = _pipeline_frame(full, 1, profile="full", positions=[12] + list(range(1, 12)) + list(range(13, 23)))
    b = _pipeline_frame(demand, 1, positions=[12] + list(range(1, 12)) + list(range(13, 23)))
    full_player = next(row for row in _rows(a) if row.car_index == 0)
    demand_player = next(row for row in _rows(b) if row.car_index == 0)
    assert full_player.to_record() == demand_player.to_record()


def test_P16_neighbor_swap_applies_on_next_processed_frame_without_stale_car():
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    first_positions = [12] + list(range(1, 12)) + list(range(13, 23))
    first = _pipeline_frame(pipeline, 1, positions=first_positions)
    second_positions = [12] + list(range(13, 23)) + list(range(1, 12))
    second = _pipeline_frame(pipeline, 2, positions=second_positions)
    assert {row.car_index for row in _rows(first)} == {0, 11, 12}
    assert {row.car_index for row in _rows(second)} == {0, 1, 22 - 1}


def test_P17_duplicate_conflicting_lap_packets_keep_player_and_missing_channel_diagnostics():
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    output = _pipeline_frame(pipeline, 1, duplicate=True)
    assert _rows(output) == ()
    assert output[2] and output[2][0].car_index == 0
    assert pipeline.car_observation_conflict_frames == 1
    assert pipeline.missing_car_telemetry_frame_count == 1


@pytest.mark.parametrize("old_count,new_count,fmt", [(22, 24, 2026), (24, 22, 2025)])
def test_P18_format_and_roster_shape_changes_clear_old_associations(old_count, new_count, fmt):
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    _pipeline_frame(pipeline, 1, count=old_count, packet_format=2025 if old_count == 22 else 2026)
    output = _pipeline_frame(pipeline, 2, count=new_count, packet_format=fmt)
    assert all(row.car_index < new_count for row in _rows(output))
    assert len(pipeline.car_lap_inventory.active_tenures({SESSION: 2})) == new_count


def test_P19_thousand_stable_frames_construct_exact_rows_and_only_emit_selection_change():
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    positions = [12] + list(range(1, 12)) + list(range(13, 23))
    frames = [
        _pipeline_frame(pipeline, ordinal, positions=positions)
        for ordinal in range(1, 1001)
    ]
    assert sum(len(_rows(output)) for output in frames) == 3000
    assert pipeline.observation_rows_created == 3000
    assert pipeline.observation_rows_skipped_by_policy == 19000
    assert len(pipeline._drain_detail_selections()) == 1
    tt = TelemetryPipeline(detail_profile="demand_v1")
    tt_frames = [_pipeline_frame(tt, ordinal, kind=SessionType.TIME_TRIAL)
                 for ordinal in range(1, 5)]
    assert all(len(_rows(output)) == 1 for output in tt_frames)


def test_P20_four_task_targets_are_bounded_at_seven_and_release_does_not_fallback():
    from f1_engineer.processing.detail_policy import DetailDemandTarget
    pipeline = TelemetryPipeline(detail_profile="demand_v1")
    positions = [12] + list(range(1, 12)) + list(range(13, 23))
    _pipeline_frame(pipeline, 1, positions=positions)
    tenures = pipeline.car_lap_inventory.active_tenures({SESSION: 1})
    targets = tuple(DetailDemandTarget(f"task-{index}", tenures[index]) for index in (1, 2, 3, 4))
    pipeline.set_detail_demands(targets)
    selected = _pipeline_frame(pipeline, 2, positions=positions)
    assert len(_rows(selected)) <= 7
    pipeline.set_detail_demands(())
    released = _pipeline_frame(pipeline, 3, positions=positions)
    assert len(_rows(released)) == 3
