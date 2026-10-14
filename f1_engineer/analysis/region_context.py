from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field


_FIELDS = {"tyre_age_laps": (0, 255), "weather_id": (0, 5),
           "track_temperature_c": (-128, 127), "air_temperature_c": (-128, 127)}


def _valid(value, bounds):
    return type(value) is int and bounds[0] <= value <= bounds[1]


def regional_sample_context(context, player):
    tyre = player.get("tyre")
    tyre_value = (tyre.get("value") if isinstance(tyre, dict) and tyre.get("status") == "available" else None)
    values = {"tyre_age_laps": tyre_value.get("age_laps") if isinstance(tyre_value, dict) else None}
    metadata = context if isinstance(context, dict) else {}
    values.update({key: metadata.get(key) for key in _FIELDS if key != "tyre_age_laps"})
    return {key: value if _valid(value, _FIELDS[key]) else None for key, value in values.items()}


def scoped_region_context(snapshot):
    context = snapshot.get("context")
    value = (context.get("value") if isinstance(context, dict) and context.get("status") == "available" else None)
    scope = snapshot.get("scope")
    if (not isinstance(value, dict) or not isinstance(scope, dict)
            or any(type(value.get(key)) is not int or type(scope.get(key)) is not int
                   or value[key] != scope[key] for key in ("session_uid", "packet_format"))):
        return None
    return value


@dataclass(slots=True)
class RegionSampleContext:
    sample_count: int = 0
    ranges: dict = field(default_factory=lambda: {key: None for key in _FIELDS})

    def observe(self, values):
        self.sample_count += 1
        for key, bounds in _FIELDS.items():
            value = values.get(key) if isinstance(values, dict) else None
            previous = self.ranges[key]
            if self.sample_count == 1:
                self.ranges[key] = [value, value] if _valid(value, bounds) else None
            elif previous is not None:
                self.ranges[key] = ([min(previous[0], value), max(previous[1], value)]
                                    if _valid(value, bounds) else None)

    def view(self):
        return {"schema_version": 1, "source": "context_attached_to_retained_region_samples",
                "sample_count": self.sample_count, "reported_ranges": deepcopy(self.ranges),
                "continuous_context_verified": False, "diagnostic_only": True,
                "coaching_eligible": False}


def validate_region_context(value, sample_count):
    if (not isinstance(value, dict) or set(value) != {"schema_version", "source", "sample_count",
            "reported_ranges", "continuous_context_verified", "diagnostic_only", "coaching_eligible"}
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("source") != "context_attached_to_retained_region_samples"
            or type(value.get("sample_count")) is not int or value["sample_count"] != sample_count
            or not 2 <= value["sample_count"] <= 4096
            or value.get("continuous_context_verified") is not False
            or value.get("diagnostic_only") is not True or value.get("coaching_eligible") is not False
            or not isinstance(value.get("reported_ranges"), dict) or set(value["reported_ranges"]) != set(_FIELDS)):
        return None
    for key, bounds in _FIELDS.items():
        reported = value["reported_ranges"][key]
        if reported is not None and (type(reported) is not list or len(reported) != 2
                or any(not _valid(item, bounds) for item in reported) or reported[0] > reported[1]):
            return None
    return deepcopy(value)


def compare_reported_context(left, right):
    ranges = []
    for value in (left, right):
        ranges.append(value["reported_ranges"] if isinstance(value, dict) else {key: None for key in _FIELDS})
    available = [key for key in _FIELDS if all(value[key] is not None for value in ranges)]
    different = [key for key in available if ranges[0][key] != ranges[1][key]]
    labels = {"tyre_age_laps": "tyre ages", "weather_id": "weather codes",
              "track_temperature_c": "track temperatures", "air_temperature_c": "air temperatures"}
    text = ""
    if different:
        key = different[0]
        first, second = (value[key] for value in ranges)
        text = f"Reported {labels[key]} {first[0]} to {first[1]} versus {second[0]} to {second[1]}. "
    elif available:
        text = "Available reported context ranges match. "
    return text, {"player": deepcopy(left), "reference": deepcopy(right),
                  "different_reported_fields": different,
                  "missing_reported_fields": [key for key in _FIELDS if key not in available],
                  "continuous_equivalence_verified": False, "fuel_verified": False, "traffic_verified": False}
