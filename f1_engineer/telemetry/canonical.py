from __future__ import annotations

import math
from dataclasses import dataclass

from ..udp.lap_data import CarLapData
from ..udp.car_telemetry import CarTelemetryData


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
        **values,
    )


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None
