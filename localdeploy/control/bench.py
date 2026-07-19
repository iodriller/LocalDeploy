"""Steps 7-8 - benchmark over the web.

GET  /benchmark/example   -> the canonical question-set example
POST /benchmark/validate  -> validate an uploaded question set (no run)
POST /benchmark/run       -> run the benchmark, streaming per-test results

All heavy lifting (grading, the run generator, the schema) lives in the
top-level `benchmark.py` so the CLI and this endpoint share one implementation.
`benchmark` is imported lazily so the server boot stays light and free of a
hard import-order coupling.
"""
from __future__ import annotations

import dataclasses
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()

_bench_lock = threading.Lock()
_bench_module = None


def _bench():
    """Lazy, memoized import of the top-level `benchmark` module.

    FastAPI runs each request handler in a threadpool worker, so the first
    few requests after startup (e.g. a page load firing several /benchmark/*
    calls at once) can call this concurrently from different threads before
    `benchmark` has ever been imported. A bare `import benchmark` in that
    window is not reliably safe against concurrent first-import from
    multiple threads; double-checked locking here makes the import happen
    exactly once, with every other caller blocking on the lock rather than
    racing it.
    """
    global _bench_module
    if _bench_module is None:
        with _bench_lock:
            if _bench_module is None:
                import benchmark  # avoids importing the big module + TEST_CASES at boot

                _bench_module = benchmark
    return _bench_module


@router.get("/benchmark/example")
def benchmark_example() -> Dict[str, Any]:
    return _bench().EXAMPLE_QUESTION_SET


@router.get("/benchmark/test-bench")
def benchmark_test_bench() -> Dict[str, Any]:
    bench = _bench()
    tests = list(bench.TEST_CASES)
    categories: Dict[str, int] = {}
    for test in tests:
        categories[test.category] = categories.get(test.category, 0) + 1
    return {
        "success": True,
        "test_count": len(tests),
        "categories": categories,
        "question_set": bench.BUILTIN_QUESTION_SET,
        "tests": [{"name": t.name, "category": t.category, "max_output_tokens": t.max_output_tokens} for t in tests],
    }


@router.post("/benchmark/validate")
async def benchmark_validate(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        return {
            "success": True,
            "valid": False,
            "question_count": 0,
            "errors": [{"index": -1, "error": f"invalid JSON body: {exc}"}],
            "grader_types": _bench().GRADER_TYPES,
        }
    return _bench().validate_question_set(payload)


# --- benchmark packs (Release R4) --------------------------------------------
# A pack is a named, reusable preset over /benchmark/run's existing
# `include_categories` filter - not a new engine. "all" (the default UI
# behaviour today) intentionally has no entry here; omit include_categories
# for that.
BENCHMARK_PACKS: Dict[str, Dict[str, Any]] = {
    "general": {
        "label": "General assistant",
        "categories": ["planning", "classification"],
        "description": "Planning and classification - everyday assistant tasks.",
    },
    "coding": {
        "label": "Coding",
        "categories": ["code"],
        "description": "Non-trivial Python/SQL, graded by AST parsing and unit tests.",
    },
    "structured": {
        "label": "JSON and structured extraction",
        "categories": ["structured", "structured_hard"],
        "description": "Schema-shaped JSON output, including nested/conditional schemas.",
    },
    "reasoning": {
        "label": "Reasoning",
        "categories": ["math", "planning"],
        "description": "Multi-step math and planning tasks that reward chain-of-thought.",
    },
}


@router.get("/benchmark/packs")
def benchmark_packs() -> Dict[str, Any]:
    bench = _bench()
    known_categories = {t.category for t in bench.TEST_CASES}
    packs = []
    for key, pack in BENCHMARK_PACKS.items():
        cats = [c for c in pack["categories"] if c in known_categories]
        packs.append(
            {
                "id": key,
                "label": pack["label"],
                "description": pack["description"],
                "categories": cats,
                "test_count": sum(1 for t in bench.TEST_CASES if t.category in cats),
            }
        )
    return {"success": True, "packs": packs}


class RunRequest(BaseModel):
    profiles: Optional[List[str]] = None
    questions: Optional[Dict[str, Any]] = None
    device: Optional[str] = None
    max_output_tokens: Optional[int] = None
    timeout: int = 240
    skip_categories: Optional[List[str]] = None
    include_categories: Optional[List[str]] = None
    pack: Optional[str] = None  # convenience: expands to include_categories via BENCHMARK_PACKS
    repetitions: int = Field(default=1, ge=1, le=10)


@router.post("/benchmark/run")
def benchmark_run(req: RunRequest):
    bench = _bench()
    from api_server import load_config  # lazy: api_server owns config loading

    profiles_map = load_config().get("profiles", {})
    selected = req.profiles or [n for n, p in profiles_map.items() if p.get("enabled", False)]
    selected = [n for n in selected if n in profiles_map]
    if not selected:
        return {"success": False, "error": "No profiles selected or enabled in config."}

    # Build the test list. Copies are used so the shared module-level TEST_CASES
    # are never mutated by a request (the CLI mutates in-place for a single run;
    # a long-lived server must not).
    if req.questions is not None:
        report = bench.validate_question_set(req.questions)
        if not report["valid"]:
            return {"success": False, "error": "invalid question set", "validation": report}
        tests = bench.build_test_cases(req.questions)
    else:
        skip = set(req.skip_categories or [])
        include = set(req.include_categories or [])
        if not include and req.pack and req.pack in BENCHMARK_PACKS:
            include = set(BENCHMARK_PACKS[req.pack]["categories"])
        tests = [
            dataclasses.replace(t)
            for t in bench.TEST_CASES
            if t.category not in skip and (not include or t.category in include)
        ]
    if not tests:
        return {"success": False, "error": "No tests selected."}

    if req.max_output_tokens:
        for test in tests:
            if test.category != "classification":
                test.max_output_tokens = max(test.max_output_tokens, req.max_output_tokens)

    base_url = bench.api_base_url()

    def event_stream():
        def sse(event: Dict[str, Any]) -> str:
            return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        model_routes = None
        pre_existing: set[str] = set()
        unloaded_profiles: set[str] = set()
        requested_device = (req.device or "auto").strip().lower()

        def ensure_model_routes():
            nonlocal model_routes
            if model_routes is None:
                from . import models as routes  # lazy: avoid extra startup coupling

                model_routes = routes
            return model_routes

        def model_for_profile(profile_name: str) -> Optional[str]:
            profile = profiles_map.get(profile_name) or {}
            if str(profile.get("backend", "ollama")).lower() != "ollama":
                return None
            return str(profile.get("model_id") or profile_name)

        def matches_running(model_id: str, running_name: str) -> bool:
            routes = ensure_model_routes()
            return routes._matches_model_name(running_name, model_id)

        def was_pre_existing(model_id: str) -> bool:
            return any(matches_running(model_id, name) for name in pre_existing)

        def current_placement(profile_name: str) -> Optional[str]:
            model_id = model_for_profile(profile_name)
            if not model_id:
                return None
            try:
                routes = ensure_model_routes()
                running, _ = routes._ollama.list_running()
                for item in running:
                    item.update(routes._placement(item.get("size"), item.get("size_vram")))
                    if matches_running(model_id, str(item.get("name") or "")):
                        placement = item.get("placement")
                        return str(placement).lower() if placement else None
            except Exception:
                return None
            return None

        def should_unload(profile_name: str) -> bool:
            model_id = model_for_profile(profile_name)
            if not model_id:
                return False
            if requested_device in {"cpu", "gpu"}:
                return True
            return not was_pre_existing(model_id)

        def unload_profile(profile_name: str) -> Optional[Dict[str, Any]]:
            if profile_name in unloaded_profiles or not should_unload(profile_name):
                return None
            model_id = model_for_profile(profile_name)
            if not model_id:
                return None
            unloaded_profiles.add(profile_name)
            try:
                ensure_model_routes()._ollama.unload_model(model_id)
            except Exception as exc:
                return {
                    "event": "benchmark_unload_error",
                    "profile": profile_name,
                    "model_id": model_id,
                    "error": str(exc),
                }
            return {
                "event": "benchmark_unload_end",
                "profile": profile_name,
                "model_id": model_id,
                "message": f"Unloaded temporary benchmark model '{model_id}'.",
            }

        try:
            try:
                routes = ensure_model_routes()
                running, _ = routes._ollama.list_running()
                pre_existing = {str(item.get("name")) for item in running if item.get("name")}
            except Exception:
                pre_existing = set()

            from localdeploy import __version__
            from . import _ollama
            from .hardware import detect_hardware

            installed, _ = _ollama.list_installed()
            installed_by_name = {str(item.get("name") or ""): item for item in installed}
            ollama_version, _ = _ollama.version()
            profile_provenance: Dict[str, Any] = {}
            for profile_name in selected:
                profile = profiles_map[profile_name]
                backend = str(profile.get("backend") or "ollama").lower()
                model_id = str(profile.get("model_id") or profile_name)
                installed_item = next(
                    (
                        item
                        for installed_name, item in installed_by_name.items()
                        if backend == "ollama" and matches_running(model_id, installed_name)
                    ),
                    {},
                )
                details = installed_item.get("details") or {}
                shown: Dict[str, Any] = {}
                if backend == "ollama":
                    shown_result, _ = _ollama.show_model(model_id)
                    shown = shown_result or {}
                shown_details = shown.get("details") if isinstance(shown.get("details"), dict) else {}
                profile_provenance[profile_name] = {
                    "backend": backend,
                    "backend_version": ollama_version if backend == "ollama" else None,
                    "model": model_id,
                    "model_digest": installed_item.get("digest"),
                    "quant": details.get("quantization_level")
                    or shown_details.get("quantization_level")
                    or profile.get("quantization"),
                    "context": profile.get("context_limit"),
                    "warm_state": "warm"
                    if backend == "ollama" and any(matches_running(model_id, name) for name in pre_existing)
                    else "cold",
                }
            provenance = {
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "localdeploy_version": __version__,
                "hardware": detect_hardware(),
                "providers": {"ollama": {"version": ollama_version}},
                "profiles": profile_provenance,
            }

            # For a forced CPU/GPU run, pin the same num_gpu on every inference
            # call (not just the warm-up) so the benchmark measures the requested
            # device end-to-end instead of whatever Ollama re-places it onto.
            forced_num_gpu: Optional[int] = None
            force_deploy = requested_device in {"cpu", "gpu"}
            if force_deploy:
                routes = ensure_model_routes()
                forced_num_gpu = routes._resolve_num_gpu(requested_device, None)
                # Fail fast, before deploying anything, if any selected profile
                # can't be device-forced.
                for profile_name in selected:
                    backend = str(profiles_map[profile_name].get("backend", "ollama")).lower()
                    if backend != "ollama":
                        yield sse(
                            {
                                "event": "error",
                                "error": (
                                    "Benchmark device forcing is only supported for Ollama profiles; "
                                    f"{profile_name} uses {backend}"
                                ),
                            }
                        )
                        return

            peak_vram_by_profile: Dict[str, int] = {}

            def _track_peak_vram(profile_name: str) -> None:
                # Best-effort (nvidia-smi only, same as the CLI's VRAM sampling);
                # a missing/failed reading just means no peak_vram_mb is reported.
                used = bench.nvidia_smi_used_mb()
                if used is not None:
                    peak_vram_by_profile[profile_name] = max(peak_vram_by_profile.get(profile_name, 0), used)

            for event in bench.iter_run(
                base_url,
                profiles_map,
                selected,
                tests,
                req.timeout,
                num_gpu=forced_num_gpu,
                repetitions=req.repetitions,
                provenance=provenance,
            ):
                if force_deploy and event.get("event") == "profile_start":
                    # Deploy just this profile's model right before its tests run
                    # (instead of deploying every selected profile up front) so a
                    # multi-profile forced-device comparison never stacks more
                    # than one model in VRAM/RAM at a time.
                    profile_name = str(event.get("profile") or "")
                    model_id = str(event.get("model_id") or profile_name)
                    yield sse(
                        {
                            "event": "deploy_start",
                            "profile": profile_name,
                            "model_id": model_id,
                            "device": requested_device,
                        }
                    )
                    served = model_routes._serve_ollama(model_id, "60m", forced_num_gpu)
                    if not served.get("success"):
                        yield sse(
                            {
                                "event": "error",
                                "error": served.get("error") or served.get("message") or "model deploy failed",
                                "profile": profile_name,
                                "device": requested_device,
                            }
                        )
                        unload_event = unload_profile(profile_name)
                        if unload_event:
                            yield sse(unload_event)
                        break
                    deploy_end = {
                        "event": "deploy_end",
                        "profile": profile_name,
                        "model_id": model_id,
                        "device": requested_device,
                    }
                    if served.get("warning"):
                        deploy_end["warning"] = served["warning"]
                    yield sse(deploy_end)

                if event.get("event") == "test_result":
                    _track_peak_vram(str(event.get("profile") or ""))
                if event.get("event") == "profile_end":
                    profile_name = str(event.get("profile") or "")
                    actual = current_placement(profile_name)
                    if actual:
                        event["actual_device"] = actual
                    if profile_name in peak_vram_by_profile and isinstance(event.get("summary"), dict):
                        event["summary"]["peak_vram_mb"] = peak_vram_by_profile[profile_name]
                yield sse(event)
                if event.get("event") in {"profile_end", "profile_aborted"}:
                    unload_event = unload_profile(str(event.get("profile") or ""))
                    if unload_event:
                        yield sse(unload_event)
        except Exception as exc:  # never break the SSE contract
            yield sse({"event": "error", "error": str(exc)})
        finally:
            # Best-effort cleanup for client aborts and unexpected stream exits.
            for profile_name in selected:
                if profile_name in unloaded_profiles or not should_unload(profile_name):
                    continue
                model_id = model_for_profile(profile_name)
                if not model_id:
                    continue
                unloaded_profiles.add(profile_name)
                try:
                    ensure_model_routes()._ollama.unload_model(model_id)
                except Exception:
                    pass
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --- context-scaling sweep (Release R4) --------------------------------------
# "How does memory, prompt-processing speed, first-token latency, and
# generation speed change with context?" - the same profile, the same short
# test subset used by /system/recommend, run once per context tier via the
# /chat endpoint's existing context_limit override. No separate estimation or
# grading logic: this reuses execute_test, so numbers agree with every other
# benchmark surface in the app.
DEFAULT_SWEEP_CONTEXTS = [4096, 8192, 16384, 32768, 65536]


class ContextSweepRequest(BaseModel):
    profile: str
    contexts: List[int] = Field(default_factory=lambda: list(DEFAULT_SWEEP_CONTEXTS))
    sample_size: int = Field(default=3, ge=1, le=10)
    timeout: int = Field(default=120, ge=1, le=3_600)


@router.post("/benchmark/context-sweep")
def benchmark_context_sweep(req: ContextSweepRequest):
    bench = _bench()
    from api_server import load_config  # lazy: api_server owns config loading

    profiles_map = load_config().get("profiles", {})

    def event_stream():
        def sse(event: Dict[str, Any]) -> str:
            return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        if req.profile not in profiles_map:
            yield sse({"event": "error", "error": f"Unknown profile '{req.profile}'."})
            yield "data: [DONE]\n\n"
            return

        profile = profiles_map[req.profile]
        tests = sorted(bench.TEST_CASES, key=lambda t: t.max_output_tokens)[: max(1, req.sample_size)]
        base_url = bench.api_base_url()
        contexts = sorted(dict.fromkeys(c for c in req.contexts if c > 0))
        yield sse(
            {
                "event": "sweep_start", "profile": req.profile, "contexts": contexts,
                "sample_tests": [t.name for t in tests],
            }
        )

        for context in contexts:
            yield sse({"event": "context_start", "context": context})
            results = []
            for t in tests:
                try:
                    item = bench.execute_test(base_url, req.profile, profile, t, req.timeout, context_override=context)
                except Exception:
                    item = bench.TestResult(
                        name=t.name, category=t.category, success=False, elapsed_seconds=0.0,
                        response_length=0, response_preview="", accuracy=0.0, error="execute_test error",
                    )
                results.append(item)
                yield sse(
                    {
                        "event": "test_result", "context": context, "name": item.name,
                        "success": item.success, "elapsed_seconds": item.elapsed_seconds,
                        "context_limit_used": item.context_limit_used, "metrics": item.metrics,
                    }
                )
            successes = [r for r in results if r.success]
            rates = [r.approx_tokens_per_second for r in successes if r.approx_tokens_per_second is not None]
            ttft_values = [r.metrics.get("ttft_ms") for r in successes if r.metrics.get("ttft_ms") is not None]
            actual_contexts = {r.context_limit_used for r in successes if r.context_limit_used}
            yield sse(
                {
                    "event": "context_end",
                    "context": context,
                    "actual_context_used": next(iter(actual_contexts), None) if len(actual_contexts) == 1 else None,
                    "passed": len(successes),
                    "tests": len(results),
                    "mean_tokens_per_second": round(sum(rates) / len(rates), 2) if rates else None,
                    "mean_ttft_ms": round(sum(ttft_values) / len(ttft_values), 1) if ttft_values else None,
                    "vram_used_mb": bench.nvidia_smi_used_mb(),
                }
            )
        yield sse({"event": "sweep_end", "profile": req.profile})
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
