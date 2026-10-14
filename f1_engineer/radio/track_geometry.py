from __future__ import annotations

import json
import math
import re
import time
from copy import deepcopy
from .spoken_numbers import region_name_key

from .regions import RegionWindow


def geometry_binding(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("status") != "available":
        return None
    for key in ("generation", "session_id"):
        if not isinstance(snapshot.get(key), str) or re.fullmatch(r"[a-f0-9]{32}", snapshot[key]) is None:
            return None
    scope = snapshot.get("scope")
    context = snapshot.get("context")
    if (not isinstance(scope, dict) or set(scope) != {"session_uid", "packet_format", "epoch"}
            or any(type(scope.get(key)) is not int for key in scope)
            or not 1 <= scope["session_uid"] <= 2**64 - 1
            or not 0 <= scope["epoch"] <= 2**64 - 1 or scope["packet_format"] not in (2025, 2026)
            or not isinstance(context, dict) or context.get("status") != "available"
            or not isinstance(context.get("value"), dict)):
        return None
    value = context["value"]
    length = value.get("track_length_m")
    if (any(type(value.get(key)) is not int or value[key] != scope[key]
            for key in ("session_uid", "packet_format"))
            or type(value.get("track_id")) is not int or not 0 <= value["track_id"] <= 127
            or type(length) not in (int, float) or not math.isfinite(length) or not 0 < length <= 20_000):
        return None
    return json.dumps([snapshot["generation"], snapshot["session_id"], scope,
                       value["track_id"], length], sort_keys=True, separators=(",", ":"))


class RadioTrackGeometry:
    def __init__(self, client, *, clock=time.monotonic):
        self.client = client
        self.clock = clock
        self.binding = None
        self.result = None
        self.retry_at = 0.0

    def poll(self, snapshot):
        binding = geometry_binding(snapshot)
        age = snapshot.get("age_s") if isinstance(snapshot, dict) else None
        if (binding is None or type(age) not in (int, float)
                or not math.isfinite(age) or not 0 <= age < 3):
            self.binding, self.result, self.retry_at = None, None, 0.0
            return []
        if binding != self.binding:
            self.binding, self.result, self.retry_at = binding, None, 0.0
        if self.result is None or (self.result.get("status") != "available" and self.clock() >= self.retry_at):
            self.result = self.client.track_models(snapshot["session_id"])
            self.retry_at = self.clock() + 30
        return [{"kind": "track_geometry", "binding": binding, "result": deepcopy(self.result)}]


def _name(value):
    return region_name_key(value)


def resolve_named_region(result, binding, name):
    message = "No unambiguous reviewed corner geometry is available. Choose explicit start and end distances in metres."
    if not isinstance(result, dict) or result.get("status") != "available":
        raise ValueError(message)
    data = result.get("data")
    try:
        generation, session_id, scope, track_id, length = json.loads(binding)
    except (ValueError, TypeError, RecursionError):
        raise ValueError(message) from None
    if (not isinstance(scope, dict) or type(scope.get("packet_format")) is not int
            or scope["packet_format"] not in (2025, 2026) or not isinstance(session_id, str)
            or type(track_id) is not int or type(length) not in (int, float)
            or not math.isfinite(length) or not 0 < length <= 20_000):
        raise ValueError(message)
    if (not isinstance(data, dict) or data.get("session_id") != session_id
            or not isinstance(data.get("models"), list) or len(data["models"]) > 16):
        raise ValueError(message)
    models = [model for model in data["models"] if isinstance(model, dict) and model.get("origin") == "reviewed"]
    if len(models) != 1:
        raise ValueError(message)
    model = models[0]
    review = model.get("review")
    if (model.get("validation_status") != "validated" or model.get("source_kind") != "review_bundle"
            or model.get("approved_for_candidate_ranking") is not True
            or model.get("diagnostic_only") is not True or model.get("coaching_eligible") is not False
            or model.get("ranking_eligible") is not False
            or type(model.get("track_id")) is not int or model["track_id"] != track_id
            or type(model.get("packet_format")) is not int or model["packet_format"] != scope["packet_format"]
            or type(model.get("track_length_m")) not in (int, float) or model["track_length_m"] != length
            or not isinstance(review, dict) or review.get("scope") != "distance_region_measurements"
            or review.get("model_fingerprint_version") != "track-model-canonical-v1"
            or not isinstance(model.get("model_id"), str) or not 1 <= len(model["model_id"]) <= 128
            or type(model.get("revision")) is not int or model["revision"] < 1
            or any(not isinstance(model.get(key), str) or re.fullmatch(r"[a-f0-9]{64}", model[key]) is None
                   for key in ("model_content_sha256", "bundle_content_sha256"))
            or not isinstance(model.get("regions"), list) or not 1 <= len(model["regions"]) <= 64
            or type(model.get("region_count")) is not int or model["region_count"] != len(model["regions"])):
        raise ValueError(message)
    target = _name(name)
    matches = []
    for region in model["regions"]:
        if (not isinstance(region, dict) or any(not isinstance(region.get(key), str)
                or not 1 <= len(region[key]) <= 128 for key in ("id", "label"))):
            raise ValueError(message)
        aliases = {_name(region.get("id")), _name(region.get("label"))}
        if target and target in aliases:
            matches.append(region)
    if len(matches) != 1:
        raise ValueError(message)
    region = matches[0]
    window = RegionWindow(region.get("start_distance_m"), region.get("end_distance_m"), length)
    return window, f"Reviewed model {model['model_id']} revision {model['revision']}, region {region['id']}"
