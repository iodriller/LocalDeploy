"""Shared config.json read/write helpers for the control plane.

`config.json` is the live, gitignored, machine-local file that mirrors what the
user actually has: pulling a model creates a profile here, deleting one can remove
it. Several routers (recommend/set-default, profiles CRUD, models/pull auto-create)
mutate it, so the atomic writer, the example-file guard, and the "build a sensible
default profile for a pulled model" logic live here in one place.

Nothing here imports ``api_server`` at module load (that would be circular); the
functions that need config loading do a lazy import.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def write_config_atomic(config: Dict[str, Any], path: Path) -> Optional[str]:
    """Atomically write `config` to `path`. Returns an error string, or None on success."""
    try:
        # Atomic write: a concurrent load_config() reader must never observe a
        # half-written file (which would raise JSONDecodeError -> 500).
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".config.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(config, indent=2))
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    except OSError as exc:
        return f"Could not write {path}: {exc}"
    return None


def refuse_example(path: Path) -> Optional[str]:
    """Guard: never overwrite the shipped, test-fixture config.example.json."""
    if path.name == "config.example.json":
        return (
            "Refusing to overwrite config.example.json. Point CONFIG_PATH at a real "
            "config.json (a missing live config starts empty)."
        )
    return None


# --- profile derivation (auto-create-from-pulled-model) ----------------------

# Families whose names signal a vision-language model. Best-effort only; the
# profile's supports_vision is a hint for the UI, not a hard capability gate.
_VISION_HINTS = (
    "-vl",
    "vl:",
    "vision",
    "llava",
    "moondream",
    "minicpm-v",
    "-v:",
    # Qwen 3.5+ unified models carry a native vision encoder without `-vl` in
    # the public model name (for example `qwen3.6:27b`).
    "qwen3.5",
    "qwen3.6",
)


def slugify_profile_name(model_id: str) -> str:
    """Turn a model id into a config-safe profile key.

    'gemma3:4b' -> 'gemma3_4b'; 'hf.co/org/Repo-GGUF' -> 'hf_co_org_repo_gguf'.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (model_id or "").lower()).strip("_")
    return slug or "model"


def _looks_like_vision(model_id: str) -> bool:
    low = (model_id or "").lower()
    return any(hint in low for hint in _VISION_HINTS)


def default_profile_for(
    model_id: str, *, params_b: Optional[float] = None, quant: Optional[str] = None, backend: str = "ollama"
) -> Dict[str, Any]:
    """A conservative profile for a freshly pulled/imported model.

    Mirrors the field shape of the shipped example profiles so the rest of the
    app (fit-check, benchmark, serve) treats it identically to a hand-written one.
    """
    name = slugify_profile_name(model_id)
    supports_vision = _looks_like_vision(model_id)
    try:
        from ..utils import get_backend_base_url

        base_url = get_backend_base_url({}, backend)
    except Exception:
        base_url = "http://127.0.0.1:11434"
    profile = {
        "name": name,
        "backend": backend,
        "model_id": model_id,
        "base_url": base_url,
        "enabled": True,
        "description": f"Auto-created from pulled model {model_id}.",
        "auto_created": True,
        "supports_vision": supports_vision,
        # Existing hand-authored profiles without this field retain the legacy
        # one-image limit. New vision profiles start conservatively at four and
        # can be raised explicitly after hardware qualification.
        "max_images": 4 if supports_vision else 1,
        "context_limit": 8192,
        "safe_context_limit": 8192,
        "max_prompt_chars": 20000,
        "max_output_tokens": 2048,
        "temperature": 0.2,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
        "timeout_seconds": 240,
        "slow_response_seconds": 60,
        "notes": "Auto-created when this model was pulled. Edit tuning as needed.",
        "quantization": quant,
        "gpu_layers": None,
        "flash_attention": None,
        "kv_cache_type_k": None,
        "kv_cache_type_v": None,
        "batch_size": None,
        "ubatch_size": None,
        "threads": None,
        "mmap": None,
        "mlock": None,
        "params_b": params_b,
    }
    # Ollama thinking models can spend their entire output budget in the
    # separate hidden reasoning field and return empty final content. Fresh
    # profiles default to non-thinking for reliable chat and structured output;
    # users can explicitly enable thinking for long-form reasoning workloads.
    low = (model_id or "").lower()
    if low.startswith("qwen3") or "deepseek-r1" in low:
        profile["think"] = False
    return profile


def ensure_profile_for_model(model_id: str) -> Tuple[Optional[str], bool, Optional[str]]:
    """Ensure the live config has a profile for `model_id`. Best-effort.

    Returns ``(profile_name, created, error)``. Never raises - auto-creation is a
    convenience that must never turn a successful pull into a failure. When the
    active config is the read-only example fixture, this no-ops (created=False).
    """
    try:
        from api_server import get_config_path, load_config  # lazy: avoid circular import
        from .fit import _parse_params_b, _parse_quant

        path = get_config_path()
        if refuse_example(path):
            return None, False, None  # example fixture is read-only; skip silently
        config = load_config()
        profiles = config.setdefault("profiles", {})
        # Already have a profile pointing at this exact model? Leave it untouched.
        for pname, prof in profiles.items():
            if prof.get("model_id") == model_id:
                return pname, False, None
        name = slugify_profile_name(model_id)
        # Avoid clobbering an unrelated profile that happens to share the slug.
        if name in profiles:
            suffix = 2
            while f"{name}_{suffix}" in profiles:
                suffix += 1
            name = f"{name}_{suffix}"
        profiles[name] = default_profile_for(
            model_id, params_b=_parse_params_b(model_id), quant=_parse_quant(model_id)
        )
        if not config.get("default_profile"):
            config["default_profile"] = name
        err = write_config_atomic(config, path)
        return (name, True, None) if not err else (None, False, err)
    except Exception as exc:  # pragma: no cover - defensive; pull must still succeed
        return None, False, str(exc)
