from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

from ..utils import BackendCallError, get_backend_base_url


def _headers(profile: Dict[str, Any]) -> Dict[str, str]:
    token = str(profile.get("api_key") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _messages(prepared: Dict[str, Any]) -> List[Dict[str, Any]]:
    supplied = prepared.get("messages") or []
    if supplied:
        return [dict(item) for item in supplied if isinstance(item, dict)]
    messages: List[Dict[str, Any]] = []
    if prepared.get("system_prompt"):
        messages.append({"role": "system", "content": prepared["system_prompt"]})
    messages.append({"role": "user", "content": prepared.get("prompt") or ""})
    return messages


def _payload(prepared: Dict[str, Any], stream: bool) -> Dict[str, Any]:
    model = str(prepared["model"])
    if prepared.get("backend") == "llamacpp" and model.lower().endswith(".gguf"):
        model = Path(model).stem
    payload: Dict[str, Any] = {
        "model": model,
        "messages": _messages(prepared),
        "stream": stream,
        "max_tokens": prepared["max_output_tokens_used"],
    }
    for name in ("temperature", "top_p", "repeat_penalty", "response_format"):
        if prepared.get(name) is not None:
            payload[name] = prepared[name]
    tools = prepared.get("tools") or []
    if tools and prepared.get("tool_choice") != "none":
        payload["tools"] = tools
        if prepared.get("tool_choice") is not None:
            payload["tool_choice"] = prepared["tool_choice"]
    return payload


def _error(response: requests.Response, provider: str) -> str:
    body = response.text.strip()
    try:
        data = response.json()
        error = data.get("error")
        if isinstance(error, dict):
            body = str(error.get("message") or body)
        elif error:
            body = str(error)
    except Exception:
        pass
    return f"{provider} request failed with HTTP {response.status_code}. Details: {body[:1000]}"


def call_openai_compatible(prepared: Dict[str, Any]) -> Dict[str, Any]:
    profile = prepared["profile"]
    provider = str(prepared["backend"])
    base_url = get_backend_base_url(profile, provider)
    try:
        response = requests.post(
            f"{base_url}/v1/chat/completions",
            json=_payload(prepared, False),
            headers=_headers(profile),
            timeout=prepared["timeout_seconds"],
        )
    except requests.Timeout as exc:
        raise BackendCallError(f"{provider} timed out after {prepared['timeout_seconds']} seconds.") from exc
    except requests.ConnectionError as exc:
        raise BackendCallError(f"{provider} is not reachable at {base_url}.") from exc
    if not response.ok:
        raise BackendCallError(_error(response, provider))
    try:
        data = response.json()
        choice = data["choices"][0]
        message = choice.get("message") or {}
    except Exception as exc:
        raise BackendCallError(f"{provider} returned an invalid chat response: {response.text[:500]}") from exc
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    return {
        "content": str(message.get("content") or ""),
        "tool_calls": message.get("tool_calls") or [],
        "done_reason": choice.get("finish_reason"),
        "metrics": {
            "prompt_eval_count": usage.get("prompt_tokens"),
            "eval_count": usage.get("completion_tokens"),
            "tokens_per_second": details.get("tokens_per_second") or usage.get("tokens_per_second"),
        },
    }


def stream_openai_compatible(prepared: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    profile = prepared["profile"]
    provider = str(prepared["backend"])
    base_url = get_backend_base_url(profile, provider)
    try:
        response = requests.post(
            f"{base_url}/v1/chat/completions",
            json=_payload(prepared, True),
            headers=_headers(profile),
            timeout=prepared["timeout_seconds"],
            stream=True,
        )
    except requests.Timeout as exc:
        raise BackendCallError(f"{provider} timed out after {prepared['timeout_seconds']} seconds.") from exc
    except requests.ConnectionError as exc:
        raise BackendCallError(f"{provider} is not reachable at {base_url}.") from exc
    with response:
        if not response.ok:
            raise BackendCallError(_error(response, provider))
        for raw in response.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            if not line.startswith("data:"):
                continue
            value = line[5:].strip()
            if value == "[DONE]":
                break
            try:
                data = json.loads(value)
                choice = data["choices"][0]
                delta = choice.get("delta") or {}
            except Exception:
                continue
            yield {
                "content": str(delta.get("content") or ""),
                "tool_calls": delta.get("tool_calls") or [],
                "done": bool(choice.get("finish_reason")),
                "done_reason": choice.get("finish_reason"),
                "metrics": data.get("usage"),
            }


def embed_openai_compatible(
    profile: Dict[str, Any], backend: str, model: str, inputs: List[str], timeout: int
) -> Dict[str, Any]:
    base_url = get_backend_base_url(profile, backend)
    if backend == "llamacpp" and model.lower().endswith(".gguf"):
        model = Path(model).stem
    try:
        response = requests.post(
            f"{base_url}/v1/embeddings",
            json={"model": model, "input": inputs, "encoding_format": "float"},
            headers=_headers(profile),
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise BackendCallError(f"{backend} embedding request timed out after {timeout} seconds.") from exc
    except requests.ConnectionError as exc:
        raise BackendCallError(f"{backend} is not reachable at {base_url}.") from exc
    if not response.ok:
        raise BackendCallError(_error(response, backend))
    data = response.json()
    rows = sorted(data.get("data") or [], key=lambda item: int(item.get("index", 0)))
    return {"embeddings": [item.get("embedding") or [] for item in rows], "usage": data.get("usage") or {}}


def list_openai_models(
    base_url: str, provider: str, headers: Optional[Dict[str, str]] = None
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/v1/models", headers=headers or {}, timeout=3)
    except (requests.ConnectionError, requests.Timeout):
        return [], f"{provider} is not reachable at {base_url}."
    if not response.ok:
        return [], _error(response, provider)
    try:
        return [item for item in response.json().get("data", []) if item.get("id")], None
    except Exception as exc:
        return [], f"{provider} returned invalid model inventory: {exc}"
