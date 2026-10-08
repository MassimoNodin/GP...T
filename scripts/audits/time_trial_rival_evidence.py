"""Read-only audit of Time Trial rival and personal-best evidence.

The script does not modify captures. A rival index, a lap time, or a packet 14
dataset is never treated as a speed, brake, throttle, gear, or position trace.
Channel claims come only from decoded samples joined on session UID and overall
frame identifier.

``label`` is a channel-coverage classifier. It is not a verdict that the indexed
slot is a usable ghost trace. ``blocks_detailed_comparison`` is true when the
selected slot never has Lap Data result status active (2 or higher), or when
every selected index is outside Participants ``m_numActiveCars``. Official EA
guidance says a slot is actively providing data only when its result status is
neither Invalid (0) nor Inactive (1).

Reproduction, from the repository root:

    uv run --frozen --extra dev python scripts/audits/time_trial_rival_evidence.py CAPTURE.f1ecap
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter, deque
from pathlib import Path
from typing import Iterable, Mapping

from f1_engineer.errors import CaptureFormatError, ProtocolError
from f1_engineer.recording.capture import CaptureReader
from f1_engineer.udp.car_telemetry import CarTelemetryDecoder
from f1_engineer.udp.decoder import PacketDecoder
from f1_engineer.udp.lap_data import LapDataDecoder
from f1_engineer.udp.models import PacketId, RawDatagram
from f1_engineer.udp.motion import MotionDecoder
from f1_engineer.udp.participants import ParticipantsDecoder
from f1_engineer.udp.session_context import SessionContextDecoder
from f1_engineer.udp.session_history import SessionHistoryDecoder

HEADER_SIZE = 29
INVALID_CAR_INDEX = 255
FRAME_BUFFER_LIMIT = 256
MIN_CHANNEL_FRAMES = 20
MIN_DISTANCE_SPAN_M = 100.0
MIN_SPEED_SPAN_KPH = 20.0
MIN_MOTION_SPAN_M = 20.0
MIN_SYNC_FRACTION = 0.8
MAX_COPY_FRACTION = 0.5
MIN_LAP_COVERAGE_FRACTION = 0.85
MOVING_SPEED_KPH = 10
_F1_25_TIME_TRIAL_DATASET = struct.Struct("<BBIIIIBBBBBB")
_SEASON_PACK_2026_TIME_TRIAL_DATASET = struct.Struct("<BHIIIIBBBBBB")
_DATASET_NAMES = ("player_session_best", "personal_best", "rival")


def parse_time_trial_body(packet_format: int, body: bytes) -> dict[str, object]:
    """Parse a Time Trial packet body when it matches one documented layout.

    F1 25 packet format 2025 is a 72-byte body of three 24-byte datasets.
    The 2026 Season Pack packet format 2026 is a 75-byte body of three 25-byte
    datasets because team id widened to uint16. Either size is timing metadata
    only: car index, team, lap and sector times, assist flags, and validity.
    """

    if packet_format == 2025:
        record = _F1_25_TIME_TRIAL_DATASET
        expected = record.size * 3
    elif packet_format == 2026:
        record = _SEASON_PACK_2026_TIME_TRIAL_DATASET
        expected = record.size * 3
    else:
        return {"parsed": False, "reason": "unsupported_packet_format", "body_bytes": len(body)}
    if len(body) != expected:
        return {
            "parsed": False,
            "reason": "unexpected_body_size",
            "body_bytes": len(body),
            "expected_body_bytes": expected,
        }
    datasets = []
    for index, name in enumerate(_DATASET_NAMES):
        fields = record.unpack_from(body, index * record.size)
        datasets.append(
            {
                "name": name,
                "car_index": int(fields[0]),
                "team_id": int(fields[1]),
                "lap_time_ms": int(fields[2]),
                "sector1_time_ms": int(fields[3]),
                "sector2_time_ms": int(fields[4]),
                "sector3_time_ms": int(fields[5]),
                "traction_control": int(fields[6]),
                "gearbox_assist": int(fields[7]),
                "anti_lock_brakes": int(fields[8]),
                "equal_car_performance": int(fields[9]),
                "custom_setup": int(fields[10]),
                "valid": int(fields[11]),
            }
        )
    return {"parsed": True, "datasets": datasets}


def conclude_rival(
    role: Mapping[str, object],
    *,
    packet14_valid_lap_times: int,
    history_positive_lap_times: int,
    track_length_m: int | None,
) -> dict[str, object]:
    """Classify rival evidence without promoting an index or a lap time to a trace."""

    in_range = int(role["index_in_range"])
    equals_player = int(role["index_equals_player"])
    reasons: list[str] = []
    distinct_frames = in_range - equals_player
    if in_range == 0:
        reasons.append("no_in_range_rival_index")
    elif distinct_frames <= 0:
        reasons.append("rival_index_matches_player_whenever_selected")
    channels = _channel_support(role, distinct_frames)
    if not channels["supported"]:
        reasons.extend(channels["missing"])
    copy_fraction = _copy_fraction(role)
    if copy_fraction is not None and copy_fraction > MAX_COPY_FRACTION:
        reasons.append("selected_slot_speed_matches_player_while_both_move")
    coverage = _lap_coverage(role, track_length_m)
    if not coverage["supported"]:
        reasons.extend(coverage["missing"])
    timing = packet14_valid_lap_times > 0 or history_positive_lap_times > 0 or int(
        role["last_lap_time_positive_frames"]
    ) > 0
    detailed = (
        distinct_frames >= MIN_CHANNEL_FRAMES
        and channels["supported"]
        and (copy_fraction is None or copy_fraction <= MAX_COPY_FRACTION)
        and coverage["supported"]
    )
    partial = (
        not detailed
        and distinct_frames >= MIN_CHANNEL_FRAMES
        and channels["any_motion_or_input"]
        and (copy_fraction is None or copy_fraction <= MAX_COPY_FRACTION)
    )
    if detailed:
        label = "detailed_channels_with_lap_coverage"
    elif partial:
        label = "detailed_channels_partial"
    elif timing:
        label = "timing_only"
    else:
        label = "not_established"
    activity = _activity_gate(role, distinct_frames=distinct_frames, in_range=in_range)
    reasons.extend(activity["reasons"])
    kinematic_frames = role.get("kinematic_frames")
    kinematic_mismatches = role.get("kinematic_mismatch_frames")
    speed_disagrees = False
    if (
        isinstance(kinematic_frames, int)
        and isinstance(kinematic_mismatches, int)
        and kinematic_frames >= MIN_CHANNEL_FRAMES
        and kinematic_mismatches / kinematic_frames > 0.5
    ):
        reasons.append("reported_speed_disagrees_with_lap_distance")
        speed_disagrees = True
    blocks = activity["blocks_detailed_comparison"]
    if speed_disagrees:
        blocks = True
    return {
        "label": label,
        "reasons": reasons,
        "blocks_detailed_comparison": blocks,
        "distinct_selected_frames": distinct_frames,
        "copy_fraction_while_both_moving": copy_fraction,
        "best_lap_distance_span_m": coverage["best_span_m"],
        "packet14_valid_lap_times": packet14_valid_lap_times,
        "history_positive_lap_times": history_positive_lap_times,
    }


def _activity_gate(
    role: Mapping[str, object], *, distinct_frames: int, in_range: int
) -> dict[str, object]:
    """Apply the official result-status rule without turning an index into a trace."""

    reasons: list[str] = []
    result_status = role.get("result_status")
    active_frames = 0
    if isinstance(result_status, Mapping) and result_status:
        for status, count in result_status.items():
            if int(status) >= 2:
                active_frames += int(count)
        if distinct_frames > 0 and active_frames == 0:
            reasons.append("indexed_slot_result_status_never_active")
    outside = role.get("outside_active_count")
    if isinstance(outside, int) and in_range > 0 and outside == in_range:
        reasons.append("indexed_slot_outside_reported_active_cars")
    known = isinstance(result_status, Mapping) and bool(result_status)
    blocks = True if known and active_frames == 0 and distinct_frames > 0 else False
    if "indexed_slot_outside_reported_active_cars" in reasons:
        blocks = True
    if not known and "indexed_slot_outside_reported_active_cars" not in reasons:
        blocks_value: bool | None = None
    else:
        blocks_value = blocks
    return {"reasons": reasons, "blocks_detailed_comparison": blocks_value}


def _channel_support(role: Mapping[str, object], distinct_frames: int) -> dict[str, object]:
    missing: list[str] = []
    synced = int(role["synced_frames"])
    if distinct_frames < MIN_CHANNEL_FRAMES:
        missing.append("fewer_than_minimum_distinct_selected_frames")
    sync_fraction = (synced / distinct_frames) if distinct_frames else 0.0
    if sync_fraction < MIN_SYNC_FRACTION:
        missing.append("lap_telemetry_motion_not_synchronized")
    distance_span = _span(role.get("lap_distance_min"), role.get("lap_distance_max"))
    speed_span = _span(role.get("speed_min_kph"), role.get("speed_max_kph"))
    motion_span = float(role["motion_span_m"])
    if distance_span < MIN_DISTANCE_SPAN_M:
        missing.append("lap_distance_span_too_small")
    if speed_span < MIN_SPEED_SPAN_KPH:
        missing.append("speed_span_too_small")
    if int(role["brake_positive_frames"]) < 3:
        missing.append("brake_channel_not_observed")
    if int(role["throttle_positive_frames"]) < 3:
        missing.append("throttle_channel_not_observed")
    if len(role["gears"]) < 2:
        missing.append("gear_channel_not_changing")
    if motion_span < MIN_MOTION_SPAN_M:
        missing.append("motion_span_too_small")
    any_signal = (
        distance_span >= MIN_DISTANCE_SPAN_M
        or speed_span >= MIN_SPEED_SPAN_KPH
        or int(role["brake_positive_frames"]) >= 3
        or motion_span >= MIN_MOTION_SPAN_M
    )
    return {"supported": not missing, "missing": missing, "any_motion_or_input": any_signal}


def _lap_coverage(role: Mapping[str, object], track_length_m: int | None) -> dict[str, object]:
    best = 0.0
    best_samples = 0
    per_lap = role["per_lap_distance_span"]
    assert isinstance(per_lap, Mapping)
    for lap_stats in per_lap.values():
        assert isinstance(lap_stats, Mapping)
        span = _span(lap_stats.get("min_m"), lap_stats.get("max_m"))
        samples = int(lap_stats["samples"])
        if span > best:
            best = span
            best_samples = samples
    if track_length_m is None or track_length_m <= 0:
        return {
            "supported": False,
            "missing": ["track_length_unavailable"],
            "best_span_m": best,
        }
    required = MIN_LAP_COVERAGE_FRACTION * track_length_m
    if best < required or best_samples < MIN_CHANNEL_FRAMES:
        return {
            "supported": False,
            "missing": ["no_selected_lap_covers_track_length"],
            "best_span_m": best,
        }
    return {"supported": True, "missing": [], "best_span_m": best}


def _copy_fraction(role: Mapping[str, object]) -> float | None:
    both = int(role["both_moving_frames"])
    if both <= 0:
        return None
    return int(role["equal_speed_frames"]) / both


def _span(minimum: object, maximum: object) -> float:
    if minimum is None or maximum is None:
        return 0.0
    return float(maximum) - float(minimum)


class _Range:
    def __init__(self) -> None:
        self.minimum: float | None = None
        self.maximum: float | None = None

    def add(self, value: float) -> None:
        if self.minimum is None or value < self.minimum:
            self.minimum = value
        if self.maximum is None or value > self.maximum:
            self.maximum = value

    def span(self) -> float:
        return _span(self.minimum, self.maximum)


class _RoleStats:
    def __init__(self) -> None:
        self.index_missing = 0
        self.index_out_of_range = 0
        self.index_in_range = 0
        self.index_equals_player = 0
        self.index_equals_other = 0
        self.outside_active_count = 0
        self.synced_frames = 0
        self.telemetry_frames = 0
        self.motion_frames = 0
        self.distance = _Range()
        self.speed = _Range()
        self.brake_positive_frames = 0
        self.brake_max = 0.0
        self.throttle_positive_frames = 0
        self.throttle_max = 0.0
        self.gears: set[int] = set()
        self.motion_x = _Range()
        self.motion_z = _Range()
        self.result_status: Counter[int] = Counter()
        self.driver_status: Counter[int] = Counter()
        self.current_lap_invalid: Counter[int] = Counter()
        self.last_lap_time_positive_frames = 0
        self.last_lap_time = _Range()
        self.lap_numbers: set[int] = set()
        self.per_lap: dict[int, dict[str, float | int]] = {}
        self.equal_speed_frames = 0
        self.both_moving_frames = 0
        self.position_separated_frames = 0
        self.selected_indices: list[int] = []
        self.last_distance: float | None = None
        self.forward_distance_m = 0.0
        self.backward_distance_m = 0.0
        self.forward_frames = 0
        self.backward_frames = 0
        self.large_backward_frames = 0
        self.speed_samples = 0
        self.speed_over_360_frames = 0
        self.speed_over_400_frames = 0
        self.speed_trap_max_kph = 0.0
        self.last_session_time: float | None = None
        self.kinematic_frames = 0
        self.kinematic_mismatch_frames = 0
        self.kinematic_abs_error_m = 0.0

    def observe_index(self, index: int | None, *, car_count: int, player_index: int | None) -> None:
        if index is None or index == INVALID_CAR_INDEX:
            self.index_missing += 1
            return
        if not 0 <= index < car_count:
            self.index_out_of_range += 1
            return
        self.index_in_range += 1
        if player_index is not None and index == player_index:
            self.index_equals_player += 1
        if index not in self.selected_indices:
            self.selected_indices.append(index)

    def to_dict(self) -> dict[str, object]:
        return {
            "index_missing": self.index_missing,
            "index_out_of_range": self.index_out_of_range,
            "index_in_range": self.index_in_range,
            "index_equals_player": self.index_equals_player,
            "index_equals_other": self.index_equals_other,
            "outside_active_count": self.outside_active_count,
            "selected_indices": list(self.selected_indices),
            "synced_frames": self.synced_frames,
            "telemetry_frames": self.telemetry_frames,
            "motion_frames": self.motion_frames,
            "lap_distance_min": self.distance.minimum,
            "lap_distance_max": self.distance.maximum,
            "speed_min_kph": self.speed.minimum,
            "speed_max_kph": self.speed.maximum,
            "brake_positive_frames": self.brake_positive_frames,
            "brake_max": self.brake_max,
            "throttle_positive_frames": self.throttle_positive_frames,
            "throttle_max": self.throttle_max,
            "gears": sorted(self.gears),
            "motion_span_m": max(self.motion_x.span(), self.motion_z.span()),
            "result_status": _counter_dict(self.result_status),
            "driver_status": _counter_dict(self.driver_status),
            "current_lap_invalid": _counter_dict(self.current_lap_invalid),
            "last_lap_time_positive_frames": self.last_lap_time_positive_frames,
            "last_lap_time_min_ms": self.last_lap_time.minimum,
            "last_lap_time_max_ms": self.last_lap_time.maximum,
            "lap_numbers": sorted(self.lap_numbers),
            "per_lap_distance_span": {
                str(lap): stats for lap, stats in sorted(self.per_lap.items())
            },
            "equal_speed_frames": self.equal_speed_frames,
            "both_moving_frames": self.both_moving_frames,
            "position_separated_frames": self.position_separated_frames,
            "forward_distance_m": round(self.forward_distance_m, 3),
            "backward_distance_m": round(self.backward_distance_m, 3),
            "forward_frames": self.forward_frames,
            "backward_frames": self.backward_frames,
            "large_backward_frames": self.large_backward_frames,
            "speed_samples": self.speed_samples,
            "speed_over_360_frames": self.speed_over_360_frames,
            "speed_over_400_frames": self.speed_over_400_frames,
            "speed_trap_max_kph": self.speed_trap_max_kph,
            "kinematic_frames": self.kinematic_frames,
            "kinematic_mismatch_frames": self.kinematic_mismatch_frames,
            "kinematic_abs_error_m": round(self.kinematic_abs_error_m, 3),
        }


class _SlotStats:
    def __init__(self) -> None:
        self.lap_frames = 0
        self.distance = _Range()
        self.speed_max = 0
        self.moving_frames = 0
        self.motion = _Range()
        self.motion_z = _Range()
        self.result_active_frames = 0
        self.last_distance: float | None = None
        self.distance_changes = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "lap_frames": self.lap_frames,
            "lap_distance_span_m": round(self.distance.span(), 3),
            "speed_max_kph": self.speed_max,
            "moving_frames": self.moving_frames,
            "motion_span_m": round(max(self.motion.span(), self.motion_z.span()), 3),
            "result_active_frames": self.result_active_frames,
            "distance_changes": self.distance_changes,
        }


class _HistoryStats:
    def __init__(self) -> None:
        self.packets = 0
        self.max_num_laps = 0
        self.positive_lap_rows = 0
        self.positive_lap_times: set[int] = set()
        self.matched_player_header = 0
        self.decode_errors = 0

    def to_dict(self) -> dict[str, object]:
        times = sorted(self.positive_lap_times)
        return {
            "packets": self.packets,
            "max_num_laps": self.max_num_laps,
            "positive_lap_rows": self.positive_lap_rows,
            "distinct_positive_lap_times_capped": times[:8],
            "distinct_positive_lap_time_count": len(times),
            "matched_player_header": self.matched_player_header,
            "decode_errors": self.decode_errors,
        }


class _TimingStats:
    def __init__(self) -> None:
        self.packets = 0
        self.unparsed = Counter()
        self.valid = 0
        self.invalid = 0
        self.positive_lap_times = 0
        self.valid_positive_lap_times = 0
        self.lap_time = _Range()
        self.car_indices: Counter[int] = Counter()
        self.agrees_with_lap_index = 0
        self.disagrees_with_lap_index = 0
        self.no_lap_index_yet = 0
        self.team_ids: Counter[int] = Counter()

    def to_dict(self) -> dict[str, object]:
        return {
            "packets": self.packets,
            "unparsed": _counter_dict(self.unparsed),
            "valid_flags": self.valid,
            "invalid_flags": self.invalid,
            "positive_lap_times": self.positive_lap_times,
            "valid_positive_lap_times": self.valid_positive_lap_times,
            "lap_time_min_ms": self.lap_time.minimum,
            "lap_time_max_ms": self.lap_time.maximum,
            "car_indices": _counter_dict(self.car_indices),
            "car_index_agrees_with_lap_packet": self.agrees_with_lap_index,
            "car_index_disagrees_with_lap_packet": self.disagrees_with_lap_index,
            "no_lap_index_yet": self.no_lap_index_yet,
            "team_ids": _counter_dict(self.team_ids),
        }


class _SessionAudit:
    def __init__(self) -> None:
        self.roles = {"player": _RoleStats(), "pb": _RoleStats(), "rival": _RoleStats()}
        self.slots: dict[int, _SlotStats] = {}
        self.history: dict[int, _HistoryStats] = {}
        self.timing = {name: _TimingStats() for name in _DATASET_NAMES}
        self.contexts: list[dict[str, object]] = []
        self.context_key: tuple[object, ...] | None = None
        self.selection_changes: list[dict[str, object]] = []
        self.selection_change_count = 0
        self.last_selection: tuple[int | None, int | None, int | None] | None = None
        self.latest_pb: int | None = None
        self.latest_rival: int | None = None
        self.previous_rival_slots: set[int] = set()
        self.previous_pb_slots: set[int] = set()
        self.stale_rival_movement_frames = 0
        self.stale_pb_movement_frames = 0
        self.active_car_count: int | None = None
        self.participants: dict[int, dict[str, object]] = {}
        self.participant_changes: Counter[int] = Counter()
        self.lap_packets = 0
        self.lap_index_conflicts = 0
        self.telemetry_conflicts = 0
        self.motion_conflicts = 0
        self.player_index_conflicts = 0
        self.late_lap_after_flush = 0
        self.flushed_without_telemetry = 0
        self.flushed_without_motion = 0
        self.session_time_min: float | None = None
        self.session_time_max: float | None = None

    def slot(self, car_index: int) -> _SlotStats:
        stats = self.slots.get(car_index)
        if stats is None:
            stats = _SlotStats()
            self.slots[car_index] = stats
        return stats

    def history_slot(self, car_index: int) -> _HistoryStats:
        stats = self.history.get(car_index)
        if stats is None:
            stats = _HistoryStats()
            self.history[car_index] = stats
        return stats


class _PendingFrame:
    def __init__(self, session_uid: int, frame: int, sequence: int) -> None:
        self.session_uid = session_uid
        self.frame = frame
        self.sequence = sequence
        self.session_time: float | None = None
        self.player: int | None = None
        self.pb: int | None = None
        self.rival: int | None = None
        self.car_count = 0
        self.laps: list[tuple[object, ...]] | None = None
        self.lap_signature: tuple[object, ...] | None = None
        self.telemetry: list[tuple[int, float, float, int] | None] | None = None
        self.motion: list[tuple[float, float, float] | None] | None = None
        self.player_conflict = False
        self.lap_conflict = False
        self.telemetry_conflict = False
        self.motion_conflict = False


def audit_datagrams(datagrams: Iterable[RawDatagram]) -> dict[str, object]:
    decoder = PacketDecoder()
    lap_decoder = LapDataDecoder()
    telemetry_decoder = CarTelemetryDecoder()
    motion_decoder = MotionDecoder()
    participants_decoder = ParticipantsDecoder()
    session_decoder = SessionContextDecoder()
    history_decoder = SessionHistoryDecoder()
    sessions: dict[int, _SessionAudit] = {}
    pending: dict[tuple[int, int], _PendingFrame] = {}
    order: deque[tuple[int, int]] = deque()
    flushed: set[tuple[int, int]] = set()
    packet_counts: Counter[tuple[int, int, int, int]] = Counter()
    header_versions: Counter[tuple[int, int, int, int]] = Counter()
    decode_errors: Counter[str] = Counter()
    datagrams_seen = 0
    header_errors = 0

    def session_for(uid: int) -> _SessionAudit:
        audit = sessions.get(uid)
        if audit is None:
            audit = _SessionAudit()
            sessions[uid] = audit
        return audit

    def flush(key: tuple[int, int]) -> None:
        frame = pending.pop(key, None)
        order_remove(key)
        if frame is None:
            return
        flushed.add(key)
        if frame.laps is None:
            return
        audit = session_for(frame.session_uid)
        if frame.telemetry is None:
            audit.flushed_without_telemetry += 1
        if frame.motion is None:
            audit.flushed_without_motion += 1
        _apply_frame(audit, frame)

    def order_remove(key: tuple[int, int]) -> None:
        try:
            order.remove(key)
        except ValueError:
            return

    for datagram in datagrams:
        datagrams_seen += 1
        try:
            packet = decoder.decode(datagram)
        except ProtocolError:
            header_errors += 1
            continue
        header = packet.header
        packet_counts[(header.packet_format, header.packet_id, header.packet_version, len(datagram.payload))] += 1
        header_versions[
            (header.packet_format, header.game_year, header.game_major_version, header.game_minor_version)
        ] += 1
        uid = header.session_uid
        if uid == 0:
            continue
        audit = session_for(uid)
        if audit.session_time_min is None or header.session_time < audit.session_time_min:
            audit.session_time_min = header.session_time
        if audit.session_time_max is None or header.session_time > audit.session_time_max:
            audit.session_time_max = header.session_time
        kind = packet.packet_kind
        if kind is PacketId.SESSION:
            result = session_decoder.decode(packet)
            if result.error is not None:
                decode_errors[result.error] += 1
            elif result.context is not None:
                _observe_context(audit, result.context.to_dict())
        elif kind is PacketId.PARTICIPANTS:
            result = participants_decoder.decode(packet)
            if result.error is not None:
                decode_errors[result.error] += 1
            elif result.participants is not None:
                _observe_participants(audit, result.participants)
        elif kind is PacketId.SESSION_HISTORY:
            _observe_history(audit, history_decoder, packet, decode_errors)
        elif kind is PacketId.TIME_TRIAL:
            _observe_time_trial(audit, header.packet_format, packet.body)
        elif kind in (PacketId.LAP_DATA, PacketId.CAR_TELEMETRY, PacketId.MOTION):
            key = (uid, header.overall_frame_identifier)
            if key in flushed:
                if kind is PacketId.LAP_DATA:
                    audit.late_lap_after_flush += 1
                continue
            frame = pending.get(key)
            if frame is None:
                frame = _PendingFrame(uid, header.overall_frame_identifier, datagram.sequence or 0)
                pending[key] = frame
                order.append(key)
            _fill_frame(
                frame,
                packet,
                lap_decoder,
                telemetry_decoder,
                motion_decoder,
                audit,
                decode_errors,
            )
            while len(order) > FRAME_BUFFER_LIMIT:
                flush(order[0])

    while order:
        flush(order[0])

    return {
        "datagrams": datagrams_seen,
        "header_errors": header_errors,
        "packet_counts": [
            {
                "packet_format": item[0],
                "packet_id": item[1],
                "packet_version": item[2],
                "payload_bytes": item[3],
                "count": count,
            }
            for item, count in sorted(packet_counts.items())
        ],
        "header_versions": [
            {
                "packet_format": item[0],
                "game_year": item[1],
                "game_major_version": item[2],
                "game_minor_version": item[3],
                "count": count,
            }
            for item, count in sorted(header_versions.items())
        ],
        "decode_errors": _counter_dict(decode_errors),
        "sessions": [
            _session_dict(uid, audit) for uid, audit in sorted(sessions.items(), key=lambda item: str(item[0]))
        ],
    }


def audit_capture(path: Path) -> dict[str, object]:
    sha256 = _file_sha256(path)
    reader_errors: list[str] = []
    with CaptureReader(path) as reader:
        metadata = dict(reader.metadata)
        body = audit_datagrams(_datagrams_until_capture_error(reader, reader_errors))
        completion = reader.completion
        complete = reader.complete
    return {
        "file": {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256,
            "metadata": _public_document(metadata),
            "footer": _public_document(completion or {}),
            "complete": complete,
            "reader_error": reader_errors[0] if reader_errors else None,
        },
        **body,
    }


def _datagrams_until_capture_error(
    datagrams: Iterable[RawDatagram], errors: list[str]
) -> Iterable[RawDatagram]:
    try:
        yield from datagrams
    except CaptureFormatError as exc:
        errors.append(str(exc))


def _fill_frame(
    frame: _PendingFrame,
    packet: object,
    lap_decoder: LapDataDecoder,
    telemetry_decoder: CarTelemetryDecoder,
    motion_decoder: MotionDecoder,
    audit: _SessionAudit,
    decode_errors: Counter[str],
) -> None:
    header = packet.header
    kind = packet.packet_kind
    if frame.player is None:
        frame.player = header.player_car_index
    elif frame.player != header.player_car_index:
        frame.player_conflict = True
        audit.player_index_conflicts += 1
    if kind is PacketId.LAP_DATA:
        result = lap_decoder.decode(packet)
        if result.error is not None:
            decode_errors[result.error] += 1
            return
        if result.lap_data is None:
            return
        signature = (
            header.player_car_index,
            result.lap_data.time_trial_pb_car_index,
            result.lap_data.time_trial_rival_car_index,
            tuple(
                (
                    car.lap_distance_m,
                    car.current_lap_time_ms,
                    car.last_lap_time_ms,
                    car.current_lap_number,
                )
                for car in result.lap_data.cars
            ),
        )
        if frame.laps is not None and signature != frame.lap_signature:
            frame.lap_conflict = True
            audit.lap_index_conflicts += 1
            return
        frame.lap_signature = signature
        frame.session_time = header.session_time
        frame.player = header.player_car_index
        frame.pb = result.lap_data.time_trial_pb_car_index
        frame.rival = result.lap_data.time_trial_rival_car_index
        frame.car_count = len(result.lap_data.cars)
        frame.laps = [
            (
                car.lap_distance_m,
                car.total_distance_m,
                car.current_lap_time_ms,
                car.last_lap_time_ms,
                car.current_lap_number,
                car.sector_id,
                car.current_lap_invalid_id,
                car.result_status_id,
                car.driver_status_id,
                car.speed_trap_fastest_speed_kph,
            )
            for car in result.lap_data.cars
        ]
    elif kind is PacketId.CAR_TELEMETRY:
        result = telemetry_decoder.decode(packet)
        if result.error is not None:
            decode_errors[result.error] += 1
            return
        if result.telemetry is None:
            return
        compact = [
            (car.speed_kph, car.throttle, car.brake, car.gear) for car in result.telemetry.cars
        ]
        if frame.telemetry is not None and compact != frame.telemetry:
            frame.telemetry_conflict = True
            audit.telemetry_conflicts += 1
            frame.telemetry = None
            return
        frame.telemetry = compact
    elif kind is PacketId.MOTION:
        result = motion_decoder.decode(packet)
        if result.error is not None:
            decode_errors[result.error] += 1
            return
        if result.motion is None:
            return
        compact_motion: list[tuple[float, float, float] | None] = []
        for car in result.motion.cars:
            position = car.world_position_m
            compact_motion.append(None if position is None else (position[0], position[1], position[2]))
        if frame.motion is not None and compact_motion != frame.motion:
            frame.motion_conflict = True
            audit.motion_conflicts += 1
            frame.motion = None
            return
        frame.motion = compact_motion


def _apply_frame(audit: _SessionAudit, frame: _PendingFrame) -> None:
    if frame.lap_conflict or frame.player_conflict or frame.laps is None:
        return
    audit.lap_packets += 1
    if frame.session_time is not None:
        if audit.session_time_min is None or frame.session_time < audit.session_time_min:
            audit.session_time_min = frame.session_time
        if audit.session_time_max is None or frame.session_time > audit.session_time_max:
            audit.session_time_max = frame.session_time
    player = frame.player if frame.player is not None and 0 <= frame.player < frame.car_count else None
    selection = (player, frame.pb, frame.rival)
    if selection != audit.last_selection:
        audit.selection_change_count += 1
        if len(audit.selection_changes) < 32:
            audit.selection_changes.append(
                {
                    "session_time_s": frame.session_time,
                    "overall_frame": frame.frame,
                    "player_index": player,
                    "pb_index": frame.pb,
                    "rival_index": frame.rival,
                }
            )
        if audit.latest_rival is not None and 0 <= audit.latest_rival < frame.car_count:
            audit.previous_rival_slots.add(audit.latest_rival)
        if audit.latest_pb is not None and 0 <= audit.latest_pb < frame.car_count:
            audit.previous_pb_slots.add(audit.latest_pb)
        audit.last_selection = selection
    audit.latest_pb = frame.pb
    audit.latest_rival = frame.rival
    audit.roles["player"].observe_index(player, car_count=frame.car_count, player_index=None)
    audit.roles["pb"].observe_index(frame.pb, car_count=frame.car_count, player_index=player)
    audit.roles["rival"].observe_index(frame.rival, car_count=frame.car_count, player_index=player)
    if (
        frame.pb is not None
        and frame.rival is not None
        and frame.pb != INVALID_CAR_INDEX
        and frame.pb == frame.rival
    ):
        audit.roles["pb"].index_equals_other += 1
        audit.roles["rival"].index_equals_other += 1
    _observe_active_bound(audit.roles["pb"], frame.pb, audit.active_car_count, frame.car_count)
    _observe_active_bound(audit.roles["rival"], frame.rival, audit.active_car_count, frame.car_count)
    for car_index, lap in enumerate(frame.laps):
        telemetry = _at(frame.telemetry, car_index)
        motion = _at(frame.motion, car_index)
        _observe_slot(audit, car_index, lap, telemetry, motion, frame)
        if player is not None and car_index == player:
            _observe_role_sample(audit.roles["player"], lap, telemetry, motion, frame, other=None)
        if _selected(frame.pb, car_index, frame.car_count) and car_index != player:
            _observe_role_sample(audit.roles["pb"], lap, telemetry, motion, frame, other=player)
        if _selected(frame.rival, car_index, frame.car_count) and car_index != player:
            _observe_role_sample(audit.roles["rival"], lap, telemetry, motion, frame, other=player)


def _observe_slot(
    audit: _SessionAudit,
    car_index: int,
    lap: tuple[object, ...],
    telemetry: tuple[int, float, float, int] | None,
    motion: tuple[float, float, float] | None,
    frame: _PendingFrame,
) -> None:
    slot = audit.slot(car_index)
    slot.lap_frames += 1
    distance = float(lap[0])
    slot.distance.add(distance)
    if slot.last_distance is not None and abs(distance - slot.last_distance) > 0.5:
        slot.distance_changes += 1
    slot.last_distance = distance
    if int(lap[7]) >= 2:
        slot.result_active_frames += 1
    speed = 0 if telemetry is None else int(telemetry[0])
    if speed > slot.speed_max:
        slot.speed_max = speed
    if speed > MOVING_SPEED_KPH:
        slot.moving_frames += 1
    if motion is not None:
        slot.motion.add(motion[0])
        slot.motion_z.add(motion[2])
    if (
        car_index in audit.previous_rival_slots
        and car_index != frame.rival
        and speed > MOVING_SPEED_KPH
    ):
        audit.stale_rival_movement_frames += 1
    if car_index in audit.previous_pb_slots and car_index != frame.pb and speed > MOVING_SPEED_KPH:
        audit.stale_pb_movement_frames += 1


def _observe_role_sample(
    role: _RoleStats,
    lap: tuple[object, ...],
    telemetry: tuple[int, float, float, int] | None,
    motion: tuple[float, float, float] | None,
    frame: _PendingFrame,
    other: int | None,
) -> None:
    distance = float(lap[0])
    role.distance.add(distance)
    previous_distance = role.last_distance
    previous_time = role.last_session_time
    if role.last_distance is not None:
        delta = distance - role.last_distance
        if delta > 0.5:
            role.forward_distance_m += delta
            role.forward_frames += 1
        elif delta < -0.5:
            role.backward_distance_m += -delta
            role.backward_frames += 1
            if delta < -1000.0:
                role.large_backward_frames += 1
    role.last_distance = distance
    if frame.session_time is not None:
        role.last_session_time = float(frame.session_time)
    if len(lap) > 9:
        trap = float(lap[9])
        if trap > role.speed_trap_max_kph:
            role.speed_trap_max_kph = trap
    lap_number = int(lap[4])
    role.lap_numbers.add(lap_number)
    per_lap = role.per_lap.setdefault(
        lap_number, {"min_m": distance, "max_m": distance, "samples": 0}
    )
    per_lap["samples"] = int(per_lap["samples"]) + 1
    if distance < float(per_lap["min_m"]):
        per_lap["min_m"] = distance
    if distance > float(per_lap["max_m"]):
        per_lap["max_m"] = distance
    role.result_status[int(lap[7])] += 1
    role.driver_status[int(lap[8])] += 1
    role.current_lap_invalid[int(lap[6])] += 1
    last_lap = int(lap[3])
    if last_lap > 0:
        role.last_lap_time_positive_frames += 1
        role.last_lap_time.add(float(last_lap))
    if telemetry is not None:
        role.telemetry_frames += 1
        speed, throttle, brake, gear = telemetry
        role.speed.add(float(speed))
        role.speed_samples += 1
        if speed >= 360:
            role.speed_over_360_frames += 1
        if speed >= 400:
            role.speed_over_400_frames += 1
        if previous_distance is not None and previous_time is not None and frame.session_time is not None:
            dt = float(frame.session_time) - previous_time
            actual_m = distance - previous_distance
            if 0.0 < dt <= 0.1 and abs(actual_m) <= 200.0:
                predicted_m = (float(speed) / 3.6) * dt
                role.kinematic_frames += 1
                role.kinematic_abs_error_m += abs(actual_m - predicted_m)
                if abs(actual_m - predicted_m) > max(1.0, 0.5 * abs(predicted_m)):
                    role.kinematic_mismatch_frames += 1
        if brake > 0.05:
            role.brake_positive_frames += 1
        if brake > role.brake_max:
            role.brake_max = float(brake)
        if throttle > 0.05:
            role.throttle_positive_frames += 1
        if throttle > role.throttle_max:
            role.throttle_max = float(throttle)
        role.gears.add(int(gear))
    if motion is not None:
        role.motion_frames += 1
        role.motion_x.add(motion[0])
        role.motion_z.add(motion[2])
    if telemetry is not None and motion is not None:
        role.synced_frames += 1
    if other is None or frame.telemetry is None or telemetry is None:
        return
    other_telemetry = _at(frame.telemetry, other)
    if other_telemetry is None:
        return
    if speed > MOVING_SPEED_KPH and other_telemetry[0] > MOVING_SPEED_KPH:
        role.both_moving_frames += 1
        if speed == other_telemetry[0]:
            role.equal_speed_frames += 1
    other_motion = _at(frame.motion, other) if frame.motion is not None else None
    if motion is not None and other_motion is not None:
        separation = abs(motion[0] - other_motion[0]) + abs(motion[2] - other_motion[2])
        if separation > 2.0:
            role.position_separated_frames += 1


def _observe_context(audit: _SessionAudit, context: dict[str, object]) -> None:
    key = (
        context.get("packet_format"),
        context.get("session_type_id"),
        context.get("session_type"),
        context.get("game_mode_id"),
        context.get("game_mode"),
        context.get("rule_set_id"),
        context.get("rule_set"),
        context.get("track_id"),
        context.get("track_name"),
        context.get("track_length_m"),
        context.get("formula_id"),
    )
    if key == audit.context_key:
        return
    audit.context_key = key
    if len(audit.contexts) < 16:
        audit.contexts.append(
            {
                "packet_format": context.get("packet_format"),
                "session_type_id": context.get("session_type_id"),
                "session_type": context.get("session_type"),
                "game_mode_id": context.get("game_mode_id"),
                "game_mode": context.get("game_mode"),
                "rule_set_id": context.get("rule_set_id"),
                "rule_set": context.get("rule_set"),
                "track_id": context.get("track_id"),
                "track_name": context.get("track_name"),
                "track_length_m": context.get("track_length_m"),
                "formula_id": context.get("formula_id"),
            }
        )


def _observe_participants(audit: _SessionAudit, participants: object) -> None:
    audit.active_car_count = int(participants.active_car_count)
    for car_index, participant in enumerate(participants.cars):
        name = participant.name.strip()
        identity = {
            "ai_controlled": bool(participant.ai_controlled),
            "driver_id": int(participant.driver_id),
            "network_id": int(participant.network_id),
            "team_id": int(participant.team_id),
            "race_number": int(participant.race_number),
            "name_present": bool(name),
            "name_sha256": hashlib.sha256(name.encode("utf-8")).hexdigest() if name else None,
        }
        previous = audit.participants.get(car_index)
        if previous is not None and previous != identity:
            audit.participant_changes[car_index] += 1
        audit.participants[car_index] = identity


def _observe_history(audit: _SessionAudit, decoder: SessionHistoryDecoder, packet: object, errors: Counter[str]) -> None:
    decoded = decoder.decode(packet, frame_ordinal=0)
    if decoded.error is not None or decoded.history is None:
        errors[decoded.error or "session_history_unavailable"] += 1
        if packet.body:
            audit.history_slot(packet.body[0]).decode_errors += 1
        return
    history = decoded.history
    stats = audit.history_slot(history.car_index)
    stats.packets += 1
    if history.num_laps > stats.max_num_laps:
        stats.max_num_laps = history.num_laps
    if history.car_index == history.header_player_car_index:
        stats.matched_player_header += 1
    for lap in history.lap_history:
        if lap.lap_time_ms > 0:
            stats.positive_lap_rows += 1
            if len(stats.positive_lap_times) < 32:
                stats.positive_lap_times.add(lap.lap_time_ms)


def _observe_time_trial(audit: _SessionAudit, packet_format: int, body: bytes) -> None:
    parsed = parse_time_trial_body(packet_format, body)
    datasets = parsed.get("datasets")
    if not isinstance(datasets, list):
        reason = str(parsed.get("reason") or "unparsed")
        for stats in audit.timing.values():
            stats.packets += 1
            stats.unparsed[reason] += 1
        return
    for dataset in datasets:
        assert isinstance(dataset, dict)
        stats = audit.timing[str(dataset["name"])]
        stats.packets += 1
        car_index = int(dataset["car_index"])
        stats.car_indices[car_index] += 1
        stats.team_ids[int(dataset["team_id"])] += 1
        lap_time = int(dataset["lap_time_ms"])
        if int(dataset["valid"]) == 1:
            stats.valid += 1
        else:
            stats.invalid += 1
        if lap_time > 0:
            stats.positive_lap_times += 1
            stats.lap_time.add(float(lap_time))
            if int(dataset["valid"]) == 1:
                stats.valid_positive_lap_times += 1
        compared = audit.latest_rival if dataset["name"] == "rival" else audit.latest_pb
        if dataset["name"] == "player_session_best":
            compared = None
        if dataset["name"] == "player_session_best":
            continue
        if compared is None:
            stats.no_lap_index_yet += 1
        elif compared == car_index:
            stats.agrees_with_lap_index += 1
        else:
            stats.disagrees_with_lap_index += 1


def _session_dict(uid: int, audit: _SessionAudit) -> dict[str, object]:
    track_length = None
    if audit.contexts:
        value = audit.contexts[-1].get("track_length_m")
        if isinstance(value, int):
            track_length = value
    roles = {name: role.to_dict() for name, role in audit.roles.items()}
    rival_history = 0
    for car_index in audit.roles["rival"].selected_indices:
        rival_history += audit.history.get(car_index, _HistoryStats()).positive_lap_rows
    conclusion = conclude_rival(
        roles["rival"],
        packet14_valid_lap_times=audit.timing["rival"].valid_positive_lap_times,
        history_positive_lap_times=rival_history,
        track_length_m=track_length,
    )
    interesting_slots = {
        str(car_index): slot.to_dict()
        for car_index, slot in sorted(audit.slots.items())
        if slot.moving_frames or slot.distance.span() > 1.0 or slot.result_active_frames
    }
    return {
        "session_uid": str(uid),
        "session_time_start_s": audit.session_time_min,
        "session_time_end_s": audit.session_time_max,
        "contexts": audit.contexts,
        "lap_packets": audit.lap_packets,
        "selection_change_count": audit.selection_change_count,
        "selection_transitions": max(0, audit.selection_change_count - 1),
        "selection_changes_capped": audit.selection_changes,
        "lap_index_conflicts": audit.lap_index_conflicts,
        "telemetry_conflicts": audit.telemetry_conflicts,
        "motion_conflicts": audit.motion_conflicts,
        "player_index_conflicts": audit.player_index_conflicts,
        "late_lap_after_flush": audit.late_lap_after_flush,
        "flushed_without_telemetry": audit.flushed_without_telemetry,
        "flushed_without_motion": audit.flushed_without_motion,
        "stale_rival_movement_frames": audit.stale_rival_movement_frames,
        "stale_pb_movement_frames": audit.stale_pb_movement_frames,
        "active_car_count": audit.active_car_count,
        "participant_changes": _counter_dict(audit.participant_changes),
        "participants": {str(index): value for index, value in sorted(audit.participants.items())},
        "roles": roles,
        "time_trial_packet": {name: stats.to_dict() for name, stats in audit.timing.items()},
        "session_history": {
            str(index): stats.to_dict() for index, stats in sorted(audit.history.items()) if stats.packets or stats.decode_errors
        },
        "active_or_moving_slots": interesting_slots,
        "rival_conclusion": conclusion,
    }


def _observe_active_bound(
    role: _RoleStats, index: int | None, active_count: int | None, car_count: int
) -> None:
    if index is None or index == INVALID_CAR_INDEX or not 0 <= index < car_count:
        return
    if active_count is not None and index >= active_count:
        role.outside_active_count += 1


def _selected(index: int | None, car_index: int, car_count: int) -> bool:
    return index is not None and index != INVALID_CAR_INDEX and 0 <= index < car_count and index == car_index


def _at(values: list | None, index: int) -> object | None:
    if values is None or not 0 <= index < len(values):
        return None
    return values[index]


def _counter_dict(counter: Counter) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda item: str(item[0]))}


def _public_document(document: Mapping[str, object]) -> dict[str, object]:
    public: dict[str, object] = {}
    for key, value in document.items():
        if "host" in key or "path" in key or "token" in key:
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            public[str(key)] = value
        elif isinstance(value, str) and len(value) <= 80 and "\\" not in value and "://" not in value:
            public[str(key)] = value
    return public


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    reports = [audit_capture(path) for path in args.captures]
    payload = reports[0] if len(reports) == 1 else {"captures": reports}
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    if args.json_out is not None:
        args.json_out.write_text(encoded + "\n", encoding="utf-8")
    else:
        sys.stdout.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
