"""Tests for the starter-pack endpoint (Step 15).

CI has no GPU, so these monkeypatch `detect_hardware` to simulate a few
representative hardware profiles (8 GB card, 31 GB card, CPU-only) and assert
the fit-margin contract described in the feature request: recommendations
must fit within (detected budget - margin), ranked, capped at 5, and every
route returns 200 even in degenerate cases (no GPU + no RAM info).
"""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.control import starter

client = TestClient(app)


def _hw(vram_free_mb=None, ram_available_mb=None, gpu_available=None):
    gpus = [{"name": "Fake GPU", "vram_free_mb": vram_free_mb}] if vram_free_mb is not None else []
    return {
        "success": True,
        "gpu_available": gpu_available if gpu_available is not None else bool(gpus),
        "gpus": gpus,
        "system": {"ram_available_mb": ram_available_mb},
        "message": None,
    }


def test_starter_pack_8gb_card_stays_within_margin(monkeypatch) -> None:
    monkeypatch.setattr(starter, "detect_hardware", lambda: _hw(vram_free_mb=8192))
    body = client.post("/registry/starter-pack", json={"margin_gb": 2.0, "limit": 5}).json()
    assert body["success"] is True
    assert body["budget_source"] == "vram"
    assert body["budget_gb"] == pytest.approx(6.0, abs=0.01)
    assert 1 <= len(body["candidates"]) <= 5
    for c in body["candidates"]:
        assert c["required_gb"] <= 6.0
        assert c["pull_name"] == c["id"]


def test_starter_pack_31gb_card_returns_larger_models(monkeypatch) -> None:
    monkeypatch.setattr(starter, "detect_hardware", lambda: _hw(vram_free_mb=31 * 1024))
    body = client.post("/registry/starter-pack", json={"margin_gb": 2.0, "limit": 5}).json()
    assert body["success"] is True
    assert len(body["candidates"]) == 5
    # Larger budget should surface at least one model bigger than the 8B class.
    assert any(c["params_b"] > 8.0 for c in body["candidates"])


def test_starter_pack_no_gpu_falls_back_to_ram(monkeypatch) -> None:
    monkeypatch.setattr(starter, "detect_hardware", lambda: _hw(ram_available_mb=16 * 1024))
    body = client.post("/registry/starter-pack", json={}).json()
    assert body["success"] is True
    assert body["budget_source"] == "ram"
    assert len(body["candidates"]) > 0


def test_starter_pack_no_hardware_info_is_graceful(monkeypatch) -> None:
    monkeypatch.setattr(starter, "detect_hardware", lambda: _hw())
    body = client.post("/registry/starter-pack", json={}).json()
    assert body["success"] is True
    assert body["candidates"] == []
    assert body["message"]


def test_starter_pack_tiny_budget_relaxes_margin(monkeypatch) -> None:
    # Smaller than any catalog entry with the margin applied, but the raw
    # budget (no margin) fits the smallest model.
    smallest = min(starter.STARTER_CATALOG, key=lambda e: e["params_b"])
    required = starter._required_gb(smallest["params_b"])
    # Add one MiB because `_required_gb` is rounded to two decimals; truncating
    # the reconstructed MiB value can otherwise put the fake budget just below
    # the advertised requirement.
    monkeypatch.setattr(starter, "detect_hardware", lambda: _hw(vram_free_mb=int(required * 1024) + 1))
    body = client.post("/registry/starter-pack", json={"margin_gb": 2.0}).json()
    assert body["success"] is True
    assert len(body["candidates"]) >= 1
    assert body["margin_relaxed"] is True
    assert body["message"]
