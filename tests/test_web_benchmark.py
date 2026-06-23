"""Endpoint tests for the benchmark web layer (Steps 7-8)."""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

import benchmark
from api_server import app

client = TestClient(app)


def test_benchmark_example_is_served_and_valid() -> None:
    example = client.get("/benchmark/example").json()
    assert example["version"] == 1
    assert len(example["questions"]) == 2
    # the example must itself pass validation
    report = client.post("/benchmark/validate", json=example).json()
    assert report["valid"] is True


def test_benchmark_test_bench_metadata_matches_builtin_cases() -> None:
    info = client.get("/benchmark/test-bench").json()
    assert info["success"] is True
    assert info["test_count"] == len(benchmark.TEST_CASES)
    assert info["test_count"] > len(client.get("/benchmark/example").json()["questions"])
    assert sum(info["categories"].values()) == info["test_count"]
    assert {item["name"] for item in info["tests"]} == {test.name for test in benchmark.TEST_CASES}
    assert len(info["question_set"]["questions"]) == info["test_count"]
    assert client.post("/benchmark/validate", json=info["question_set"]).json()["valid"] is True


def test_validate_rejects_bad_set() -> None:
    bad = {"questions": [{"name": "x", "category": "c", "grader": {"type": "nope"}}]}
    report = client.post("/benchmark/validate", json=bad).json()
    assert report["valid"] is False
    assert report["errors"]


def test_run_streams_results(monkeypatch) -> None:
    def fake_execute(base_url, name, profile, test, timeout):
        return benchmark.TestResult(
            name=test.name,
            category=test.category,
            success=True,
            elapsed_seconds=0.1,
            response_length=4,
            response_preview="ok",
            accuracy=1.0,
        )

    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    example = client.get("/benchmark/example").json()
    response = client.post(
        "/benchmark/run",
        json={"profiles": ["gemma3_4b_ollama_safe"], "questions": example},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert "run_start" in text
    assert "test_result" in text
    assert "run_end" in text
    assert "[DONE]" in text


def test_run_rejects_invalid_question_set() -> None:
    body = {"profiles": ["gemma3_4b_ollama_safe"], "questions": {"questions": [{"name": "x"}]}}
    out = client.post("/benchmark/run", json=body).json()
    assert out["success"] is False
    assert out["validation"]["valid"] is False


def test_run_requires_known_profile() -> None:
    out = client.post("/benchmark/run", json={"profiles": ["no_such_profile"]}).json()
    assert out["success"] is False
    assert "profiles" in out["error"].lower()
