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
        assert page.get_by_role("heading", name="Hardware").is_visible()
        # Switch to tab 2 and confirm the Run Builder renders.
        page.get_by_role("tab", name="Benchmark & Compare").click()
        assert page.get_by_role("heading", name="Run Builder").is_visible()
        assert page.get_by_role("heading", name="Run queue").is_visible()
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
