"""HTTP-level tests using FastAPI's TestClient. No external services required."""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

import api_server
from api_server import app

client = TestClient(app)


def test_run_benchmark_reports_backend_for_single_profile(monkeypatch) -> None:
    # A single-profile run must report that profile's real backend, not null
    # (null was previously reserved for "unknown", but every single-profile
    # run has a known backend from its config entry).
    monkeypatch.setattr(
        api_server,
        "run_local_request",
        lambda kind, call_data: {"success": True, "elapsed_seconds": 0.1, "response": "ok"},
    )
    result = api_server.run_benchmark({"profile": "gemma3_4b_ollama_safe", "prompt": "hi"})
    assert result["backend"] == "ollama"


def test_run_benchmark_reports_mixed_for_multiple_profiles(monkeypatch) -> None:
    monkeypatch.setattr(
        api_server,
        "run_local_request",
        lambda kind, call_data: {"success": True, "elapsed_seconds": 0.1, "response": "ok"},
    )
    result = api_server.run_benchmark({"all_profiles": True, "prompt": "hi"})
    assert result["backend"] == "mixed"


def test_embeddings_returns_501() -> None:
    response = client.post("/v1/embeddings", json={"input": "hello", "model": "any"})
    assert response.status_code == 501
    body = response.json()
    assert body["error"]["code"] == "embeddings_not_implemented"
    assert "ollama" in body["error"]["message"].lower()


def test_v1_models_lists_enabled_profiles() -> None:
    response = client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert isinstance(body["data"], list)


def test_profiles_endpoint_returns_default() -> None:
    response = client.get("/profiles")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["default_profile"] is not None
    assert isinstance(body["profiles"], dict)


def test_streaming_endpoint_returns_sse_on_validation_error() -> None:
    # Oversized prompt fails validation before backend is called; we should still
    # see a valid SSE stream that delivers the error and a [DONE] terminator.
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gemma3_4b_ollama_safe",
            "messages": [{"role": "user", "content": "x" * 50_000}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "data: " in body
    assert "[DONE]" in body


def test_native_chat_request_schema_has_no_stream_field() -> None:
    schema = client.get("/openapi.json").json()
    chat_request = schema["components"]["schemas"]["ChatRequest"]
    assert "stream" not in chat_request.get("properties", {})
