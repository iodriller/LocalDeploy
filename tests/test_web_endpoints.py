"""Tests for the additive web control-plane (Steps 1-3).

These run against the in-process FastAPI app via TestClient and require no GPU,
Ollama, or llama.cpp. The CI Linux runner has no NVIDIA GPU, so hardware/fit
responses exercise the graceful no-GPU paths plus explicit-VRAM fit math.
"""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app

client = TestClient(app)


# --- Step 1: scaffolding & flag-guarded mount --------------------------------


def test_ui_is_served() -> None:
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "LocalDeploy" in response.text


def test_existing_endpoints_still_present() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    # Original API is untouched...
    assert "/health" in paths
    assert "/v1/chat/completions" in paths
    # ...and the new control-plane routes are registered.
    assert "/system/hardware" in paths
    assert "/system/fit-check" in paths


# --- Step 2: hardware probe --------------------------------------------------


def test_hardware_probe_shape() -> None:
    body = client.get("/system/hardware").json()
    assert body["success"] is True
    assert isinstance(body["gpu_available"], bool)
    assert isinstance(body["gpus"], list)
    assert "logical_cores" in body["system"]
    if not body["gpu_available"]:
        assert body["message"]  # explains the CPU-only fallback


# --- Step 3: VRAM fit-check --------------------------------------------------


def test_fit_check_small_model_fits_8gb() -> None:
    body = client.post(
        "/system/fit-check",
        json={"params_b": 4, "quant": "Q4", "context": 4096, "free_vram_mb": 8192},
    ).json()
    assert body["success"] is True
    assert body["verdict"] == "FITS"
    assert body["estimate_gb"]["required"] < body["free_vram_gb"]


def test_fit_check_large_long_context_wont_fit_8gb() -> None:
    body = client.post(
        "/system/fit-check",
        json={"params_b": 12, "quant": "Q4_0", "context": 8192, "free_vram_mb": 8192},
    ).json()
    assert body["success"] is True
    assert body["verdict"] == "WONT_FIT"
    assert body["suggestions"]


def test_fit_check_resolves_profile() -> None:
    body = client.post(
        "/system/fit-check",
        json={"profile": "gemma3_4b_ollama_safe", "free_vram_mb": 8192},
    ).json()
    assert body["success"] is True
    assert body["model"]["params_b"] == 4
    assert body["verdict"] in {"FITS", "WONT_FIT"}


def test_fit_check_unknown_profile() -> None:
    body = client.post("/system/fit-check", json={"profile": "does_not_exist"}).json()
    assert body["success"] is False
    assert body["verdict"] == "UNKNOWN"


def test_fit_check_missing_params() -> None:
    body = client.post("/system/fit-check", json={"free_vram_mb": 8192}).json()
    assert body["success"] is False
    assert body["verdict"] == "UNKNOWN"
