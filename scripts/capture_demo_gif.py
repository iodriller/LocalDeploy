#!/usr/bin/env python3
"""Record the animated README demo (docs/assets/demo.gif).

Same in-process app pattern as capture_screenshots.py: launches the real
server, seeds synthetic benchmark history so the dashboard renders populated,
then drives a short scripted tour with a visible fake cursor while Playwright
records video. The webm is converted to a GIF with ffmpeg (the copy bundled
with Playwright is found automatically, so no separate install is needed).

The tour degrades gracefully without Ollama — cards it can't populate are
simply skipped — but looks best on a machine with a GPU and a few pulled
models.

Usage:
    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python scripts/capture_demo_gif.py
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from capture_screenshots import _best_chat_profile, _seed_runs  # noqa: E402  (shared helpers)

OUT_PATH = ROOT / "docs" / "assets" / "demo.gif"
VIEWPORT = {"width": 1280, "height": 800}

# A soft blue dot that follows the mouse, so clicks are visible in the GIF.
CURSOR_SCRIPT = """
window.addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;left:-40px;top:-40px;width:22px;height:22px;' +
    'border-radius:50%;background:rgba(79,134,247,.35);border:2.5px solid #4f86f7;' +
    'box-shadow:0 2px 10px rgba(0,0,0,.35);pointer-events:none;z-index:2147483647;' +
    'transform:translate(-50%,-50%);transition:width .12s,height .12s';
  document.body.appendChild(c);
  document.addEventListener('mousemove', (e) => {
    c.style.left = e.clientX + 'px';
    c.style.top = e.clientY + 'px';
  }, true);
  document.addEventListener('mousedown', () => { c.style.width = '15px'; c.style.height = '15px'; }, true);
  document.addEventListener('mouseup', () => { c.style.width = '22px'; c.style.height = '22px'; }, true);
});
"""


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _find_ffmpeg() -> str | None:
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        base = Path.home() / ".cache" / "ms-playwright"
    for hit in sorted(base.glob("ffmpeg-*/ffmpeg*")):
        if hit.is_file() and not hit.name.endswith(".txt"):
            return str(hit)
    return None


def _glide(page, selector: str, pause_ms: int = 350) -> bool:
    """Move the fake cursor smoothly onto `selector`. Returns False if absent."""
    el = page.locator(selector).first
    try:
        el.scroll_into_view_if_needed(timeout=2000)
        box = el.bounding_box()
    except Exception:
        return False
    if not box:
        return False
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=28)
    page.wait_for_timeout(pause_ms)
    return True


def _jump_to(page, selector: str, pause_ms: int = 1400) -> None:
    # Instant jump, not smooth scroll: in a GIF a scroll animation makes every
    # frame differ, which triples the file size for no narrative benefit.
    page.evaluate(
        "(sel) => { const el = document.querySelector(sel); if (el) el.scrollIntoView({behavior: 'instant', block: 'start'}); }",
        selector,
    )
    page.wait_for_timeout(pause_ms)


def _frames_to_gif(frames_dir: Path, out_path: Path) -> Path:
    """Assemble PNG frames into a looping GIF with one shared adaptive palette."""
    from PIL import Image

    files = sorted(frames_dir.glob("f*.png"))
    if not files:
        raise RuntimeError("no frames extracted")
    frames = [Image.open(f).convert("RGB") for f in files]
    # Build the palette from a spread of sample frames so every scene is covered.
    step = max(1, len(frames) // 6)
    samples = frames[::step][:6]
    strip = Image.new("RGB", (frames[0].width, frames[0].height * len(samples)))
    for i, f in enumerate(samples):
        strip.paste(f, (0, i * f.height))
    palette = strip.quantize(colors=255, method=Image.MEDIANCUT)
    quantized = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    quantized[0].save(
        out_path,
        save_all=True,
        append_images=quantized[1:],
        duration=111,  # ms per frame at fps=9
        loop=0,
        optimize=True,
    )
    return out_path


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run: pip install -r requirements-dev.txt")
        return 1

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print("ffmpeg not found (neither on PATH nor in the Playwright cache).")
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
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    video_path: Path | None = None
    try:
        with sync_playwright() as p, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport=VIEWPORT,
                record_video_dir=tmp,
                record_video_size=VIEWPORT,
            )
            page = context.new_page()
            page.add_init_script(
                f'window.localStorage.setItem("localdeploy.benchmarkRuns.v1", {_seed_runs()!r});'
                'window.localStorage.setItem("localdeploy_theme", "dark");'
            )
            page.add_init_script(CURSOR_SCRIPT)

            # --- the tour -----------------------------------------------------
            page.goto(f"{base}/ui", wait_until="networkidle")
            page.mouse.move(640, 220, steps=10)
            page.wait_for_timeout(1600)  # hardware + installed models fill in

            # 1. Curated picks for this machine's hardware.
            if _glide(page, "#btn-starter-pack"):
                page.locator("#btn-starter-pack").click()
                try:
                    page.wait_for_selector("#starter-pack-body .fit-card", timeout=8000)
                except Exception:
                    pass
                page.wait_for_timeout(1800)

            # 2. The installed-models list with live fit badges.
            _jump_to(page, "#your-models-card", pause_ms=1900)

            # 3. Chat playground: type a prompt and stream a real reply.
            #    Skipped cleanly when no model can answer (no Ollama).
            if _glide(page, '.tab[data-tab="chat"]'):
                page.locator('.tab[data-tab="chat"]').click()
                page.wait_for_timeout(900)
                chat_model = _best_chat_profile(base)
                if chat_model:
                    page.select_option("#chat-model", chat_model)
                    page.click("#btn-chat-session")
                    try:
                        page.wait_for_selector(".chat-session-state.ready", timeout=120000)
                    except Exception:
                        chat_model = None
                if not chat_model:
                    raise RuntimeError  # handled below: skip the chat scene cleanly
                page.locator("#chat-input").click()
                page.keyboard.type("In one short sentence: why run AI models locally?", delay=16)
                page.wait_for_timeout(300)
                if _glide(page, "#btn-chat-send", pause_ms=200):
                    page.locator("#btn-chat-send").click()
                    try:
                        page.wait_for_selector(
                            ".chat-row.assistant .chat-bubble-meta:not(:empty)", timeout=45000
                        )
                        page.wait_for_timeout(1500)
                    except Exception:
                        pass  # no backend — move on, the tour still works

            # 4. Benchmark workspace: leaderboard, scatter, heatmap.
            if _glide(page, '.tab[data-tab="bench"]'):
                page.locator('.tab[data-tab="bench"]').click()
                page.wait_for_timeout(1300)
            _jump_to(page, "#results-dashboard-card", pause_ms=2400)
            _glide(page, "#heatmap-body", pause_ms=1500)

            # 5. End back on the setup tab so the GIF loops naturally.
            _jump_to(page, "body", pause_ms=400)
            if _glide(page, '.tab[data-tab="serve"]'):
                page.locator('.tab[data-tab="serve"]').click()
                page.wait_for_timeout(1400)

            video = page.video
            context.close()  # flushes the recording
            browser.close()
            raw = Path(video.path())

            # --- webm -> GIF ---------------------------------------------------
            # Playwright's bundled ffmpeg is a minimal build without a GIF muxer,
            # so decode to PNG frames (which it does support) and assemble the
            # GIF with Pillow using one shared palette (no per-frame flicker).
            frames_dir = Path(tmp) / "frames"
            frames_dir.mkdir()
            result = subprocess.run(
                [
                    # -r (not the fps filter): Playwright's minimal ffmpeg build
                    # only ships the pad/crop/scale filters.
                    ffmpeg, "-y", "-i", str(raw),
                    "-vf", "scale=900:-2",
                    "-r", "9",
                    str(frames_dir / "f%04d.png"),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"ffmpeg frame extraction failed:\n{result.stderr[-2000:]}")
                return 1
            video_path = _frames_to_gif(frames_dir, OUT_PATH)
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    if video_path:
        size_mb = video_path.stat().st_size / 1e6
        print(f"wrote {video_path} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
