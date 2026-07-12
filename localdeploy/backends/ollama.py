from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

from ..utils import (
    BackendCallError,
    get_backend_base_url,
    is_loopback_url,
    require_gpu_only,
    strip_trailing_slash,
)


def ollama_format(response_format: Any) -> Any:
    """Translate OpenAI response_format into Ollama's native `format` value."""
    if not isinstance(response_format, dict):
        return None
    format_type = response_format.get("type")
    if format_type == "json_object":
        return "json"
    if format_type == "json_schema":
        wrapper = response_format.get("json_schema")
        if isinstance(wrapper, dict) and isinstance(wrapper.get("schema"), dict):
            return wrapper["schema"]
    return None


def options_payload(prepared: Dict[str, Any]) -> Dict[str, Any]:
    options: Dict[str, Any] = {
        "num_ctx": prepared["context_limit_used"],
        "num_predict": prepared["max_output_tokens_used"],
    }
    for key in ("temperature", "top_p", "repeat_penalty"):
        if prepared.get(key) is not None:
            options[key] = prepared[key]
    # Optional CPU/GPU placement: num_gpu is the number of layers offloaded to GPU
    # (0 = force CPU). Absent -> Ollama's default (auto), preserving prior behaviour.
    if prepared.get("num_gpu") is not None:
        options["num_gpu"] = prepared["num_gpu"]
    return options


def ollama_error_message(response: requests.Response, model_id: str) -> str:
    body = response.text.strip()
    try:
        parsed = response.json()
        body = parsed.get("error") or body
    except Exception:
        pass
    lowered = body.lower()
    if response.status_code == 404 or "not found" in lowered or "pull" in lowered:
        return f"Ollama model '{model_id}' is not available. Run: ollama pull {model_id}. Details: {body}"
    if "out of memory" in lowered or "cuda" in lowered or "memory" in lowered:
        return f"Ollama reported a memory-related failure. Lower context/output limits or use a smaller profile. Details: {body}"
    return f"Ollama request failed with HTTP {response.status_code}. Details: {body}"


def call_ollama(prepared: Dict[str, Any]) -> str:
    if require_gpu_only():
        raise BackendCallError("GPU-only mode is enabled; refusing to call Ollama.")
    profile = prepared["profile"]
    base_url = get_backend_base_url(profile, "ollama")
    messages: List[Dict[str, Any]] = []
    if prepared["system_prompt"]:
        messages.append({"role": "system", "content": prepared["system_prompt"]})
    user_message: Dict[str, Any] = {"role": "user", "content": prepared["prompt"]}
    if prepared["images_base64"]:
        user_message["images"] = prepared["images_base64"]
    messages.append(user_message)

    payload = {
        "model": prepared["model"],
        "messages": messages,
        "stream": False,
        "options": options_payload(prepared),
    }
    native_format = ollama_format(prepared.get("response_format"))
    if native_format is not None:
        payload["format"] = native_format
    # Thinking models (Qwen3, DeepSeek-R1) put reasoning into a separate `thinking`
    # field; `content` is empty until the model finishes. `think: false` disables
    # the reasoning pass entirely for cases where we only want the final answer.
    if profile.get("think") is not None:
        payload["think"] = bool(profile["think"])
    try:
        response = requests.post(f"{base_url}/api/chat", json=payload, timeout=prepared["timeout_seconds"])
    except requests.Timeout as exc:
        raise BackendCallError(
            f"Ollama request timed out after {prepared['timeout_seconds']} seconds. Lower context/output limits or try a smaller profile."
        ) from exc
    except requests.ConnectionError as exc:
        raise BackendCallError(f"Ollama is not running or is unreachable at {base_url}. Start Ollama and retry.") from exc

    if not response.ok:
        raise BackendCallError(ollama_error_message(response, prepared["model"]))

    try:
        data = response.json()
    except Exception as exc:
        raise BackendCallError(f"Ollama returned invalid JSON: {response.text[:500]}") from exc

    if isinstance(data.get("message"), dict):
        return str(data["message"].get("content", ""))
    if "response" in data:
        return str(data.get("response", ""))
    return json.dumps(data, ensure_ascii=False)


def stream_ollama(prepared: Dict[str, Any]) -> Iterator[str]:
    """Yield successive content chunks from an Ollama streaming chat call."""
    if require_gpu_only():
        raise BackendCallError("GPU-only mode is enabled; refusing to call Ollama.")
    profile = prepared["profile"]
    base_url = get_backend_base_url(profile, "ollama")
    messages: List[Dict[str, Any]] = []
    if prepared["system_prompt"]:
        messages.append({"role": "system", "content": prepared["system_prompt"]})
    user_message: Dict[str, Any] = {"role": "user", "content": prepared["prompt"]}
    if prepared["images_base64"]:
        user_message["images"] = prepared["images_base64"]
    messages.append(user_message)

    payload = {
        "model": prepared["model"],
        "messages": messages,
        "stream": True,
        "options": options_payload(prepared),
    }
    native_format = ollama_format(prepared.get("response_format"))
    if native_format is not None:
        payload["format"] = native_format
    if profile.get("think") is not None:
        payload["think"] = bool(profile["think"])
    try:
        response = requests.post(
            f"{base_url}/api/chat",
            json=payload,
            timeout=prepared["timeout_seconds"],
            stream=True,
        )
    except requests.Timeout as exc:
        raise BackendCallError(
            f"Ollama request timed out after {prepared['timeout_seconds']} seconds. Lower context/output limits or try a smaller profile."
        ) from exc
    except requests.ConnectionError as exc:
        raise BackendCallError(f"Ollama is not running or is unreachable at {base_url}. Start Ollama and retry.") from exc

    # Use the response as a context manager so the underlying connection is
    # released even if the SSE consumer disconnects mid-stream (GeneratorExit).
    with response:
        if not response.ok:
            raise BackendCallError(ollama_error_message(response, prepared["model"]))

        try:
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                chunk = ""
                if isinstance(data.get("message"), dict):
                    chunk = str(data["message"].get("content", ""))
                elif "response" in data:
                    chunk = str(data.get("response", ""))
                if chunk:
                    yield chunk
                if data.get("done"):
                    break
        except requests.exceptions.RequestException as exc:
            raise BackendCallError(f"Ollama connection was lost mid-response: {exc}") from exc


def ollama_models(base_url: str) -> Tuple[List[str], Optional[str]]:
    if not is_loopback_url(base_url):
        return [], f"Refusing non-local Ollama URL: {base_url}"
    try:
        response = requests.get(f"{strip_trailing_slash(base_url)}/api/tags", timeout=3)
        response.raise_for_status()
        data = response.json()
        return [item.get("name", "") for item in data.get("models", []) if item.get("name")], None
    except requests.ConnectionError:
        return [], f"Ollama is not reachable at {base_url}."
    except Exception as exc:
        return [], str(exc)
