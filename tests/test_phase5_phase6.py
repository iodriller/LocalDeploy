"""Tests for Phase 5 (discovery) and Phase 6 (device-tagged benchmark cards)."""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.web.report import build_card, render_html, render_md

client = TestClient(app)


# ---------------------------------------------------------------------------
# Phase 5 — discovery: check-updates accepts search params
# ---------------------------------------------------------------------------

def test_check_updates_accepts_explicit_queries(monkeypatch) -> None:
    """Explicit queries are forwarded to the HF search (offline path)."""
    from localdeploy.web import registry as reg

    called_with: list = []

    def fake_list_hf(query, limit, gguf_only=True):
        called_with.append({"query": query, "limit": limit, "gguf_only": gguf_only})
        return [], None

    monkeypatch.setattr(reg, "_list_hf", fake_list_hf)
    monkeypatch.setattr(reg, "offline_mode", lambda: False)
    from localdeploy.web import _ollama

    monkeypatch.setattr(_ollama, "list_installed", lambda: ([], None))

    resp = client.post(
        "/registry/check-updates",
        json={"queries": ["llama", "qwen"], "limit": 8, "gguf_only": False},
    )
    assert resp.status_code == 200
    assert len(called_with) == 2
    assert called_with[0]["query"] == "llama"
    assert called_with[0]["limit"] == 8
    assert called_with[0]["gguf_only"] is False
    assert called_with[1]["query"] == "qwen"


def test_check_updates_gguf_only_default(monkeypatch) -> None:
    """gguf_only defaults to True when omitted."""
    from localdeploy.web import registry as reg

    captured: list = []

    def fake_list_hf(query, limit, gguf_only=True):
        captured.append(gguf_only)
        return [], None

    monkeypatch.setattr(reg, "_list_hf", fake_list_hf)
    monkeypatch.setattr(reg, "offline_mode", lambda: False)
    from localdeploy.web import _ollama

    monkeypatch.setattr(_ollama, "list_installed", lambda: ([], None))

    client.post("/registry/check-updates", json={"queries": ["gemma"]})
    assert captured == [True]


def test_check_updates_hf_response_shape(monkeypatch) -> None:
    """Candidates include downloads/likes so the UI can render them."""
    from localdeploy.web import registry as reg

    candidate = {
        "id": "unsloth/gemma-3-4b-it-GGUF",
        "last_modified": "2026-01-15T12:00:00",
        "downloads": 50000,
        "likes": 120,
        "gated": False,
        "pullable": True,
        "pull_name": "hf.co/unsloth/gemma-3-4b-it-GGUF",
    }

    monkeypatch.setattr(reg, "_list_hf", lambda q, limit, gguf_only=True: ([candidate], None))
    monkeypatch.setattr(reg, "offline_mode", lambda: False)
    from localdeploy.web import _ollama

    monkeypatch.setattr(_ollama, "list_installed", lambda: ([], None))

    resp = client.post("/registry/check-updates", json={"queries": ["gemma"]}).json()
    assert resp["success"] is True
    c = resp["results"][0]["candidates"][0]
    assert c["downloads"] == 50000
    assert c["likes"] == 120
    assert c["pull_name"] == "hf.co/unsloth/gemma-3-4b-it-GGUF"


def test_check_updates_adds_fit_and_filters_gpu_matches(monkeypatch) -> None:
    from localdeploy.web import registry as reg

    candidates = [
        {"id": "org/qwen-4b-q4_k_m-GGUF", "pullable": True, "pull_name": "hf.co/org/qwen-4b-q4_k_m-GGUF"},
        {"id": "org/qwen-27b-q4_k_m-GGUF", "pullable": True, "pull_name": "hf.co/org/qwen-27b-q4_k_m-GGUF"},
    ]

    monkeypatch.setattr(reg, "_list_hf", lambda q, limit, gguf_only=True: (list(candidates), None))
    monkeypatch.setattr(reg, "offline_mode", lambda: False)
    from localdeploy.web import _ollama

    monkeypatch.setattr(_ollama, "list_installed", lambda: ([], None))

    resp = client.post(
        "/registry/check-updates",
        json={"queries": ["qwen"], "free_vram_mb": 8192, "fit_filter": "gpu"},
    ).json()
    found = resp["results"][0]["candidates"]
    assert [c["id"] for c in found] == ["org/qwen-4b-q4_k_m-GGUF"]
    assert found[0]["fit"]["verdict"] == "FITS"


def test_check_updates_installed_match_requires_family_and_size(monkeypatch) -> None:
    from localdeploy.web import registry as reg

    candidates = [
        {"id": "org/qwen3-8b-GGUF"},
        {"id": "org/qwen3.6-27B-GGUF"},
    ]

    monkeypatch.setattr(reg, "_list_hf", lambda q, limit, gguf_only=True: (list(candidates), None))
    monkeypatch.setattr(reg, "offline_mode", lambda: False)
    from localdeploy.web import _ollama

    monkeypatch.setattr(_ollama, "list_installed", lambda: ([{"name": "qwen3:8b"}], None))

    resp = client.post("/registry/check-updates", json={"queries": ["qwen"]}).json()
    found = resp["results"][0]["candidates"]
    assert found[0]["installed_match"] is True
    assert found[1]["installed_match"] is False


def test_installed_list_includes_details(monkeypatch) -> None:
    """Installed endpoint returns details (quant, param size) for UI rendering."""
    from localdeploy.web import _ollama

    fake_models = [
        {
            "name": "gemma3:4b",
            "size": 2_500_000_000,
            "modified_at": "2026-01-10T08:00:00Z",
            "digest": "abc123def456",
            "details": {
                "quantization_level": "Q4_K_M",
                "parameter_size": "4B",
                "family": "gemma",
            },
        }
    ]
    monkeypatch.setattr(_ollama, "list_installed", lambda: (fake_models, None))

    resp = client.get("/registry/installed").json()
    assert resp["success"] is True
    m = resp["installed"][0]
    assert m["name"] == "gemma3:4b"
    assert m["details"]["quantization_level"] == "Q4_K_M"
    assert m["details"]["parameter_size"] == "4B"


# ---------------------------------------------------------------------------
# Phase 6 — device-tagged report cards
# ---------------------------------------------------------------------------

def _make_card(device=None) -> dict:
    return build_card(
        {
            "profile": "gemma3_4b",
            "model_id": "gemma3:4b",
            "device": device,
            "hardware": {"gpu": "RTX 4090", "vram_total_mb": 24576},
            "tests": [
                {"name": "t1", "category": "planning", "success": True, "accuracy": 1.0, "elapsed_seconds": 0.5},
            ],
        }
    )


def test_build_card_includes_device() -> None:
    card = _make_card(device="gpu")
    assert card["device"] == "gpu"


def test_build_card_device_none_when_absent() -> None:
    card = _make_card(device=None)
    assert card.get("device") is None


def test_render_md_shows_device() -> None:
    card = _make_card(device="cpu")
    md = render_md(card)
    assert "[CPU]" in md


def test_render_md_no_device_suffix_when_absent() -> None:
    card = _make_card(device=None)
    md = render_md(card)
    assert "[CPU]" not in md
    assert "[GPU]" not in md


def test_render_html_shows_device() -> None:
    card = _make_card(device="gpu")
    html = render_html(card)
    assert "[GPU]" in html


def test_render_html_no_device_when_absent() -> None:
    card = _make_card(device=None)
    html = render_html(card)
    assert "[GPU]" not in html
    assert "[CPU]" not in html


def test_compare_labels_include_device() -> None:
    """When cards have a device field, compare labels show 'model/GPU' style."""
    resp = client.post(
        "/benchmark/compare",
        json={
            "card_a": {**_make_card("gpu"), "tests": []},
            "card_b": {**_make_card("cpu"), "tests": []},
        },
    ).json()
    assert resp["success"] is True
    assert "GPU" in resp["label_a"]
    assert "CPU" in resp["label_b"]


def test_compare_labels_no_device_suffix_when_absent() -> None:
    """Cards without device produce plain model-name labels."""
    resp = client.post(
        "/benchmark/compare",
        json={
            "card_a": {**_make_card(None), "tests": []},
            "card_b": {**_make_card(None), "tests": []},
        },
    ).json()
    assert resp["success"] is True
    assert "/" not in resp["label_a"]
    assert "/" not in resp["label_b"]


def test_export_card_endpoint_includes_device(monkeypatch) -> None:
    """POST /benchmark/export round-trips the device field."""
    payload = {
        "profile": "gemma3_4b",
        "model_id": "gemma3:4b",
        "device": "gpu",
        "hardware": {},
        "tests": [],
    }
    resp = client.post("/benchmark/export", json=payload).json()
    assert resp["success"] is True
    assert resp["card"]["device"] == "gpu"
    assert "[GPU]" in resp["html"]
    assert "[GPU]" in resp["md"]
