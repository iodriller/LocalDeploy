"""Shared Ollama control-plane helpers for the web package.

One place for the local Ollama HTTP calls used by registry + model lifecycle, so
those routes never duplicate connection/error handling. Every function either
returns a clean ``(data, error)`` pair or raises ``BackendCallError`` /
``requests.RequestException`` for the caller to translate into a graceful
response. Nothing here ever talks to a non-loopback host.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

from ..utils import BackendCallError, is_loopback_url, strip_trailing_slash


def base_url() -> str:
    base = strip_trailing_slash(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    if not is_loopback_url(base):
        raise BackendCallError(f"Refusing non-local Ollama URL: {base}")
    return base


def list_installed() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Installed models via /api/tags. Returns ([], error) when unreachable."""
    try:
        base = base_url()
    except BackendCallError as exc:
        return [], str(exc)
    try:
        response = requests.get(f"{base}/api/tags", timeout=5)
        response.raise_for_status()
    except requests.ConnectionError:
        return [], f"Ollama is not reachable at {base}. Start Ollama and retry."
    except requests.RequestException as exc:
        return [], str(exc)
    out: List[Dict[str, Any]] = []
    for item in response.json().get("models", []):
        out.append(
            {
                "name": item.get("name"),
                "size": item.get("size"),
                "modified_at": item.get("modified_at"),
                "digest": (item.get("digest") or "")[:12] or None,
                "details": item.get("details", {}),
            }
        )
    return out, None


def list_running() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Currently loaded models via /api/ps. Returns ([], error) when unreachable."""
    try:
        base = base_url()
    except BackendCallError as exc:
        return [], str(exc)
    try:
        response = requests.get(f"{base}/api/ps", timeout=5)
        response.raise_for_status()
    except requests.ConnectionError:
        return [], f"Ollama is not reachable at {base}. Start Ollama and retry."
    except requests.RequestException as exc:
        return [], str(exc)
    out: List[Dict[str, Any]] = []
    for item in response.json().get("models", []):
        out.append(
            {
                "name": item.get("name"),
                "size": item.get("size"),
                "size_vram": item.get("size_vram"),
                "expires_at": item.get("expires_at"),
            }
        )
    return out, None


def load_model(
    model: str, keep_alive: str = "5m", num_gpu: Optional[int] = None
) -> Dict[str, Any]:
    """Warm a model into memory. An empty prompt makes Ollama load without generating.

    ``num_gpu`` sets how many layers to offload to the GPU (0 = force CPU). When
    None, Ollama decides (auto) — identical to the prior behaviour.
    """
    base = base_url()
    payload: Dict[str, Any] = {"model": model, "keep_alive": keep_alive}
    if num_gpu is not None:
        payload["options"] = {"num_gpu": num_gpu}
    response = requests.post(
        f"{base}/api/generate",
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def unload_model(model: str) -> Dict[str, Any]:
    """Unload a model immediately via keep_alive=0."""
    base = base_url()
    response = requests.post(
        f"{base}/api/generate",
        json={"model": model, "keep_alive": 0},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def pull_stream(model: str) -> Iterator[Dict[str, Any]]:
    """Yield progress events from `ollama pull` (JSON lines)."""
    base = base_url()
    with requests.post(
        f"{base}/api/pull",
        json={"name": model, "stream": True},
        stream=True,
        timeout=(10, 120),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            try:
                yield json.loads(line)
            except (ValueError, TypeError):
                continue
