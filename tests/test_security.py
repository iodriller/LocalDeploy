"""Tests for the opt-in API token (zero overhead when unset)."""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app

client = TestClient(app)


def test_no_token_means_no_auth(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    assert client.get("/profiles").status_code == 200


def test_token_enforced_when_set(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret")
    # Missing/wrong token is rejected on data endpoints.
    assert client.get("/profiles").status_code == 401
    assert client.get("/profiles", headers={"X-API-Token": "nope"}).status_code == 401
    # Accepted via any of the three supported channels.
    assert client.get("/profiles", headers={"X-API-Token": "secret"}).status_code == 200
    assert client.get("/profiles", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert client.get("/profiles?token=secret").status_code == 200


def test_ui_and_health_stay_open_with_token(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret")
    # The page must load (so it can prompt for the token) and health must answer.
    assert client.get("/ui/").status_code == 200
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/health").status_code == 200
