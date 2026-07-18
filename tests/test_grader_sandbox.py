"""Security tests for the code-grader worker.

The code-category graders run a model's *response* code. These verify the
  restricted worker contains it: side effects are blocked, infinite loops time
out instead of hanging, and a correct answer still scores fully.
"""
from __future__ import annotations

import time

from localdeploy.grader_sandbox import run_code_fraction


def test_correct_code_scores_full():
    code = (
        "def levenshtein(a, b):\n"
        "    if not a: return len(b)\n"
        "    if not b: return len(a)\n"
        "    prev = list(range(len(b) + 1))\n"
        "    for i, ca in enumerate(a, 1):\n"
        "        cur = [i]\n"
        "        for j, cb in enumerate(b, 1):\n"
        "            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))\n"
        "        prev = cur\n"
        "    return prev[-1]\n"
    )
    assert run_code_fraction(code, "levenshtein") == 1.0


def test_wrong_code_scores_zero():
    assert run_code_fraction("def levenshtein(a, b):\n    return 999\n", "levenshtein") == 0.0


def test_file_side_effect_is_blocked(tmp_path):
    # A model answer that tries to write a file as a top-level side effect is
    # rejected before execution.
    marker = tmp_path / "pwned.txt"
    code = (
        f"open({str(marker)!r}, 'w').write('x')\n"
        "def levenshtein(a, b):\n    return 999\n"  # clearly wrong → 0.0
    )
    score = run_code_fraction(code, "levenshtein")
    assert score == 0.0
    assert not marker.exists()


def test_non_allowlisted_import_is_blocked(tmp_path):
    marker = tmp_path / "imported.txt"
    code = (
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('x')\n"
        "def levenshtein(a, b):\n    return 0\n"
    )
    assert run_code_fraction(code, "levenshtein") == 0.0
    assert not marker.exists()


def test_infinite_loop_times_out_quickly():
    # Without the sandbox this would hang the grader forever; with it, the parent
    # timeout fires and we degrade to 0.0 in a bounded time.
    code = "def levenshtein(a, b):\n    while True:\n        pass\n"
    start = time.perf_counter()
    score = run_code_fraction(code, "levenshtein", timeout=3.0)
    elapsed = time.perf_counter() - start
    assert score == 0.0
    assert elapsed < 10.0  # bounded by the timeout, not infinite


def test_syntactically_broken_code_scores_zero():
    assert run_code_fraction("def levenshtein(:\n  pass", "levenshtein") == 0.0


def test_unknown_harness_scores_zero():
    assert run_code_fraction("x = 1", "nonexistent") == 0.0
