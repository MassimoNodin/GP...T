from __future__ import annotations

import asyncio
import builtins

import pytest

import f1_engineer.storage.artifacts as artifacts
from f1_engineer.storage.artifacts import (
    RunArtifactInventoryUnavailable,
    list_processing_run_artifacts,
)
from f1_engineer.storage.database import Database


RUN_ID = "a" * 64
CAPTURE_SHA256 = "b" * 64
SESSION_KEY = f"{RUN_ID}:123"
ATTEMPT_KEY = f"{SESSION_KEY}:0:1"


def _get_api(app, path: str, params=None):
    httpx = pytest.importorskip("httpx")

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get(path, params=params)

    return asyncio.run(request())


def _seed_artifacts(database_path, *, trace_path: str | None = None) -> None:
    with Database(database_path) as database:
        with database.connection:
            connection = database.connection
            connection.execute(
                """INSERT INTO captures(capture_sha256, source_path, byte_size,
                       complete, metadata_json) VALUES (?, ?, ?, ?, ?)""",
                (CAPTURE_SHA256, "capture.f1ecap", 100, 0, "{}"),
            )
            connection.execute(
                """INSERT INTO processing_runs(run_id, capture_sha256,
                       pipeline_version, config_json, status) VALUES (?, ?, ?, ?, ?)""",
                (RUN_ID, CAPTURE_SHA256, "test", "{}", "complete"),
            )
            connection.execute(
                """INSERT INTO sessions(session_key, run_id, session_uid,
                       packet_format, context_json) VALUES (?, ?, ?, ?, ?)""",
                (SESSION_KEY, RUN_ID, "123", 2025, "{}"),
            )
            connection.execute(
                """INSERT INTO lap_attempts(attempt_key, session_key, car_index,
                       attempt_number, lap_number, disposition, start_frame_identifier,
                       start_session_time_s, start_observed, pit_encountered,
                       sample_count, reference_eligible, exclusion_reasons_json,
                       attempt_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ATTEMPT_KEY,
                    SESSION_KEY,
                    0,
                    1,
                    1,
                    "completed",
                    1,
                    0.0,
                    1,
                    0,
                    2,
                    0,
                    "[]",
                    "{}",
                ),
            )
            connection.execute(
                """INSERT INTO telemetry_files(attempt_key, relative_path,
                       schema_version, row_count, sha256, quality_json, ready)
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    ATTEMPT_KEY,
                    trace_path or f"{database_path.name}.traces/{RUN_ID}/trace.parquet",
                    4,
                    2,
                    "c" * 64,
                    "{}",
                    1,
                ),
            )
            connection.execute(
                """INSERT INTO car_observation_chunks(chunk_key, session_key,
                       packet_format, lifecycle_epoch, chunk_ordinal, relative_path,
                       schema_version, row_count, sha256, quality_json, ready)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "chunk-0",
                    SESSION_KEY,
                    2025,
                    0,
                    0,
                    f"{database_path.name}.traces/{RUN_ID}/observations.parquet",
                    1,
                    2,
                    "d" * 64,
                    "{}",
                    0,
                ),
            )


def test_inventory_is_exact_registered_run_ordered_and_paged(tmp_path) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    trace_dir = database_path.parent / f"{database_path.name}.traces" / RUN_ID
    trace_dir.mkdir(parents=True)
    (trace_dir / "trace.parquet").write_bytes(b"trace bytes")

    page = list_processing_run_artifacts(database_path, RUN_ID, limit=1)

    assert page is not None
    assert page["total"] == 2
    assert page["has_more"] is True
    assert page["items"][0]["artifact_kind"] == "player_trace"
    assert page["items"][0]["filesystem_availability"] == "present"
    assert page["items"][0]["observed_size_bytes"] == 11
    assert page["items"][0]["checksum_verification"] == "not_performed"
    assert "relative_path" not in page["items"][0]

    second = list_processing_run_artifacts(database_path, RUN_ID, limit=1, offset=1)
    assert second is not None
    assert second["items"][0]["artifact_kind"] == "car_observation_chunk"
    assert second["items"][0]["registration_readiness"] == "not_ready"
    assert second["items"][0]["filesystem_availability"] == "missing"
    assert second["has_more"] is False

    traces = list_processing_run_artifacts(
        database_path, RUN_ID, kind="player_trace", limit=50, offset=0
    )
    assert traces is not None
    assert traces["total"] == 1
    assert [item["artifact_kind"] for item in traces["items"]] == ["player_trace"]
    assert list_processing_run_artifacts(database_path, "e" * 64) is None


def test_processing_and_failed_runs_can_have_a_valid_empty_inventory(tmp_path) -> None:
    database_path = tmp_path / "empty.sqlite3"
    empty_run_id = "f" * 64
    empty_capture_sha = "9" * 64
    with Database(database_path) as database:
        with database.connection:
            database.connection.execute(
                """INSERT INTO captures(capture_sha256, source_path, byte_size,
                       complete, metadata_json) VALUES (?, ?, ?, ?, ?)""",
                (empty_capture_sha, "empty.f1ecap", 0, 0, "{}"),
            )
            database.connection.execute(
                """INSERT INTO processing_runs(run_id, capture_sha256,
                       pipeline_version, config_json, status) VALUES (?, ?, ?, ?, ?)""",
                (empty_run_id, empty_capture_sha, "test", "{}", "processing"),
            )

    processing = list_processing_run_artifacts(database_path, empty_run_id)
    assert processing is not None
    assert processing["processing_status"] == "processing"
    assert processing["total"] == 0
    assert processing["items"] == []

    with Database(database_path) as database:
        with database.connection:
            database.connection.execute(
                "UPDATE processing_runs SET status = 'failed' WHERE run_id = ?",
                (empty_run_id,),
            )
    failed = list_processing_run_artifacts(database_path, empty_run_id)
    assert failed is not None
    assert failed["processing_status"] == "failed"
    assert failed["total"] == 0


def test_invalid_registered_path_stays_visible_without_filesystem_escape(tmp_path) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path, trace_path="../outside.parquet")

    page = list_processing_run_artifacts(database_path, RUN_ID, kind="player_trace")

    assert page is not None
    assert page["total"] == 1
    item = page["items"][0]
    assert item["filesystem_availability"] == "unavailable"
    assert item["filesystem_reason"] == "registered_path_invalid"
    assert "registered_path_invalid" in item["metadata_reasons"]


def test_malformed_optional_metadata_does_not_hide_registration(tmp_path) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    with Database(database_path) as database:
        with database.connection:
            database.connection.execute("PRAGMA ignore_check_constraints = ON")
            database.connection.execute(
                "UPDATE telemetry_files SET ready = 3, sha256 = 'not-a-hash'"
            )

    page = list_processing_run_artifacts(database_path, RUN_ID, kind="player_trace")

    assert page is not None
    assert len(page["items"]) == 1
    assert page["items"][0]["registration_readiness"] == "unknown"
    assert page["items"][0]["stored_sha256"] is None
    assert "registration_readiness_invalid" in page["items"][0]["metadata_reasons"]
    assert "stored_sha256_invalid" in page["items"][0]["metadata_reasons"]


def test_invalid_utf8_metadata_becomes_unknown_without_failing_page(tmp_path) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    with Database(database_path) as database:
        database.connection.execute("PRAGMA foreign_keys = OFF")
        with database.connection:
            database.connection.execute("PRAGMA ignore_check_constraints = ON")
            database.connection.execute(
                "UPDATE processing_runs SET capture_sha256 = CAST(X'80' AS TEXT), status = CAST(X'80' AS TEXT)"
            )
            database.connection.execute(
                "UPDATE sessions SET session_uid = CAST(X'80' AS TEXT)"
            )
            database.connection.execute(
                "UPDATE telemetry_files SET relative_path = CAST(X'80' AS TEXT), sha256 = CAST(X'80' AS TEXT)"
            )

    page = list_processing_run_artifacts(database_path, RUN_ID, kind="player_trace")

    assert page is not None
    assert page["capture_sha256"] is None
    assert page["processing_status"] is None
    item = page["items"][0]
    assert item["session_uid"] is None
    assert item["stored_sha256"] is None
    assert item["filesystem_availability"] == "unavailable"
    assert "registered_path_invalid" in item["metadata_reasons"]


def test_non_integer_sqlite_metadata_is_sanitized_before_page_transfer(tmp_path) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    with Database(database_path) as database:
        with database.connection:
            database.connection.execute("PRAGMA ignore_check_constraints = ON")
            database.connection.execute(
                "UPDATE lap_attempts SET attempt_number = zeroblob(1000000), car_index = zeroblob(1000000)"
            )
            database.connection.execute(
                "UPDATE telemetry_files SET schema_version = zeroblob(1000000), row_count = zeroblob(1000000), ready = zeroblob(1000000)"
            )
            database.connection.execute(
                "UPDATE car_observation_chunks SET packet_format = zeroblob(1000000), lifecycle_epoch = zeroblob(1000000), chunk_ordinal = zeroblob(1000000), schema_version = zeroblob(1000000), row_count = zeroblob(1000000), ready = zeroblob(1000000)"
            )

    page = list_processing_run_artifacts(database_path, RUN_ID)

    assert page is not None
    assert page["total"] == 2
    assert len(str(page).encode("utf-8")) < 16_384
    trace, observation = page["items"]
    assert trace["attempt_number"] is None
    assert trace["car_index"] is None
    assert trace["schema_version"] is None
    assert trace["row_count"] is None
    assert trace["registration_readiness"] == "unknown"
    assert observation["packet_format"] is None
    assert observation["lifecycle_epoch"] is None
    assert observation["chunk_ordinal"] is None
    assert observation["schema_version"] is None
    assert observation["row_count"] is None
    assert observation["registration_readiness"] == "unknown"


def test_inventory_cap_is_preflighted_before_count_and_page(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    monkeypatch.setattr(artifacts, "MAX_RUN_ARTIFACTS", 1)

    with pytest.raises(RunArtifactInventoryUnavailable) as error:
        list_processing_run_artifacts(database_path, RUN_ID)

    assert error.value.reason_code == "processing_run_artifact_inventory_limit_exceeded"


def test_inventory_page_byte_cap_abstains_instead_of_returning_partial_metadata(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    monkeypatch.setattr(artifacts, "MAX_RUN_ARTIFACT_PAGE_BYTES", 1)

    with pytest.raises(RunArtifactInventoryUnavailable) as error:
        list_processing_run_artifacts(database_path, RUN_ID)

    assert error.value.reason_code == "processing_run_artifact_page_size_limit_exceeded"


def test_filesystem_observations_do_not_read_registered_file_contents(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    trace_dir = database_path.parent / f"{database_path.name}.traces" / RUN_ID
    trace_dir.mkdir(parents=True)
    (trace_dir / "trace.parquet").write_bytes(b"must not be opened")

    def fail_read(*_args, **_kwargs):
        raise AssertionError("artifact inventory read file contents")

    monkeypatch.setattr(artifacts.Path, "open", fail_read)
    monkeypatch.setattr(artifacts.Path, "read_bytes", fail_read)
    monkeypatch.setattr(artifacts.Path, "read_text", fail_read)
    monkeypatch.setattr(builtins, "open", fail_read)
    monkeypatch.setattr(artifacts.Path, "iterdir", fail_read)
    monkeypatch.setattr(artifacts.os, "scandir", fail_read)

    page = list_processing_run_artifacts(database_path, RUN_ID)

    assert page is not None
    assert page["items"][0]["filesystem_availability"] == "present"


def test_registered_file_permission_error_is_explicit(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    trace_dir = database_path.parent / f"{database_path.name}.traces" / RUN_ID
    trace_dir.mkdir(parents=True)
    trace_path = trace_dir / "trace.parquet"
    trace_path.write_bytes(b"trace")
    real_stat = artifacts.os.stat

    def stat(path, *, follow_symlinks=True):
        if artifacts.Path(path) == trace_path:
            raise PermissionError
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(artifacts.os, "stat", stat)

    page = list_processing_run_artifacts(database_path, RUN_ID, kind="player_trace")

    assert page is not None
    assert page["items"][0]["filesystem_availability"] == "unavailable"
    assert page["items"][0]["filesystem_reason"] == "permission_denied"


def test_internal_directory_reparse_point_is_not_followed(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(
        database_path,
        trace_path=f"{database_path.name}.traces/{RUN_ID}/nested/trace.parquet",
    )
    trace_dir = database_path.parent / f"{database_path.name}.traces" / RUN_ID
    nested = trace_dir / "nested"
    nested.mkdir(parents=True)
    (nested / "trace.parquet").write_bytes(b"trace")
    real_stat = artifacts.os.stat

    class ReparseDirectory:
        st_mode = artifacts.stat.S_IFDIR
        st_file_attributes = artifacts._REPARSE_POINT_ATTRIBUTE

    def stat(path, *, follow_symlinks=True):
        if artifacts.Path(path) == nested:
            assert follow_symlinks is False
            return ReparseDirectory()
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(artifacts.os, "stat", stat)

    page = list_processing_run_artifacts(database_path, RUN_ID, kind="player_trace")

    assert page is not None
    assert page["items"][0]["filesystem_availability"] == "unavailable"
    assert page["items"][0]["filesystem_reason"] == "artifact_path_reparse_point"


def test_reparse_point_namespace_is_not_followed(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    namespace = database_path.parent / f"{database_path.name}.traces"
    namespace.mkdir()
    real_stat = artifacts.os.stat

    class ReparseDirectory:
        st_mode = artifacts.stat.S_IFDIR
        st_file_attributes = artifacts._REPARSE_POINT_ATTRIBUTE

    def stat(path, *, follow_symlinks=True):
        if artifacts.Path(path) == namespace:
            assert follow_symlinks is False
            return ReparseDirectory()
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(artifacts.os, "stat", stat)

    page = list_processing_run_artifacts(database_path, RUN_ID, kind="player_trace")

    assert page is not None
    assert page["items"][0]["filesystem_availability"] == "unavailable"
    assert page["items"][0]["filesystem_reason"] == "trace_namespace_unavailable"


def test_count_and_page_are_one_database_snapshot(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    original_observe = artifacts._observe_artifact_file
    inserted = False

    def insert_after_snapshot(database, run_id, relative_path):
        nonlocal inserted
        if not inserted:
            inserted = True
            with Database(database) as writable:
                with writable.connection:
                    writable.connection.execute(
                        """INSERT INTO car_observation_chunks(chunk_key, session_key,
                               packet_format, lifecycle_epoch, chunk_ordinal, relative_path,
                               schema_version, row_count, sha256, quality_json, ready)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            "inserted-after-snapshot",
                            SESSION_KEY,
                            2025,
                            1,
                            0,
                            f"{database.name}.traces/{run_id}/late.parquet",
                            1,
                            0,
                            "e" * 64,
                            "{}",
                            0,
                        ),
                    )
        return original_observe(database, run_id, relative_path)

    monkeypatch.setattr(artifacts, "_observe_artifact_file", insert_after_snapshot)

    snapshot = list_processing_run_artifacts(database_path, RUN_ID)
    current = list_processing_run_artifacts(database_path, RUN_ID)

    assert snapshot is not None and current is not None
    assert snapshot["total"] == 2
    assert len(snapshot["items"]) == 2
    assert current["total"] == 3


def test_artifact_api_strictly_validates_query_and_returns_versioned_page(tmp_path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from f1_engineer.api.app import create_app

    database_path = tmp_path / "artifacts.sqlite3"
    _seed_artifacts(database_path)
    app = create_app(database_path)
    path = f"/api/v1/processing-runs/{RUN_ID}/artifacts"

    response = _get_api(app, path, params={"kind": "player_trace", "limit": "1"})
    assert response.status_code == 200
    body = response.json()
    assert body["api_version"] == "v1"
    assert body["status"] == "ok"
    assert body["data"]["query"] == {
        "kind": "player_trace",
        "limit": 1,
        "offset": 0,
    }
    assert body["data"]["total"] == 1

    duplicate = _get_api(app, path, params=[("limit", "1"), ("limit", "2")])
    assert duplicate.status_code == 422
    assert duplicate.json()["reason"] == "invalid_processing_run_artifact_repeated_parameter"

    unknown = _get_api(app, path, params={"path": "outside"})
    assert unknown.status_code == 422
    assert unknown.json()["reason"] == "invalid_processing_run_artifact_query_parameter"

    missing = _get_api(app, f"/api/v1/processing-runs/{'f' * 64}/artifacts")
    assert missing.status_code == 404
    assert missing.json()["reason"] == "processing_run_unavailable"

    unavailable = _get_api(
        create_app(tmp_path / "missing.sqlite3"),
        path,
    )
    assert unavailable.status_code == 200
    assert unavailable.json()["status"] == "unavailable"
    assert unavailable.json()["reason"] == "configured_database_unavailable"
