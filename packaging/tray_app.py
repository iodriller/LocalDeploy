"""LocalDeploy desktop tray launcher (Release R7, Phase A packaging).

Runs the existing FastAPI backend (``localdeploy.server:app``) in a
background thread inside this same process — no second executable to spawn
or supervise — and exposes the tray menu actions from the roadmap: Open,
Start/Stop/Restart service, View logs, Open model storage, Open reports,
Check for updates, Quit.

This is the process PyInstaller bundles (see ``localdeploy.spec``); it is not
imported by the web app or covered by the pytest suite — pystray owns a
native OS event loop that can't run headless in CI. Verify manually:

    pip install -e ".[packaging]"
    python packaging/tray_app.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import pystray
import uvicorn
from PIL import Image, ImageDraw

from localdeploy import __version__
from localdeploy.utils import api_client_base_url, app_home

_server: "uvicorn.Server | None" = None
_server_thread: threading.Thread | None = None
_lock = threading.Lock()


def _icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill="#0f1115")
    draw.ellipse((14, 14, 50, 50), fill="#4f86f7")
    return img


def _is_running() -> bool:
    return bool(_server_thread and _server_thread.is_alive())


def _start_server(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
    global _server, _server_thread
    with _lock:
        if _is_running():
            return
        host = os.getenv("API_HOST", "127.0.0.1")
        port = int(os.getenv("API_PORT", "8000"))
        from localdeploy.server import app  # imported lazily so --version stays fast

        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        _server = uvicorn.Server(config)
        _server_thread = threading.Thread(target=_server.run, daemon=True)
        _server_thread.start()
    if icon:
        icon.notify("LocalDeploy service started.")


def _stop_server(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
    global _server
    with _lock:
        if _server:
            _server.should_exit = True
    if icon:
        icon.notify("LocalDeploy service stopped.")


def _restart_server(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
    _stop_server()
    threading.Timer(1.0, lambda: _start_server(icon)).start()


def _open_ui(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
    webbrowser.open(f"{api_client_base_url()}/ui")


def _open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - user-initiated, local path only
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _open_logs(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
    _open_path(app_home() / "logs")


def _open_model_storage(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
    # Ollama owns the actual model store; LocalDeploy never relocates it.
    _open_path(Path(os.getenv("OLLAMA_MODELS") or (Path.home() / ".ollama" / "models")))


def _open_reports(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
    _open_path(app_home() / "reports")


def _check_updates(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
    import requests

    try:
        resp = requests.get(f"{api_client_base_url()}/system/update-check", timeout=8)
        data = resp.json()
    except Exception as exc:
        if icon:
            icon.notify(f"Update check failed: {exc}")
        return
    if not icon:
        return
    if not data.get("checked"):
        icon.notify(data.get("message") or "Update check unavailable.")
    elif data.get("update_available"):
        icon.notify(f"LocalDeploy {data['latest_version']} is available.")
        if data.get("url"):
            webbrowser.open(data["url"])
    else:
        icon.notify("LocalDeploy is up to date.")


def _quit(icon: pystray.Icon, item: pystray.MenuItem | None = None) -> None:
    _stop_server()
    icon.stop()


def build_menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem("Open LocalDeploy", _open_ui, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start service", _start_server, enabled=lambda item: not _is_running()),
        pystray.MenuItem("Stop service", _stop_server, enabled=lambda item: _is_running()),
        pystray.MenuItem("Restart service", _restart_server),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("View logs", _open_logs),
        pystray.MenuItem("Open model storage", _open_model_storage),
        pystray.MenuItem("Open reports", _open_reports),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Check for updates", _check_updates),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit),
    )


def main() -> None:
    _start_server()
    icon = pystray.Icon("localdeploy", _icon_image(), f"LocalDeploy {__version__}", build_menu())
    threading.Timer(1.5, lambda: _open_ui(icon)).start()
    icon.run()


if __name__ == "__main__":
    main()
