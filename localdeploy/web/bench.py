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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


def _bench():
    import benchmark  # lazy: avoids importing the big module + TEST_CASES at boot

    return benchmark


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


class RunRequest(BaseModel):
    profiles: Optional[List[str]] = None
    questions: Optional[Dict[str, Any]] = None
    device: Optional[str] = None
    max_output_tokens: Optional[int] = None
    timeout: int = 240
    skip_categories: Optional[List[str]] = None
    include_categories: Optional[List[str]] = None


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

            if requested_device in {"cpu", "gpu"}:
                routes = ensure_model_routes()

                for profile_name in selected:
                    profile = profiles_map[profile_name]
                    backend = str(profile.get("backend", "ollama")).lower()
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
                    model_id = str(profile.get("model_id") or profile_name)
                    yield sse(
                        {
                            "event": "deploy_start",
                            "profile": profile_name,
                            "model_id": model_id,
                            "device": requested_device,
                        }
                    )
                    served = model_routes._serve_ollama(
                        model_id,
                        "60m",
                        model_routes._resolve_num_gpu(requested_device, None),
                    )
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
                        return
                    deploy_end = {
                        "event": "deploy_end",
                        "profile": profile_name,
                        "model_id": model_id,
                        "device": requested_device,
                    }
                    if served.get("warning"):
                        deploy_end["warning"] = served["warning"]
                    yield sse(deploy_end)
            for event in bench.iter_run(base_url, profiles_map, selected, tests, req.timeout):
                if event.get("event") == "profile_end":
                    actual = current_placement(str(event.get("profile") or ""))
                    if actual:
                        event["actual_device"] = actual
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
