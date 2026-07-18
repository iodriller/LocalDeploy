"""Tests for the quantization advisor (POST /system/quant-advisor)."""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app

client = TestClient(app)

_LADDER = ["Q2_K", "Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "F16"]


def _advise(**payload):
    return client.post("/system/quant-advisor", json=payload).json()


def test_requires_a_parameter_count():
    body = _advise(model_id="mystery-model")
    assert body["success"] is False
    assert "parameter count" in body["message"]


def test_full_ladder_with_ample_budget():
    body = _advise(model_id="gemma3:12b", free_vram_mb=48 * 1024)
    assert body["success"] is True
    assert [v["quant"] for v in body["variants"]] == _LADDER
    assert all(v["verdict"] == "FITS" for v in body["variants"])
    # With everything fitting comfortably, the best pick is the top of the ladder.
    assert "F16" in body["recommendation"]
    assert body["model"]["family"] == "gemma3"
    assert body["tags_url"] == "https://ollama.com/library/gemma3/tags"


def test_headroom_recommends_step_up_from_q4():
    # 8 GB budget on a 7B: Q4 fits with room, F16 (~14+ GB) does not.
    body = _advise(model_id="qwen2.5:7b", free_vram_mb=8 * 1024, context=4096)
    assert body["success"] is True
    by_quant = {v["quant"]: v for v in body["variants"]}
    assert by_quant["Q4_K_M"]["verdict"] == "FITS"
    assert by_quant["F16"]["verdict"] == "WONT_FIT"
    # Estimates must rise monotonically up the ladder.
    required = [v["required_gb"] for v in body["variants"]]
    assert required == sorted(required)


def test_tight_budget_recommends_below_q4():
    body = _advise(model_id="llama3.1:70b", free_vram_mb=8 * 1024, context=4096)
    assert body["success"] is True
    assert all(v["verdict"] != "FITS" for v in body["variants"])
    assert "CPU" in body["recommendation"] or "too large" in body["recommendation"]


def test_explicit_params_b_and_non_library_names():
    body = _advise(params_b=4.0, free_vram_mb=8 * 1024)
    assert body["success"] is True
    assert body["model"]["family"] is None
    assert body["tags_url"] is None
    hf = _advise(model_id="hf.co/org/Some-7B-GGUF", free_vram_mb=8 * 1024)
    assert hf["success"] is True
    assert hf["tags_url"] is None  # hf.co paths aren't Ollama library families


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/system/fit-check", {"params_b": -7, "free_vram_mb": 8192}),
        ("/system/quant-advisor", {"params_b": 7, "context": -1}),
        ("/registry/starter-pack", {"free_vram_mb": -1}),
        ("/system/recommend", {"sample_size": 0}),
    ],
)
def test_resource_advice_rejects_negative_or_zero_inputs(path, payload):
    assert client.post(path, json=payload).status_code == 422
