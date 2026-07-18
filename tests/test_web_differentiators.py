"""Tests for the differentiator endpoints: report cards + compare (Step 13),
recommend / tune-for-my-GPU (Step 14), and offline mode (Step 15).
"""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

import benchmark
from api_server import app
from localdeploy.control import fit as fitmod
from localdeploy.control import recommend as recmod

client = TestClient(app)


# --- Step 13: report cards ---------------------------------------------------

_RUN = {
    "profile": "gemma3_4b_ollama_safe",
    "model_id": "gemma3:4b",
    "hardware": {"gpu": "RTX 4060", "vram_total_mb": 8192},
    "tests": [
        {"name": "t1", "category": "planning", "success": True, "accuracy": 1.0, "elapsed_seconds": 1.2},
        {"name": "t2", "category": "math", "success": False, "accuracy": 0.0, "elapsed_seconds": 2.0},
    ],
}


def test_export_card_html_and_md():
    out = client.post("/benchmark/export", json=_RUN).json()
    assert out["success"] is True
    assert out["card"]["summary"]["tests"] == 2
    assert out["card"]["summary"]["passed"] == 1
    # self-contained HTML embeds the card JSON for re-import
    assert "localdeploy-card" in out["html"]
    assert "Report Card" in out["html"]
    assert "RTX 4060" in out["html"]
    assert "| Test | Category | Result |" in out["md"]


def test_compare_two_cards_shows_deltas():
    card_a = client.post("/benchmark/export", json=_RUN).json()["card"]
    better = {
        **_RUN,
        "model_id": "gemma3:12b",
        "tests": [
            {"name": "t1", "category": "planning", "success": True, "accuracy": 1.0, "elapsed_seconds": 1.0},
            {"name": "t2", "category": "math", "success": True, "accuracy": 1.0, "elapsed_seconds": 1.5},
        ],
    }
    card_b = client.post("/benchmark/export", json=better).json()["card"]
    diff = client.post("/benchmark/compare", json={"card_a": card_a, "card_b": card_b}).json()
    assert diff["success"] is True
    assert diff["label_a"] == "gemma3:4b"
    assert diff["label_b"] == "gemma3:12b"
    # t2 went from fail (0.0) to pass (1.0)
    t2 = next(r for r in diff["tests"] if r["name"] == "t2")
    assert t2["accuracy_delta"] == 1.0
    assert diff["summary_delta"]["avg_accuracy"] > 0


# --- Step 14: recommend ------------------------------------------------------


def test_recommend_ranks_fitting_profiles(monkeypatch):
    # Two profiles fit, one won't. The faster/more-accurate one should win.
    def fake_fit(req):
        if req.profile == "qwen3vl_8b_ollama":
            return {"success": True, "verdict": "WONT_FIT", "severity": "hard", "estimate_gb": {"required": 99}}
        margin = 4.0 if req.profile == "gemma3_4b_ollama_safe" else 1.0
        return {"success": True, "verdict": "FITS", "severity": "ok", "margin_gb": margin}

    def fake_execute(base_url, name, profile, test, timeout, num_gpu=None):
        fast = name == "gemma3_4b_ollama_safe"
        return benchmark.TestResult(
            name=test.name, category=test.category, success=True,
            elapsed_seconds=0.5 if fast else 3.0, response_length=4,
            response_preview="ok", accuracy=1.0 if fast else 0.7,
        )

    monkeypatch.setattr(fitmod, "fit_check", fake_fit)
    monkeypatch.setattr(benchmark, "execute_test", fake_execute)

    body = client.post(
        "/system/recommend",
        json={"profiles": ["gemma3_4b_ollama_safe", "gemma3_12b_ollama_safe", "qwen3vl_8b_ollama"], "sample_size": 2},
    ).json()
    assert body["success"] is True
    assert body["recommended"]["profile"] == "gemma3_4b_ollama_safe"
    assert any(s["profile"] == "qwen3vl_8b_ollama" for s in body["skipped"])
    # ranking is monotonic by score
    scores = [c["score"] for c in body["candidates"]]
    assert scores == sorted(scores, reverse=True)


def test_recommend_when_nothing_fits(monkeypatch):
    monkeypatch.setattr(fitmod, "fit_check", lambda req: {"verdict": "WONT_FIT", "estimate_gb": {"required": 99}})
    body = client.post("/system/recommend", json={"profiles": ["gemma3_4b_ollama_safe"]}).json()
    assert body["success"] is True
    assert body["recommended"] is None
    assert body["skipped"]


def test_set_default_refuses_to_overwrite_example():
    # conftest points CONFIG_PATH at config.example.json; must not be clobbered.
    body = client.post("/system/set-default", json={"profile": "gemma3_4b_ollama_safe"}).json()
    assert body["success"] is False
    assert "config.example.json" in body["error"]


def test_set_default_unknown_profile():
    body = client.post("/system/set-default", json={"profile": "nope"}).json()
    assert body["success"] is False
    assert "Unknown" in body["error"]


def test_set_default_writes_config(monkeypatch, tmp_path):
    import json

    # A fresh install has zero profiles (no more example fallback), so seed one
    # the way a pull would before pointing the default at it.
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "version": 1,
                "default_profile": None,
                "profiles": {"gemma3_4b_ollama_safe": {"backend": "ollama", "model_id": "gemma3:4b", "enabled": True}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    body = client.post("/system/set-default", json={"profile": "gemma3_4b_ollama_safe"}).json()
    assert body["success"] is True
    assert json.loads(cfg.read_text())["default_profile"] == "gemma3_4b_ollama_safe"


def test_fresh_install_has_no_phantom_profiles(monkeypatch, tmp_path):
    """A missing config.json must yield zero profiles — never the example's
    sample profiles (the pre-0.4 fallback showed models nobody pulled)."""
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config.json"))
    body = client.get("/profiles").json()
    assert body["success"] is True
    assert body["profiles"] == {}

    chat = client.post("/chat", json={"prompt": "hi"}).json()
    assert chat["success"] is False
    assert "No model profiles configured yet" in chat["error"]


def test_rank_candidates_pure():
    out = recmod.rank_candidates(
        [
            {"profile": "a", "avg_accuracy": 0.9, "avg_latency_s": 1.0, "margin_gb": 2.0},
            {"profile": "b", "avg_accuracy": 0.5, "avg_latency_s": 1.0, "margin_gb": 2.0},
        ]
    )
    assert out[0]["profile"] == "a"  # higher accuracy wins


# --- Step 15: offline --------------------------------------------------------


def test_offline_skips_hugging_face(monkeypatch):
    monkeypatch.setenv("OFFLINE", "true")
    body = client.post("/registry/check-updates", json={"queries": ["gemma"]}).json()
    assert body["success"] is True
    assert body["online"] is False
    assert "offline" in body["message"].lower()
