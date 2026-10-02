from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

TRACE_SCHEMA_VERSION = 1
ROW_GROUP_SIZE = 4096


def _field(name: str, data_type: pa.DataType, *, unit: str | None = None) -> pa.Field:
    metadata = {b"unit": unit.encode("ascii")} if unit else None
    return pa.field(name, data_type, nullable=True, metadata=metadata)


TRACE_SCHEMA = pa.schema(
    [
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
    ],
    metadata={b"trace_schema_version": str(TRACE_SCHEMA_VERSION).encode("ascii")},
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

    def close(self) -> tuple[int, str, dict[str, int]]:
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_trace(path: str | Path) -> tuple[pq.FileMetaData, pa.Table]:
    parquet = pq.ParquetFile(path)
    return parquet.metadata, parquet.read()
