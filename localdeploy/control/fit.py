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
_COMFORTABLE_MARGIN_GB = 1.0  # VRAM headroom above which a fit is "comfortable"


class FitRequest(BaseModel):
    profile: Optional[str] = None
    model_id: Optional[str] = None
    params_b: Optional[float] = None
    quant: Optional[str] = None
    context: Optional[int] = None
    free_vram_mb: Optional[int] = None
    size_bytes: Optional[int] = None


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


def _parse_quant(text: Optional[str]) -> Optional[str]:
    """Pull a GGUF-style quant label out of a model or repo name."""
    if not text:
        return None
    lowered = text.lower()
    match = re.search(r"\b(iq[2345]_[a-z0-9_]+|q[234568](?:_[a-z0-9_]+)?)\b", lowered)
    return match.group(1).upper() if match else None


def _round(value: float) -> float:
    return round(value, 2)


def _classify(
    required_gb: float, free_vram_gb: Optional[float], ram_available_gb: Optional[float]
) -> Dict[str, Any]:
    """Tier the fit into comfortable / tight (soft) / cpu-only (soft) / won't-fit (hard).

    ``verdict`` stays coarse and backward-compatible: FITS when it fits VRAM,
    WONT_FIT when VRAM is known but too small, UNKNOWN when VRAM is unknown.
    ``severity`` (ok / soft / hard / unknown) drives the green/yellow/red UI.
    """
    if free_vram_gb is not None:
        margin = free_vram_gb - required_gb
        if margin >= _COMFORTABLE_MARGIN_GB:
            return {
                "verdict": "FITS", "tier": "comfortable", "severity": "ok",
                "headline": "Fits comfortably on the GPU.", "cpu_deployable": True,
            }
        if margin >= 0:
            return {
                "verdict": "FITS", "tier": "tight", "severity": "soft",
                "headline": f"Fits on the GPU, but headroom is tight (~{_round(margin)} GB).",
                "cpu_deployable": True,
            }
        # Doesn't fit VRAM — can it run on CPU+RAM instead?
        if ram_available_gb is not None:
            if required_gb <= ram_available_gb:
                return {
                    "verdict": "WONT_FIT", "tier": "cpu_only", "severity": "soft",
                    "headline": "Won't fit the GPU, but can run on CPU (slower).",
                    "cpu_deployable": True,
                }
            return {
                "verdict": "WONT_FIT", "tier": "wont_fit", "severity": "hard",
                "headline": "Too large for both GPU VRAM and system RAM.",
                "cpu_deployable": False,
            }
        return {
            "verdict": "WONT_FIT", "tier": "wont_fit_gpu", "severity": "hard",
            "headline": "Won't fit the GPU (system RAM unknown).", "cpu_deployable": None,
        }

    # No VRAM figure — judge CPU deployability from RAM alone.
    if ram_available_gb is not None:
        if required_gb <= ram_available_gb:
            return {
                "verdict": "UNKNOWN", "tier": "cpu_only", "severity": "soft",
                "headline": "No GPU detected — fits in system RAM, can run on CPU (slower).",
                "cpu_deployable": True,
            }
        return {
            "verdict": "UNKNOWN", "tier": "cpu_too_big", "severity": "hard",
            "headline": "No GPU detected — too large for available system RAM.",
            "cpu_deployable": False,
        }
    return {
        "verdict": "UNKNOWN", "tier": "unknown", "severity": "unknown",
        "headline": "No GPU VRAM detected; pass 'free_vram_mb' to validate against a target.",
        "cpu_deployable": None,
    }


def _resolve_from_profile(req: FitRequest) -> Dict[str, Any]:
    """Fill missing params/quant/context from a config profile when given."""
    resolved = {
        "model_id": req.model_id,
        "params_b": req.params_b,
        "quant": req.quant,
        "context": req.context,
        "kv_quant": None,
        "size_bytes": req.size_bytes,
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
    resolved["quant"] = resolved["quant"] or profile.get("quantization") or _parse_quant(profile.get("model_id"))
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
    if not params_b and not resolved.get("size_bytes"):
        return {
            "success": False,
            "verdict": "UNKNOWN",
            "message": (
                "Could not determine parameter count. Pass 'params_b' explicitly "
                "or use a profile/model_id that encodes size (e.g. '12b')."
            ),
        }

    context = resolved["context"] or 4096
    quant = resolved["quant"] or _parse_quant(resolved.get("model_id"))

    size_bytes = resolved.get("size_bytes")
    if size_bytes:
        # Installed Ollama/GGUF size is a much better load-footprint anchor than
        # guessing from parameter count. Keep KV cache out of this load estimate;
        # long prompts can still need more memory, which the note makes explicit.
        weights_gb = size_bytes / 1_000_000_000.0
        kv_cache_gb = 0.0
        note = (
            "Estimate uses installed model size plus runtime overhead. Long context "
            "windows can need extra memory during generation."
        )
    else:
        weights_gb = params_b * _weight_gb_per_b(quant)
        kv_factor = _kv_quant_factor(resolved.get("kv_quant"))
        kv_cache_gb = _KV_MB_PER_TOKEN_PER_B * params_b * kv_factor * context / 1024.0
        note = "Conservative estimate. The real proof is a short warmup via /models/serve."
    required_gb = weights_gb + kv_cache_gb + _OVERHEAD_GB

    # Pull both VRAM and system RAM from the hardware probe (Phase 1). RAM lets us
    # tell "won't fit GPU but runs on CPU" apart from "too big for this machine".
    hw = detect_hardware()
    free_vram_mb = req.free_vram_mb
    if free_vram_mb is None and hw["gpu_available"] and hw["gpus"]:
        free_vram_mb = hw["gpus"][0].get("vram_free_mb")
    ram_available_mb = (hw.get("system") or {}).get("ram_available_mb")

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
    if size_bytes:
        model_info["size_gb"] = _round(size_bytes / 1_000_000_000.0)
    free_vram_gb = (free_vram_mb / 1024.0) if free_vram_mb is not None else None
    ram_available_gb = (ram_available_mb / 1024.0) if ram_available_mb is not None else None
    cls = _classify(required_gb, free_vram_gb, ram_available_gb)

    suggestions = []
    if cls["severity"] in ("soft", "hard") and cls["tier"] != "tight":
        suggestions = [
            "Use a smaller quantization (e.g. Q4 or Q3).",
            "Lower the context window (try the profile's safe_context_limit).",
            "Deploy to CPU (slower) — pick CPU in the serve panel.",
        ]

    return {
        "success": True,
        # verdict stays coarse + backward-compatible (FITS / WONT_FIT / UNKNOWN);
        # tier/severity/headline carry the new soft-vs-hard nuance for the UI.
        "verdict": cls["verdict"],
        "tier": cls["tier"],
        "severity": cls["severity"],
        "headline": cls["headline"],
        "cpu_deployable": cls["cpu_deployable"],
        "model": model_info,
        "estimate_gb": estimate,
        "free_vram_gb": _round(free_vram_gb) if free_vram_gb is not None else None,
        "ram_available_gb": _round(ram_available_gb) if ram_available_gb is not None else None,
        "margin_gb": _round(free_vram_gb - required_gb) if free_vram_gb is not None else None,
        "note": note,
        "suggestions": suggestions,
    }
