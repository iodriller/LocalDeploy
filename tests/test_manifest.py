"""Tests for deployment manifests (Release R5): export, compatibility
validation, streamed recreation, and integration snippets.
"""
from __future__ import annotations

import copy

import pytest
import yaml

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

import api_server
from api_server import app
from localdeploy.control import _ollama, calibration
from localdeploy.control import fit as fit_mod
from localdeploy.control import manifest as manifest_mod

client = TestClient(app)

PROFILE = "gemma3_4b_ollama_safe"
MODEL_ID = "gemma3:4b"


def _hw():
    return {
        "success": True,
        "gpu_available": True,
        "gpus": [{"name": "RTX 4090", "vendor": "NVIDIA", "backend": "CUDA", "vram_total_mb": 24576, "vram_free_mb": 20000}],
        "gpu_summary": {"best_pool_free_mb": 20000, "best_pool_total_mb": 24576},
        "system": {"ram_total_mb": 65536, "ram_available_mb": 32768, "cpu_model": "Test CPU"},
        "message": None,
    }


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(calibration, "_store_path", lambda: tmp_path / "calibration.json")
    monkeypatch.setattr(fit_mod, "detect_hardware", lambda: _hw())
    monkeypatch.setattr(manifest_mod, "detect_hardware", lambda: _hw())


# ---- export -------------------------------------------------------------------

def test_export_unknown_profile_errors():
    body = client.post("/system/manifest/export", json={"profile": "does-not-exist"}).json()
    assert body["success"] is False


def test_export_produces_full_schema(monkeypatch):
    monkeypatch.setattr(_ollama, "list_installed", lambda: ([{"name": MODEL_ID, "digest": "sha256:abc123"}], None))
    monkeypatch.setattr(_ollama, "version", lambda: ("0.12.0", None))
    monkeypatch.setattr(_ollama, "list_running", lambda: ([], None))
    body = client.post("/system/manifest/export", json={"profile": PROFILE}).json()
    assert body["success"] is True
    m = body["manifest"]
    assert m["schema_version"] == 1
    assert m["model"]["name"] == MODEL_ID
    assert m["model"]["digest"] == "sha256:abc123"
    assert m["runtime"]["provider"] == "ollama"
    assert m["runtime"]["version"] == "0.12.0"
    assert m["hardware"]["gpu"] == "RTX 4090"
    assert m["hardware"]["vram_gb"] == 24.0
    assert m["fit"]["estimated_vram_gb"] is not None
    assert m["fit"]["confidence"] in {"low", "medium", "high"}
    # YAML round-trips to the same structure.
    assert yaml.safe_load(body["yaml"]) == m


def test_export_non_ollama_backend_rejected(monkeypatch):
    config = copy.deepcopy(api_server.load_config())
    config["profiles"]["llamacpp_test"] = {"backend": "llamacpp", "model_id": "some.gguf"}
    monkeypatch.setattr(api_server, "load_config", lambda: config)
    body = client.post("/system/manifest/export", json={"profile": "llamacpp_test"}).json()
    assert body["success"] is False


# ---- validate -------------------------------------------------------------------

def _base_manifest(**overrides):
    manifest = {
        "schema_version": 1,
        "model": {"name": MODEL_ID, "digest": "sha256:abc123", "quantization": "Q4_K_M", "source": "ollama"},
        "runtime": {"provider": "ollama", "version": "0.11.0", "endpoint": "http://127.0.0.1:11434"},
        "deployment": {"context_length": 4096, "placement_observed": "GPU"},
        "hardware": {"gpu": "RTX 3080", "vram_gb": 10.0, "cpu": "Other CPU", "ram_gb": 32.0, "operating_system": "Windows"},
        "fit": {"estimated_vram_gb": 3.5, "observed_vram_gb": 3.8, "confidence": "medium"},
        "performance": {},
        "benchmark": {},
    }
    manifest.update(overrides)
    return manifest


def test_validate_missing_model_section_errors():
    body = client.post("/system/manifest/validate", json={"manifest": {"schema_version": 1}}).json()
    assert body["success"] is False


def test_validate_reports_exact_digest_match(monkeypatch):
    monkeypatch.setattr(_ollama, "list_installed", lambda: ([{"name": MODEL_ID, "digest": "sha256:abc123"}], None))
    monkeypatch.setattr(_ollama, "version", lambda: ("0.12.0", None))
    body = client.post("/system/manifest/validate", json={"manifest": _base_manifest()}).json()
    assert body["success"] is True
    assert body["model_available"] is True
    assert body["runtime_available"] is True
    assert body["can_recreate"] is True
    assert any("Exact model digest" in d["text"] for d in body["diffs"])


def test_validate_reports_model_not_installed(monkeypatch):
    monkeypatch.setattr(_ollama, "list_installed", lambda: ([], None))
    monkeypatch.setattr(_ollama, "version", lambda: ("0.12.0", None))
    body = client.post("/system/manifest/validate", json={"manifest": _base_manifest()}).json()
    assert body["model_available"] is False
    assert any("not installed here yet" in d["text"] for d in body["diffs"])


def test_validate_reports_ollama_unreachable(monkeypatch):
    monkeypatch.setattr(_ollama, "list_installed", lambda: ([], "Ollama is not reachable."))
    monkeypatch.setattr(_ollama, "version", lambda: (None, "Ollama is not reachable."))
    body = client.post("/system/manifest/validate", json={"manifest": _base_manifest()}).json()
    assert body["runtime_available"] is False
    assert body["can_recreate"] is False


def test_validate_suggests_smaller_context_when_original_does_not_fit(monkeypatch):
    monkeypatch.setattr(_ollama, "list_installed", lambda: ([], None))
    monkeypatch.setattr(_ollama, "version", lambda: ("0.12.0", None))
    # 14B fits comfortably in the mocked ~19.5 GB free VRAM at a normal context,
    # but a 131072-token context balloons the flat KV-cache estimate past both
    # VRAM and the mocked 32 GB RAM - a smaller context should be suggested.
    huge_context_manifest = _base_manifest(
        model={"name": "qwen3:14b", "digest": None, "quantization": "Q4_K_M", "source": "ollama"},
        deployment={"context_length": 131072, "placement_observed": "GPU"},
    )
    body = client.post("/system/manifest/validate", json={"manifest": huge_context_manifest}).json()
    assert body["success"] is True
    assert body["can_recreate"] is False
    assert body["substitutions"], "expected a smaller-context suggestion when the original doesn't fit"
    assert "4096" in body["substitutions"][0]


# ---- recreate (streamed) -------------------------------------------------------

def _sse_events(text):
    import json as _json

    return [_json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]


def test_recreate_pulls_when_not_installed(monkeypatch):
    monkeypatch.setattr(_ollama, "list_installed", lambda: ([], None))
    monkeypatch.setattr(_ollama, "version", lambda: ("0.12.0", None))
    pulled = []

    def fake_pull_stream(model_id):
        pulled.append(model_id)
        yield {"status": "success", "done": True}

    monkeypatch.setattr(_ollama, "pull_stream", fake_pull_stream)

    from localdeploy.control import models as models_mod

    def fake_serve(model_id, keep_alive, num_gpu=None):
        return {
            "success": True,
            "running": [{"name": model_id, "size": 4_000_000_000, "size_vram": 3_900_000_000, "placement": "GPU"}],
        }

    monkeypatch.setattr(models_mod, "_serve_ollama", fake_serve)

    resp = client.post("/system/manifest/recreate", json={"manifest": _base_manifest()})
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert pulled == [MODEL_ID]
    kinds = [e.get("event") for e in events]
    assert "validated" in kinds
    assert "pull_start" in kinds
    assert "pull_end" in kinds
    assert "recreate_end" in kinds
    end = next(e for e in events if e["event"] == "recreate_end")
    assert end["placement_observed"] == "GPU"


def test_recreate_skips_pull_when_already_installed(monkeypatch):
    monkeypatch.setattr(_ollama, "list_installed", lambda: ([{"name": MODEL_ID, "digest": "sha256:abc123"}], None))
    monkeypatch.setattr(_ollama, "version", lambda: ("0.12.0", None))

    from localdeploy.control import models as models_mod

    def fake_serve(model_id, keep_alive, num_gpu=None):
        return {
            "success": True,
            "running": [{"name": model_id, "size": 4_000_000_000, "size_vram": 3_900_000_000, "placement": "GPU"}],
        }

    monkeypatch.setattr(models_mod, "_serve_ollama", fake_serve)

    resp = client.post("/system/manifest/recreate", json={"manifest": _base_manifest()})
    events = _sse_events(resp.text)
    kinds = [e.get("event") for e in events]
    assert "pull_start" not in kinds
    assert "recreate_end" in kinds


def test_recreate_blocks_when_hard_incompatible(monkeypatch):
    monkeypatch.setattr(_ollama, "list_installed", lambda: ([], "Ollama is not reachable."))
    monkeypatch.setattr(_ollama, "version", lambda: (None, "err"))
    resp = client.post("/system/manifest/recreate", json={"manifest": _base_manifest()})
    events = _sse_events(resp.text)
    kinds = [e.get("event") for e in events]
    assert "error" in kinds
    assert "pull_start" not in kinds


# ---- integration snippets -------------------------------------------------------

def test_integration_snippets_returns_all_cards():
    body = client.get("/system/integration-snippets", params={"model": MODEL_ID, "context": 8192}).json()
    assert body["success"] is True
    labels = {c["label"] for c in body["cards"]}
    assert {"Open WebUI", "curl", "Python (openai SDK)"} <= labels
    curl_card = next(c for c in body["cards"] if c["label"] == "curl")
    assert MODEL_ID in curl_card["snippet"]
    assert "/v1/chat/completions" in curl_card["snippet"]


def test_integration_snippets_include_auth_header_when_token_set(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret123")
    body = client.get(
        "/system/integration-snippets", params={"model": MODEL_ID}, headers={"X-API-Token": "secret123"}
    ).json()
    curl_card = next(c for c in body["cards"] if c["label"] == "curl")
    assert "X-API-Token" in curl_card["snippet"]
