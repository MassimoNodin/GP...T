from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .geometry import (
    MAX_GEOMETRY_ANCHORS,
    MAX_GEOMETRY_MODEL_BYTES,
    MAX_GEOMETRY_SEGMENTS,
    GeometryAnchor,
    GeometryModel,
    GeometrySegment,
    GeometryValidationEvidence,
)


def load_geometry_model(path: str | Path) -> GeometryModel:
    source = Path(path)
    try:
        with source.open("rb") as stream:
            content = stream.read(MAX_GEOMETRY_MODEL_BYTES + 1)
        if len(content) > MAX_GEOMETRY_MODEL_BYTES:
            raise ValueError("geometry model exceeds the 8 MiB file limit")
        value = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load geometry model {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("geometry model root must be an object")
    if value.get("schema_version") != 1:
        raise ValueError(
            f"unsupported geometry model schema version {value.get('schema_version')!r}"
        )
    segment_values = value.get("segments")
    if not isinstance(segment_values, list):
        raise ValueError("geometry model segments must be an array")
    if len(segment_values) > MAX_GEOMETRY_SEGMENTS:
        raise ValueError("geometry model exceeds the 256 segment limit")
    anchor_count = 0
    for segment_value in segment_values:
        if not isinstance(segment_value, dict):
            raise ValueError("each geometry segment must be an object")
        raw_anchors = segment_value.get("anchors")
        if not isinstance(raw_anchors, list):
            raise ValueError("geometry segment anchors must be an array")
        anchor_count += len(raw_anchors)
        if anchor_count > MAX_GEOMETRY_ANCHORS:
            raise ValueError("geometry model exceeds the 20000 anchor limit")
    segments = tuple(_segment(item) for item in segment_values)
    try:
        return GeometryModel(
            model_id=_string(value, "model_id"),
            revision=_integer(value, "revision"),
            packet_format=_integer(value, "packet_format"),
            track_id=_integer(value, "track_id"),
            track_name=_string(value, "track_name"),
            layout_id=_string(value, "layout_id"),
            lap_length_m=_number(value, "lap_length_m"),
            game_distance_origin_m=_number(value, "game_distance_origin_m"),
            coordinate_frame=_string(value, "coordinate_frame"),
            coordinate_units=_string(value, "coordinate_units"),
            lateral_sign_convention=_string(value, "lateral_sign_convention"),
            cyclic_seam_policy=_string(value, "cyclic_seam_policy"),
            role=_string(value, "role"),
            provenance=_string(value, "provenance"),
            geometry_validation=_validation_evidence(
                value.get("geometry_validation"), "geometry_validation"
            ),
            calibration_validation=_validation_evidence(
                value.get("calibration_validation"), "calibration_validation"
            ),
            segments=segments,
            artifact_sha256=hashlib.sha256(content).hexdigest(),
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid geometry model {source}: {exc}") from exc


def _segment(value: Any) -> GeometrySegment:
    if not isinstance(value, dict):
        raise ValueError("each geometry segment must be an object")
    anchor_values = value.get("anchors")
    if not isinstance(anchor_values, list):
        raise ValueError("geometry segment anchors must be an array")
    anchors = tuple(_anchor(item) for item in anchor_values)
    return GeometrySegment(
        identifier=_string(value, "identifier"),
        anchors=anchors,
    )


def _anchor(value: Any) -> GeometryAnchor:
    if not isinstance(value, dict):
        raise ValueError("each geometry anchor must be an object")
    return GeometryAnchor(
        distance_m=_number(value, "distance_m"),
        x_m=_number(value, "x_m"),
        y_m=_number(value, "y_m"),
        z_m=_number(value, "z_m"),
    )


def _validation_evidence(value: Any, name: str) -> GeometryValidationEvidence:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    status = _string(value, "status")
    return GeometryValidationEvidence(
        status=status,
        reviewer=_optional_string(value.get("reviewer"), f"{name}.reviewer"),
        method=_optional_string(value.get("method"), f"{name}.method"),
        evidence_reference=_optional_string(
            value.get("evidence_reference"), f"{name}.evidence_reference"
        ),
    )


def _number(value: dict[str, Any], name: str) -> float:
    return _number_value(value.get(name), name)


def _number_value(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} is outside the finite numeric range") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _integer(value: dict[str, Any], name: str) -> int:
    result = value.get(name)
    if not isinstance(result, int) or isinstance(result, bool):
        raise ValueError(f"{name} must be an integer")
    return result


def _string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str):
        raise ValueError(f"{name} must be a string")
    return result


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value
