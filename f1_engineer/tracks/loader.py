from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .model import CornerDefinition, MAX_TRACK_MODEL_REGIONS, TrackModel


MAX_TRACK_MODEL_JSON_BYTES = 1024 * 1024


def load_track_model(path: str | Path) -> TrackModel:
    model, _content_sha256 = load_track_model_with_checksum(path)
    return model


def load_track_model_with_checksum(path: str | Path) -> tuple[TrackModel, str]:
    """Load one bounded model and return the digest of the exact source bytes."""
    source = Path(path)
    try:
        with source.open("rb") as file:
            content = file.read(MAX_TRACK_MODEL_JSON_BYTES + 1)
        if len(content) > MAX_TRACK_MODEL_JSON_BYTES:
            raise ValueError("track_model_file_size_limit_exceeded")
        value = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load track model {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("track model root must be an object")
    if value.get("schema_version") != 1:
        raise ValueError(f"unsupported track model schema version {value.get('schema_version')!r}")
    corners_value = value.get("corners")
    if not isinstance(corners_value, list):
        raise ValueError("track model corners must be an array")
    if len(corners_value) > MAX_TRACK_MODEL_REGIONS:
        raise ValueError("region_count_limit_exceeded")
    corners = tuple(_corner(item) for item in corners_value)
    try:
        model = TrackModel(
            model_id=_string(value, "model_id"),
            revision=_integer(value, "revision"),
            packet_format=_integer(value, "packet_format"),
            track_id=_integer(value, "track_id"),
            track_name=_string(value, "track_name"),
            layout_id=_string(value, "layout_id"),
            track_length_m=_number(value, "track_length_m"),
            distance_origin_m=_number(value, "distance_origin_m"),
            provenance=_string(value, "provenance"),
            validation_status=_string(value, "validation_status"),
            corners=corners,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid track model {source}: {exc}") from exc
    return model, hashlib.sha256(content).hexdigest()


def _corner(value: Any) -> CornerDefinition:
    if not isinstance(value, dict):
        raise ValueError("each track corner must be an object")
    start = _number(value, "start_distance_m")
    end = _number(value, "end_distance_m")
    return CornerDefinition(
        identifier=_string(value, "identifier"),
        label=_string(value, "label"),
        start_distance_m=start,
        end_distance_m=end,
        braking_search_window_m=_window(value, "braking_search_window_m"),
        turn_in_search_window_m=_window(value, "turn_in_search_window_m"),
        throttle_pickup_window_m=_window(value, "throttle_pickup_window_m"),
        nominal_apex_m=_optional_number(value.get("nominal_apex_m")),
        exit_distance_m=_optional_number(value.get("exit_distance_m")),
        direction=value.get("direction"),
        complex_id=value.get("complex_id"),
    )


def _window(value: dict[str, Any], name: str) -> tuple[float, float] | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError(f"{name} must contain exactly two distances")
    return _number_value(raw[0], name), _number_value(raw[1], name)


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    return _number_value(value, "optional distance")


def _number(value: dict[str, Any], name: str) -> float:
    return _number_value(value.get(name), name)


def _number_value(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
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
