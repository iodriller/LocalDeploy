"""Browser smoke tests for the web UI (`/ui`), driven with Playwright.

These complement the Python-only suite (which never actually loads the page) by
launching the real FastAPI app and driving the rendered UI in headless Chromium:
tab switching, and the benchmark run-library controls (per-run delete, clear
history) added for local history management.

They skip cleanly when Playwright or its browser isn't installed, so the default
`pytest` run on a fresh checkout stays green:

    pip install -r requirements-dev.txt
    python -m playwright install chromium
    pytest tests/test_ui_playwright.py -v

No Ollama or GPU is required — the page loads and the benchmark tab renders from
seeded browser localStorage, independent of any backend model state.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_server():
    """Run the real app in a background uvicorn thread; yield its base URL."""
    import uvicorn

    from api_server import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        pytest.fail("uvicorn did not start within 20s")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser binary not installed
            pytest.skip(f"chromium not available: run 'python -m playwright install chromium' ({exc})")
        yield b
        b.close()


def _seed_runs(n: int) -> str:
    runs = []
    for i in range(n):
        runs.append(
            {
                "id": f"run-{i}",
                "createdAt": "2026-06-24T00:00:00.000Z",
                "profile": f"profile_{i}",
                "modelId": f"model_{i}",
                "source": "restored-history",
                "tests": [
                    {"name": "t1", "category": "planning", "success": True, "accuracy": 1.0, "elapsed_seconds": 1.0, "approx_tokens_per_second": 10.0}
                ],
                "summary": {"tests": 1, "passed": 1, "avg_accuracy": 1.0, "avg_latency_s": 1.0, "avg_tokens_per_second": 10.0},
            }
        )
    return json.dumps(runs)


def _open_bench_tab(page, base, seed=0):
    if seed:
        # add_init_script runs before the page's own scripts, so the app reads
        # our seeded history on first load.
        page.add_init_script(f'window.localStorage.setItem("localdeploy.benchmarkRuns.v1", {json.dumps(_seed_runs(seed))});')
    page.goto(f"{base}/ui", wait_until="domcontentloaded")
    page.get_by_role("tab", name="Benchmark & Compare").click()


def test_page_loads_and_tabs_switch(live_server, browser):
    page = browser.new_page()
    try:
        page.goto(f"{live_server}/ui", wait_until="domcontentloaded")
        assert "LocalDeploy" in page.title()
        # Tab 1 default content.
        assert page.get_by_role("heading", name="System").is_visible()
        # Switch to tab 2 and confirm the benchmark runner renders.
        page.get_by_role("tab", name="Benchmark & Compare").click()
        assert page.get_by_role("heading", name="Benchmark runner").is_visible()
        assert page.get_by_role("heading", name="Run queue").is_visible()
    finally:
        page.close()


def test_tooltips_support_touch_and_stay_inside_mobile_viewport(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
    try:
        page.goto(f"{live_server}/ui", wait_until="domcontentloaded")
        page.get_by_role("tab", name="Setup & Deploy").click()
        trigger = page.locator(".deploy-opts .help-tip").first
        trigger.tap()

        tooltip = page.locator("#ui-tooltip")
        sync_api.expect(tooltip).to_be_visible()
        sync_api.expect(tooltip).to_contain_text("Auto lets Ollama choose placement")
        sync_api.expect(trigger).to_have_attribute("aria-expanded", "true")
        box = tooltip.bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= 390
        assert page.evaluate("document.documentElement.scrollWidth") == 390

        trigger.tap()
        sync_api.expect(tooltip).to_be_hidden()
        sync_api.expect(trigger).to_have_attribute("aria-expanded", "false")
    finally:
        page.close()


def test_run_library_per_run_delete(live_server, browser):
    page = browser.new_page()
    try:
        _open_bench_tab(page, live_server, seed=3)
        page.wait_for_selector(".run-library-row")
        rows = page.locator(".run-library-row")
        assert rows.count() == 3
        # Each row exposes a per-run delete control.
        assert page.locator(".run-library-row .run-delete").count() == 3
        # Delete one run; the library shrinks to two.
        page.locator(".run-library-row .run-delete").first.click()
        page.wait_for_function("document.querySelectorAll('.run-library-row').length === 2")
        assert page.locator(".run-library-row").count() == 2
    finally:
        page.close()


def test_clear_history_is_present_and_confirms(live_server, browser):
    page = browser.new_page()
    try:
        _open_bench_tab(page, live_server, seed=2)
        page.wait_for_selector(".run-library-row")
        # Dismiss the confirm() dialog -> nothing is cleared.
        page.on("dialog", lambda d: d.dismiss())
        page.get_by_role("button", name="Clear history").click()
        page.wait_for_timeout(200)
        assert page.locator(".run-library-row").count() == 2
    finally:
        page.close()


def test_chat_only_lists_installed_models_and_tracks_load_delete(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    runtime = {
        "installed": [
            {
                "name": "gemma3:4b",
                "size": 3_300_000_000,
                "details": {"parameter_size": "4.3B", "quantization_level": "Q4_K_M"},
            }
        ],
        "running": [],
        "stop_pending": 0,
    }
    stopped_models = []
    profiles = {
        "gemma3_4b": {
            "backend": "ollama",
            "model_id": "gemma3:4b",
            "enabled": True,
            "supports_vision": True,
        },
        "missing_disabled_gguf": {
            "backend": "llamacpp",
            "model_id": "C:/models/missing.gguf",
            "enabled": False,
            "model_file_exists": False,
        },
    }

    page.route(
        "**/profiles",
        lambda route: route.fulfill(json={"success": True, "default_profile": "missing_disabled_gguf", "profiles": profiles}),
    )
    page.route(
        "**/profiles/upsert",
        lambda route: route.fulfill(json={"success": True, "profile": "gemma3_4b", "profile_data": profiles["gemma3_4b"]}),
    )
    page.route(
        "**/registry/installed",
        lambda route: route.fulfill(json={"success": True, "installed": runtime["installed"], "error": None}),
    )

    def status_route(route):
        if runtime["stop_pending"] > 0:
            runtime["stop_pending"] -= 1
            if runtime["stop_pending"] == 0:
                runtime["running"] = []
        route.fulfill(
            json={
                "success": True,
                "ollama": {"reachable": True, "running": runtime["running"], "error": None},
                "served_models": [item["name"] for item in runtime["running"]],
                "hardware": {"gpu_available": False, "gpus": [], "system": {}},
            }
        )

    def serve_route(route):
        runtime["running"] = [
            {
                "name": "gemma3:4b",
                "size": 3_300_000_000,
                "size_vram": 3_100_000_000,
                "placement": "GPU",
                "gpu_percent": 100,
                "expires_at": "2099-01-01T00:00:00Z",
            }
        ]
        route.fulfill(json={"success": True, "served": "gemma3:4b", "message": "Loaded for 60m."})

    def delete_route(route):
        runtime["installed"] = []
        runtime["running"] = []
        route.fulfill(json={"success": True, "deleted": "gemma3:4b"})

    def stop_route(route):
        stopped_models.append(route.request.post_data_json["model"])
        runtime["stop_pending"] = 2
        route.fulfill(
            json={
                "success": True,
                "status": "pending",
                "confirmed": False,
                "stopped": "gemma3:4b",
                "message": "Unload requested.",
            }
        )

    page.route("**/system/status", status_route)
    page.route("**/models/serve", serve_route)
    page.route("**/models/stop", stop_route)
    page.route("**/models/delete", delete_route)
    try:
        page.goto(f"{live_server}/ui", wait_until="domcontentloaded")
        page.get_by_role("tab", name="Chat").click()
        page.wait_for_function("document.querySelector('#chat-model')?.options.length === 1")
        model_select = page.locator("#chat-model")
        assert model_select.input_value() == "gemma3:4b"
        assert "missing.gguf" not in model_select.inner_text()
        assert page.locator("#chat-input").is_disabled()
        assert page.locator("#btn-chat-session").inner_text() == "Load model"

        page.locator("#btn-chat-session").click()
        sync_api.expect(page.locator("#chat-session-state")).to_contain_text("Ready")
        assert page.locator("#chat-input").is_enabled()
        assert page.locator("#btn-chat-session").inner_text() == "Unload"

        page.get_by_role("tab", name="Setup & Deploy").click()
        model_row = page.locator('#installed-body .model-row[data-model="gemma3:4b"]')
        sync_api.expect(model_row.locator(".unload-installed-btn")).to_have_text("■ Unload")
        sync_api.expect(page.locator("#status-body .api-docs-link")).to_have_attribute("href", f"{live_server}/docs")
        assert model_row.locator(".start-installed-btn").count() == 0

        quant = model_row.locator(".quant-label")
        quant.hover()
        tooltip = page.locator("#ui-tooltip")
        sync_api.expect(tooltip).to_be_visible()
        sync_api.expect(tooltip).to_contain_text("4-bit K-quant, medium variant")
        sync_api.expect(quant).to_have_attribute("aria-expanded", "true")
        quant.press("Escape")
        sync_api.expect(tooltip).to_be_hidden()

        model_row.locator(".unload-installed-btn").click()
        sync_api.expect(model_row.locator(".unload-installed-btn")).to_contain_text("Unloading")
        sync_api.expect(model_row.locator(".start-installed-btn")).to_have_text("▶ Deploy")
        assert stopped_models == ["gemma3:4b"]

        page.once("dialog", lambda dialog: dialog.accept())
        model_row.locator(".del-btn").click()
        page.wait_for_function("document.querySelector('#chat-model')?.value === ''")
        page.get_by_role("tab", name="Chat").click()
        sync_api.expect(page.locator("#chat-hint")).to_contain_text("No local models are installed")
        assert page.locator("#chat-input").is_disabled()
    finally:
        page.close()


def test_catalog_keeps_clicked_size_and_renders_json_inspector(live_server, browser):
    page = browser.new_page(viewport={"width": 1365, "height": 900})
    advisor_requests = []

    page.route(
        "**/registry/search-models",
        lambda route: route.fulfill(
            json={
                "success": True,
                "online": True,
                "message": None,
                "sources": {
                    "ollama": {"online": True, "count": 1, "error": None},
                    "huggingface": {"online": True, "count": 0, "error": None},
                },
                "results": [
                    {
                        "source": "ollama",
                        "name": "qwen3.5",
                        "family": "qwen3.5",
                        "provider": "ollama",
                        "publisher": "ollama",
                        "description": "A family with several parameter sizes.",
                        "sizes": ["0.8b", "4b", "122b"],
                        "capabilities": ["chat", "tools"],
                        "pulls": "2.5M",
                        "popularity": 2_500_000,
                        "updated": "today",
                        "pullable": True,
                        "pull_name": "qwen3.5",
                        "url": "https://ollama.com/library/qwen3.5",
                        "variants": [
                            {"label": "0.8b", "params_b": 0.8, "pull_name": "qwen3.5:0.8b"},
                            {"label": "4b", "params_b": 4, "pull_name": "qwen3.5:4b"},
                            {"label": "122b", "params_b": 122, "pull_name": "qwen3.5:122b"},
                        ],
                    }
                ],
            }
        ),
    )
    page.route(
        "**/system/fit-batch",
        lambda route: route.fulfill(
            json={
                "success": True,
                "items": [
                    {"params_b": 0.8, "required_gb": 1.3, "severity": "ok", "tier": "comfortable"},
                    {"params_b": 4, "required_gb": 3.1, "severity": "ok", "tier": "comfortable"},
                    {"params_b": 122, "required_gb": 72, "severity": "hard", "tier": "wont_fit"},
                ],
            }
        ),
    )

    def advisor_route(route):
        advisor_requests.append(route.request.post_data_json)
        route.fulfill(
            json={
                "success": True,
                "recommendation": "Q4_K_M fits comfortably.",
                "free_vram_gb": 8,
                "model": {"family": "qwen3.5", "params_b": 4, "context": 4096},
                "variants": [
                    {
                        "quant": "Q4_K_M",
                        "weights_gb": 2,
                        "required_gb": 3.1,
                        "margin_gb": 4.9,
                        "severity": "ok",
                        "tier": "comfortable",
                        "quality": "good",
                    }
                ],
                "note": "Estimate only.",
                "tags_url": "https://ollama.com/library/qwen3.5/tags",
            }
        )

    page.route("**/system/quant-advisor", advisor_route)
    page.route(
        "**/registry/library-tags",
        lambda route: route.fulfill(json={"success": True, "online": True, "family": "qwen3.5", "tags": []}),
    )
    try:
        page.goto(f"{live_server}/ui", wait_until="domcontentloaded")
        page.locator('.seg-btn[data-seg="hf"]').click()
        sync_api.expect(page.locator("#updates-body tbody tr")).to_have_count(3)
        page.locator("#remote-size-filter").select_option("4to8")
        sync_api.expect(page.locator("#updates-body tbody tr")).to_have_count(1)
        sync_api.expect(page.locator("#updates-body tbody tr")).to_contain_text("4B")
        page.locator("#updates-body .quant-jump-btn").click()
        sync_api.expect(page.locator("#quant-model")).to_have_value("qwen3.5:4b")
        page.wait_for_function("document.querySelector('#quant-body')?.textContent.includes('4B parameters')")
        assert advisor_requests[-1]["params_b"] == 4

        page.evaluate(
            """() => {
              const node = document.createElement('div');
              node.id = 'json-test-node';
              document.body.appendChild(node);
              renderChatText(node, '{"model":"qwen3.5","scores":[1,2,3]}');
            }"""
        )
        sync_api.expect(page.locator("#json-test-node .chat-json")).to_be_visible()
        page.locator("#json-test-node").get_by_role("button", name="Raw").click()
        sync_api.expect(page.locator("#json-test-node .json-raw")).to_contain_text('"scores"')
    finally:
        page.close()
