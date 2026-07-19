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
    base = strip_trailing_slash(os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
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
                "digest": item.get("digest") or None,
                "digest_short": (item.get("digest") or "")[:12] or None,
                "details": item.get("details", {}),
            }
        )
    return out, None


def version() -> Tuple[Optional[str], Optional[str]]:
    """Return the exact Ollama server version when the local daemon is reachable."""
    try:
        base = base_url()
        response = requests.get(f"{base}/api/version", timeout=3)
        response.raise_for_status()
        value = response.json().get("version")
        return (str(value) if value else None), None
    except requests.ConnectionError:
        return None, f"Ollama is not reachable at {base if 'base' in locals() else 'localhost'}."
    except (BackendCallError, requests.RequestException, ValueError) as exc:
        return None, str(exc)


def show_model(model: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return Ollama's model metadata, including quant and context parameters."""
    try:
        base = base_url()
        response = requests.post(f"{base}/api/show", json={"model": model}, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None, None
    except requests.ConnectionError:
        return None, f"Ollama is not reachable at {base if 'base' in locals() else 'localhost'}."
    except (BackendCallError, requests.RequestException, ValueError) as exc:
        return None, str(exc)


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


# Warming a model means loading all its weights into memory. On the GPU this is
# quick; forcing CPU offload (num_gpu=0) for a large model can take minutes, so a
# flat 120s timeout would spuriously fail those. Both are overridable via env.
_GPU_LOAD_TIMEOUT = 120
_CPU_LOAD_TIMEOUT = 600


def _load_timeout(num_gpu: Optional[int]) -> int:
    """Pick a warm-up timeout: longer for CPU offload, overridable via env."""
    override = os.getenv("OLLAMA_LOAD_TIMEOUT")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return _CPU_LOAD_TIMEOUT if num_gpu == 0 else _GPU_LOAD_TIMEOUT


def load_model(
    model: str, keep_alive: str = "60m", num_gpu: Optional[int] = None
) -> Dict[str, Any]:
    """Warm a model into memory. An empty prompt makes Ollama load without generating.

    ``num_gpu`` sets how many layers to offload to the GPU (0 = force CPU). When
    None, Ollama decides (auto) - identical to the prior behaviour. The request
    timeout scales with the target device (CPU loads are slower).
    """
    base = base_url()
    payload: Dict[str, Any] = {"model": model, "keep_alive": keep_alive}
    if num_gpu is not None:
        payload["options"] = {"num_gpu": num_gpu}
    response = requests.post(
        f"{base}/api/generate",
        json=payload,
        timeout=_load_timeout(num_gpu),
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


def delete_model(model: str) -> Dict[str, Any]:
    """Delete a model from disk via /api/delete (frees disk space)."""
    base = base_url()
    response = requests.delete(f"{base}/api/delete", json={"name": model}, timeout=30)
    # Ollama returns 200 (often with an empty body) on success, 404 if absent.
    if response.status_code == 404:
        raise BackendCallError(f"Model '{model}' is not installed.")
    response.raise_for_status()
    return {"deleted": model}


def unload_all() -> Tuple[int, Optional[str]]:
    """Unload every currently-loaded model from memory. Returns (count, error)."""
    running, err = list_running()
    if err is not None:
        return 0, err
    unloaded = 0
    for m in running:
        name = m.get("name")
        if not name:
            continue
        try:
            unload_model(name)
            unloaded += 1
        except (BackendCallError, requests.RequestException):
            continue  # best-effort; keep unloading the rest
    return unloaded, None


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
