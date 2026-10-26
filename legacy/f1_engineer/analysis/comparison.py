from __future__ import annotations

from dataclasses import dataclass

from .resampling import ResampledTrace


@dataclass(frozen=True, slots=True)
class DeltaTime:
    values_s: tuple[float | None, ...]
    mask: tuple[bool, ...]
    coverage: float
    first_supported_distance_m: float | None
    last_supported_distance_m: float | None
    observed_range_change_s: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "values_s": list(self.values_s),
            "mask": list(self.mask),
            "coverage": self.coverage,
            "first_supported_distance_m": self.first_supported_distance_m,
            "last_supported_distance_m": self.last_supported_distance_m,
            "observed_range_change_s": self.observed_range_change_s,
        }


@dataclass(frozen=True, slots=True)
class ChannelDifference:
    values: tuple[float | None, ...]
    mask: tuple[bool, ...]
    coverage: float

    def to_dict(self) -> dict[str, object]:
        return {
            "values": list(self.values),
            "mask": list(self.mask),
            "coverage": self.coverage,
        }


def calculate_delta_time(
    target: ResampledTrace, reference: ResampledTrace
) -> DeltaTime:
    if target.distance_m != reference.distance_m:
        raise ValueError("resampled traces must use the same distance grid")
    target_time = target.values["time_s"]
    reference_time = reference.values["time_s"]
    target_mask = target.masks["time_s"]
    reference_mask = reference.masks["time_s"]
    deltas: list[float | None] = []
    mask: list[bool] = []
    supported: list[int] = []
    for index, (target_value, reference_value) in enumerate(zip(target_time, reference_time)):
        available = target_mask[index] and reference_mask[index]
        if available and target_value is not None and reference_value is not None:
            deltas.append(float(target_value) - float(reference_value))
            mask.append(True)
            supported.append(index)
        else:
            deltas.append(None)
            mask.append(False)
    if supported:
        first_index = supported[0]
        last_index = supported[-1]
        observed_change = float(deltas[last_index]) - float(deltas[first_index])
        first_distance = target.distance_m[first_index]
        last_distance = target.distance_m[last_index]
    else:
        observed_change = None
        first_distance = None
        last_distance = None
    return DeltaTime(
        values_s=tuple(deltas),
        mask=tuple(mask),
        coverage=sum(mask) / len(mask),
        first_supported_distance_m=first_distance,
        last_supported_distance_m=last_distance,
        observed_range_change_s=observed_change,
    )


def calculate_channel_differences(
    target: ResampledTrace,
    reference: ResampledTrace,
    channels: tuple[str, ...] = ("speed_mps", "brake", "throttle", "steering"),
) -> dict[str, ChannelDifference]:
    if target.distance_m != reference.distance_m:
        raise ValueError("resampled traces must use the same distance grid")
    result: dict[str, ChannelDifference] = {}
    for channel in channels:
        target_values = target.values[channel]
        reference_values = reference.values[channel]
        target_mask = target.masks[channel]
        reference_mask = reference.masks[channel]
        values: list[float | None] = []
        mask: list[bool] = []
        for index, (target_value, reference_value) in enumerate(
            zip(target_values, reference_values)
        ):
            available = target_mask[index] and reference_mask[index]
            if available and target_value is not None and reference_value is not None:
                values.append(float(target_value) - float(reference_value))
                mask.append(True)
            else:
                values.append(None)
                mask.append(False)
        result[channel] = ChannelDifference(
            values=tuple(values),
            mask=tuple(mask),
            coverage=sum(mask) / len(mask),
        )
    return result
