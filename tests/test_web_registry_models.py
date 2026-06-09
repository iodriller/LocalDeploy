"""Tests for the registry + model-lifecycle endpoints (Steps 4-6).

CI has no Ollama, no GPU, and no network, so these assert the graceful-failure
contract (every route returns 200 with a clear payload, never a 500) and use
monkeypatching for the Hugging Face and Ollama-pull paths.
"""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.web import registry

client = TestClient(app)


# --- Step 4: registry --------------------------------------------------------


def test_registry_installed_graceful_when_ollama_down() -> None:
    body = client.get("/registry/installed").json()
    # Ollama is not running in CI: endpoint still returns 200 with a clear error.
    assert "installed" in body
    assert isinstance(body["installed"], list)
    assert body["success"] is False
    assert body["error"]


def test_check_updates_with_mocked_hf(monkeypatch) -> None:
    def fake_list_hf(query, limit):
        return ([{"id": f"org/{query}-new", "last_modified": "2026-01-01"}], None)

    monkeypatch.setattr(registry, "_list_hf", fake_list_hf)
    body = client.post("/registry/check-updates", json={"queries": ["gemma"], "limit": 3}).json()
    assert body["success"] is True
    assert body["online"] is True
    assert body["results"][0]["candidates"][0]["id"] == "org/gemma-new"
    assert "installed_match" in body["results"][0]["candidates"][0]


def test_check_updates_offline_is_graceful(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_list_hf", lambda query, limit: (None, "network down"))
    body = client.post("/registry/check-updates", json={"queries": ["gemma"]}).json()
    assert body["success"] is True
    assert body["online"] is False
    assert "Hugging Face" in body["message"]


# --- Step 5: pull ------------------------------------------------------------


def test_pull_blocked_by_fit_check() -> None:
    # 70B against 8 GB must be refused before any network call.
    body = client.post(
        "/models/pull",
        json={"model": "llama3:70b", "free_vram_mb": 8192},
    ).json()
    assert body["success"] is False
    assert body["blocked_by"] == "fit-check"
    assert body["fit"]["verdict"] == "WONT_FIT"


def test_pull_streams_and_terminates() -> None:
    # Small model passes the fit gate; with no Ollama the stream reports an error
    # event and still terminates cleanly with [DONE].
    response = client.post("/models/pull", json={"model": "gemma3:4b", "free_vram_mb": 8192})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "[DONE]" in response.text


def test_pull_requires_a_target() -> None:
    body = client.post("/models/pull", json={}).json()
    assert body["success"] is False
    assert "profile" in body["error"]


# --- Step 6: serve / stop / switch / status ----------------------------------


def test_system_status_graceful() -> None:
    body = client.get("/system/status").json()
    assert body["success"] is True
    assert "served_models" in body
    assert body["ollama"]["reachable"] is False  # no Ollama in CI
    assert "hardware" in body


def test_serve_graceful_when_ollama_down() -> None:
    body = client.post("/models/serve", json={"model": "gemma3:4b"}).json()
    assert body["success"] is False
    assert body["error"]


def test_stop_graceful_when_ollama_down() -> None:
    body = client.post("/models/stop", json={"model": "gemma3:4b"}).json()
    assert body["success"] is False
    assert body["error"]


def test_switch_graceful_when_ollama_down() -> None:
    body = client.post(
        "/models/switch",
        json={"to_model": "gemma3:4b", "from_model": "qwen3:8b"},
    ).json()
    assert body["success"] is False


def test_serve_unknown_profile() -> None:
    body = client.post("/models/serve", json={"profile": "nope"}).json()
    assert body["success"] is False
    assert "Unknown profile" in body["error"]
