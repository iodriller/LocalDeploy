"""Step 14 (D2) - one-click "Tune for my GPU".

POST /system/recommend fit-filters the configured profiles, runs a short
benchmark subset on the ones that fit, and ranks them by a transparent
quality x speed x headroom score. It reuses the existing fit-check (Step 3) and
benchmark engine (Step 8) - this is orchestration only, no new scoring engine.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class SetDefaultRequest(BaseModel):
    profile: str


@router.post("/system/set-default")
def set_default(req: SetDefaultRequest) -> Dict[str, Any]:
    """Persist the chosen profile as the config's default_profile.

    Writes config.json (seeding from the loaded config if it does not exist yet).
    Refuses to overwrite config.example.json so the bundled example stays pristine.
    """
    from api_server import get_config_path, load_config

    config = load_config()
    if req.profile not in config.get("profiles", {}):
        return {"success": False, "error": f"Unknown profile '{req.profile}'."}
    path = get_config_path()
    if path.name == "config.example.json":
        return {
            "success": False,
            "error": "Refusing to overwrite config.example.json. Point CONFIG_PATH at a real "
            "config.json (the app seeds from the example when it is missing).",
        }
    config["default_profile"] = req.profile
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
        return {"success": False, "error": f"Could not write {path}: {exc}"}
    return {"success": True, "default_profile": req.profile, "path": str(path)}


class RecommendRequest(BaseModel):
    profiles: Optional[List[str]] = None
    free_vram_mb: Optional[int] = None
    sample_size: int = 3
    timeout: int = 120


def _score(quality: float, speed_norm: float, headroom_norm: float) -> float:
    # Quality dominates; speed and VRAM headroom break ties. Transparent weights.
    return round(0.60 * quality + 0.25 * speed_norm + 0.15 * headroom_norm, 4)


def rank_candidates(scored: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize speed/headroom within the set and assign a score. Pure function."""
    max_speed = max((1.0 / s["avg_latency_s"] for s in scored if s["avg_latency_s"] > 0), default=0.0)
    max_margin = max((s["margin_gb"] for s in scored if s["margin_gb"] is not None), default=0.0)
    for s in scored:
        speed_norm = (1.0 / s["avg_latency_s"]) / max_speed if s["avg_latency_s"] > 0 and max_speed > 0 else 0.0
        headroom_norm = s["margin_gb"] / max_margin if s["margin_gb"] is not None and max_margin > 0 else 0.0
        s["score"] = _score(s["avg_accuracy"], speed_norm, headroom_norm)
        reasons = [f"accuracy {s['avg_accuracy']}", f"~{s['avg_latency_s']}s/test"]
        if s["margin_gb"] is not None:
            reasons.append(f"{s['margin_gb']} GB headroom")
        s["reasoning"] = ", ".join(reasons)
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored


@router.post("/system/recommend")
def recommend(req: RecommendRequest) -> Dict[str, Any]:
    from api_server import load_config  # lazy: api_server owns config loading
    import benchmark as bench
    from .fit import FitRequest, fit_check

    profiles_map = load_config().get("profiles", {})
    names = req.profiles or [n for n, p in profiles_map.items() if p.get("enabled", False)]
    names = [n for n in names if n in profiles_map]
    if not names:
        return {"success": False, "error": "No profiles to evaluate (enable some or pass 'profiles')."}

    # Fit-filter first so we never benchmark something that can't load.
    candidates: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for name in names:
        fit = fit_check(FitRequest(profile=name, free_vram_mb=req.free_vram_mb))
        if fit.get("verdict") == "WONT_FIT":
            # This is GPU tuning, so anything that won't fit the GPU is skipped —
            # but distinguish "CPU-capable" from "too big for anything" so the
            # reason isn't misleading.
            reason = "CPU-only (skipped for GPU tuning)" if fit.get("cpu_deployable") else "won't fit VRAM"
            skipped.append(
                {"profile": name, "reason": reason, "required_gb": (fit.get("estimate_gb") or {}).get("required")}
            )
            continue
        candidates.append({"profile": name, "margin_gb": fit.get("margin_gb")})

    if not candidates:
        return {
            "success": True,
            "recommended": None,
            "candidates": [],
            "skipped": skipped,
            "message": "No profile fits the available VRAM.",
        }

    # Short subset: the fastest (smallest-output) tests, to keep tuning quick.
    tests = sorted(bench.TEST_CASES, key=lambda t: t.max_output_tokens)[: max(1, req.sample_size)]
    base_url = bench.api_base_url()

    scored: List[Dict[str, Any]] = []
    for cand in candidates:
        name = cand["profile"]
        results = []
        for t in tests:
            try:
                results.append(bench.execute_test(base_url, name, profiles_map[name], t, req.timeout))
            except Exception:
                # An unexpected failure counts as a failed test, never a 500.
                results.append(
                    bench.TestResult(
                        name=t.name, category=t.category, success=False, elapsed_seconds=0.0,
                        response_length=0, response_preview="", accuracy=0.0, error="execute_test error",
                    )
                )
        accs = [r.accuracy for r in results]
        lats = [r.elapsed_seconds for r in results if r.elapsed_seconds]
        scored.append(
            {
                "profile": name,
                "avg_accuracy": round(sum(accs) / len(accs), 3) if accs else 0.0,
                "avg_latency_s": round(sum(lats) / len(lats), 3) if lats else 0.0,
                "passed": sum(1 for r in results if r.success),
                "tests": len(results),
                "margin_gb": cand["margin_gb"],
            }
        )

    ranked = rank_candidates(scored)
    return {
        "success": True,
        "recommended": ranked[0],
        "candidates": ranked,
        "skipped": skipped,
        "sample_tests": [t.name for t in tests],
    }
