"""Endpoint tests for the benchmark web layer (Steps 7-8)."""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

import benchmark
from api_server import app
from localdeploy.control import models as models_mod

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
    expected = benchmark.builtin_test_cases()
    assert info["success"] is True
    assert info["test_count"] == len(expected)
    assert info["test_count"] > len(client.get("/benchmark/example").json()["questions"])
    assert sum(info["categories"].values()) == info["test_count"]
    assert {item["name"] for item in info["tests"]} == {test.name for test in expected}
    assert len(info["question_set"]["questions"]) == info["test_count"]
    assert client.post("/benchmark/validate", json=info["question_set"]).json()["valid"] is True


def test_validate_rejects_bad_set() -> None:
    bad = {"questions": [{"name": "x", "category": "c", "grader": {"type": "nope"}}]}
    report = client.post("/benchmark/validate", json=bad).json()
    assert report["valid"] is False
    assert report["errors"]


def test_run_streams_results(monkeypatch) -> None:
    def fake_execute(base_url, name, profile, test, timeout, num_gpu=None):
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
    assert "test_start" in text
    assert "test_result" in text
    assert "run_end" in text
    assert "[DONE]" in text


def test_run_with_device_deploys_before_benchmark(monkeypatch) -> None:
    deploys = []
    unloaded = []

    def fake_serve(model_id, keep_alive, num_gpu=None):
        deploys.append({"model_id": model_id, "keep_alive": keep_alive, "num_gpu": num_gpu})
        return {"success": True}

    def fake_execute(base_url, name, profile, test, timeout, num_gpu=None):
        return benchmark.TestResult(
            name=test.name,
            category=test.category,
            success=True,
            elapsed_seconds=0.1,
            response_length=4,
            response_preview="ok",
            accuracy=1.0,
        )

    monkeypatch.setattr(models_mod, "_serve_ollama", fake_serve)
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([{"name": "gemma3:4b", "size": 1, "size_vram": 0}], None))
    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda model: unloaded.append(model) or {})
    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    example = client.get("/benchmark/example").json()
    response = client.post(
        "/benchmark/run",
        json={"profiles": ["gemma3_4b_ollama_safe"], "questions": example, "device": "cpu"},
    )
    text = response.text
    assert response.status_code == 200
    assert deploys == [{"model_id": "gemma3:4b", "keep_alive": "60m", "num_gpu": 0}]
    # Deploy happens just-in-time for each profile (right after its profile_start,
    # before its first test) rather than for every selected profile up front -
    # this keeps a multi-profile forced-device run from stacking several models
    # in VRAM/RAM at once. What must hold is: deploy completes before any test runs.
    assert text.index("deploy_start") < text.index("test_start")
    assert text.index("deploy_end") < text.index("test_start")
    assert "benchmark_unload_end" in text
    assert "test_result" in text
    assert unloaded == ["gemma3:4b"]


def test_forced_device_multi_profile_never_stacks_deploys(monkeypatch) -> None:
    # Regression test: a forced-device run across N profiles must deploy/run/
    # unload one profile at a time, never loading a second profile's model
    # while the first is still resident (VRAM/RAM stacking risk).
    events: list[str] = []

    def fake_serve(model_id, keep_alive, num_gpu=None):
        events.append(f"deploy:{model_id}")
        return {"success": True}

    def fake_unload(model_id):
        events.append(f"unload:{model_id}")
        return {}

    def fake_execute(base_url, name, profile, test, timeout, num_gpu=None):
        events.append(f"test:{name}")
        return benchmark.TestResult(
            name=test.name,
            category=test.category,
            success=True,
            elapsed_seconds=0.1,
            response_length=4,
            response_preview="ok",
            accuracy=1.0,
        )

    monkeypatch.setattr(models_mod, "_serve_ollama", fake_serve)
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([], None))
    monkeypatch.setattr(models_mod._ollama, "unload_model", fake_unload)
    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    example = client.get("/benchmark/example").json()
    response = client.post(
        "/benchmark/run",
        json={
            "profiles": ["gemma3_4b_ollama_safe", "gemma3_12b_ollama_safe"],
            "questions": example,
            "device": "cpu",
        },
    )
    assert response.status_code == 200
    assert events == [
        "deploy:gemma3:4b",
        "test:gemma3_4b_ollama_safe",
        "test:gemma3_4b_ollama_safe",
        "unload:gemma3:4b",
        "deploy:gemma3:12b",
        "test:gemma3_12b_ollama_safe",
        "test:gemma3_12b_ollama_safe",
        "unload:gemma3:12b",
    ]


def test_forced_device_pins_num_gpu_on_each_inference(monkeypatch) -> None:
    # The fix: a forced CPU run must pin num_gpu on the inference calls too, not
    # just the warm-up - otherwise Ollama can re-place the model on GPU mid-run.
    seen_num_gpu = []

    def fake_execute(base_url, name, profile, test, timeout, num_gpu=None):
        seen_num_gpu.append(num_gpu)
        return benchmark.TestResult(
            name=test.name,
            category=test.category,
            success=True,
            elapsed_seconds=0.1,
            response_length=4,
            response_preview="ok",
            accuracy=1.0,
        )

    monkeypatch.setattr(models_mod, "_serve_ollama", lambda *a, **k: {"success": True})
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([{"name": "gemma3:4b", "size": 1, "size_vram": 0}], None))
    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda model: {})
    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    example = client.get("/benchmark/example").json()
    response = client.post(
        "/benchmark/run",
        json={"profiles": ["gemma3_4b_ollama_safe"], "questions": example, "device": "cpu"},
    )
    assert response.status_code == 200
    assert seen_num_gpu, "execute_test was never called"
    assert all(n == 0 for n in seen_num_gpu), f"expected num_gpu=0 on every call, got {seen_num_gpu}"


def test_auto_device_leaves_num_gpu_unset(monkeypatch) -> None:
    # Auto (no device) must not pin num_gpu - unchanged behavior, Ollama decides.
    seen_num_gpu = []

    def fake_execute(base_url, name, profile, test, timeout, num_gpu=None):
        seen_num_gpu.append(num_gpu)
        return benchmark.TestResult(
            name=test.name,
            category=test.category,
            success=True,
            elapsed_seconds=0.1,
            response_length=4,
            response_preview="ok",
            accuracy=1.0,
        )

    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([], None))
    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    example = client.get("/benchmark/example").json()
    response = client.post(
        "/benchmark/run",
        json={"profiles": ["gemma3_4b_ollama_safe"], "questions": example},
    )
    assert response.status_code == 200
    assert seen_num_gpu and all(n is None for n in seen_num_gpu)


def test_run_unloads_auto_loaded_benchmark_model(monkeypatch) -> None:
    unloaded = []

    def fake_execute(base_url, name, profile, test, timeout, num_gpu=None):
        return benchmark.TestResult(
            name=test.name,
            category=test.category,
            success=True,
            elapsed_seconds=0.1,
            response_length=4,
            response_preview="ok",
            accuracy=1.0,
        )

    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([], None))
    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda model: unloaded.append(model) or {})
    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    example = client.get("/benchmark/example").json()
    response = client.post(
        "/benchmark/run",
        json={"profiles": ["gemma3_4b_ollama_safe"], "questions": example},
    )
    assert response.status_code == 200
    assert "benchmark_unload_end" in response.text
    assert unloaded == ["gemma3:4b"]


def test_run_rejects_invalid_question_set() -> None:
    body = {"profiles": ["gemma3_4b_ollama_safe"], "questions": {"questions": [{"name": "x"}]}}
    out = client.post("/benchmark/run", json=body).json()
    assert out["success"] is False
    assert out["validation"]["valid"] is False


def test_run_requires_known_profile() -> None:
    out = client.post("/benchmark/run", json={"profiles": ["no_such_profile"]}).json()
    assert out["success"] is False
    assert "profiles" in out["error"].lower()
