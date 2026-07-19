"""Tests for the local-only community-sharing groundwork (Release R8):
anonymization whitelist, preview, and local-only export (no transmission).
"""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.control import community as community_mod

client = TestClient(app)


def _sample_card():
    return {
        "profile": "my_secret_profile_name",
        "model_id": "qwen3:8b",
        "device": "gpu",
        "peak_vram_mb": 12800,
        "hardware": {"gpu": "RTX 4090", "vram_total_mb": 24576},
        "provenance": {
            "profiles": {
                "my_secret_profile_name": {
                    "backend": "ollama", "backend_version": "0.12.0",
                    "model_digest": "sha256:abcdef", "quant": "Q4_K_M", "context": 8192,
                }
            },
            "hardware": {
                "gpus": [{"name": "RTX 4090", "vram_total_mb": 24576}],
                "system": {"cpu_model": "AMD Ryzen 9", "ram_total_mb": 65536},
            },
        },
        "summary": {"avg_accuracy": 0.88, "avg_latency_s": 1.2, "avg_tokens_per_second": 55.0, "avg_ttft_ms": 240.0},
        "repetitions": 3,
        "tests": [
            {
                "name": "code_lru_cache", "category": "code", "success": True, "accuracy": 0.9,
                "elapsed_seconds": 1.1, "approx_tokens_per_second": 50.0,
                "response_preview": "def solve(): return 'the secret prompt answer was 42'",
                "error": None,
                "metrics": {"ttft_ms": 200.0, "tokens_per_second": 50.0, "prompt_tokens_per_second": 300.0},
            }
        ],
    }


def test_anonymize_strips_profile_name_and_response_text():
    out = community_mod.anonymize_card(_sample_card())
    dumped = str(out)
    assert "my_secret_profile_name" not in dumped
    assert "secret prompt answer" not in dumped
    assert "response_preview" not in out["tests"][0]
    assert "error" not in out["tests"][0]


def test_anonymize_keeps_safe_grouping_fields():
    out = community_mod.anonymize_card(_sample_card())
    assert out["model"]["id"] == "qwen3:8b"
    assert out["model"]["digest"] == "sha256:abcdef"
    assert out["model"]["quantization"] == "Q4_K_M"
    assert out["hardware"]["gpu"] == "RTX 4090"
    assert out["hardware"]["vram_gb"] == 24.0
    assert out["hardware"]["ram_gb"] == 64.0
    assert out["hardware"]["os_category"] in {"Windows", "macOS", "Linux"}
    assert out["performance"]["avg_tokens_per_second"] == 55.0
    assert out["performance"]["peak_vram_mb"] == 12800
    assert out["tests"][0]["accuracy"] == 0.9
    assert out["tests"][0]["metrics"]["ttft_ms"] == 200.0


def test_anonymize_never_includes_excluded_field_names():
    out = community_mod.anonymize_card(_sample_card())
    import json

    dumped_keys = json.dumps(out).lower()
    for banned in ("username", "computer_name", "ip_address", "api_key", "profile_name"):
        assert banned not in dumped_keys


def test_preview_endpoint_never_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(community_mod, "_contributions_dir", lambda: tmp_path / "community-contributions")
    body = client.post("/system/community/preview", json={"card": _sample_card()}).json()
    assert body["success"] is True
    assert body["would_share"]["model"]["id"] == "qwen3:8b"
    assert "nothing is sent anywhere" in body["note"].lower()
    assert not (tmp_path / "community-contributions").exists()


def test_preview_lists_excluded_fields():
    body = client.post("/system/community/preview", json={"card": _sample_card()}).json()
    assert "model prompts" in body["excluded_fields"]
    assert "local profile name" in body["excluded_fields"]


def test_export_saves_locally_and_reports_no_transmission(tmp_path, monkeypatch):
    monkeypatch.setattr(community_mod, "_contributions_dir", lambda: tmp_path / "community-contributions")
    body = client.post("/system/community/export", json={"card": _sample_card()}).json()
    assert body["success"] is True
    assert "not transmitted" in body["note"].lower()
    saved = list((tmp_path / "community-contributions").glob("*.json"))
    assert len(saved) == 1
    import json

    on_disk = json.loads(saved[0].read_text(encoding="utf-8"))
    assert on_disk["model"]["id"] == "qwen3:8b"
    assert "my_secret_profile_name" not in json.dumps(on_disk)
