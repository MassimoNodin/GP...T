from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Literal


MAX_GEOMETRY_SEGMENTS = 256
MAX_GEOMETRY_ANCHORS = 20_000
MAX_GEOMETRY_MODEL_BYTES = 8 * 1024 * 1024
_DISTANCE_EPSILON_M = 1e-9
_TANGENT_EPSILON_M = 1e-6
_SEAM_POSITION_TOLERANCE_M = 1e-3
_SEAM_TANGENT_DOT_MINIMUM = 0.999999
_LATERAL_SIGN_CONVENTION = "positive_is_normal_(-tangent_z,+tangent_x)_in_world_xz"

GeometryRole = Literal["observed_reference_path", "reviewed_centreline"]
ValidationStatus = Literal["unreviewed", "validated"]
CyclicSeamPolicy = Literal["unsupported", "closed"]


@dataclass(frozen=True, slots=True)
class GeometryValidationEvidence:
    status: ValidationStatus
    reviewer: str | None
    method: str | None
    evidence_reference: str | None

    def __post_init__(self) -> None:
        if self.status not in ("unreviewed", "validated"):
            raise ValueError("geometry validation status must be unreviewed or validated")
        if self.status == "validated" and not all(
            _nonempty(value)
            for value in (self.reviewer, self.method, self.evidence_reference)
        ):
            raise ValueError(
                "validated geometry requires reviewer, method, and evidence reference"
            )


@dataclass(frozen=True, slots=True)
class GeometryAnchor:
    distance_m: float
    x_m: float
    y_m: float
    z_m: float

    def __post_init__(self) -> None:
        if not all(_finite_numeric(value) for value in (self.distance_m, self.x_m, self.y_m, self.z_m)):
            raise ValueError("geometry anchor values must be finite")


@dataclass(frozen=True, slots=True)
class GeometrySegment:
    identifier: str
    anchors: tuple[GeometryAnchor, ...]

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("geometry segment identifier must not be empty")
        if len(self.anchors) < 2:
            raise ValueError("geometry segments require at least two anchors")
        previous_distance = -math.inf
        for anchor in self.anchors:
            if anchor.distance_m <= previous_distance:
                raise ValueError(
                    "geometry anchor distances must be strictly increasing within a segment"
                )
            previous_distance = anchor.distance_m

    @property
    def start_distance_m(self) -> float:
        return self.anchors[0].distance_m

    @property
    def end_distance_m(self) -> float:
        return self.anchors[-1].distance_m


@dataclass(frozen=True, slots=True)
class GeometryModel:
    model_id: str
    revision: int
    packet_format: int
    track_id: int
    track_name: str
    layout_id: str
    lap_length_m: float
    game_distance_origin_m: float
    coordinate_frame: str
    coordinate_units: str
    lateral_sign_convention: str
    cyclic_seam_policy: CyclicSeamPolicy
    role: GeometryRole
    provenance: str
    geometry_validation: GeometryValidationEvidence
    calibration_validation: GeometryValidationEvidence
    segments: tuple[GeometrySegment, ...]
    artifact_sha256: str | None = None

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.model_id,
                self.track_name,
                self.layout_id,
                self.provenance,
            )
        ):
            raise ValueError("geometry model identity and provenance must not be empty")
        if self.revision < 1 or self.packet_format < 1 or self.track_id < 0:
            raise ValueError("geometry model version, format, or track ID is invalid")
        if not _finite_numeric(self.lap_length_m) or self.lap_length_m <= 0:
            raise ValueError("geometry lap length must be finite and positive")
        if (
            not _finite_numeric(self.game_distance_origin_m)
            or not 0 <= self.game_distance_origin_m < self.lap_length_m
        ):
            raise ValueError("game distance origin must be in [0, lap length)")
        if not math.isfinite(self.game_distance_origin_m + self.lap_length_m):
            raise ValueError("origin-shifted lap end must remain finite")
        if self.coordinate_frame != "world_xyz":
            raise ValueError("geometry coordinate frame must be world_xyz")
        if self.coordinate_units != "m":
            raise ValueError("geometry coordinate units must be metres")
        if self.lateral_sign_convention != _LATERAL_SIGN_CONVENTION:
            raise ValueError("unsupported geometry lateral sign convention")
        if self.cyclic_seam_policy not in ("unsupported", "closed"):
            raise ValueError("cyclic seam policy must be unsupported or closed")
        if self.role not in ("observed_reference_path", "reviewed_centreline"):
            raise ValueError("geometry role must be an observed path or reviewed centreline")
        if not self.segments or len(self.segments) > MAX_GEOMETRY_SEGMENTS:
            raise ValueError("geometry model must contain 1 to 256 supported segments")
        anchor_count = sum(len(segment.anchors) for segment in self.segments)
        if anchor_count > MAX_GEOMETRY_ANCHORS:
            raise ValueError("geometry model exceeds the 20000 anchor limit")
        identifiers: set[str] = set()
        previous_end = -math.inf
        for segment in self.segments:
            if segment.identifier in identifiers:
                raise ValueError("geometry segment identifiers must be unique")
            identifiers.add(segment.identifier)
            if (
                segment.start_distance_m < self.game_distance_origin_m
                or segment.end_distance_m
                > self.game_distance_origin_m + self.lap_length_m
            ):
                raise ValueError("geometry segment lies outside the origin-shifted lap range")
            if segment.start_distance_m < previous_end:
                raise ValueError("geometry segments must be ordered and non-overlapping")
            previous_end = segment.end_distance_m
        if self.cyclic_seam_policy == "closed" and not _has_closed_seam(self):
            raise ValueError(
                "closed cyclic seam requires supported, position- and tangent-continuous lap endpoints"
            )
        if self.artifact_sha256 is not None and (
            len(self.artifact_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.artifact_sha256)
        ):
            raise ValueError("geometry artifact checksum must be a lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class UnsupportedGeometryInterval:
    segment_id: str
    start_distance_m: float
    end_distance_m: float
    reason: str


def unsupported_projection_intervals(
    model: GeometryModel,
) -> tuple[UnsupportedGeometryInterval, ...]:
    """Return anchor brackets the point kernel cannot project through."""
    unsupported: list[UnsupportedGeometryInterval] = []
    for segment in model.segments:
        for lower, upper in zip(segment.anchors, segment.anchors[1:]):
            tangent_dx = upper.x_m - lower.x_m
            tangent_dz = upper.z_m - lower.z_m
            tangent_length = math.hypot(tangent_dx, tangent_dz)
            if not all(math.isfinite(value) for value in (tangent_dx, tangent_dz, tangent_length)):
                reason = "non_finite_geometry"
            elif tangent_length <= _TANGENT_EPSILON_M:
                reason = "degenerate_horizontal_tangent"
            else:
                continue
            unsupported.append(
                UnsupportedGeometryInterval(
                    segment_id=segment.identifier,
                    start_distance_m=lower.distance_m,
                    end_distance_m=upper.distance_m,
                    reason=reason,
                )
            )
    return tuple(unsupported)


def project_sample(
    model: GeometryModel,
    *,
    packet_format: int,
    track_id: int,
    layout_id: str,
    game_distance_m: float | None,
    world_x_m: float | None,
    world_y_m: float | None,
    world_z_m: float | None,
) -> dict[str, object]:
    """Project one sample without bridging unsupported geometry segments."""
    if packet_format != model.packet_format:
        return _unavailable(model, "packet_format_mismatch")
    if track_id != model.track_id:
        return _unavailable(model, "track_id_mismatch")
    if layout_id != model.layout_id:
        return _unavailable(model, "layout_mismatch")
    if game_distance_m is None:
        return _unavailable(model, "game_distance_unavailable")
    if any(value is None for value in (world_x_m, world_y_m, world_z_m)):
        return _unavailable(model, "position_unavailable")
    assert world_x_m is not None and world_y_m is not None and world_z_m is not None
    if not all(
        _finite_numeric(value)
        for value in (game_distance_m, world_x_m, world_y_m, world_z_m)
    ):
        return _unavailable(model, "non_finite_sample")
    if not 0 <= game_distance_m <= model.lap_length_m:
        return _unavailable(model, "game_distance_out_of_range")

    geometry_distance_m = game_distance_m + model.game_distance_origin_m
    if geometry_distance_m > model.game_distance_origin_m + model.lap_length_m:
        return _unavailable(model, "unsupported_distance")
    if _is_unsupported_lap_seam(model, geometry_distance_m):
        return _unavailable(model, "unsupported_lap_seam")
    if _is_unsupported_seam(model, geometry_distance_m):
        return _unavailable(model, "unsupported_segment_seam")
    segment = next(
        (
            candidate
            for candidate in model.segments
            if candidate.start_distance_m <= geometry_distance_m <= candidate.end_distance_m
        ),
        None,
    )
    if segment is None:
        return _unavailable(model, "unsupported_distance")

    upper_index = bisect.bisect_right(
        segment.anchors,
        geometry_distance_m,
        key=lambda anchor: anchor.distance_m,
    )
    if upper_index <= 0:
        lower, upper = segment.anchors[0], segment.anchors[1]
    elif upper_index >= len(segment.anchors):
        lower, upper = segment.anchors[-2], segment.anchors[-1]
    else:
        lower, upper = segment.anchors[upper_index - 1], segment.anchors[upper_index]
    distance_span = upper.distance_m - lower.distance_m
    if distance_span <= 0:
        return _unavailable(model, "ambiguous_anchor_distance")
    ratio = (geometry_distance_m - lower.distance_m) / distance_span
    reference_x = _lerp(lower.x_m, upper.x_m, ratio)
    reference_y = _lerp(lower.y_m, upper.y_m, ratio)
    reference_z = _lerp(lower.z_m, upper.z_m, ratio)
    if not all(math.isfinite(value) for value in (reference_x, reference_y, reference_z)):
        return _unavailable(model, "non_finite_geometry")

    tangent_dx = upper.x_m - lower.x_m
    tangent_dz = upper.z_m - lower.z_m
    tangent_length = math.hypot(tangent_dx, tangent_dz)
    if not all(math.isfinite(value) for value in (tangent_dx, tangent_dz, tangent_length)):
        return _unavailable(model, "non_finite_geometry")
    if tangent_length <= _TANGENT_EPSILON_M:
        return _unavailable(model, "degenerate_horizontal_tangent")
    tangent_x = tangent_dx / tangent_length
    tangent_z = tangent_dz / tangent_length
    normal_x = -tangent_z
    normal_z = tangent_x
    residual_x = world_x_m - reference_x
    residual_z = world_z_m - reference_z
    longitudinal_residual = residual_x * tangent_x + residual_z * tangent_z
    lateral_residual = residual_x * normal_x + residual_z * normal_z
    vertical_residual = world_y_m - reference_y
    if not all(
        math.isfinite(value)
        for value in (
            residual_x,
            residual_z,
            longitudinal_residual,
            lateral_residual,
            vertical_residual,
        )
    ):
        return _unavailable(model, "non_finite_projection")
    evidence_status = _evidence_status(model)
    return {
        "status": "supported",
        "reason": None,
        "model": _identity(model),
        "segment_id": segment.identifier,
        "game_distance_m": game_distance_m,
        "geometry_distance_m": geometry_distance_m,
        "reference_position_m": {
            "x": reference_x,
            "y": reference_y,
            "z": reference_z,
        },
        "local_tangent_xz": {"x": tangent_x, "z": tangent_z},
        "longitudinal_residual_m": longitudinal_residual,
        "lateral_residual_m": lateral_residual,
        "vertical_residual_m": vertical_residual,
        "geometry_evidence_status": evidence_status,
        "lap_reference_eligibility_evaluated": False,
    }


def _is_unsupported_seam(model: GeometryModel, distance_m: float) -> bool:
    for left, right in zip(model.segments, model.segments[1:]):
        if (
            left.end_distance_m == right.start_distance_m
            and math.isclose(
                distance_m,
                left.end_distance_m,
                rel_tol=0,
                abs_tol=_DISTANCE_EPSILON_M,
            )
        ):
            return True
    return False


def _is_unsupported_lap_seam(model: GeometryModel, distance_m: float) -> bool:
    if model.cyclic_seam_policy == "closed":
        return False
    lap_start = model.game_distance_origin_m
    lap_end = lap_start + model.lap_length_m
    return any(
        math.isclose(distance_m, endpoint, rel_tol=0, abs_tol=_DISTANCE_EPSILON_M)
        for endpoint in (lap_start, lap_end)
    )


def _has_closed_seam(model: GeometryModel) -> bool:
    lap_start = model.game_distance_origin_m
    lap_end = lap_start + model.lap_length_m
    start_segment = next(
        (segment for segment in model.segments if segment.start_distance_m == lap_start),
        None,
    )
    end_segment = next(
        (segment for segment in reversed(model.segments) if segment.end_distance_m == lap_end),
        None,
    )
    if start_segment is None or end_segment is None:
        return False
    start, start_next = start_segment.anchors[:2]
    end_previous, end = end_segment.anchors[-2:]
    position_gap = math.hypot(
        start.x_m - end.x_m,
        start.y_m - end.y_m,
        start.z_m - end.z_m,
    )
    if not math.isfinite(position_gap) or position_gap > _SEAM_POSITION_TOLERANCE_M:
        return False
    start_tangent = _unit_xz(start_next.x_m - start.x_m, start_next.z_m - start.z_m)
    end_tangent = _unit_xz(end.x_m - end_previous.x_m, end.z_m - end_previous.z_m)
    if start_tangent is None or end_tangent is None:
        return False
    dot = start_tangent[0] * end_tangent[0] + start_tangent[1] * end_tangent[1]
    return math.isfinite(dot) and dot >= _SEAM_TANGENT_DOT_MINIMUM


def _unit_xz(dx: float, dz: float) -> tuple[float, float] | None:
    length = math.hypot(dx, dz)
    if not math.isfinite(length) or length <= _TANGENT_EPSILON_M:
        return None
    return dx / length, dz / length


def _lerp(start: float, end: float, ratio: float) -> float:
    return start * (1.0 - ratio) + end * ratio


def _evidence_status(model: GeometryModel) -> str:
    if model.role != "reviewed_centreline":
        return "diagnostic_observed_reference_path"
    if model.geometry_validation.status != "validated":
        return "geometry_unreviewed"
    if model.calibration_validation.status != "validated":
        return "distance_calibration_unreviewed"
    return "reviewed_centreline"


def _unavailable(model: GeometryModel, reason: str) -> dict[str, object]:
    return {
        "status": "unavailable",
        "reason": reason,
        "model": _identity(model),
        "geometry_evidence_status": _evidence_status(model),
        "lap_reference_eligibility_evaluated": False,
    }


def _identity(model: GeometryModel) -> dict[str, object]:
    return {
        "model_id": model.model_id,
        "revision": model.revision,
        "packet_format": model.packet_format,
        "track_id": model.track_id,
        "track_name": model.track_name,
        "layout_id": model.layout_id,
        "role": model.role,
        "geometry_validation_status": model.geometry_validation.status,
        "calibration_validation_status": model.calibration_validation.status,
        "artifact_sha256": model.artifact_sha256,
        "provenance": model.provenance,
    }


def _nonempty(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite_numeric(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False
