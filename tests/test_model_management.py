"""Phase 4 - delete / free-memory routes."""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.control import models as models_mod

client = TestClient(app)


def test_delete_success(monkeypatch):
    calls = {}
    monkeypatch.setattr(models_mod._ollama, "delete_model", lambda m: calls.update(model=m) or {"deleted": m})
    body = client.post("/models/delete", json={"model": "gemma3:4b"}).json()
    assert body["success"] is True
    assert body["deleted"] == "gemma3:4b"
    assert calls == {"model": "gemma3:4b"}


def test_delete_requires_target():
    body = client.post("/models/delete", json={}).json()
    assert body["success"] is False


def test_delete_reports_backend_error(monkeypatch):
    from localdeploy.utils import BackendCallError

    def boom(_m):
        raise BackendCallError("Model 'x' is not installed.")

    monkeypatch.setattr(models_mod._ollama, "delete_model", boom)
    body = client.post("/models/delete", json={"model": "x"}).json()
    assert body["success"] is False
    assert "not installed" in body["error"]


def test_free_unloads_all(monkeypatch):
    monkeypatch.setattr(models_mod._ollama, "unload_all", lambda: (3, None))
    body = client.post("/models/free", json={}).json()
    assert body["success"] is True
    assert body["unloaded"] == 3


def test_free_reports_unreachable(monkeypatch):
    monkeypatch.setattr(models_mod._ollama, "unload_all", lambda: (0, "Ollama is not reachable."))
    body = client.post("/models/free", json={}).json()
    assert body["success"] is False
    assert "reachable" in body["error"]
