"""Tests for the question-set grader registry, validator, and streaming run
generator added to benchmark.py (Steps 7-8). No server or backend required:
the run generator is exercised with a stubbed `execute_test`.
"""
from __future__ import annotations

import pytest

import benchmark
from benchmark import (
    EXAMPLE_QUESTION_SET,
    build_grader,
    build_test_cases,
    iter_run,
    validate_question_set,
)

# Referenced via the module (not imported by name) so pytest does not try to
# collect the `TestCase` / `TestResult` dataclasses as test classes.
_TestCase = benchmark.TestCase
_TestResult = benchmark.TestResult


# --- grader registry ---------------------------------------------------------


def test_number_within():
    grader = build_grader({"type": "number_within", "expected": 30, "tolerance": 0.5})
    assert grader("the answer is 30") == 1.0
    assert grader("31") == 0.0


def test_json_array_min_len():
    grader = build_grader({"type": "json_array_min_len", "min": 3})
    assert grader('["a", "b", "c"]') == 1.0
    assert grader('["a"]') == 0.0


def test_contains_all_partial_credit():
    grader = build_grader({"type": "contains_all", "keywords": ["foo", "bar"]})
    assert grader("foo and bar") == 1.0
    assert grader("only foo") == 0.5


def test_exact_match_case_insensitive_default():
    grader = build_grader({"type": "exact_match", "expected": "Yes"})
    assert grader("  yes ") == 1.0
    assert grader("no") == 0.0


def test_classification_set():
    grader = build_grader({"type": "classification_set", "expected": ["a", "b"]})
    assert grader('["b", "a"]') == 1.0
    assert grader("a") == 0.0


def test_api_base_url_normalizes_bind_all(monkeypatch):
    monkeypatch.setenv("API_HOST", "0.0.0.0")
    monkeypatch.setenv("API_PORT", "8000")
    assert benchmark.api_base_url() == "http://127.0.0.1:8000"


def test_unknown_grader_type_raises():
    with pytest.raises(ValueError):
        build_grader({"type": "does_not_exist"})


def test_json_array_min_len_bad_spec_raises():
    with pytest.raises(ValueError):
        build_grader({"type": "json_array_min_len", "min": "three"})


# --- validation --------------------------------------------------------------


def test_example_set_is_valid():
    report = validate_question_set(EXAMPLE_QUESTION_SET)
    assert report["valid"] is True
    assert report["errors"] == []
    assert report["question_count"] == 2


def test_validation_reports_row_errors():
    bad = {
        "questions": [
            {"name": "ok", "category": "c", "prompt": "p", "grader": {"type": "number_within", "expected": 1}},
            {"name": "ok", "category": "c", "prompt": "p", "grader": {"type": "number_within", "expected": 1}},  # dup
            {"name": "missing_prompt", "category": "c", "grader": {"type": "exact_match", "expected": "x"}},
            {"name": "bad_grader", "category": "c", "prompt": "p", "grader": {"type": "nope"}},
        ]
    }
    report = validate_question_set(bad)
    assert report["valid"] is False
    messages = " ".join(e["error"] for e in report["errors"])
    assert "duplicate" in messages
    assert "prompt" in messages
    assert "grader" in messages


def test_validation_rejects_non_object():
    report = validate_question_set([1, 2, 3])
    assert report["valid"] is False


# --- streaming run generator -------------------------------------------------


def _stub(success: bool, error: str = None):
    def execute(base_url, name, profile, test, timeout, num_gpu=None):
        return _TestResult(
            name=test.name,
            category=test.category,
            success=success,
            elapsed_seconds=0.1,
            response_length=4,
            response_preview="ok",
            accuracy=1.0 if success else 0.0,
            error=error,
        )

    return execute


def test_iter_run_happy_path(monkeypatch):
    monkeypatch.setattr(benchmark, "execute_test", _stub(success=True))
    tests = build_test_cases(EXAMPLE_QUESTION_SET)
    events = list(iter_run("http://x", {"p": {"model_id": "m"}}, ["p"], tests, 10))
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start"
    assert kinds[-1] == "run_end"
    assert kinds.count("test_result") == 2
    end = [e for e in events if e["event"] == "profile_end"][0]
    assert end["summary"] == {"tests": 2, "passed": 2, "avg_accuracy": 1.0}


def test_iter_run_aborts_on_not_pulled(monkeypatch):
    monkeypatch.setattr(benchmark, "execute_test", _stub(success=False, error="model not found; run ollama pull"))
    tests = build_test_cases(EXAMPLE_QUESTION_SET)
    events = list(iter_run("http://x", {"p": {"model_id": "m"}}, ["p"], tests, 10))
    kinds = [e["event"] for e in events]
    assert "profile_aborted" in kinds
    assert kinds.count("test_result") == 1  # stopped after the first failure


def test_iter_run_aborts_after_four_consecutive_failures(monkeypatch):
    monkeypatch.setattr(benchmark, "execute_test", _stub(success=False, error="some generic error"))
    tests = [
        _TestCase(name=f"t{i}", category="x", prompt="p", grader=lambda s: 1.0, grader_explainer="")
        for i in range(6)
    ]
    events = list(iter_run("http://x", {"p": {"model_id": "m"}}, ["p"], tests, 10))
    assert events[-1]["event"] == "run_end"
    aborted = [e for e in events if e["event"] == "profile_aborted"]
    assert aborted and "4 consecutive" in aborted[0]["reason"]
    # 4 failures then abort -> exactly 4 test_result events
    assert sum(1 for e in events if e["event"] == "test_result") == 4
