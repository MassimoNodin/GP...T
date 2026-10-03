from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


ValidationStatus = Literal["draft", "validated"]
CornerDirection = Literal["left", "right"]
MAX_TRACK_MODEL_REGIONS = 64


@dataclass(frozen=True, slots=True)
class CornerDefinition:
    identifier: str
    label: str
    start_distance_m: float
    end_distance_m: float
    braking_search_window_m: tuple[float, float] | None = None
    turn_in_search_window_m: tuple[float, float] | None = None
    throttle_pickup_window_m: tuple[float, float] | None = None
    nominal_apex_m: float | None = None
    exit_distance_m: float | None = None
    direction: CornerDirection | None = None
    complex_id: str | None = None

    def validate(self, track_length_m: float, distance_origin_m: float) -> None:
        if not self.identifier.strip() or not self.label.strip():
            raise ValueError("corner identifier and label must not be empty")
        start = self.start_distance_m + distance_origin_m
        end = self.end_distance_m + distance_origin_m
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= track_length_m:
            raise ValueError(f"corner {self.identifier!r} has invalid analysis bounds")
        for name, window in (
            ("braking", self.braking_search_window_m),
            ("turn-in", self.turn_in_search_window_m),
            ("throttle-pickup", self.throttle_pickup_window_m),
        ):
            if window is None:
                continue
            window_start, window_end = window
            if (
                not math.isfinite(window_start)
                or not math.isfinite(window_end)
                or window_start < self.start_distance_m
                or window_end > self.end_distance_m
                or window_start >= window_end
            ):
                raise ValueError(
                    f"corner {self.identifier!r} has invalid {name} search bounds"
                )
        if self.nominal_apex_m is not None and not (
            math.isfinite(self.nominal_apex_m)
            and self.start_distance_m <= self.nominal_apex_m < self.end_distance_m
        ):
            raise ValueError(f"corner {self.identifier!r} has an invalid nominal apex")
        if self.exit_distance_m is not None and not (
            math.isfinite(self.exit_distance_m)
            and self.start_distance_m <= self.exit_distance_m < self.end_distance_m
        ):
            raise ValueError(f"corner {self.identifier!r} has an invalid exit distance")
        if self.direction not in (None, "left", "right"):
            raise ValueError(f"corner {self.identifier!r} has an invalid direction")


@dataclass(frozen=True, slots=True)
class TrackModel:
    model_id: str
    revision: int
    packet_format: int
    track_id: int
    track_name: str
    layout_id: str
    track_length_m: float
    distance_origin_m: float
    provenance: str
    validation_status: ValidationStatus
    corners: tuple[CornerDefinition, ...]

    def __post_init__(self) -> None:
        if len(self.corners) > MAX_TRACK_MODEL_REGIONS:
            raise ValueError("region_count_limit_exceeded")
        if not self.model_id.strip() or not self.track_name.strip() or not self.layout_id.strip():
            raise ValueError("track model identity fields must not be empty")
        if self.revision < 1:
            raise ValueError("track model revision must be positive")
        if self.packet_format < 1 or self.track_id < 0:
            raise ValueError("track packet format and ID are invalid")
        if not math.isfinite(self.track_length_m) or self.track_length_m <= 0:
            raise ValueError("track length must be finite and greater than zero")
        if (
            not math.isfinite(self.distance_origin_m)
            or self.distance_origin_m < 0
            or self.distance_origin_m >= self.track_length_m
        ):
            raise ValueError("track distance origin is outside the lap")
        if not self.provenance.strip():
            raise ValueError("track model provenance must not be empty")
        if self.validation_status not in ("draft", "validated"):
            raise ValueError("track model validation status must be draft or validated")
        previous_start = -math.inf
        prior_corners: list[CornerDefinition] = []
        identifiers: set[str] = set()
        for corner in self.corners:
            corner.validate(self.track_length_m, self.distance_origin_m)
            if corner.identifier in identifiers:
                raise ValueError(f"duplicate corner identifier {corner.identifier!r}")
            identifiers.add(corner.identifier)
            if corner.start_distance_m < previous_start:
                raise ValueError("corner regions must be ordered by start distance")
            for previous in prior_corners:
                overlaps = (
                    corner.start_distance_m < previous.end_distance_m
                    and previous.start_distance_m < corner.end_distance_m
                )
                if overlaps and (
                    not previous.complex_id
                    or previous.complex_id != corner.complex_id
                ):
                    raise ValueError("overlapping corner regions must share a complex ID")
            previous_start = corner.start_distance_m
            prior_corners.append(corner)
