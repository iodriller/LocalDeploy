"""Regression coverage for the CLI run loop (`run_profile`) after the Step 7
refactor that extracted `execute_test`. Stubs `call_chat` so no server is
needed; asserts the notes/break/VRAM behavior is preserved.
"""
from __future__ import annotations

import benchmark

_TestCase = benchmark.TestCase


def _profile():
    return {
        "model_id": "m",
        "backend": "ollama",
        "enabled": True,
        "recommended_for_8gb_vram": True,
    }


def _cases(n):
    return [
        _TestCase(name=f"t{i}", category="x", prompt="p", grader=lambda s: 1.0, grader_explainer="")
        for i in range(n)
    ]


def test_run_profile_breaks_on_not_pulled(monkeypatch):
    monkeypatch.setattr(benchmark, "nvidia_smi_used_mb", lambda: None)
    monkeypatch.setattr(
        benchmark, "call_chat", lambda *a, **k: {"success": False, "error": "model not found, run ollama pull"}
    )
    result = benchmark.run_profile("http://x", "p", _profile(), _cases(3), 10)
    assert len(result.tests) == 1  # stopped after first failure
    assert any("not pulled" in note.lower() for note in result.notes)


def test_run_profile_flags_oom(monkeypatch):
    monkeypatch.setattr(benchmark, "nvidia_smi_used_mb", lambda: None)
    monkeypatch.setattr(
        benchmark, "call_chat", lambda *a, **k: {"success": False, "error": "CUDA out of memory"}
    )
    result = benchmark.run_profile("http://x", "p", _profile(), _cases(1), 10)
    assert result.fits_in_vram is False
    assert any("oom" in note.lower() for note in result.notes)


def test_run_profile_aborts_after_four_failures(monkeypatch):
    monkeypatch.setattr(benchmark, "nvidia_smi_used_mb", lambda: None)
    monkeypatch.setattr(
        benchmark, "call_chat", lambda *a, **k: {"success": False, "error": "generic failure"}
    )
    result = benchmark.run_profile("http://x", "p", _profile(), _cases(6), 10)
    assert len(result.tests) == 4
    assert any("aborted after 4" in note.lower() for note in result.notes)


def test_run_profile_passes_and_grades(monkeypatch):
    monkeypatch.setattr(benchmark, "nvidia_smi_used_mb", lambda: None)
    monkeypatch.setattr(
        benchmark, "call_chat", lambda *a, **k: {"success": True, "response": '["a", "b", "c"]'}
    )
    cases = [
        _TestCase(name="arr", category="x", prompt="p", grader=lambda s: 1.0, grader_explainer="")
    ]
    result = benchmark.run_profile("http://x", "p", _profile(), cases, 10)
    assert result.tests[0].success is True
    assert result.tests[0].accuracy == 1.0
    assert not result.notes
