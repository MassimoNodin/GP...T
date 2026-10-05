from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .fingerprint import TRACK_MODEL_FINGERPRINT_VERSION, track_model_fingerprint
from .loader import MAX_TRACK_MODEL_JSON_BYTES, track_model_from_value
from .model import TrackModel


REVIEWED_TRACK_MODEL_BUNDLE_VERSION = "reviewed-distance-regions-v1"
REVIEWED_TRACK_MODEL_SCOPE = "distance_region_measurements"
MAX_REVIEWED_TRACK_MODELS = 16
MAX_REVIEWED_TRACK_MODEL_DIRECTORY_ENTRIES = 128
MAX_REVIEWED_TRACK_MODEL_TOTAL_BYTES = 16 * 1024 * 1024
MAX_REVIEW_JSON_BYTES = 16 * 1024
MAX_REVIEW_TEXT_LENGTH = 256
MAX_REVIEW_NOTES_LENGTH = 2048
MAX_REVIEW_EVIDENCE_REFERENCES = 16
MAX_REVIEWED_JSON_DEPTH = 32
MAX_REVIEWED_TRACK_MODEL_REVISION = 9_007_199_254_740_991
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


@dataclass(frozen=True, slots=True)
class ReviewEvidence:
    reference: str
    sha256: str | None


@dataclass(frozen=True, slots=True)
class ReviewProvenance:
    review_id: str
    reviewer: str
    reviewed_at_utc: str
    scope: str
    model_fingerprint_version: str
    evidence: tuple[ReviewEvidence, ...]
    notes: str

    def metadata(self) -> dict[str, object]:
        return {
            "review_id": self.review_id,
            "reviewer": self.reviewer,
            "reviewed_at_utc": self.reviewed_at_utc,
            "scope": self.scope,
            "model_fingerprint_version": self.model_fingerprint_version,
            "evidence": [
                {"reference": item.reference, "sha256": item.sha256}
                for item in self.evidence
            ],
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class ReviewedTrackModel:
    model: TrackModel
    model_content_sha256: str
    bundle_content_sha256: str
    source_filename: str
    review: ReviewProvenance


def load_reviewed_track_models(root_path: str | Path) -> tuple[ReviewedTrackModel, ...]:
    """Load a bounded, flat set of immutable review bundles from one root."""
    try:
        root = Path(root_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("reviewed_track_model_root_unavailable") from exc
    if not root.is_dir():
        raise ValueError("reviewed_track_model_root_must_be_a_directory")

    candidates: list[Path] = []
    try:
        for count, candidate in enumerate(root.iterdir(), start=1):
            if count > MAX_REVIEWED_TRACK_MODEL_DIRECTORY_ENTRIES:
                raise ValueError("reviewed_track_model_directory_entry_limit_exceeded")
            if candidate.name.casefold().endswith(".reviewed.json"):
                candidates.append(candidate)
    except OSError as exc:
        raise ValueError("reviewed_track_model_root_unavailable") from exc
    if len(candidates) > MAX_REVIEWED_TRACK_MODELS:
        raise ValueError("reviewed_track_model_count_limit_exceeded")

    loaded: list[ReviewedTrackModel] = []
    total_bytes = 0
    for candidate in sorted(candidates, key=lambda item: item.name.casefold()):
        try:
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ValueError("reviewed_track_model_path_outside_root")
            if not stat.S_ISREG(resolved.stat().st_mode):
                raise ValueError("reviewed_track_model_path_must_be_a_file")
            with resolved.open("rb") as source:
                content = source.read(MAX_TRACK_MODEL_JSON_BYTES + 1)
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError("reviewed_track_model_file_unavailable") from exc
        if len(content) > MAX_TRACK_MODEL_JSON_BYTES:
            raise ValueError("reviewed_track_model_file_size_limit_exceeded")
        total_bytes += len(content)
        if total_bytes > MAX_REVIEWED_TRACK_MODEL_TOTAL_BYTES:
            raise ValueError("reviewed_track_model_total_size_limit_exceeded")
        loaded.append(_parse_bundle(content, candidate.name))
    return tuple(loaded)


def validate_reviewed_track_model_bundle(path: str | Path) -> dict[str, object]:
    """Validate structure and fingerprint scope; this does not inspect geometry."""
    source = Path(path)
    try:
        with source.open("rb") as stream:
            content = stream.read(MAX_TRACK_MODEL_JSON_BYTES + 1)
    except OSError as exc:
        raise ValueError("reviewed_track_model_file_unavailable") from exc
    if len(content) > MAX_TRACK_MODEL_JSON_BYTES:
        raise ValueError("reviewed_track_model_file_size_limit_exceeded")
    bundle = _parse_bundle(content, source.name)
    return {
        "valid": True,
        "validation_scope": "bundle_structure_and_fingerprint_binding_only",
        "physical_geometry_verified": False,
        "model_id": bundle.model.model_id,
        "revision": bundle.model.revision,
        "origin": "reviewed",
        "model_content_sha256": bundle.model_content_sha256,
        "bundle_content_sha256": bundle.bundle_content_sha256,
        "review": bundle.review.metadata(),
    }


def _parse_bundle(content: bytes, filename: str) -> ReviewedTrackModel:
    try:
        text = content.decode("utf-8", errors="strict")
        _check_json_nesting(text)
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("reviewed_track_model_"):
            raise
        raise ValueError("reviewed_track_model_json_invalid") from exc
    _validate_json_values(value)
    _require_fields(value, {"schema_version", "bundle_version", "model", "review"}, "bundle")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("reviewed_track_model_schema_version_unsupported")
    if value["bundle_version"] != REVIEWED_TRACK_MODEL_BUNDLE_VERSION:
        raise ValueError("reviewed_track_model_bundle_version_unsupported")

    review_value = value["review"]
    review_keys = {
        "review_id",
        "reviewer",
        "reviewed_at_utc",
        "scope",
        "model_fingerprint_version",
        "model_content_sha256",
        "evidence",
        "notes",
    }
    _require_fields(review_value, review_keys, "review")
    try:
        review_bytes = json.dumps(
            review_value, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("reviewed_track_model_review_invalid") from exc
    if len(review_bytes) > MAX_REVIEW_JSON_BYTES:
        raise ValueError("reviewed_track_model_review_size_limit_exceeded")

    model = track_model_from_value(value["model"], strict_fields=True)
    _validate_model_identity_limits(model)
    if model.validation_status != "validated":
        raise ValueError("reviewed_track_model_must_be_validated")
    review = _parse_review(review_value)
    fingerprint = track_model_fingerprint(model)
    if review_value["model_content_sha256"] != fingerprint:
        raise ValueError("reviewed_track_model_fingerprint_mismatch")
    return ReviewedTrackModel(
        model=model,
        model_content_sha256=fingerprint,
        bundle_content_sha256=hashlib.sha256(content).hexdigest(),
        source_filename=filename,
        review=review,
    )


def _parse_review(value: Any) -> ReviewProvenance:
    if not isinstance(value, dict):
        raise ValueError("reviewed_track_model_review_invalid")
    review_id = _bounded_text(value, "review_id", MAX_REVIEW_TEXT_LENGTH)
    reviewer = _bounded_text(value, "reviewer", MAX_REVIEW_TEXT_LENGTH)
    reviewed_at_utc = _bounded_text(value, "reviewed_at_utc", 40)
    if not _UTC_TIMESTAMP_RE.fullmatch(reviewed_at_utc):
        raise ValueError("reviewed_track_model_review_timestamp_invalid")
    try:
        timestamp = datetime.fromisoformat(reviewed_at_utc[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("reviewed_track_model_review_timestamp_invalid") from exc
    if timestamp.tzinfo != timezone.utc:
        raise ValueError("reviewed_track_model_review_timestamp_invalid")
    if value["scope"] != REVIEWED_TRACK_MODEL_SCOPE:
        raise ValueError("reviewed_track_model_review_scope_unsupported")
    if value["model_fingerprint_version"] != TRACK_MODEL_FINGERPRINT_VERSION:
        raise ValueError("reviewed_track_model_fingerprint_version_unsupported")
    digest = value["model_content_sha256"]
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError("reviewed_track_model_fingerprint_invalid")
    raw_evidence = value["evidence"]
    if not isinstance(raw_evidence, list) or not 1 <= len(raw_evidence) <= MAX_REVIEW_EVIDENCE_REFERENCES:
        raise ValueError("reviewed_track_model_evidence_count_invalid")
    evidence: list[ReviewEvidence] = []
    for item in raw_evidence:
        _require_fields(item, {"reference", "sha256"}, "evidence")
        reference = _bounded_text(item, "reference", MAX_REVIEW_TEXT_LENGTH)
        evidence_digest = item["sha256"]
        if evidence_digest is not None and (
            not isinstance(evidence_digest, str)
            or not _SHA256_RE.fullmatch(evidence_digest)
        ):
            raise ValueError("reviewed_track_model_evidence_hash_invalid")
        evidence.append(ReviewEvidence(reference, evidence_digest))
    notes = value["notes"]
    if not isinstance(notes, str) or len(notes) > MAX_REVIEW_NOTES_LENGTH:
        raise ValueError("reviewed_track_model_notes_invalid")
    return ReviewProvenance(
        review_id=review_id,
        reviewer=reviewer,
        reviewed_at_utc=reviewed_at_utc,
        scope=REVIEWED_TRACK_MODEL_SCOPE,
        model_fingerprint_version=TRACK_MODEL_FINGERPRINT_VERSION,
        evidence=tuple(evidence),
        notes=notes,
    )


def _validate_model_identity_limits(model: TrackModel) -> None:
    identities = [model.model_id, model.track_name, model.layout_id]
    for corner in model.corners:
        identities.extend((corner.identifier, corner.label))
        if corner.complex_id is not None:
            identities.append(corner.complex_id)
    if model.revision > MAX_REVIEWED_TRACK_MODEL_REVISION:
        raise ValueError("reviewed_track_model_revision_limit_exceeded")
    if any(_utf16_length(value) > MAX_REVIEW_TEXT_LENGTH for value in identities):
        raise ValueError("reviewed_track_model_identity_string_limit_exceeded")


def _utf16_length(value: str) -> int:
    """Match JavaScript string.length, which counts UTF-16 code units."""
    return len(value.encode("utf-16-le")) // 2


def _bounded_text(value: Mapping[str, Any], key: str, max_length: int) -> str:
    item = value[key]
    if (
        not isinstance(item, str)
        or not item.strip()
        or _utf16_length(item) > max_length
    ):
        raise ValueError(f"reviewed_track_model_{key}_invalid")
    return item


def _require_fields(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"reviewed_track_model_{label}_fields_invalid")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("reviewed_track_model_duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("reviewed_track_model_non_finite_number")


def _check_json_nesting(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_REVIEWED_JSON_DEPTH:
                raise ValueError("reviewed_track_model_json_depth_limit_exceeded")
        elif char in "]}":
            depth -= 1


def _validate_json_values(value: Any) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("reviewed_track_model_malformed_unicode")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("reviewed_track_model_non_finite_number")
    elif isinstance(value, int) and not isinstance(value, bool):
        try:
            if not math.isfinite(float(value)):
                raise ValueError("reviewed_track_model_non_finite_number")
        except OverflowError as exc:
            raise ValueError("reviewed_track_model_non_finite_number") from exc
    elif isinstance(value, list):
        for item in value:
            _validate_json_values(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json_values(key)
            _validate_json_values(item)
