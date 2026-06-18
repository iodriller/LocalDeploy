"""Sandboxed execution for the benchmark's code-category graders.

Those graders score a model's answer by *running the candidate code* it returned
(e.g. a `levenshtein` function) against known test cases. Executing model output
in the server process is unsafe — it could have side effects, and a buggy answer
with an infinite loop would hang the grader forever.

This module runs that code in a short-lived child process with:
  - Python's isolated mode (`-I`): ignores env vars, user site-packages, and `$PYTHONPATH`.
  - A wall-clock timeout in the parent.
  - CPU-time and address-space rlimits in the child (POSIX; best-effort on others).
  - Candidate stdout/stderr redirected away from the result channel.

Any failure (timeout, crash, resource cap, malformed output) degrades to a 0.0
score. Only the candidate code is untrusted; the test harnesses below are ours.
"""
from __future__ import annotations

import json
import subprocess
import sys

# The harnesses + worker that run inside the child. The candidate code is fed in
# on stdin (never via argv), so there are no escaping/length pitfalls.
_WORKER_SRC = r'''
import io, contextlib, json, sys


def _h_levenshtein(ns):
    fn = ns.get("levenshtein")
    if not callable(fn):
        return 0, 5
    cases = [("", "", 0), ("a", "a", 0), ("kitten", "sitting", 3), ("flaw", "lawn", 2), ("abc", "", 3)]
    passes = 0
    for a, b, expected in cases:
        try:
            if fn(a, b) == expected:
                passes += 1
        except Exception:
            pass
    return passes, len(cases)


def _h_merge_intervals(ns):
    fn = ns.get("merge_intervals")
    if not callable(fn):
        return 0, 4
    cases = [
        ([(1, 3), (2, 6), (8, 10), (15, 18)], {(1, 6), (8, 10), (15, 18)}),
        ([(1, 4), (4, 5)], {(1, 5)}),
        ([], set()),
        ([(1, 10), (2, 3), (4, 5)], {(1, 10)}),
    ]
    passes = 0
    for inp, expected in cases:
        try:
            got = fn(list(inp))
            if {tuple(x) for x in got} == expected:
                passes += 1
        except Exception:
            pass
    return passes, len(cases)


def _h_lru_cache(ns):
    cls = ns.get("LRUCache")
    if cls is None:
        return 0, 5
    try:
        cache = cls(2)
        cache.put(1, 1)
        cache.put(2, 2)
        passes = 0
        if cache.get(1) == 1:
            passes += 1
        cache.put(3, 3)
        if cache.get(2) == -1:
            passes += 1
        cache.put(4, 4)
        if cache.get(1) == -1:
            passes += 1
        if cache.get(3) == 3:
            passes += 1
        if cache.get(4) == 4:
            passes += 1
        return passes, 5
    except Exception:
        return 0, 5


_HARNESSES = {
    "levenshtein": _h_levenshtein,
    "merge_intervals": _h_merge_intervals,
    "lru_cache": _h_lru_cache,
}


def _apply_limits():
    try:
        import resource
        # 5s CPU and 512 MB address space — ample for these tiny problems, but
        # caps runaway loops and allocation bombs in candidate code.
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        cap = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except Exception:
        pass  # not available (e.g. Windows) — rely on the parent's timeout


def _main():
    real_stdout = sys.stdout

    def emit(d):
        real_stdout.write(json.dumps(d))

    try:
        req = json.loads(sys.stdin.read())
    except Exception:
        emit({"passes": 0, "total": 0})
        return

    harness = _HARNESSES.get(req.get("harness", ""))
    code = req.get("code", "")
    if harness is None:
        emit({"passes": 0, "total": 0})
        return

    _apply_limits()
    ns = {}
    sink = io.StringIO()
    try:
        # Swallow anything the candidate prints so it can't pollute the result.
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            exec(compile(code, "<candidate>", "exec"), ns)
            passes, total = harness(ns)
    except BaseException:
        emit({"passes": 0, "total": 1})
        return
    emit({"passes": int(passes), "total": int(total)})


_main()
'''


def run_code_fraction(code: str, harness: str, timeout: float = 8.0) -> float:
    """Exec candidate ``code`` in a sandboxed subprocess; return pass fraction [0, 1].

    ``harness`` selects which test battery to run ("levenshtein",
    "merge_intervals", "lru_cache"). Any failure yields 0.0.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _WORKER_SRC],
            input=json.dumps({"code": code, "harness": harness}),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return 0.0
    if proc.returncode != 0 or not proc.stdout.strip():
        return 0.0
    try:
        out = json.loads(proc.stdout.strip())
        total = int(out.get("total", 0))
        if total <= 0:
            return 0.0
        return max(0.0, min(1.0, int(out.get("passes", 0)) / total))
    except Exception:
        return 0.0
