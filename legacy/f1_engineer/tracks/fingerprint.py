from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from .model import TrackModel


TRACK_MODEL_FINGERPRINT_VERSION = "track-model-canonical-v1"


def track_model_fingerprint(model: TrackModel) -> str:
    """Fingerprint the canonical validated model using the D0032 algorithm."""
    payload = json.dumps(
        asdict(model), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
