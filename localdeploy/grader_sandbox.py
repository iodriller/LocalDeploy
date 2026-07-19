"""Restricted execution for the benchmark's code-category graders.

Those graders score a model's answer by *running the candidate code* it returned
(e.g. a `levenshtein` function) against known test cases. Executing model output
in the server process is unsafe - it could have side effects, and a buggy answer
with an infinite loop would hang the grader forever.

This module runs that code in a short-lived child process with:
  - Python's isolated mode (`-I`): ignores env vars, user site-packages, and `$PYTHONPATH`.
  - AST validation plus a small builtin/import allowlist.
  - An audit hook blocking filesystem, process, network, registry, and native-library operations.
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
import ast, builtins, collections, contextlib, io, json, sys


class _SafeCollections:
    OrderedDict = collections.OrderedDict
    defaultdict = collections.defaultdict
    deque = collections.deque


_SAFE_COLLECTIONS = _SafeCollections()
_BANNED_NAMES = {
    "breakpoint", "compile", "delattr", "dir", "eval", "exec", "getattr",
    "globals", "help", "input", "locals", "open", "setattr", "vars",
}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level == 0 and name == "collections":
        return _SAFE_COLLECTIONS
    raise ImportError("only selected collections helpers are available")


def _validate_candidate(code):
    if not isinstance(code, str) or len(code) > 50_000:
        raise ValueError("candidate code is too large")
    tree = ast.parse(code, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name != "collections" for alias in node.names):
                raise ValueError("import is not allowed")
        elif isinstance(node, ast.ImportFrom):
            allowed = {"OrderedDict", "defaultdict", "deque"}
            if node.level != 0 or node.module != "collections" or any(alias.name not in allowed for alias in node.names):
                raise ValueError("import is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("private attributes are not allowed")
        elif isinstance(node, ast.Name) and (node.id in _BANNED_NAMES or node.id.startswith("__")):
            raise ValueError("unsafe name is not allowed")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_") and node.name != "__init__":
                raise ValueError("private functions are not allowed")
    return compile(tree, "<candidate>", "exec")


_SAFE_BUILTINS = {
    "__build_class__": builtins.__build_class__,
    "__import__": _safe_import,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "callable": callable,
    "dict": dict,
    "enumerate": enumerate,
    "Exception": Exception,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "KeyError": KeyError,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "object": object,
    "print": print,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "ValueError": ValueError,
    "zip": zip,
}


def _audit(event, args):
    blocked = (
        "ctypes.", "os.", "pathlib.", "shutil.", "socket.", "subprocess.",
        "winreg.",
    )
    if event == "open" or event.startswith(blocked):
        raise PermissionError("candidate side effect blocked")


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
        # 5s CPU and 512 MB address space - ample for these tiny problems, but
        # caps runaway loops and allocation bombs in candidate code.
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        cap = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except Exception:
        pass  # not available (e.g. Windows) - rely on the parent's timeout


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
    try:
        compiled = _validate_candidate(code)
    except Exception:
        emit({"passes": 0, "total": 1})
        return

    ns = {"__builtins__": _SAFE_BUILTINS, "__name__": "__candidate__"}
    sink = io.StringIO()
    try:
        sys.addaudithook(_audit)
        # Swallow anything the candidate prints so it can't pollute the result.
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            exec(compiled, ns)
            passes, total = harness(ns)
    except BaseException:
        emit({"passes": 0, "total": 1})
        return
    emit({"passes": int(passes), "total": int(total)})


_main()
'''


def run_code_fraction(code: str, harness: str, timeout: float = 8.0) -> float:
    """Exec candidate ``code`` in a restricted subprocess; return pass fraction [0, 1].

    ``harness`` selects which test battery to run ("levenshtein",
    "merge_intervals", "lru_cache"). Any failure yields 0.0.
    """
    if not isinstance(code, str) or len(code) > 50_000:
        return 0.0
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
