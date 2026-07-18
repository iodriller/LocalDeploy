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


def test_ui_exemption_is_exact_prefix_not_substring(monkeypatch):
    # The auth exemption must match the /ui mount exactly, not any path that
    # merely starts with "/ui" — otherwise a future "/uixxx" route would be open.
    monkeypatch.setenv("API_TOKEN", "secret")
    # A non-existent "/ui"-prefixed path must hit auth (401), not slip through.
    assert client.get("/uixyz").status_code == 401


# The control-plane surface documented in SECURITY.md as dangerous without a
# token: pulling/deleting/unloading models, changing the default profile, and
# running benchmarks. Every one must 401 with no token once API_TOKEN is set,
# regardless of request body validity — auth must run before body parsing.
CONTROL_PLANE_ENDPOINTS = [
    "/models/pull",
    "/models/delete",
    "/models/stop",
    "/models/free",
    "/models/switch",
    "/system/set-default",
    "/system/recommend",
    "/benchmark/run",
]


def test_control_plane_endpoints_require_token_when_set(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret")
    for path in CONTROL_PLANE_ENDPOINTS:
        resp = client.post(path, json={})
        assert resp.status_code == 401, f"{path} did not require auth: {resp.status_code} {resp.text}"


def test_control_plane_endpoints_open_without_token(monkeypatch):
    # Confirms the opt-in nature: with no API_TOKEN configured, these endpoints
    # are reachable (they may still 4xx/5xx on an empty body, just not for auth).
    # Keep this auth-only probe inert: /models/free otherwise unloads the user's
    # live models, and /system/recommend can launch real benchmark requests when
    # the developer's local config contains enabled profiles.
    import api_server
    from localdeploy.control import models as model_routes

    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.setattr(model_routes._ollama, "unload_all", lambda: (0, None))
    monkeypatch.setattr(api_server, "load_config", lambda: {"profiles": {}})
    for path in CONTROL_PLANE_ENDPOINTS:
        resp = client.post(path, json={})
        assert resp.status_code != 401, f"{path} unexpectedly required auth with no token set"


def test_openai_compatible_endpoints_require_same_token(monkeypatch):
    # The token guard is one global middleware over every path except the /ui,
    # /health, /favicon.ico exemptions — so /v1/* must be covered identically
    # to the native API, with no separate auth path to fall out of sync.
    monkeypatch.setenv("API_TOKEN", "secret")
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"X-API-Token": "secret"}).status_code == 200
    assert (
        client.post(
            "/v1/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
        ).status_code
        == 401
    )
