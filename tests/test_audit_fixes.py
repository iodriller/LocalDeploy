"""Tests for the final audit fixes: Apple Silicon detection + tiered pull gate."""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.control import hardware
from localdeploy.control import models

client = TestClient(app)


# ---- Fix #2: Apple Silicon (Metal) detection -------------------------------

def test_apple_silicon_reported_as_gpu(monkeypatch) -> None:
    """On an Apple Silicon Mac with no nvidia-smi, the GPU must NOT read CPU-only."""
    monkeypatch.setattr(hardware, "_query_nvidia_smi", lambda: None)
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(hardware.shutil, "which", lambda _: None)  # no sysctl → generic name

    hw = hardware.detect_hardware()
    assert hw["gpu_available"] is True
    g = hw["gpus"][0]
    assert "Metal" in g["name"]
    assert g["unified_memory"] is True
    assert g["vram_total_mb"] is None  # unified memory → no separate VRAM
    assert "unified memory" in hw["message"].lower()


def test_non_apple_non_nvidia_is_cpu_only(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "_query_nvidia_smi", lambda: None)
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "x86_64")

    hw = hardware.detect_hardware()
    assert hw["gpu_available"] is False
    assert hw["gpus"] == []
    assert "CPU-only" in hw["message"]


def test_nvidia_takes_precedence_over_apple(monkeypatch) -> None:
    """An NVIDIA GPU is reported as-is even on a hypothetical Darwin host."""
    fake_gpu = [{"name": "RTX 4090", "vram_total_mb": 24576, "vram_free_mb": 24000,
                 "vram_used_mb": 576, "driver_version": "550"}]
    monkeypatch.setattr(hardware, "_query_nvidia_smi", lambda: fake_gpu)
    hw = hardware.detect_hardware()
    assert hw["gpus"][0]["name"] == "RTX 4090"
    assert "unified_memory" not in hw["gpus"][0]


# ---- Fix #3: pull gate uses tiered severity --------------------------------

def _patch_resolve(monkeypatch):
    # Make _resolve_target return a plain Ollama model so we reach the fit gate.
    monkeypatch.setattr(models, "_resolve_target", lambda p, m, b: ("ollama", "some-model", {}))
    monkeypatch.setattr(models, "require_gpu_only", lambda: False)


def test_pull_hard_severity_is_blocked(monkeypatch) -> None:
    _patch_resolve(monkeypatch)
    monkeypatch.setattr(
        models, "fit_check",
        lambda req: {"verdict": "WONT_FIT", "severity": "hard", "cpu_deployable": False,
                     "headline": "Too large for GPU and system RAM.", "estimate_gb": {"required": 99}},
    )
    body = client.post("/models/pull", json={"model": "huge", "free_vram_mb": 8192}).json()
    assert body["success"] is False
    assert body["blocked_by"] == "fit-check"


def test_pull_cpu_only_severity_is_allowed(monkeypatch) -> None:
    """A model that won't fit VRAM but runs on CPU (soft) must NOT be blocked."""
    _patch_resolve(monkeypatch)
    monkeypatch.setattr(
        models, "fit_check",
        lambda req: {"verdict": "WONT_FIT", "severity": "soft", "cpu_deployable": True,
                     "headline": "Won't fit GPU, but can run on CPU (slower)."},
    )
    # No Ollama running → the stream emits an error event but still starts (not blocked).
    resp = client.post("/models/pull", json={"model": "midsize", "free_vram_mb": 4096})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    text = resp.text
    assert "blocked_by" not in text  # the soft case was allowed through
    assert "run on CPU" in text  # the informational note rode the start event
    assert "[DONE]" in text


def test_pull_missing_severity_falls_back_to_verdict(monkeypatch) -> None:
    """Older fit responses without 'severity' still hard-block on WONT_FIT."""
    _patch_resolve(monkeypatch)
    monkeypatch.setattr(
        models, "fit_check",
        lambda req: {"verdict": "WONT_FIT", "estimate_gb": {"required": 99}},
    )
    body = client.post("/models/pull", json={"model": "huge", "free_vram_mb": 8192}).json()
    assert body["success"] is False
    assert body["blocked_by"] == "fit-check"
