"""Run dependency-free Node unit tests for pure frontend module exports."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_frontend_node_units() -> None:
    tests = sorted((PROJECT_ROOT / "tests" / "js").glob("*.test.mjs"))
    result = subprocess.run(
        ["node", "--experimental-default-type=module", "--test", *map(str, tests)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
