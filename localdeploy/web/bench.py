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
        try:
            for event in bench.iter_run(base_url, profiles_map, selected, tests, req.timeout):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # never break the SSE contract
            yield f"data: {json.dumps({'event': 'error', 'error': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
