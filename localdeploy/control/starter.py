"""Step 15 - "Starter pack" - one-click curated model picks for new users.

POST /registry/starter-pack takes the detected free VRAM (or system RAM when
there is no GPU), subtracts a safety margin, and returns the best-fitting
handful of well-known Ollama models from a small curated catalog. This is
deliberately a static catalog rather than a live Hugging Face search — a
brand-new user's first pull should be something known-good, not whatever is
newest/most-downloaded this week (that's what /registry/check-updates is for).

Each candidate's VRAM estimate reuses the same weights+KV+overhead formula as
fit.py, so the numbers agree with the rest of the app.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .fit import _KV_MB_PER_TOKEN_PER_B, _OVERHEAD_GB, _kv_quant_factor, _weight_gb_per_b
from .hardware import detect_hardware

router = APIRouter()

# A small, curated catalog of well-known dense instruct models spanning
# ~0.5B -> 72B. Deliberately short so recommendations stay trustworthy. `tier`
# is a hand-curated 1-5 quality rating (5 = best-in-class for its size class),
# informed by this repo's own benchmark results (docs/MODELS.md) plus general
# community consensus for sizes not benchmarked here. `params_b` is set
# explicitly (not parsed from the tag) so MoE / odd naming can't throw the
# VRAM estimate off; every entry below is a dense model.
STARTER_CATALOG: List[Dict[str, Any]] = [
    {"id": "qwen2.5:0.5b", "family": "qwen2.5", "params_b": 0.5, "tier": 2, "vision": False,
     "use_case": "tiny/edge",
     "description": "Smallest usable Qwen2.5 instruct model. Fits almost anywhere, but expect basic quality."},
    {"id": "qwen2.5:1.5b", "family": "qwen2.5", "params_b": 1.5, "tier": 3, "vision": False,
     "use_case": "tiny/edge",
     "description": "Small Qwen2.5 instruct model with noticeably better quality than the 0.5B."},
    {"id": "llama3.2:1b", "family": "llama3.2", "params_b": 1.0, "tier": 3, "vision": False,
     "use_case": "tiny/edge",
     "description": "Meta's smallest Llama 3.2 instruct model. Fast and lightweight for simple tasks."},
    {"id": "gemma3:1b", "family": "gemma3", "params_b": 1.0, "tier": 3, "vision": False,
     "use_case": "tiny/edge",
     "description": "Smallest Gemma 3 model, text-only. Good for very constrained hardware."},
    {"id": "qwen2.5:3b", "family": "qwen2.5", "params_b": 3.0, "tier": 3, "vision": False,
     "use_case": "small/general", "description": "Solid small general-purpose Qwen2.5 model."},
    {"id": "llama3.2:3b", "family": "llama3.2", "params_b": 3.0, "tier": 4, "vision": False,
     "use_case": "small/fast",
     "description": "Fastest profile in this repo's own benchmarks (~4s/test) with acceptable quality."},
    {"id": "phi4-mini", "family": "phi4", "params_b": 3.8, "tier": 3, "vision": False,
     "use_case": "small/general", "description": "Compact Microsoft Phi-4 Mini instruct model."},
    {"id": "gemma3:4b", "family": "gemma3", "params_b": 4.0, "tier": 4, "vision": True,
     "use_case": "small/vision",
     "description": "This repo's safe default profile — small, multimodal, dependable."},
    {"id": "qwen3-vl:4b-instruct", "family": "qwen3-vl", "params_b": 4.0, "tier": 4, "vision": True,
     "use_case": "small/vision",
     "description": "Fast small vision-language model, ~2x faster than the 8B at somewhat lower quality."},
    {"id": "qwen2.5:7b", "family": "qwen2.5", "params_b": 7.0, "tier": 5, "vision": False,
     "use_case": "mid/general",
     "description": "Fastest strong generalist measured in this repo (0.79 overall @ 5.4s/test)."},
    {"id": "qwen2.5-coder:7b", "family": "qwen2.5-coder", "params_b": 7.0, "tier": 4, "vision": False,
     "use_case": "mid/coding",
     "description": "Strongest small coding model measured here (0.93 on code tasks)."},
    {"id": "mistral:7b", "family": "mistral", "params_b": 7.0, "tier": 3, "vision": False,
     "use_case": "mid/general",
     "description": "Solid general model, but weaker on math/classification than the Qwen alternatives at this size."},
    {"id": "deepseek-r1:7b", "family": "deepseek-r1", "params_b": 7.0, "tier": 3, "vision": False,
     "use_case": "mid/reasoning",
     "description": "Chain-of-thought reasoning model; best for math when 'think' is enabled."},
    {"id": "qwen3:8b", "family": "qwen3", "params_b": 8.0, "tier": 5, "vision": False,
     "use_case": "mid/structured",
     "description": "Structured-output champion measured here (0.91 hard-JSON); set think:false."},
    {"id": "qwen3-vl:8b-instruct", "family": "qwen3-vl", "params_b": 8.0, "tier": 5, "vision": True,
     "use_case": "mid/vision",
     "description": "Best overall all-rounder measured in this repo (0.84 overall, strong on every category)."},
    {"id": "llama3.1:8b", "family": "llama3.1", "params_b": 8.0, "tier": 4, "vision": False,
     "use_case": "mid/general",
     "description": "Strong sleeper pick for JSON pipelines (0.89 hard-JSON) despite a modest overall score."},
    {"id": "granite3.3:8b", "family": "granite3.3", "params_b": 8.0, "tier": 3, "vision": False,
     "use_case": "mid/general", "description": "IBM Granite 3.3 general instruct model."},
    {"id": "gemma3:12b-it-qat", "family": "gemma3", "params_b": 12.0, "tier": 4, "vision": True,
     "use_case": "large/vision",
     "description": "Quantization-aware-trained Gemma 3 12B — beats the default Q4 tag at the same speed/VRAM."},
    {"id": "qwen3:14b", "family": "qwen3", "params_b": 14.0, "tier": 5, "vision": False,
     "use_case": "large/general",
     "description": "Larger dense Qwen3 generalist for hardware with headroom beyond the 8B class."},
    {"id": "gemma3:27b", "family": "gemma3", "params_b": 27.0, "tier": 4, "vision": True,
     "use_case": "xlarge/vision", "description": "Large multimodal Gemma 3 for high-VRAM cards."},
    {"id": "qwen3.6:27b", "family": "qwen3.6", "params_b": 27.0, "tier": 5, "vision": True,
     "use_case": "xlarge/vision-agent",
     "description": "Dense native-multimodal Qwen3.6 for high-VRAM visual reasoning and structured agent work."},
    {"id": "qwen3:32b", "family": "qwen3", "params_b": 32.0, "tier": 5, "vision": False,
     "use_case": "xlarge/general",
     "description": "Flagship dense Qwen3 size — strong all-around quality for 24GB+ cards."},
    {"id": "llama3.1:70b", "family": "llama3.1", "params_b": 70.0, "tier": 5, "vision": False,
     "use_case": "huge/flagship",
     "description": "Meta's flagship dense Llama 3.1 — needs a very high-VRAM card or multi-GPU."},
    {"id": "qwen2.5:72b", "family": "qwen2.5", "params_b": 72.0, "tier": 5, "vision": False,
     "use_case": "huge/flagship",
     "description": "Flagship dense Qwen2.5 size — top-tier quality, needs a very high-VRAM card."},
]


def _required_gb(params_b: float, quant: Optional[str] = None, context: int = 4096) -> float:
    """Same estimator as fit.py: weights + KV cache + fixed overhead."""
    weights_gb = params_b * _weight_gb_per_b(quant)
    kv_gb = _KV_MB_PER_TOKEN_PER_B * params_b * _kv_quant_factor(quant) * context / 1024.0
    return round(weights_gb + kv_gb + _OVERHEAD_GB, 2)


def _resolve_budget(
    free_vram_mb: Optional[int], margin_gb: float
) -> Tuple[Optional[float], Optional[float], Optional[str], Dict[str, Any]]:
    """Return (raw_budget_gb, margin_budget_gb, source, hardware)."""
    hw = detect_hardware()
    vram_mb = free_vram_mb
    if vram_mb is None:
        vram_mb = (hw.get("gpu_summary") or {}).get("best_pool_free_mb")
    if vram_mb is None and hw["gpu_available"] and hw["gpus"]:
        vram_mb = hw["gpus"][0].get("vram_free_mb") or hw["gpus"][0].get("vram_total_mb")
    if vram_mb is not None:
        raw = vram_mb / 1024.0
        return raw, max(raw - margin_gb, 0.0), "vram", hw
    ram_mb = (hw.get("system") or {}).get("ram_available_mb")
    if ram_mb is not None:
        raw = ram_mb / 1024.0
        return raw, max(raw - margin_gb, 0.0), "ram", hw
    return None, None, None, hw


def _pick(catalog: List[Dict[str, Any]], budget_gb: float, limit: int) -> List[Dict[str, Any]]:
    """Fit-filter the catalog against `budget_gb`, then rank + cap per family.

    Ranking is quality tier first (desc), model size as a tie-break (desc) —
    among equally-rated models, prefer the one that uses more of the
    available headroom. At most 2 picks per family so the top-5 isn't just
    one family's whole size ladder.
    """
    fitting: List[Dict[str, Any]] = []
    for entry in catalog:
        required = _required_gb(entry["params_b"])
        if required <= budget_gb:
            fitting.append({**entry, "required_gb": required, "margin_gb": round(budget_gb - required, 2)})
    fitting.sort(key=lambda e: (e["tier"], e["params_b"]), reverse=True)

    picked: List[Dict[str, Any]] = []
    per_family: Dict[str, int] = {}
    for entry in fitting:
        if per_family.get(entry["family"], 0) >= 2:
            continue
        picked.append(entry)
        per_family[entry["family"]] = per_family.get(entry["family"], 0) + 1
        if len(picked) >= limit:
            break
    if len(picked) < limit:
        # Family cap left the list short (e.g. a narrow budget) — top up with
        # the next-best fitting entries regardless of family.
        picked_ids = {p["id"] for p in picked}
        for entry in fitting:
            if entry["id"] in picked_ids:
                continue
            picked.append(entry)
            if len(picked) >= limit:
                break
    return picked


class StarterPackRequest(BaseModel):
    free_vram_mb: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    margin_gb: float = Field(default=2.0, ge=0, le=1_024)
    limit: int = Field(default=5, ge=1, le=20)


@router.post("/registry/starter-pack")
def starter_pack(req: StarterPackRequest) -> Dict[str, Any]:
    raw_budget_gb, budget_gb, source, hw = _resolve_budget(req.free_vram_mb, req.margin_gb)

    if budget_gb is None:
        return {
            "success": True,
            "budget_source": None,
            "budget_gb": None,
            "margin_gb": req.margin_gb,
            "candidates": [],
            "hardware": {"gpu_available": hw["gpu_available"], "gpus": hw["gpus"]},
            "message": (
                "Could not determine VRAM or system RAM. Pass "
                "'free_vram_mb' explicitly to request a manual estimate."
            ),
        }

    limit = max(1, min(req.limit, len(STARTER_CATALOG)))
    candidates = _pick(STARTER_CATALOG, budget_gb, limit)
    margin_relaxed = False
    note: Optional[str] = None

    if not candidates:
        # Nothing fits within the margin — retry against the raw budget so a
        # user on tight hardware still gets *something*, flagged as tight.
        candidates = _pick(STARTER_CATALOG, raw_budget_gb, limit)
        margin_relaxed = bool(candidates)
        if candidates:
            note = (
                f"Nothing fit within the {req.margin_gb} GB safety margin, so this list "
                "uses your full detected budget instead — treat these as tight fits."
            )
        else:
            # Still nothing: hand back the smallest catalog entry as a last resort.
            smallest = min(STARTER_CATALOG, key=lambda e: e["params_b"])
            required = _required_gb(smallest["params_b"])
            candidates = [
                {**smallest, "required_gb": required, "margin_gb": round(raw_budget_gb - required, 2)}
            ]
            note = (
                "Even the smallest starter model may not comfortably fit your detected "
                "memory. Listing it anyway as a best-effort pick — consider CPU "
                "deployment or freeing memory first."
            )

    effective_budget_gb = raw_budget_gb if margin_relaxed else budget_gb
    for c in candidates:
        c["pull_name"] = c["id"]
        unit = "VRAM" if source == "vram" else "RAM"
        c["reasoning"] = (
            f"tier {c['tier']}/5, ~{c['required_gb']} GB estimated, "
            f"~{c['margin_gb']} GB headroom left in your {unit} budget"
        )

    return {
        "success": True,
        "budget_source": source,
        "raw_budget_gb": round(raw_budget_gb, 2),
        "budget_gb": round(effective_budget_gb, 2),
        "margin_gb": req.margin_gb,
        "margin_relaxed": margin_relaxed,
        "candidates": candidates,
        "hardware": {"gpu_available": hw["gpu_available"], "gpus": hw["gpus"]},
        "message": note,
    }
