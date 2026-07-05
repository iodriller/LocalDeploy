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


def test_ui_assets_are_cache_busted_and_no_favicon_404() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'href="styles.css?v=20260705-ui17"' in html
    assert 'src="app.js?v=20260705-ui17"' in html
    assert 'rel="icon" type="image/png" href="favicon.png?v=20260705-ui17"' in html
    assert (WEB_DIR / "favicon.png").is_file()


def test_benchmark_workspace_v2_labels_are_present() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    for label in ("Benchmark runner", "Leaderboard", "Category heatmap", "Compare selected"):
        assert label in html


def test_new_ui_controls_have_safe_bindings() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    for dom_id in ("btn-hf-search", "btn-fit-profiles", "fit-filter", "hf-fit-filter", "vram-budget-gb"):
        assert f'id="{dom_id}"' in html
    assert '$("#btn-hf-search")?.addEventListener("click", (e) => checkUpdates(e))' in js
    assert '$("#btn-fit-profiles")?.addEventListener("click", scanConfiguredFits)' in js
    assert '$("#vram-budget-gb")?.addEventListener("input", () => {' in js


def test_hardware_is_readonly_and_model_budget_lives_with_models() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    hardware = html.split("<!-- Served model / status -->", 1)[0]
    models = html.split("<!-- Models -->", 1)[1].split("<!-- Auto-pick a profile -->", 1)[0]
    assert "Live VRAM" in hardware
    assert "Custom GB" not in hardware
    assert "Model fit budget" in models
    assert "Custom GB" in models
    assert 'id="keep-alive" value="60m"' in html
    assert 'id="btn-stop"' not in html
    assert "unload from the Served model card" in html


def test_saved_profiles_and_benchmark_copy_are_clear() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert "Saved run profiles" in html
    assert "Profiles are recipes from <code>config.json</code>" in html
    assert "Scan saved profiles" in html
    assert "Use LocalDeploy test bench" in html
    assert "Load JSON sample" in html
    assert "Leave the editor empty to run the built-in LocalDeploy test bench" in html


def test_model_discovery_readability_and_tune_progress_styles_exist() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    assert ".mrow a.name" in css
    assert "#c7ddff" in css
    assert ".discover-section .mrow" in css
    assert ".run-progress-fill.indeterminate" in css
    assert ".tune-steps" in css


def test_ui_does_not_guess_unattributed_vram_sources() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "Windows, the display" not in js
    assert "browser/GPU apps" not in js
    assert "cannot attribute this VRAM to exact processes" not in js
    assert "LocalDeploy cannot attribute" not in js
    assert "Ollama reports" not in js
    assert "Ollama model VRAM" in js
    assert "Unload from Served model card" in js
    assert "downloads not reported" in js
