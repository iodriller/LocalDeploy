"""Backend payload tests for native schema-constrained structured output."""
from __future__ import annotations

from pathlib import Path

from localdeploy.backends import llamacpp, ollama


SCHEMA_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "assessment",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"passed": {"type": "boolean"}},
            "required": ["passed"],
            "additionalProperties": False,
        },
    },
}


class FakeResponse:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def _prepared(model, backend, response_format=SCHEMA_FORMAT):
    return {
        "profile": {"base_url": f"http://127.0.0.1:{11434 if backend == 'ollama' else 8080}"},
        "model": str(model),
        "system_prompt": "",
        "prompt": "Assess this.",
        "images_base64": [],
        "context_limit_used": 2048,
        "max_output_tokens_used": 128,
        "temperature": 0,
        "top_p": 0.9,
        "repeat_penalty": 1.0,
        "num_gpu": None,
        "timeout_seconds": 30,
        "response_format": response_format,
    }


def test_ollama_receives_native_json_schema(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured.update({"url": url, "payload": json, "timeout": timeout})
        return FakeResponse({"message": {"content": '{"passed":true}'}})

    monkeypatch.setattr(ollama.requests, "post", fake_post)
    content = ollama.call_ollama(_prepared("qwen3.6:27b", "ollama"))
    assert content == '{"passed":true}'
    assert captured["payload"]["format"] == SCHEMA_FORMAT["json_schema"]["schema"]


def test_ollama_json_object_uses_json_mode(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse({"message": {"content": "{}"}})

    monkeypatch.setattr(ollama.requests, "post", fake_post)
    ollama.call_ollama(_prepared("qwen3.6:27b", "ollama", {"type": "json_object"}))
    assert captured["payload"]["format"] == "json"


def test_llamacpp_receives_openai_response_format(monkeypatch, tmp_path: Path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    captured = {}

    def fake_post(url, json, timeout):
        captured.update({"url": url, "payload": json, "timeout": timeout})
        return FakeResponse({"choices": [{"message": {"content": '{"passed":true}'}}]})

    monkeypatch.setattr(llamacpp.requests, "post", fake_post)
    content = llamacpp.call_llamacpp(_prepared(model, "llamacpp"))
    assert content == '{"passed":true}'
    assert captured["payload"]["response_format"] == SCHEMA_FORMAT
