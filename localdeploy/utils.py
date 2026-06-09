from __future__ import annotations

import os
from typing import Any, Dict
from urllib.parse import urlparse

from pydantic import BaseModel


class BackendCallError(Exception):
    pass


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
        base_url = profile.get("base_url") or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    elif backend == "llamacpp":
        base_url = profile.get("base_url") or os.getenv("LLAMACPP_BASE_URL", "http://localhost:8080")
    else:
        raise BackendCallError(f"Unsupported backend '{backend}'.")
    base_url = strip_trailing_slash(str(base_url))
    if not is_loopback_url(base_url):
        raise BackendCallError(
            f"Refusing to call non-local backend URL '{base_url}'. Only localhost or 127.0.0.1 are allowed."
        )
    return base_url
