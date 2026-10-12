from __future__ import annotations

import json

from fastapi.testclient import TestClient

from f1_engineer.api.app import create_app
from f1_engineer.processing.evidence import PROCESSOR_VERSION, EvidenceStore
from f1_engineer.tracks.registry import PACKAGED_TRACK_MODEL_CATALOG


def test_region_endpoint_keeps_drafts_diagnostic(tmp_path):
    app = create_app(tmp_path / "archive.sqlite3", automatic_acquisition=False)
    entry = next(iter(PACKAGED_TRACK_MODEL_CATALOG.entries.values()))
    model = entry.model
    with TestClient(app) as client:
        url = f"/api/v1/track-models/{model.model_id}/regions"
        response = client.get(url, params={"revision": model.revision})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["validation_status"] == "draft"
        assert data["diagnostic_only"] and not data["ranking_eligible"]
        assert len(data["regions"]) == len(model.corners)
        assert data["regions"][0]["start_distance_m"] == model.corners[0].start_distance_m
        assert client.get(url, params={"revision": 99}).status_code == 404
        assert client.get(url).status_code == 422


def test_session_model_lookup_requires_compatible_format_and_length(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    entry = next(iter(PACKAGED_TRACK_MODEL_CATALOG.entries.values()))
    model = entry.model
    with store.connect() as database:
        database.execute("INSERT INTO generations VALUES ('generation','test',?,'active',0,0)",
                         (PROCESSOR_VERSION,))
        for index, length in enumerate((model.track_length_m, model.track_length_m + 1), 1):
            context = json.dumps({"track_id": model.track_id, "packet_format": model.packet_format,
                                  "track_length_m": length})
            database.execute("INSERT INTO sessions VALUES (?,'generation',?,'active','receiving',?,1)",
                             (str(index), str(index), context))
    app = create_app(tmp_path / "archive.sqlite3", evidence_database_path=store.path,
                     automatic_acquisition=False)
    with TestClient(app) as client:
        url = "/api/v2/session-evidence/sessions"
        assert client.get(f"{url}/1/track-models").json()["data"]["models"][0]["model_id"] == model.model_id
        missing = client.get(f"{url}/2/track-models").json()["data"]
        assert missing["models"] == [] and missing["reason"] == "compatible_track_model_unavailable"
        assert client.get(f"{url}/missing/track-models").status_code == 404
