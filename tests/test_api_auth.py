from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from f1_engineer.api.app import create_app


TOKEN = "test-companion-" + "x" * 32


def _request(app, method, path, headers=None):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(request())


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/sessions"),
    ("GET", "/api/v2/session-evidence/status"),
    ("GET", "/docs"),
    ("GET", "/openapi.json"),
    ("POST", "/api/v1/recordings/start"),
    ("OPTIONS", "/api/v1/sessions"),
])
def test_companion_mode_requires_auth_before_routing(tmp_path, method, path):
    app = create_app(tmp_path / "archive.sqlite3", control_token=TOKEN,
                     require_auth=True, automatic_acquisition=False)
    response = _request(app, method, path)
    assert response.status_code == 403
    assert response.json()["reason"] == "api_not_authorized"


@pytest.mark.parametrize("authorization", [
    "Bearer wrong", "Basic " + TOKEN, "Bearer", "Bearer  " + TOKEN,
    b"Bearer \xc3\xa9",
])
def test_companion_mode_rejects_invalid_credentials(tmp_path, authorization):
    app = create_app(tmp_path / "archive.sqlite3", control_token=TOKEN,
                     require_auth=True, automatic_acquisition=False)
    response = _request(app, "GET", "/api/v2/session-evidence/status",
                        {"authorization": authorization})
    assert response.status_code == 403


@pytest.mark.parametrize("scheme", ["Bearer", "bearer"])
def test_companion_mode_accepts_authenticated_reads(tmp_path, scheme):
    app = create_app(tmp_path / "archive.sqlite3", control_token=TOKEN,
                     require_auth=True, automatic_acquisition=False)
    response = _request(app, "GET", "/api/v2/session-evidence/status",
                        {"authorization": f"{scheme} {TOKEN}"})
    assert response.status_code == 200


def test_legacy_loopback_mode_preserves_public_reads(tmp_path):
    app = create_app(tmp_path / "archive.sqlite3", automatic_acquisition=False)
    assert _request(app, "GET", "/api/v2/session-evidence/status").status_code == 200


@pytest.mark.parametrize("token", [None, "short"])
def test_companion_mode_refuses_missing_configuration(tmp_path, token):
    with pytest.raises(ValueError, match="authenticated API requires"):
        create_app(tmp_path / "archive.sqlite3", control_token=token, require_auth=True)
