"""Phase C tests: device-aware warm-up timeout + graceful load-timeout message."""
from __future__ import annotations

import requests

from localdeploy.control import _ollama
from localdeploy.control import models


# ---- _load_timeout: scales with device, env-overridable -------------------

def test_load_timeout_cpu_is_longer_than_gpu() -> None:
    assert _ollama._load_timeout(0) > _ollama._load_timeout(999)


def test_load_timeout_auto_uses_gpu_default() -> None:
    # None (Ollama decides) should use the shorter GPU-path default.
    assert _ollama._load_timeout(None) == _ollama._GPU_LOAD_TIMEOUT


def test_load_timeout_cpu_uses_cpu_default() -> None:
    assert _ollama._load_timeout(0) == _ollama._CPU_LOAD_TIMEOUT


def test_load_timeout_env_override(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_LOAD_TIMEOUT", "42")
    assert _ollama._load_timeout(0) == 42
    assert _ollama._load_timeout(999) == 42


def test_load_timeout_env_invalid_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_LOAD_TIMEOUT", "not-a-number")
    assert _ollama._load_timeout(0) == _ollama._CPU_LOAD_TIMEOUT


def test_load_model_passes_device_timeout(monkeypatch) -> None:
    """load_model should request with the CPU timeout when forcing CPU."""
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"done": True}

    def fake_post(url, json=None, timeout=None):
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(_ollama, "base_url", lambda: "http://localhost:11434")
    monkeypatch.setattr(_ollama.requests, "post", fake_post)
    _ollama.load_model("big-model", num_gpu=0)
    assert captured["timeout"] == _ollama._CPU_LOAD_TIMEOUT


# ---- _serve_ollama: graceful timeout message ------------------------------

def test_serve_ollama_timeout_is_graceful(monkeypatch) -> None:
    def raise_timeout(*args, **kwargs):
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(models, "require_gpu_only", lambda: False)
    monkeypatch.setattr(models._ollama, "unload_model", lambda _m: {})
    monkeypatch.setattr(models._ollama, "load_model", raise_timeout)

    res = models._serve_ollama("gemma3:27b", "5m", num_gpu=0)
    assert res["success"] is False
    assert res.get("timeout") is True
    # Message should guide the user to re-check, not imply a hard crash.
    assert "Refresh status" in res["error"]
    assert "still be loading" in res["error"]
