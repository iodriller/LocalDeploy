"""Step 14 (D2) - one-click "Tune for my GPU".

POST /system/recommend fit-filters the saved run profiles, runs a short
benchmark subset on the ones that fit, and ranks them by a transparent
quality x speed x headroom score. It reuses the existing fit-check (Step 3) and
benchmark engine (Step 8) - this is orchestration only, no new scoring engine.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ._config import refuse_example, write_config_atomic

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
    refusal = refuse_example(path)
    if refusal:
        return {"success": False, "error": refusal}
    config["default_profile"] = req.profile
    err = write_config_atomic(config, path)
    if err:
        return {"success": False, "error": err}
    return {"success": True, "default_profile": req.profile, "path": str(path)}


class SetEnabledRequest(BaseModel):
    profile: str
    enabled: bool


@router.post("/system/set-enabled")
def set_enabled(req: SetEnabledRequest) -> Dict[str, Any]:
    """Flip a saved profile's enabled flag so Auto-pick can consider it.

    Auto-pick (/system/recommend) only ever compares profiles with
    ``enabled: true``, and the shipped config.json enables just two small
    profiles — there was previously no UI path to turn on any of the others
    (or turn off ones that don't fit) without hand-editing config.json.
    """
    from api_server import get_config_path, load_config

    config = load_config()
    profiles = config.get("profiles", {})
    if req.profile not in profiles:
        return {"success": False, "error": f"Unknown profile '{req.profile}'."}
    path = get_config_path()
    refusal = refuse_example(path)
    if refusal:
        return {"success": False, "error": refusal}
    profiles[req.profile]["enabled"] = req.enabled
    err = write_config_atomic(config, path)
    if err:
        return {"success": False, "error": err}
    return {"success": True, "profile": req.profile, "enabled": req.enabled, "path": str(path)}


class RecommendRequest(BaseModel):
    profiles: Optional[List[str]] = None
    free_vram_mb: Optional[int] = None
    sample_size: int = 3
    timeout: int = 120
    # Optional scoring tilt for the three built-in UI presets (Safe Starter /
    # Best Quality / Fast & Low VRAM). Defaults to the standard "quality
    # dominates" weighting when omitted; need not sum to 1.
    quality_weight: Optional[float] = None
    speed_weight: Optional[float] = None
    headroom_weight: Optional[float] = None


DEFAULT_WEIGHTS = (0.60, 0.25, 0.15)


def _score(quality: float, speed_norm: float, headroom_norm: float, weights=DEFAULT_WEIGHTS) -> float:
    # Quality dominates by default; speed and VRAM headroom break ties. Transparent weights.
    qw, sw, hw = weights
    return round(qw * quality + sw * speed_norm + hw * headroom_norm, 4)


def rank_candidates(scored: List[Dict[str, Any]], weights=DEFAULT_WEIGHTS) -> List[Dict[str, Any]]:
    """Normalize speed/headroom within the set and assign a score. Pure function."""
    max_speed = max((1.0 / s["avg_latency_s"] for s in scored if s["avg_latency_s"] > 0), default=0.0)
    max_margin = max((s["margin_gb"] for s in scored if s["margin_gb"] is not None), default=0.0)
    for s in scored:
        speed_norm = (1.0 / s["avg_latency_s"]) / max_speed if s["avg_latency_s"] > 0 and max_speed > 0 else 0.0
        headroom_norm = s["margin_gb"] / max_margin if s["margin_gb"] is not None and max_margin > 0 else 0.0
        s["score"] = _score(s["avg_accuracy"], speed_norm, headroom_norm, weights)
        reasons = [f"accuracy {s['avg_accuracy']}", f"~{s['avg_latency_s']}s/test"]
        if s["margin_gb"] is not None:
            reasons.append(f"{s['margin_gb']} GB headroom")
        s["reasoning"] = ", ".join(reasons)
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored


def _weights_from(req: "RecommendRequest"):
    if req.quality_weight is None and req.speed_weight is None and req.headroom_weight is None:
        return DEFAULT_WEIGHTS
    return (req.quality_weight or 0.0, req.speed_weight or 0.0, req.headroom_weight or 0.0)


def _fit_candidates(
    profiles_map: Dict[str, Any], names: List[str], free_vram_mb: Optional[int]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fit-filter profile names so we never benchmark something that can't load."""
    from .fit import FitRequest, fit_check

    candidates: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for name in names:
        fit = fit_check(FitRequest(profile=name, free_vram_mb=free_vram_mb))
        # This is GPU tuning, so anything that isn't a known GPU fit is skipped:
        # verdict WONT_FIT (GPU known, too small) or severity "hard" (e.g. the
        # no-GPU "too big for system RAM either" case, which has verdict UNKNOWN
        # and was previously missed here) or a fit-check that couldn't determine
        # the model's size at all (success: False).
        if not fit.get("success") or fit.get("verdict") == "WONT_FIT" or fit.get("severity") == "hard":
            if not fit.get("success"):
                reason = fit.get("message") or "could not determine size"
            else:
                # Distinguish "CPU-capable" from "too big for anything" so the
                # reason isn't misleading.
                reason = "CPU-only (skipped for GPU tuning)" if fit.get("cpu_deployable") else "won't fit VRAM"
            skipped.append(
                {"profile": name, "reason": reason, "required_gb": (fit.get("estimate_gb") or {}).get("required")}
            )
            continue
        candidates.append({"profile": name, "margin_gb": fit.get("margin_gb")})
    return candidates, skipped


@router.post("/system/recommend")
def recommend(req: RecommendRequest) -> Dict[str, Any]:
    from api_server import load_config  # lazy: api_server owns config loading
    import benchmark as bench

    profiles_map = load_config().get("profiles", {})
    names = req.profiles or [n for n, p in profiles_map.items() if p.get("enabled", False)]
    names = [n for n in names if n in profiles_map]
    if not names:
        return {"success": False, "error": "No saved run profiles to evaluate (enable some or pass 'profiles')."}

    candidates, skipped = _fit_candidates(profiles_map, names, req.free_vram_mb)
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

    ranked = rank_candidates(scored, _weights_from(req))
    return {
        "success": True,
        "recommended": ranked[0],
        "candidates": ranked,
        "skipped": skipped,
        "sample_tests": [t.name for t in tests],
    }


@router.post("/system/recommend/stream")
def recommend_stream(req: RecommendRequest):
    """Same ranking as /system/recommend, streamed as SSE so the UI can show
    live per-candidate progress instead of a single blocking spinner.

    Emits: recommend_start, candidate_start, test_result(*), candidate_end(*),
    recommend_end. Mirrors the /benchmark/run event-stream shape so the
    frontend can reuse its SSE plumbing.
    """
    from api_server import load_config  # lazy: api_server owns config loading
    import benchmark as bench

    profiles_map = load_config().get("profiles", {})
    names = req.profiles or [n for n, p in profiles_map.items() if p.get("enabled", False)]
    names = [n for n in names if n in profiles_map]

    def event_stream() -> Iterator[str]:
        def sse(event: Dict[str, Any]) -> str:
            return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        if not names:
            yield sse({"event": "error", "error": "No saved run profiles to evaluate (enable some or pass 'profiles')."})
            yield "data: [DONE]\n\n"
            return

        candidates, skipped = _fit_candidates(profiles_map, names, req.free_vram_mb)
        for s in skipped:
            yield sse({"event": "candidate_skipped", **s})

        if not candidates:
            yield sse(
                {
                    "event": "recommend_end",
                    "recommended": None,
                    "candidates": [],
                    "skipped": skipped,
                    "message": "No profile fits the available VRAM.",
                }
            )
            yield "data: [DONE]\n\n"
            return

        tests = sorted(bench.TEST_CASES, key=lambda t: t.max_output_tokens)[: max(1, req.sample_size)]
        base_url = bench.api_base_url()
        yield sse(
            {
                "event": "recommend_start",
                "candidates": [c["profile"] for c in candidates],
                "sample_tests": [t.name for t in tests],
            }
        )

        scored: List[Dict[str, Any]] = []
        for cand in candidates:
            name = cand["profile"]
            yield sse({"event": "candidate_start", "profile": name})
            results = []
            for t in tests:
                yield sse({"event": "test_start", "profile": name, "name": t.name})
                try:
                    item = bench.execute_test(base_url, name, profiles_map[name], t, req.timeout)
                except Exception:
                    # An unexpected failure counts as a failed test, never a broken stream.
                    item = bench.TestResult(
                        name=t.name, category=t.category, success=False, elapsed_seconds=0.0,
                        response_length=0, response_preview="", accuracy=0.0, error="execute_test error",
                    )
                results.append(item)
                yield sse(
                    {
                        "event": "test_result",
                        "profile": name,
                        "name": item.name,
                        "success": item.success,
                        "accuracy": item.accuracy,
                        "elapsed_seconds": item.elapsed_seconds,
                    }
                )
            accs = [r.accuracy for r in results]
            lats = [r.elapsed_seconds for r in results if r.elapsed_seconds]
            row = {
                "profile": name,
                "avg_accuracy": round(sum(accs) / len(accs), 3) if accs else 0.0,
                "avg_latency_s": round(sum(lats) / len(lats), 3) if lats else 0.0,
                "passed": sum(1 for r in results if r.success),
                "tests": len(results),
                "margin_gb": cand["margin_gb"],
            }
            scored.append(row)
            yield sse({"event": "candidate_end", "profile": name, **row})

        ranked = rank_candidates(scored, _weights_from(req))
        yield sse(
            {
                "event": "recommend_end",
                "recommended": ranked[0],
                "candidates": ranked,
                "skipped": skipped,
                "sample_tests": [t.name for t in tests],
            }
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
