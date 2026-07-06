#!/usr/bin/env python3
"""Capture deterministic UI screenshots for docs/SCREENSHOTS.md and the README.

Launches the real app in-process (same pattern as tests/test_ui_playwright.py),
seeds browser localStorage with a few synthetic completed benchmark runs so
the UI renders populated rather than empty, and screenshots each tab. No
Ollama, GPU, or network access is required.

Usage:
    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python scripts/capture_screenshots.py
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "docs" / "screenshots"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed_runs() -> str:
    profiles = [
        ("gemma3_4b_ollama_safe", "gemma3:4b", 0.93, 1.8, 42.0),
        ("qwen3vl_8b_ollama", "qwen3-vl:8b-instruct", 0.97, 3.1, 27.0),
        ("llama32_3b_ollama", "llama3.2:3b", 0.88, 1.1, 55.0),
    ]
    runs = []
    for i, (profile, model, acc, latency, tps) in enumerate(profiles):
        runs.append(
            {
                "id": f"run-{i}",
                "createdAt": "2026-07-05T00:00:00.000Z",
                "profile": profile,
                "modelId": model,
                "source": "restored-history",
                "tests": [
                    {
                        "name": "reasoning_1",
                        "category": "reasoning",
                        "success": True,
                        "accuracy": acc,
                        "elapsed_seconds": latency,
                        "approx_tokens_per_second": tps,
                    }
                ],
                "summary": {
                    "tests": 1,
                    "passed": 1,
                    "avg_accuracy": acc,
                    "avg_latency_s": latency,
                    "avg_tokens_per_second": tps,
                },
            }
        )
    return json.dumps(runs)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run: pip install -r requirements-dev.txt")
        return 1

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
        print("uvicorn did not start within 20s")
        return 1

    base = f"http://127.0.0.1:{port}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                print(f"chromium not available: run 'python -m playwright install chromium' ({exc})")
                return 1
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 960})
                page.add_init_script(
                    f'window.localStorage.setItem("localdeploy.benchmarkRuns.v1", {json.dumps(_seed_runs())});'
                )
                page.goto(f"{base}/ui", wait_until="networkidle")
                page.screenshot(path=str(OUT_DIR / "setup-deploy.png"))
                print(f"wrote {OUT_DIR / 'setup-deploy.png'}")

                page.get_by_role("tab", name="Benchmark & Compare").click()
                page.wait_for_timeout(300)
                page.screenshot(path=str(OUT_DIR / "benchmark-compare.png"))
                print(f"wrote {OUT_DIR / 'benchmark-compare.png'}")
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
