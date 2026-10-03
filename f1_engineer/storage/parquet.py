from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

TRACE_SCHEMA_VERSION = 3
SUPPORTED_TRACE_SCHEMA_VERSIONS = (1, 2, TRACE_SCHEMA_VERSION)
ROW_GROUP_SIZE = 4096


def _field(name: str, data_type: pa.DataType, *, unit: str | None = None) -> pa.Field:
    metadata = {b"unit": unit.encode("ascii")} if unit else None
    return pa.field(name, data_type, nullable=True, metadata=metadata)


_TRACE_V1_FIELDS = [
        _field("session_uid", pa.string()),
        _field("frame_identifier", pa.uint32()),
        _field("session_time_s", pa.float64(), unit="s"),
        _field("car_index", pa.uint8()),
        _field("attempt_id", pa.string()),
        _field("lap_number", pa.uint8()),
        _field("lap_distance_m", pa.float32(), unit="m"),
        _field("total_distance_m", pa.float32(), unit="m"),
        _field("current_lap_time_ms", pa.uint32(), unit="ms"),
        _field("speed_mps", pa.float32(), unit="m/s"),
        _field("throttle", pa.float32(), unit="ratio"),
        _field("brake", pa.float32(), unit="ratio"),
        _field("steering", pa.float32(), unit="ratio"),
        _field("gear", pa.int8()),
        _field("engine_rpm", pa.uint16(), unit="rpm"),
        _field("drs_active", pa.bool_()),
        _field("clutch_percent", pa.uint8(), unit="percent"),
        _field("rev_lights_percent", pa.uint8(), unit="percent"),
        _field("rev_lights_bit_value", pa.uint16()),
        _field("car_telemetry_available", pa.bool_()),
        _field("validation_flags", pa.list_(pa.string())),
    ]

TRACE_SCHEMA_V1 = pa.schema(
    _TRACE_V1_FIELDS,
    metadata={b"trace_schema_version": b"1"},
)
TRACE_SCHEMA_V2 = pa.schema(
    [
        *_TRACE_V1_FIELDS,
        _field("motion_available", pa.bool_()),
        _field("world_position_x_m", pa.float32(), unit="m"),
        _field("world_position_y_m", pa.float32(), unit="m"),
        _field("world_position_z_m", pa.float32(), unit="m"),
        _field("world_velocity_x_mps", pa.float32(), unit="m/s"),
        _field("world_velocity_y_mps", pa.float32(), unit="m/s"),
        _field("world_velocity_z_mps", pa.float32(), unit="m/s"),
        _field("world_forward_x", pa.float32()),
        _field("world_forward_y", pa.float32()),
        _field("world_forward_z", pa.float32()),
        _field("world_right_x", pa.float32()),
        _field("world_right_y", pa.float32()),
        _field("world_right_z", pa.float32()),
        _field("g_force_lateral", pa.float32(), unit="g"),
        _field("g_force_longitudinal", pa.float32(), unit="g"),
        _field("g_force_vertical", pa.float32(), unit="g"),
        _field("yaw_rad", pa.float32(), unit="rad"),
        _field("pitch_rad", pa.float32(), unit="rad"),
        _field("roll_rad", pa.float32(), unit="rad"),
    ],
    metadata={b"trace_schema_version": b"2"},
)
TRACE_SCHEMA = pa.schema(
    [
        *TRACE_SCHEMA_V2,
        _field("car_status_available", pa.bool_()),
        _field("car_status_unavailable_reason", pa.string()),
        _field("traction_control", pa.uint8()),
        _field("anti_lock_brakes", pa.bool_()),
        _field("fuel_mix", pa.uint8()),
        _field("front_brake_bias_percent", pa.uint8(), unit="percent"),
        _field("pit_limiter_active", pa.bool_()),
        _field("fuel_in_tank_reported", pa.float32()),
        _field("fuel_capacity_reported", pa.float32()),
        _field("fuel_remaining_laps", pa.float32(), unit="laps"),
        _field("actual_tyre_compound", pa.uint8()),
        _field("visual_tyre_compound", pa.uint8()),
        _field("tyre_age_laps", pa.uint8(), unit="laps"),
        _field("drs_allowed", pa.bool_()),
        _field("drs_activation_distance_m", pa.uint16(), unit="m"),
        _field("vehicle_fia_flag", pa.int8()),
        _field("network_paused", pa.bool_()),
    ],
    metadata={b"trace_schema_version": b"3"},
)


class ParquetTraceWriter:
    """Incrementally write one attempt with bounded row-group memory."""

    def __init__(self, path: str | Path, attempt_key: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.temporary = self.path.with_name(self.path.name + ".tmp")
        self.temporary.unlink(missing_ok=True)
        self.attempt_key = attempt_key
        self.writer = pq.ParquetWriter(
            self.temporary,
            TRACE_SCHEMA,
            compression="zstd",
            use_dictionary=["session_uid", "attempt_id"],
            write_statistics=True,
        )
        self._buffer: list[dict[str, Any]] = []
        self._sample_count = 0
        self._missing_telemetry_count = 0
        self._missing_motion_count = 0
        self._matched_car_status_count = 0
        self._missing_car_status_count = 0
        self._car_status_unavailable_reasons: dict[str, int] = {}
        self._valid_position_count = 0
        self._largest_frame_gap = 0
        self._distance_discontinuities = 0
        self._previous_frame: int | None = None
        self._previous_distance: float | None = None
        self._closed = False

    def write(self, record: dict[str, Any]) -> None:
        if self._closed:
            raise ValueError("Parquet trace writer is closed")
        item = dict(record)
        item["attempt_id"] = self.attempt_key
        frame = int(item["frame_identifier"])
        distance = item["lap_distance_m"]
        if self._previous_frame is not None:
            step = (frame - self._previous_frame) & 0xFFFFFFFF
            if 0 < step < 0x80000000:
                self._largest_frame_gap = max(self._largest_frame_gap, step - 1)
        if (
            distance is not None
            and self._previous_distance is not None
            and self._previous_distance - float(distance) > 100.0
        ):
            self._distance_discontinuities += 1
        self._previous_frame = frame
        self._previous_distance = float(distance) if distance is not None else None
        if not item["car_telemetry_available"]:
            self._missing_telemetry_count += 1
        if not item.get("motion_available", False):
            self._missing_motion_count += 1
        if item.get("car_status_available") is True:
            self._matched_car_status_count += 1
        else:
            self._missing_car_status_count += 1
            reason = item.get("car_status_unavailable_reason")
            reason_key = reason if isinstance(reason, str) and reason else "status_reason_unavailable"
            self._car_status_unavailable_reasons[reason_key] = (
                self._car_status_unavailable_reasons.get(reason_key, 0) + 1
            )
        if all(item.get(field) is not None for field in (
            "world_position_x_m", "world_position_y_m", "world_position_z_m"
        )):
            self._valid_position_count += 1
        self._sample_count += 1
        self._buffer.append(item)
        if len(self._buffer) >= ROW_GROUP_SIZE:
            self._flush_group()

    def _flush_group(self) -> None:
        if not self._buffer:
            return
        table = pa.Table.from_pylist(self._buffer, schema=TRACE_SCHEMA)
        self.writer.write_table(table, row_group_size=ROW_GROUP_SIZE)
        self._buffer.clear()

    def close(self) -> tuple[int, str, dict[str, Any]]:
        if self._closed:
            raise ValueError("Parquet trace writer is already closed")
        try:
            self._flush_group()
            self.writer.close()
            with self.temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(self.temporary, self.path)
            digest = _sha256(self.path)
            quality = {
                "sample_count": self._sample_count,
                "missing_car_telemetry_count": self._missing_telemetry_count,
                "missing_motion_count": self._missing_motion_count,
                "matched_car_status_count": self._matched_car_status_count,
                "missing_car_status_count": self._missing_car_status_count,
                "car_status_unavailable_reason_counts": self._car_status_unavailable_reasons,
                "valid_motion_position_count": self._valid_position_count,
                "largest_frame_gap": self._largest_frame_gap,
                "distance_discontinuities": self._distance_discontinuities,
            }
            self._closed = True
            return self._sample_count, digest, quality
        except Exception:
            self.abort()
            raise

    def abort(self) -> None:
        if self._closed:
            return
        try:
            self.writer.close()
        finally:
            self.temporary.unlink(missing_ok=True)
            self._closed = True


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_sha256 = sha256_file


def read_trace(
    path: str | Path | bytes,
    *,
    columns: list[str] | None = None,
    expected_schema_version: int | None = None,
    max_rows: int | None = None,
) -> tuple[pq.FileMetaData, pa.Table]:
    source = pa.BufferReader(path) if isinstance(path, bytes) else path
    parquet = pq.ParquetFile(source)
    metadata = parquet.metadata
    if max_rows is not None and metadata.num_rows > max_rows:
        raise ValueError("trace_row_limit_exceeded")
    raw_version = (metadata.metadata or {}).get(b"trace_schema_version")
    try:
        schema_version = int(raw_version) if raw_version is not None else 1
    except ValueError as exc:
        raise ValueError("trace has an invalid schema version") from exc
    if schema_version not in SUPPORTED_TRACE_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported trace schema version {schema_version}")
    if expected_schema_version is not None and schema_version != expected_schema_version:
        raise ValueError("trace schema version does not match SQLite")
    if columns is None:
        return metadata, parquet.read()

    unknown = set(columns) - set(TRACE_SCHEMA.names)
    if unknown:
        raise ValueError(f"unknown trace columns: {', '.join(sorted(unknown))}")
    available = set(parquet.schema_arrow.names)
    selected = [column for column in columns if column in available]
    table = parquet.read(columns=selected)
    for column in columns:
        if column in available:
            continue
        field = TRACE_SCHEMA.field(column)
        table = table.append_column(field, pa.nulls(table.num_rows, type=field.type))
    return metadata, table.select(columns)
