"""Step 3 - VRAM fit-check.

POST /system/fit-check answers "will this model fit?" with a transparent,
deliberately conservative estimate (see PUBLIC_LAUNCH_PLAN.md, Appendix B):

    required_GB = weights_GB + kv_cache_GB + overhead_GB

The numbers are approximate by design; the breakdown is always returned so the
verdict is never a black box. The real proof is a short warmup (Step 6).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from .hardware import detect_hardware

router = APIRouter()

# Weight footprint per 1B parameters, in GB, by quantization family.
_WEIGHT_GB_PER_B = {
    "f16": 2.0,
    "fp16": 2.0,
    "bf16": 2.0,
    "q8": 1.0,
    "q6": 0.75,
    "q5": 0.65,
    "q4": 0.5,
    "q3": 0.42,
    "q2": 0.34,
}
_DEFAULT_WEIGHT_GB_PER_B = 0.55  # unknown/default Ollama quant ~= Q4_K_M

# KV-cache scaling: per-token cost grows with model size; reduced by KV quant.
_KV_MB_PER_TOKEN_PER_B = 0.07  # fp16 baseline, conservative
_KV_QUANT_FACTOR = {"f16": 1.0, "fp16": 1.0, "q8": 0.5, "q4": 0.25}
_OVERHEAD_GB = 0.8  # CUDA context + activations


class FitRequest(BaseModel):
    profile: Optional[str] = None
    model_id: Optional[str] = None
    params_b: Optional[float] = None
    quant: Optional[str] = None
    context: Optional[int] = None
    free_vram_mb: Optional[int] = None


def _parse_params_b(text: Optional[str]) -> Optional[float]:
    """Pull a parameter count like '4b' / '12b' / '1.5b' out of a model name."""
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*b\b", text.lower())
    return float(match.group(1)) if match else None


def _weight_gb_per_b(quant: Optional[str]) -> float:
    if not quant:
        return _DEFAULT_WEIGHT_GB_PER_B
    q = quant.lower()
    for key, value in _WEIGHT_GB_PER_B.items():
        if key in q:
            return value
    return _DEFAULT_WEIGHT_GB_PER_B


def _kv_quant_factor(quant: Optional[str]) -> float:
    if not quant:
        return 1.0
    q = quant.lower()
    for key, value in _KV_QUANT_FACTOR.items():
        if key in q:
            return value
    return 1.0


def _round(value: float) -> float:
    return round(value, 2)


def _resolve_from_profile(req: FitRequest) -> Dict[str, Any]:
    """Fill missing params/quant/context from a config profile when given."""
    resolved = {
        "model_id": req.model_id,
        "params_b": req.params_b,
        "quant": req.quant,
        "context": req.context,
        "kv_quant": None,
    }
    if not req.profile:
        return resolved
    # Lazy import: api_server owns config loading and finishes importing first.
    from api_server import load_config

    profile = load_config().get("profiles", {}).get(req.profile)
    if not profile:
        resolved["error"] = f"Unknown profile '{req.profile}'."
        return resolved
    resolved["model_id"] = resolved["model_id"] or profile.get("model_id")
    if resolved["params_b"] is None:
        resolved["params_b"] = _parse_params_b(profile.get("model_id")) or _parse_params_b(
            profile.get("name")
        )
    resolved["quant"] = resolved["quant"] or profile.get("quantization")
    if resolved["context"] is None:
        resolved["context"] = profile.get("safe_context_limit") or profile.get("context_limit")
    resolved["kv_quant"] = profile.get("kv_cache_type_k") or profile.get("kv_cache_type_v")
    return resolved


@router.post("/system/fit-check")
def fit_check(req: FitRequest) -> Dict[str, Any]:
    resolved = _resolve_from_profile(req)
    if resolved.get("error"):
        return {"success": False, "verdict": "UNKNOWN", "message": resolved["error"]}

    params_b = resolved["params_b"] or _parse_params_b(resolved.get("model_id"))
    if not params_b:
        return {
            "success": False,
            "verdict": "UNKNOWN",
            "message": (
                "Could not determine parameter count. Pass 'params_b' explicitly "
                "or use a profile/model_id that encodes size (e.g. '12b')."
            ),
        }

    context = resolved["context"] or 4096
    quant = resolved["quant"]

    weights_gb = params_b * _weight_gb_per_b(quant)
    kv_factor = _kv_quant_factor(resolved.get("kv_quant"))
    kv_cache_gb = _KV_MB_PER_TOKEN_PER_B * params_b * kv_factor * context / 1024.0
    required_gb = weights_gb + kv_cache_gb + _OVERHEAD_GB

    free_vram_mb = req.free_vram_mb
    if free_vram_mb is None:
        hw = detect_hardware()
        if hw["gpu_available"] and hw["gpus"]:
            free_vram_mb = hw["gpus"][0].get("vram_free_mb")

    estimate = {
        "weights": _round(weights_gb),
        "kv_cache": _round(kv_cache_gb),
        "overhead": _OVERHEAD_GB,
        "required": _round(required_gb),
    }
    model_info = {
        "params_b": params_b,
        "quant": quant or "default (~Q4)",
        "context": context,
    }
    note = "Conservative estimate. The real proof is a short warmup via /models/serve."

    if free_vram_mb is None:
        return {
            "success": True,
            "verdict": "UNKNOWN",
            "model": model_info,
            "estimate_gb": estimate,
            "free_vram_gb": None,
            "margin_gb": None,
            "note": note,
            "message": "No GPU VRAM detected; pass 'free_vram_mb' to validate against a target.",
        }

    free_vram_gb = free_vram_mb / 1024.0
    margin_gb = free_vram_gb - required_gb
    fits = required_gb <= free_vram_gb

    suggestions = []
    if not fits:
        suggestions = [
            "Use a smaller quantization (e.g. Q4 or Q3).",
            "Lower the context window (try the profile's safe_context_limit).",
            "Use a GGUF / partial-offload profile to split layers between GPU and CPU.",
        ]

    return {
        "success": True,
        "verdict": "FITS" if fits else "WONT_FIT",
        "model": model_info,
        "estimate_gb": estimate,
        "free_vram_gb": _round(free_vram_gb),
        "margin_gb": _round(margin_gb),
        "note": note,
        "suggestions": suggestions,
    }
