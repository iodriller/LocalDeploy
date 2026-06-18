"""Guard: the static web UI must parse as valid JavaScript.

The rest of the suite is Python-only and never loads `web/app.js`, so a syntax
error there (e.g. smart quotes pasted from an editor) would otherwise ship
silently and break the entire UI. This shells out to `node --check`, which
parses without executing, and skips cleanly when Node isn't installed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_js_parses() -> None:
    result = subprocess.run(
        ["node", "--check", str(WEB_DIR / "app.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"web/app.js failed to parse:\n{result.stderr}"


def test_no_smart_quotes_as_js_delimiters() -> None:
    """Catch curly quotes used as code (string/attribute delimiters) even when
    Node is unavailable. Display-text curly quotes inside straight-quoted
    strings are fine; these patterns are the fatal ones."""
    text = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    bad_markers = ['("“', '”)', '="”', 'class=”', '$(“']
    # Common fatal shapes: $(“  ”)  || “”  join(“  class=”
    offenders = [m for m in ['$(“', '”)', 'class=”', 'join(“', 'split(“'] if m in text]
    assert not offenders, f"smart quotes used as JS delimiters in web/app.js: {offenders}"
