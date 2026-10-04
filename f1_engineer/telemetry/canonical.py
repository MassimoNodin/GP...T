from __future__ import annotations

import math
from dataclasses import dataclass

from ..udp.lap_data import CarLapData
from ..udp.car_telemetry import CarTelemetryData
from ..udp.motion import CarMotionData
from ..udp.car_status import CarStatusData
from ..udp.car_damage import CarDamageData


@dataclass(frozen=True, slots=True)
class CarSample:
    session_uid: int
    frame_identifier: int
    session_time_s: float
    car_index: int
    attempt_id: str
    lap_number: int
    lap_distance_m: float | None
    total_distance_m: float | None
    current_lap_time_ms: int
    speed_mps: float | None
    throttle: float | None
    brake: float | None
    steering: float | None
    gear: int | None
    engine_rpm: int | None
    drs_active: bool | None
    clutch_percent: int | None
    rev_lights_percent: int | None
    rev_lights_bit_value: int | None
    car_telemetry_available: bool
    validation_flags: tuple[str, ...]
    motion_available: bool = False
    world_position_x_m: float | None = None
    world_position_y_m: float | None = None
    world_position_z_m: float | None = None
    world_velocity_x_mps: float | None = None
    world_velocity_y_mps: float | None = None
    world_velocity_z_mps: float | None = None
    world_forward_x: float | None = None
    world_forward_y: float | None = None
    world_forward_z: float | None = None
    world_right_x: float | None = None
    world_right_y: float | None = None
    world_right_z: float | None = None
    g_force_lateral: float | None = None
    g_force_longitudinal: float | None = None
    g_force_vertical: float | None = None
    yaw_rad: float | None = None
    pitch_rad: float | None = None
    roll_rad: float | None = None
    car_status_available: bool = False
    car_status_unavailable_reason: str | None = None
    traction_control: int | None = None
    anti_lock_brakes: bool | None = None
    fuel_mix: int | None = None
    front_brake_bias_percent: int | None = None
    pit_limiter_active: bool | None = None
    fuel_in_tank_reported: float | None = None
    fuel_capacity_reported: float | None = None
    fuel_remaining_laps: float | None = None
    actual_tyre_compound: int | None = None
    visual_tyre_compound: int | None = None
    tyre_age_laps: int | None = None
    drs_allowed: bool | None = None
    drs_activation_distance_m: int | None = None
    vehicle_fia_flag: int | None = None
    network_paused: bool | None = None
    car_damage_available: bool = False
    car_damage_unavailable_reason: str | None = None
    tyre_wear_rl_percent: float | None = None
    tyre_wear_rr_percent: float | None = None
    tyre_wear_fl_percent: float | None = None
    tyre_wear_fr_percent: float | None = None
    tyre_damage_rl_percent: int | None = None
    tyre_damage_rr_percent: int | None = None
    tyre_damage_fl_percent: int | None = None
    tyre_damage_fr_percent: int | None = None
    brake_damage_rl_percent: int | None = None
    brake_damage_rr_percent: int | None = None
    brake_damage_fl_percent: int | None = None
    brake_damage_fr_percent: int | None = None
    tyre_blister_rl_percent: int | None = None
    tyre_blister_rr_percent: int | None = None
    tyre_blister_fl_percent: int | None = None
    tyre_blister_fr_percent: int | None = None
    front_left_wing_damage_percent: int | None = None
    front_right_wing_damage_percent: int | None = None
    rear_wing_damage_percent: int | None = None
    floor_damage_percent: int | None = None
    diffuser_damage_percent: int | None = None
    sidepod_damage_percent: int | None = None
    drs_fault: bool | None = None
    ers_fault: bool | None = None
    gearbox_damage_percent: int | None = None
    engine_damage_percent: int | None = None
    engine_mguh_wear_percent: int | None = None
    engine_es_wear_percent: int | None = None
    engine_ce_wear_percent: int | None = None
    engine_ice_wear_percent: int | None = None
    engine_mguk_wear_percent: int | None = None
    engine_tc_wear_percent: int | None = None
    engine_blown: bool | None = None
    engine_seized: bool | None = None

    def to_record(self) -> dict[str, object]:
        return {
            "session_uid": str(self.session_uid),
            "frame_identifier": self.frame_identifier,
            "session_time_s": self.session_time_s,
            "car_index": self.car_index,
            "attempt_id": self.attempt_id,
            "lap_number": self.lap_number,
            "lap_distance_m": self.lap_distance_m,
            "total_distance_m": self.total_distance_m,
            "current_lap_time_ms": self.current_lap_time_ms,
            "speed_mps": self.speed_mps,
            "throttle": self.throttle,
            "brake": self.brake,
            "steering": self.steering,
            "gear": self.gear,
            "engine_rpm": self.engine_rpm,
            "drs_active": self.drs_active,
            "clutch_percent": self.clutch_percent,
            "rev_lights_percent": self.rev_lights_percent,
            "rev_lights_bit_value": self.rev_lights_bit_value,
            "car_telemetry_available": self.car_telemetry_available,
            "validation_flags": list(self.validation_flags),
            "motion_available": self.motion_available,
            "world_position_x_m": self.world_position_x_m,
            "world_position_y_m": self.world_position_y_m,
            "world_position_z_m": self.world_position_z_m,
            "world_velocity_x_mps": self.world_velocity_x_mps,
            "world_velocity_y_mps": self.world_velocity_y_mps,
            "world_velocity_z_mps": self.world_velocity_z_mps,
            "world_forward_x": self.world_forward_x,
            "world_forward_y": self.world_forward_y,
            "world_forward_z": self.world_forward_z,
            "world_right_x": self.world_right_x,
            "world_right_y": self.world_right_y,
            "world_right_z": self.world_right_z,
            "g_force_lateral": self.g_force_lateral,
            "g_force_longitudinal": self.g_force_longitudinal,
            "g_force_vertical": self.g_force_vertical,
            "yaw_rad": self.yaw_rad,
            "pitch_rad": self.pitch_rad,
            "roll_rad": self.roll_rad,
            "car_status_available": self.car_status_available,
            "car_status_unavailable_reason": self.car_status_unavailable_reason,
            "traction_control": self.traction_control,
            "anti_lock_brakes": self.anti_lock_brakes,
            "fuel_mix": self.fuel_mix,
            "front_brake_bias_percent": self.front_brake_bias_percent,
            "pit_limiter_active": self.pit_limiter_active,
            "fuel_in_tank_reported": self.fuel_in_tank_reported,
            "fuel_capacity_reported": self.fuel_capacity_reported,
            "fuel_remaining_laps": self.fuel_remaining_laps,
            "actual_tyre_compound": self.actual_tyre_compound,
            "visual_tyre_compound": self.visual_tyre_compound,
            "tyre_age_laps": self.tyre_age_laps,
            "drs_allowed": self.drs_allowed,
            "drs_activation_distance_m": self.drs_activation_distance_m,
            "vehicle_fia_flag": self.vehicle_fia_flag,
            "network_paused": self.network_paused,
            "car_damage_available": self.car_damage_available,
            "car_damage_unavailable_reason": self.car_damage_unavailable_reason,
            "tyre_wear_rl_percent": self.tyre_wear_rl_percent,
            "tyre_wear_rr_percent": self.tyre_wear_rr_percent,
            "tyre_wear_fl_percent": self.tyre_wear_fl_percent,
            "tyre_wear_fr_percent": self.tyre_wear_fr_percent,
            "tyre_damage_rl_percent": self.tyre_damage_rl_percent,
            "tyre_damage_rr_percent": self.tyre_damage_rr_percent,
            "tyre_damage_fl_percent": self.tyre_damage_fl_percent,
            "tyre_damage_fr_percent": self.tyre_damage_fr_percent,
            "brake_damage_rl_percent": self.brake_damage_rl_percent,
            "brake_damage_rr_percent": self.brake_damage_rr_percent,
            "brake_damage_fl_percent": self.brake_damage_fl_percent,
            "brake_damage_fr_percent": self.brake_damage_fr_percent,
            "tyre_blister_rl_percent": self.tyre_blister_rl_percent,
            "tyre_blister_rr_percent": self.tyre_blister_rr_percent,
            "tyre_blister_fl_percent": self.tyre_blister_fl_percent,
            "tyre_blister_fr_percent": self.tyre_blister_fr_percent,
            "front_left_wing_damage_percent": self.front_left_wing_damage_percent,
            "front_right_wing_damage_percent": self.front_right_wing_damage_percent,
            "rear_wing_damage_percent": self.rear_wing_damage_percent,
            "floor_damage_percent": self.floor_damage_percent,
            "diffuser_damage_percent": self.diffuser_damage_percent,
            "sidepod_damage_percent": self.sidepod_damage_percent,
            "drs_fault": self.drs_fault,
            "ers_fault": self.ers_fault,
            "gearbox_damage_percent": self.gearbox_damage_percent,
            "engine_damage_percent": self.engine_damage_percent,
            "engine_mguh_wear_percent": self.engine_mguh_wear_percent,
            "engine_es_wear_percent": self.engine_es_wear_percent,
            "engine_ce_wear_percent": self.engine_ce_wear_percent,
            "engine_ice_wear_percent": self.engine_ice_wear_percent,
            "engine_mguk_wear_percent": self.engine_mguk_wear_percent,
            "engine_tc_wear_percent": self.engine_tc_wear_percent,
            "engine_blown": self.engine_blown,
            "engine_seized": self.engine_seized,
        }


def make_car_sample(
    *,
    session_uid: int,
    frame_identifier: int,
    session_time_s: float,
    car_index: int,
    attempt_id: str,
    lap: CarLapData,
    telemetry: CarTelemetryData | None,
    motion: CarMotionData | None = None,
    car_status: CarStatusData | None = None,
    car_status_unavailable_reason: str | None = None,
    car_damage: CarDamageData | None = None,
    car_damage_unavailable_reason: str | None = None,
) -> CarSample:
    flags: list[str] = []
    lap_distance = _finite(lap.lap_distance_m)
    total_distance = _finite(lap.total_distance_m)
    if lap_distance is None:
        flags.append("invalid_lap_distance")
    if total_distance is None:
        flags.append("invalid_total_distance")

    values: dict[str, object] = {
        "speed_mps": None,
        "throttle": None,
        "brake": None,
        "steering": None,
        "gear": None,
        "engine_rpm": None,
        "drs_active": None,
        "clutch_percent": None,
        "rev_lights_percent": None,
        "rev_lights_bit_value": None,
    }
    if telemetry is not None:
        checks = (
            ("speed_mps", telemetry.speed_kph / 3.6, 0.0, 500.0 / 3.6),
            ("throttle", telemetry.throttle, 0.0, 1.0),
            ("brake", telemetry.brake, 0.0, 1.0),
            ("steering", telemetry.steering, -1.0, 1.0),
        )
        for name, value, minimum, maximum in checks:
            normalized = _finite(float(value))
            if normalized is None or not minimum <= normalized <= maximum:
                flags.append(f"invalid_{name}")
            else:
                values[name] = normalized
        if -1 <= telemetry.gear <= 8:
            values["gear"] = telemetry.gear
        else:
            flags.append("invalid_gear")
        if 0 <= telemetry.engine_rpm <= 20_000:
            values["engine_rpm"] = telemetry.engine_rpm
        else:
            flags.append("invalid_engine_rpm")
        if telemetry.drs in (0, 1):
            values["drs_active"] = bool(telemetry.drs)
        else:
            flags.append("invalid_drs")
        if 0 <= telemetry.clutch <= 100:
            values["clutch_percent"] = telemetry.clutch
        else:
            flags.append("invalid_clutch")
        if 0 <= telemetry.rev_lights_percent <= 100:
            values["rev_lights_percent"] = telemetry.rev_lights_percent
        else:
            flags.append("invalid_rev_lights_percent")
        values["rev_lights_bit_value"] = telemetry.rev_lights_bit_value

    if motion is not None:
        flags.extend(motion.validation_flags)
        if motion.world_position_m is not None:
            values.update(zip(
                ("world_position_x_m", "world_position_y_m", "world_position_z_m"),
                motion.world_position_m,
            ))
        if motion.world_velocity_mps is not None:
            values.update(zip(
                ("world_velocity_x_mps", "world_velocity_y_mps", "world_velocity_z_mps"),
                motion.world_velocity_mps,
            ))
        if motion.world_forward is not None:
            values.update(zip(
                ("world_forward_x", "world_forward_y", "world_forward_z"),
                motion.world_forward,
            ))
        if motion.world_right is not None:
            values.update(zip(
                ("world_right_x", "world_right_y", "world_right_z"),
                motion.world_right,
            ))
        if motion.g_force is not None:
            values.update(zip(
                ("g_force_lateral", "g_force_longitudinal", "g_force_vertical"),
                motion.g_force,
            ))
        values["yaw_rad"] = motion.yaw_rad
        values["pitch_rad"] = motion.pitch_rad
        values["roll_rad"] = motion.roll_rad

    if car_status is not None:
        status_values = _canonical_car_status(car_status, flags)
        values.update(status_values)
    damage_values = _canonical_car_damage(car_damage, flags)
    values.update(damage_values)

    return CarSample(
        session_uid=session_uid,
        frame_identifier=frame_identifier,
        session_time_s=session_time_s,
        car_index=car_index,
        attempt_id=attempt_id,
        lap_number=lap.current_lap_number,
        lap_distance_m=lap_distance,
        total_distance_m=total_distance,
        current_lap_time_ms=lap.current_lap_time_ms,
        car_telemetry_available=telemetry is not None,
        validation_flags=tuple(flags),
        motion_available=motion is not None,
        car_status_available=car_status is not None,
        car_status_unavailable_reason=(
            None if car_status is not None else car_status_unavailable_reason
        ),
        car_damage_available=car_damage is not None,
        car_damage_unavailable_reason=(
            None if car_damage is not None else car_damage_unavailable_reason
        ),
        **values,
    )


def _canonical_car_status(
    status: CarStatusData, flags: list[str]
) -> dict[str, object]:
    values: dict[str, object] = {
        "traction_control": None,
        "anti_lock_brakes": None,
        "fuel_mix": None,
        "front_brake_bias_percent": None,
        "pit_limiter_active": None,
        "fuel_in_tank_reported": None,
        "fuel_capacity_reported": None,
        "fuel_remaining_laps": None,
        "actual_tyre_compound": status.actual_tyre_compound,
        "visual_tyre_compound": status.visual_tyre_compound,
        "tyre_age_laps": status.tyre_age_laps,
        "drs_allowed": None,
        "drs_activation_distance_m": status.drs_activation_distance_m,
        "vehicle_fia_flag": status.vehicle_fia_flag,
        "network_paused": None,
    }

    if 0 <= status.traction_control <= 2:
        values["traction_control"] = status.traction_control
    else:
        flags.append("invalid_car_status_traction_control")
    if status.anti_lock_brakes in (0, 1):
        values["anti_lock_brakes"] = bool(status.anti_lock_brakes)
    else:
        flags.append("invalid_car_status_anti_lock_brakes")
    if 0 <= status.fuel_mix <= 3:
        values["fuel_mix"] = status.fuel_mix
    else:
        flags.append("invalid_car_status_fuel_mix")
    if 0 <= status.front_brake_bias_percent <= 100:
        values["front_brake_bias_percent"] = status.front_brake_bias_percent
    else:
        flags.append("invalid_car_status_front_brake_bias_percent")
    if status.pit_limiter_active in (0, 1):
        values["pit_limiter_active"] = bool(status.pit_limiter_active)
    else:
        flags.append("invalid_car_status_pit_limiter_active")

    for field in ("fuel_in_tank_reported", "fuel_capacity_reported"):
        value = getattr(status, field)
        if math.isfinite(value) and value >= 0.0:
            values[field] = value
        else:
            flags.append(f"invalid_car_status_{field}")
    if math.isfinite(status.fuel_remaining_laps):
        values["fuel_remaining_laps"] = status.fuel_remaining_laps
    else:
        flags.append("invalid_car_status_fuel_remaining_laps")

    if status.vehicle_fia_flag == -1 or not 0 <= status.vehicle_fia_flag <= 3:
        # Preserve the signed raw value, including the documented unknown sentinel.
        flags.append("invalid_car_status_vehicle_fia_flag")

    if status.drs_allowed in (0, 1):
        values["drs_allowed"] = bool(status.drs_allowed)
    else:
        flags.append("invalid_car_status_drs_allowed")
    if status.network_paused in (0, 1):
        values["network_paused"] = bool(status.network_paused)
    else:
        flags.append("invalid_car_status_network_paused")
    return values


_DAMAGE_WHEEL_SUFFIXES = ("rl", "rr", "fl", "fr")
_DAMAGE_PERCENT_FIELDS = (
    "front_left_wing_damage_percent",
    "front_right_wing_damage_percent",
    "rear_wing_damage_percent",
    "floor_damage_percent",
    "diffuser_damage_percent",
    "sidepod_damage_percent",
    "gearbox_damage_percent",
    "engine_damage_percent",
    "engine_mguh_wear_percent",
    "engine_es_wear_percent",
    "engine_ce_wear_percent",
    "engine_ice_wear_percent",
    "engine_mguk_wear_percent",
    "engine_tc_wear_percent",
)
_DAMAGE_FLAG_FIELDS = ("drs_fault", "ers_fault", "engine_blown", "engine_seized")


def _canonical_car_damage(
    damage: CarDamageData | None, flags: list[str]
) -> dict[str, object]:
    values: dict[str, object] = {}
    for prefix in (
        "tyre_wear",
        "tyre_damage",
        "brake_damage",
        "tyre_blister",
    ):
        for suffix in _DAMAGE_WHEEL_SUFFIXES:
            values[f"{prefix}_{suffix}_percent"] = None
    for field in (*_DAMAGE_PERCENT_FIELDS, *_DAMAGE_FLAG_FIELDS):
        values[field] = None
    if damage is None:
        return values

    arrays = (
        ("tyre_wear", damage.tyres_wear_percent),
        ("tyre_damage", damage.tyres_damage_percent),
        ("brake_damage", damage.brakes_damage_percent),
        ("tyre_blister", damage.tyre_blisters_percent),
    )
    for prefix, array in arrays:
        for suffix, raw in zip(_DAMAGE_WHEEL_SUFFIXES, array):
            field = f"{prefix}_{suffix}_percent"
            if math.isfinite(float(raw)) and 0.0 <= raw <= 100.0:
                values[field] = float(raw) if prefix == "tyre_wear" else int(raw)
            else:
                flags.append(f"invalid_car_damage_{field}")

    raw_values = {
        field: getattr(damage, field)
        for field in _DAMAGE_PERCENT_FIELDS
    }
    for field, raw in raw_values.items():
        if 0 <= raw <= 100:
            values[field] = raw
        else:
            flags.append(f"invalid_car_damage_{field}")
    for field in _DAMAGE_FLAG_FIELDS:
        raw = getattr(damage, field)
        if raw in (0, 1):
            values[field] = bool(raw)
        else:
            flags.append(f"invalid_car_damage_{field}")
    return values


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None
