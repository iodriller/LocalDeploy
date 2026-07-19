from __future__ import annotations

import base64

from fastapi.testclient import TestClient

import api_server
from api_server import app
from localdeploy.backends import ollama

client = TestClient(app)


def _success(**overrides):
    value = {
        "success": True,
        "backend": "ollama",
        "profile": "gemma3_4b_ollama_safe",
        "model": "gemma3:4b",
        "response": "",
        "tool_calls": [],
        "metrics": {"prompt_eval_count": 4, "eval_count": 2, "tokens_per_second": 20.0},
        "estimated_prompt_tokens": 4,
    }
    value.update(overrides)
    return value


def test_chat_completions_returns_normalized_tool_call(monkeypatch) -> None:
    monkeypatch.setattr(
        api_server,
        "run_local_request",
        lambda kind, payload: _success(
            tool_calls=[{"function": {"name": "weather", "arguments": {"city": "Austin"}}}]
        ),
    )
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gemma3_4b_ollama_safe",
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                    },
                }
            ],
        },
    )
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"]["arguments"] == '{"city": "Austin"}'


def test_responses_endpoint_returns_function_call_item(monkeypatch) -> None:
    monkeypatch.setattr(
        api_server,
        "run_local_request",
        lambda kind, payload: _success(
            tool_calls=[{"id": "call_1", "function": {"name": "lookup", "arguments": "{\"id\":1}"}}]
        ),
    )
    response = client.post(
        "/v1/responses",
        json={
            "model": "gemma3_4b_ollama_safe",
            "input": "Look it up",
            "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
        },
    )
    body = response.json()
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["output"][0]["type"] == "function_call"
    assert body["output"][0]["name"] == "lookup"


def test_responses_stream_emits_progressive_typed_events(monkeypatch) -> None:
    monkeypatch.setattr(
        api_server,
        "stream_ollama_events",
        lambda prepared: iter(
            [
                {"content": "hello ", "tool_calls": [], "done": False, "metrics": None},
                {
                    "content": "world",
                    "tool_calls": [],
                    "done": True,
                    "done_reason": "stop",
                    "metrics": {"prompt_eval_count": 3, "eval_count": 2},
                },
            ]
        ),
    )
    response = client.post(
        "/v1/responses",
        json={"model": "gemma3_4b_ollama_safe", "input": "Say hello", "stream": True},
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("event: response.output_text.delta") == 2
    assert "event: response.completed" in response.text
    assert '"output_text": "hello world"' in response.text


def test_chat_completions_stream_feeds_monitor(monkeypatch) -> None:
    # Regression: streaming responses bypassed run_local_request entirely, so
    # the Monitor tab's request log/alerts never saw streamed traffic — likely
    # the majority of real OpenAI-compatible client usage (Continue, Cline, etc).
    from localdeploy.control import monitor

    monkeypatch.setattr(
        api_server,
        "stream_ollama_events",
        lambda prepared: iter(
            [
                {"content": "hi", "tool_calls": [], "done": False, "metrics": None},
                {"content": "", "tool_calls": [], "done": True, "done_reason": "stop",
                 "metrics": {"prompt_eval_count": 5, "eval_count": 3, "tokens_per_second": 12.0}},
            ]
        ),
    )
    calls = []
    monkeypatch.setattr(monitor, "record_request", lambda **kwargs: calls.append(kwargs))
    response = client.post(
        "/v1/chat/completions",
        json={"model": "gemma3_4b_ollama_safe", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    assert len(calls) == 1
    assert calls[0]["success"] is True
    assert calls[0]["kind"] == "chat"
    assert calls[0]["metrics"]["tokens_per_second"] == 12.0
    assert monitor._active_requests == {}


def test_chat_completions_stream_error_still_feeds_monitor(monkeypatch) -> None:
    from localdeploy.backends.ollama import BackendCallError
    from localdeploy.control import monitor

    def _boom(prepared):
        raise BackendCallError("Ollama is not running or is unreachable.")
        yield  # pragma: no cover - unreachable, keeps this a generator

    monkeypatch.setattr(api_server, "stream_ollama_events", _boom)
    calls = []
    monkeypatch.setattr(monitor, "record_request", lambda **kwargs: calls.append(kwargs))
    response = client.post(
        "/v1/chat/completions",
        json={"model": "gemma3_4b_ollama_safe", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    assert len(calls) == 1
    assert calls[0]["success"] is False
    assert "unreachable" in calls[0]["error"]
    assert monitor._active_requests == {}


def test_responses_stream_feeds_monitor(monkeypatch) -> None:
    from localdeploy.control import monitor

    monkeypatch.setattr(
        api_server,
        "stream_ollama_events",
        lambda prepared: iter(
            [
                {"content": "hello ", "tool_calls": [], "done": False, "metrics": None},
                {"content": "world", "tool_calls": [], "done": True, "done_reason": "stop",
                 "metrics": {"prompt_eval_count": 3, "eval_count": 2, "ttft_ms": 150.0}},
            ]
        ),
    )
    calls = []
    monkeypatch.setattr(monitor, "record_request", lambda **kwargs: calls.append(kwargs))
    response = client.post(
        "/v1/responses",
        json={"model": "gemma3_4b_ollama_safe", "input": "Say hello", "stream": True},
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    assert len(calls) == 1
    assert calls[0]["success"] is True
    assert calls[0]["metrics"]["ttft_ms"] == 150.0
    assert monitor._active_requests == {}


def test_embeddings_base64_is_little_endian_float32(monkeypatch) -> None:
    monkeypatch.setattr(
        api_server,
        "embed_ollama",
        lambda *args, **kwargs: {"embeddings": [[1.0, -2.0]], "prompt_eval_count": 1},
    )
    response = client.post(
        "/v1/embeddings",
        json={"model": "gemma3_4b_ollama_safe", "input": "x", "encoding_format": "base64"},
    )
    encoded = response.json()["data"][0]["embedding"]
    assert base64.b64decode(encoded).hex() == "0000803f000000c0"


class _OllamaResponse:
    ok = True
    status_code = 200
    text = ""

    def json(self):
        return {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "lookup", "arguments": {"id": 1}}}],
            },
            "eval_count": 20,
            "eval_duration": 2_000_000_000,
            "prompt_eval_count": 5,
            "prompt_eval_duration": 500_000_000,
            "done_reason": "stop",
        }


def test_ollama_detailed_forwards_tools_and_records_native_rate(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, timeout):
        captured["payload"] = json
        return _OllamaResponse()

    monkeypatch.setattr(ollama.requests, "post", fake_post)
    prepared = {
        "profile": {"base_url": "http://127.0.0.1:11434"},
        "model": "model",
        "messages": [{"role": "user", "content": "Use a tool"}],
        "system_prompt": "",
        "prompt": "Use a tool",
        "images_base64": [],
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
        "tool_choice": "auto",
        "context_limit_used": 2048,
        "max_output_tokens_used": 128,
        "temperature": 0,
        "top_p": 1,
        "repeat_penalty": 1,
        "num_gpu": None,
        "timeout_seconds": 30,
        "response_format": None,
    }
    result = ollama.call_ollama_detailed(prepared)
    assert captured["payload"]["tools"][0]["function"]["name"] == "lookup"
    assert result["tool_calls"][0]["function"]["name"] == "lookup"
    assert result["metrics"]["tokens_per_second"] == 10.0
