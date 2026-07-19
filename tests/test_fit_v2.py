"""Tests for Fit v2 (Release R2): confidence, calibration display, vision
overhead, and the multi-context /system/fit-table endpoint. CI has no GPU, so
these monkeypatch detect_hardware the same way test_starter_pack.py does, and
isolate the calibration store to a tmp file so tests never touch real data.
"""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.control import calibration, fit as fit_mod

client = TestClient(app)


def _hw(vram_free_mb=8192, ram_available_mb=32768):
    return {
        "success": True,
        "gpu_available": True,
        "gpus": [{"name": "Fake GPU", "vendor": "NVIDIA", "backend": "CUDA", "vram_free_mb": vram_free_mb}],
        "gpu_summary": {"best_pool_free_mb": vram_free_mb},
        "system": {"ram_available_mb": ram_available_mb},
        "message": None,
    }


@pytest.fixture(autouse=True)
def isolate_calibration(monkeypatch, tmp_path):
    monkeypatch.setattr(calibration, "_store_path", lambda: tmp_path / "calibration.json")


def test_fit_check_reports_none_confidence_shape(monkeypatch):
    monkeypatch.setattr(fit_mod, "detect_hardware", lambda: _hw())
    body = client.post("/system/fit-check", json={"params_b": 7, "quant": "q4", "context": 4096}).json()
    assert body["confidence"] in {"low", "medium", "high"}
    assert body["calibration"]["applied"] is False
    assert body["calibration"]["sample_count"] == 0
    assert body["estimate_gb"]["calibrated_required"] is None
    assert body["calibrated_margin_gb"] is None


def test_fit_check_raw_required_unaffected_by_calibration(monkeypatch):
    # Calibration must never silently change the raw formula estimate - only
    # add a clearly separate calibrated_required figure alongside it.
    monkeypatch.setattr(fit_mod, "detect_hardware", lambda: _hw())
    raw = client.post("/system/fit-check", json={"params_b": 7, "quant": "q4", "context": 4096}).json()
    for _ in range(5):
        calibration.record_sample(
            gpu=calibration.gpu_key(_hw()), runtime="ollama", family=None, quant="q4", context=4096,
            estimated_gb=raw["estimate_gb"]["required"], observed_gb=raw["estimate_gb"]["required"] * 1.20,
        )
    calibrated = client.post("/system/fit-check", json={"params_b": 7, "quant": "q4", "context": 4096}).json()
    assert calibrated["estimate_gb"]["required"] == raw["estimate_gb"]["required"]  # unchanged
    assert calibrated["calibration"]["applied"] is True
    assert calibrated["calibration"]["sample_count"] == 5
    assert calibrated["estimate_gb"]["calibrated_required"] == pytest.approx(
        raw["estimate_gb"]["required"] * 1.20, abs=0.05
    )
    assert calibrated["confidence"] == "high"


def test_fit_check_vision_adds_overhead(monkeypatch):
    monkeypatch.setattr(fit_mod, "detect_hardware", lambda: _hw())
    plain = client.post("/system/fit-check", json={"model_id": "qwen3:8b", "params_b": 8}).json()
    vision = client.post("/system/fit-check", json={"model_id": "qwen3-vl:8b", "params_b": 8}).json()
    assert plain["estimate_gb"]["vision_overhead"] == 0.0
    assert vision["estimate_gb"]["vision_overhead"] > 0.0
    assert vision["estimate_gb"]["required"] > plain["estimate_gb"]["required"]


def test_fit_check_explicit_vision_flag_overrides_autodetect(monkeypatch):
    monkeypatch.setattr(fit_mod, "detect_hardware", lambda: _hw())
    body = client.post("/system/fit-check", json={"model_id": "mystery-model:8b", "params_b": 8, "vision": True}).json()
    assert body["estimate_gb"]["vision_overhead"] > 0.0


def test_fit_table_sweeps_contexts(monkeypatch):
    monkeypatch.setattr(fit_mod, "detect_hardware", lambda: _hw())
    body = client.post("/system/fit-table", json={"model_id": "qwen3:8b", "params_b": 8, "quant": "q4"}).json()
    assert body["success"] is True
    contexts = [row["context"] for row in body["rows"]]
    assert contexts == fit_mod.FIT_TABLE_CONTEXTS
    # Larger context must never estimate less required memory than a smaller one.
    required = [row["estimate_gb"]["required"] for row in body["rows"]]
    assert required == sorted(required)


def test_fit_table_missing_size_is_graceful(monkeypatch):
    monkeypatch.setattr(fit_mod, "detect_hardware", lambda: _hw())
    body = client.post("/system/fit-table", json={"model_id": "llama3:latest"}).json()
    assert body["success"] is False
    assert "message" in body


def test_fit_check_unknown_profile_still_errors_cleanly(monkeypatch):
    monkeypatch.setattr(fit_mod, "detect_hardware", lambda: _hw())
    body = client.post("/system/fit-check", json={"profile": "does_not_exist"}).json()
    assert body["success"] is False
    assert body["verdict"] == "UNKNOWN"
