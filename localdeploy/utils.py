from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from pydantic import BaseModel


class BackendCallError(Exception):
    pass


# Repo root when running from a source checkout (this file is localdeploy/utils.py).
_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def app_home() -> Path:
    """Directory for runtime state: .env, config.json, logs/, reports/.

    Precedence: LOCALDEPLOY_HOME env var; a source checkout's repo root
    (unchanged historical behavior for `git clone` users); otherwise
    ~/.localdeploy for pip/pipx installs, where site-packages isn't a sane
    or writable place to keep state.
    """
    configured = os.getenv("LOCALDEPLOY_HOME")
    if configured and configured.strip():
        return Path(configured).expanduser()
    if (_SOURCE_ROOT / "config.example.json").is_file():
        return _SOURCE_ROOT
    return Path.home() / ".localdeploy"


def web_dir() -> Path:
    """The static web UI, shipped as package data (localdeploy/web)."""
    return Path(__file__).resolve().parent / "web"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def require_gpu_only() -> bool:
    return env_bool("REQUIRE_GPU_ONLY", False)


def enable_web_ui() -> bool:
    return env_bool("ENABLE_WEB_UI", True)


def offline_mode() -> bool:
    """When true, the server makes no outbound internet calls (Hugging Face
    update checks are skipped). Local backend calls to loopback are unaffected.
    LocalDeploy has no telemetry; this is a hard, verifiable guarantee."""
    return env_bool("OFFLINE", False)


def api_token() -> str:
    """Optional shared secret. When set (API_TOKEN), the HTTP API requires it;
    when empty (default), there is no auth and zero overhead. Opt-in security."""
    return (os.getenv("API_TOKEN") or "").strip()


def api_auth_headers() -> Dict[str, str]:
    """Headers for bundled clients calling LocalDeploy's own HTTP API."""
    token = api_token()
    return {"X-API-Token": token} if token else {}


def api_client_base_url(host: str | None = None, port: str | int | None = None) -> str:
    """Build a usable client URL from API bind settings.

    Wildcard bind addresses are not portable connection targets, and IPv6
    literals need brackets when embedded in a URL.
    """
    resolved_host = (host if host is not None else os.getenv("API_HOST", "127.0.0.1")).strip()
    if resolved_host in {"", "0.0.0.0", "::"}:
        resolved_host = "127.0.0.1"
    url_host = resolved_host
    if ":" in url_host and not (url_host.startswith("[") and url_host.endswith("]")):
        url_host = f"[{url_host}]"
    resolved_port = port if port is not None else os.getenv("API_PORT", "8000")
    return f"http://{url_host}:{resolved_port}"


def model_dump_compat(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def model_validate_compat(model_cls: Any, data: Dict[str, Any]) -> BaseModel:
    if hasattr(model_cls, "model_validate"):
        return model_cls.model_validate(data)
    return model_cls(**data)


def is_loopback_url(base_url: str) -> bool:
    try:
        parsed = urlparse(base_url)
    except Exception:
        return False
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and hostname in {"localhost", "127.0.0.1", "::1"}


def strip_trailing_slash(url: str) -> str:
    return url.rstrip("/")


def get_backend_base_url(profile: Dict[str, Any], backend: str) -> str:
    if backend == "ollama":
        base_url = profile.get("base_url") or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    elif backend == "llamacpp":
        base_url = profile.get("base_url") or os.getenv("LLAMACPP_BASE_URL", "http://127.0.0.1:8080")
    elif backend == "lmstudio":
        base_url = profile.get("base_url") or os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234")
    elif backend == "docker":
        base_url = profile.get("base_url") or os.getenv("DOCKER_MODEL_RUNNER_BASE_URL", "http://127.0.0.1:12434")
    elif backend == "vllm":
        base_url = profile.get("base_url") or os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8001")
    elif backend == "openai":
        base_url = profile.get("base_url") or os.getenv("OPENAI_COMPAT_BASE_URL", "http://127.0.0.1:8001")
    else:
        raise BackendCallError(f"Unsupported backend '{backend}'.")
    base_url = strip_trailing_slash(str(base_url))
    if not is_loopback_url(base_url):
        raise BackendCallError(
            f"Refusing to call non-local backend URL '{base_url}'. Only localhost or 127.0.0.1 are allowed."
        )
    return base_url
