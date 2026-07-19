"""Tests for Release R4 (benchmark expansion): percentile stats, regression
detection (dimension diffs + ttft/peak-VRAM deltas), benchmark packs, and the
context-scaling sweep endpoint.
"""
from __future__ import annotations

import json

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

import benchmark
from api_server import app
from localdeploy.control.report import _dimension_diffs, _summary, build_card

client = TestClient(app)


# ---- percentile / stats block ------------------------------------------------

def test_percentile_linear_interpolation():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert benchmark._percentile(values, 50) == 30.0
    assert benchmark._percentile(values, 0) == 10.0
    assert benchmark._percentile(values, 100) == 50.0
    assert benchmark._percentile([], 90) is None
    assert benchmark._percentile([5.0], 90) == 5.0


def test_stats_block_reports_all_fields():
    block = benchmark._stats_block([1.0, 2.0, 3.0, 4.0, 5.0], digits=2)
    assert block["mean"] == 3.0
    assert block["median"] == 3.0
    assert block["min"] == 1.0
    assert block["max"] == 5.0
    assert block["p90"] is not None
    assert block["p95"] is not None
    assert block["stdev"] > 0


def test_stats_block_empty_is_graceful():
    block = benchmark._stats_block([])
    assert block["mean"] is None
    assert block["stdev"] == 0.0


def test_run_repeated_emits_p90_in_aggregate(monkeypatch):
    call_count = {"n": 0}

    def fake_execute(base_url, name, profile, test, timeout, num_gpu=None, repetition=1, warm_state=None, context_override=None):
        call_count["n"] += 1
        return benchmark.TestResult(
            name=test.name, category=test.category, success=True, elapsed_seconds=1.0 + call_count["n"] * 0.1,
            response_length=4, response_preview="ok", accuracy=1.0, approx_tokens_per_second=20.0,
        )

    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    example = client.get("/benchmark/example").json()
    response = client.post(
        "/benchmark/run",
        json={"profiles": ["gemma3_4b_ollama_safe"], "questions": example, "repetitions": 3},
    )
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
    aggregates = [e for e in events if e.get("event") == "test_aggregate"]
    assert aggregates
    assert all("latency_p90_seconds" in a for a in aggregates)
    profile_ends = [e for e in events if e.get("event") == "profile_end"]
    assert profile_ends
    assert "latency_p90_seconds" in profile_ends[0]["summary"]


# ---- regression detection (report.py) ---------------------------------------

def _card_with_provenance(profile, model_digest, backend_version, quant, context, tps, ttft_ms, peak_vram_mb):
    return build_card(
        {
            "profile": profile,
            "model_id": "qwen3:8b",
            "peak_vram_mb": peak_vram_mb,
            "provenance": {
                "localdeploy_version": "0.5.1",
                "profiles": {
                    profile: {
                        "backend_version": backend_version, "model_digest": model_digest,
                        "quant": quant, "context": context,
                    }
                },
                "hardware": {"gpus": [{"name": "RTX 4090"}]},
            },
            "tests": [
                {
                    "name": "t1", "category": "code", "success": True, "accuracy": 1.0,
                    "elapsed_seconds": 1.0, "approx_tokens_per_second": tps,
                    "metrics": {"ttft_ms": ttft_ms},
                }
            ],
        }
    )


def test_summary_includes_avg_ttft_ms():
    s = _summary([{"name": "t", "category": "c", "success": True, "accuracy": 1.0, "elapsed_seconds": 1.0, "metrics": {"ttft_ms": 250.0}}])
    assert s["avg_ttft_ms"] == 250.0


def test_dimension_diffs_flags_runtime_upgrade():
    card_a = _card_with_provenance("p", "sha256:aaa", "0.11.0", "Q4_K_M", 8192, 68.0, 300.0, 12000)
    card_b = _card_with_provenance("p", "sha256:aaa", "0.12.0", "Q4_K_M", 8192, 59.0, 320.0, 12800)
    resp = client.post("/benchmark/compare", json={"card_a": card_a, "card_b": card_b}).json()
    assert resp["success"] is True
    diffs = {d["dimension"]: d for d in resp["dimension_diffs"]}
    assert diffs["Runtime version"]["changed"] is True
    assert diffs["Runtime version"]["a"] == "0.11.0"
    assert diffs["Runtime version"]["b"] == "0.12.0"
    assert diffs["Model digest"]["changed"] is False
    assert resp["summary_delta"]["peak_vram_mb"] == 800
    assert resp["summary_delta"]["avg_ttft_ms"] == pytest.approx(20.0)
    assert resp["summary_delta"]["avg_tokens_per_second"] == pytest.approx(-9.0)


def test_dimension_diffs_empty_when_no_provenance():
    card_a = build_card({"profile": "p", "model_id": "m", "tests": []})
    card_b = build_card({"profile": "p", "model_id": "m", "tests": []})
    diffs = _dimension_diffs(card_a, card_b)
    assert diffs == []


# ---- benchmark packs ----------------------------------------------------------

def test_benchmark_packs_lists_known_packs():
    body = client.get("/benchmark/packs").json()
    assert body["success"] is True
    ids = {p["id"] for p in body["packs"]}
    assert {"general", "coding", "structured", "reasoning"} <= ids
    coding = next(p for p in body["packs"] if p["id"] == "coding")
    assert coding["categories"] == ["code"]
    assert coding["test_count"] == sum(1 for t in benchmark.TEST_CASES if t.category == "code")


def test_run_with_pack_expands_to_include_categories(monkeypatch):
    seen_categories = set()

    def fake_execute(base_url, name, profile, test, timeout, num_gpu=None, repetition=1, warm_state=None, context_override=None):
        seen_categories.add(test.category)
        return benchmark.TestResult(
            name=test.name, category=test.category, success=True, elapsed_seconds=0.1,
            response_length=2, response_preview="ok", accuracy=1.0,
        )

    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    response = client.post("/benchmark/run", json={"profiles": ["gemma3_4b_ollama_safe"], "pack": "coding"})
    assert response.status_code == 200
    assert seen_categories == {"code"}


def test_run_explicit_include_categories_wins_over_pack(monkeypatch):
    seen_categories = set()

    def fake_execute(base_url, name, profile, test, timeout, num_gpu=None, repetition=1, warm_state=None, context_override=None):
        seen_categories.add(test.category)
        return benchmark.TestResult(
            name=test.name, category=test.category, success=True, elapsed_seconds=0.1,
            response_length=2, response_preview="ok", accuracy=1.0,
        )

    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    response = client.post(
        "/benchmark/run",
        json={"profiles": ["gemma3_4b_ollama_safe"], "pack": "coding", "include_categories": ["math"]},
    )
    assert response.status_code == 200
    assert seen_categories == {"math"}


# ---- context-scaling sweep ----------------------------------------------------

def test_context_sweep_unknown_profile_errors():
    response = client.post("/benchmark/context-sweep", json={"profile": "does-not-exist"})
    assert response.status_code == 200
    assert "Unknown profile" in response.text


def test_context_sweep_streams_one_context_end_per_tier(monkeypatch):
    calls = []

    def fake_execute(base_url, profile_name, profile, test, timeout, context_override=None, **kwargs):
        calls.append(context_override)
        return benchmark.TestResult(
            name=test.name, category=test.category, success=True, elapsed_seconds=0.2,
            response_length=4, response_preview="ok", accuracy=1.0, approx_tokens_per_second=25.0,
            metrics={"ttft_ms": 150.0}, context_limit_used=context_override,
        )

    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    response = client.post(
        "/benchmark/context-sweep",
        json={"profile": "gemma3_4b_ollama_safe", "contexts": [4096, 8192], "sample_size": 2},
    )
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
    starts = [e for e in events if e.get("event") == "sweep_start"]
    assert starts[0]["contexts"] == [4096, 8192]
    ends = [e for e in events if e.get("event") == "context_end"]
    assert [e["context"] for e in ends] == [4096, 8192]
    assert all(e["mean_tokens_per_second"] == 25.0 for e in ends)
    assert all(e["mean_ttft_ms"] == 150.0 for e in ends)
    assert set(calls) == {4096, 8192}


def test_context_sweep_dedupes_and_sorts_contexts(monkeypatch):
    def fake_execute(base_url, profile_name, profile, test, timeout, context_override=None, **kwargs):
        return benchmark.TestResult(
            name=test.name, category=test.category, success=True, elapsed_seconds=0.1,
            response_length=2, response_preview="ok", accuracy=1.0,
        )

    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    response = client.post(
        "/benchmark/context-sweep",
        json={"profile": "gemma3_4b_ollama_safe", "contexts": [8192, 4096, 8192], "sample_size": 1},
    )
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
    starts = [e for e in events if e.get("event") == "sweep_start"]
    assert starts[0]["contexts"] == [4096, 8192]
