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

WEB_DIR = Path(__file__).resolve().parent.parent / "localdeploy" / "web"


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
    # Common fatal shapes: $(“  ”)  || “”  join(“  class=”
    offenders = [m for m in ['$(“', '”)', 'class=”', 'join(“', 'split(“'] if m in text]
    assert not offenders, f"smart quotes used as JS delimiters in web/app.js: {offenders}"


def test_ui_assets_are_cache_busted_and_no_favicon_404() -> None:
    """Every static asset must carry one shared ?v= token (a stale-cache guard),
    without pinning the token's value so a version bump doesn't break the test."""
    import re

    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    versions = set(re.findall(r'(?:styles\.css|app\.js|favicon\.png|logo\.svg)\?v=([\w-]+)', html))
    assert len(versions) == 1, f"expected one shared cache-bust token, found: {sorted(versions)}"
    assert 'rel="icon" type="image/png" href="favicon.png?v=' in html
    assert (WEB_DIR / "favicon.png").is_file()
    assert (WEB_DIR / "logo.svg").is_file()


def test_benchmark_workspace_v2_labels_are_present() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    for label in ("Benchmark runner", "Leaderboard", "Category heatmap", "Compare selected"):
        assert label in html


def test_new_ui_controls_have_safe_bindings() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    for dom_id in (
        "btn-hf-search", "btn-fit-profiles", "fit-filter", "catalog-source-status", "vram-budget-gb",
        # chat playground
        "chat-model", "chat-keep-alive", "btn-chat-session", "chat-session-state",
        "btn-chat-send", "btn-chat-clear", "chat-input", "chat-images",
        # quant advisor
        "quant-model", "btn-quant-advise", "quant-body",
        # disk usage / bulk delete
        "installed-sort", "models-disk-summary", "btn-bulk-delete", "btn-bulk-clear",
        # server-side history + orphan cleanup
        "bench-history-server", "btn-clean-orphans",
        # provider catalog + repeated benchmark variance
        "btn-provider-refresh", "provider-catalog-body", "bench-repetitions",
    ):
        assert f'id="{dom_id}"' in html, dom_id
    assert '$("#btn-hf-search")?.addEventListener("click", (e) => searchUnifiedModels(e))' in js
    assert '$("#btn-fit-profiles")?.addEventListener("click", scanConfiguredFits)' in js
    for binding in ("sendChatMessage", "quantAdvise", "bulkDeleteSelected", "initServerHistoryToggle"):
        assert binding in js, binding
    assert '$("#vram-budget-gb")?.addEventListener("input", () => {' in js
    assert '["#profile-select", "#bench-profile-select"]' in js
    assert "#chat-profile" not in js


def test_chat_tab_present() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'data-tab="chat"' in html
    assert 'id="tab-chat"' in html


def test_api_docs_quant_help_and_model_lifecycle_controls_are_present() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    assert "apiDocsUrl" in js
    assert "${window.location.origin}/docs" in js
    assert 'class="btn compact api-docs-link"' in js
    assert "QUANT_EXPLANATIONS" in js
    assert "4-bit K-quant, medium variant" in js
    assert html.count('class="help-tip"') >= 4
    assert ".help-tip::after" in css
    assert ".quant-label" in css
    assert "unload-installed-btn" in js
    assert 'postJSON("/models/stop"' in js


def test_system_card_holds_hardware_and_fit_budget() -> None:
    """New IA: hardware, live VRAM, and the model fit budget all live together in
    the top 'System' card; deploy options moved to 'Your models'."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    system = html.split("1. SYSTEM", 1)[1].split("2. GET A MODEL", 1)[0]
    assert "Live VRAM" in system
    assert "Model fit budget" in system
    assert "Custom GB" in system
    # Deploy options live with Your models, not a top-of-page deploy card.
    assert 'id="keep-alive" value="60m"' in html
    assert 'id="btn-stop"' not in html
    assert "<h2>Deploy a profile</h2>" not in html  # old top card is gone


def test_profiles_and_benchmark_copy_are_clear() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    # Profiles now live in the Advanced zone as "All run profiles".
    assert "All run profiles" in html
    assert "<code>config.json</code> recipes" in html
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
    assert "Unload this model from RAM and VRAM" in js
    assert "downloads not reported" in js
