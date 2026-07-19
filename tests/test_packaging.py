"""Packaging guards: app-home resolution, CLI entry point, version consistency."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import localdeploy
from localdeploy import cli
from localdeploy.utils import app_home, web_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_app_home_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALDEPLOY_HOME", str(tmp_path / "custom"))
    assert app_home() == tmp_path / "custom"


def test_app_home_in_source_checkout_is_repo_root(monkeypatch):
    monkeypatch.delenv("LOCALDEPLOY_HOME", raising=False)
    # This test runs from the checkout, where config.example.json exists.
    assert app_home() == PROJECT_ROOT


def test_web_dir_ships_all_ui_assets():
    for asset in ("index.html", "styles.css", "favicon.png", "logo.svg"):
        assert (web_dir() / asset).is_file(), asset
    expected_modules = {
        "app.js", "shared.js", "system.js", "models.js", "chat.js",
        "benchmark.js", "benchmark-views.js",
    }
    assert {path.name for path in (web_dir() / "js").glob("*.js")} == expected_modules


def test_cli_version_flag_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert localdeploy.__version__ in capsys.readouterr().out


def test_changelog_covers_current_version():
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {localdeploy.__version__}" in changelog, (
        "CHANGELOG.md has no section for localdeploy.__version__ - "
        "bump both together."
    )


def test_docker_persists_localdeploy_runtime_state():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "LOCALDEPLOY_HOME=/data/localdeploy" in dockerfile
    assert "localdeploy-data:/data/localdeploy" in compose
    assert "localdeploy-data:" in compose


def test_bundled_ollama_cloud_models_are_disabled():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    windows_launcher = (PROJECT_ROOT / "scripts" / "start.ps1").read_text(encoding="utf-8")

    for content in (dockerfile, compose, env_example, windows_launcher):
        assert "OLLAMA_NO_CLOUD" in content
    assert "OLLAMA_NO_CLOUD=true" in dockerfile
    assert "OLLAMA_NO_CLOUD=true" in compose
    assert "OLLAMA_NO_CLOUD=true" in env_example


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="Unix launcher test")
def test_unix_launcher_reads_addresses_from_dotenv(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "start.sh", scripts_dir / "start.sh")

    requirements = tmp_path / "requirements.txt"
    requirements.write_text("", encoding="utf-8")
    marker_dir = tmp_path / ".venv"
    (marker_dir / "bin").mkdir(parents=True)
    (marker_dir / "bin" / "activate").write_text("", encoding="utf-8")
    (marker_dir / "requirements.sha256").write_text(
        hashlib.sha256(b"").hexdigest() + "\n", encoding="utf-8"
    )
    (tmp_path / ".env").write_text(
        "API_HOST=0.0.0.0\n"
        "API_PORT=8123\n"
        "OLLAMA_BASE_URL=http://127.0.0.1:19999/\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    curl_log = tmp_path / "curl-args.txt"
    uvicorn_log = tmp_path / "uvicorn-args.txt"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(f'#!/usr/bin/env bash\nprintf "%s" "$*" > "{curl_log}"\n', encoding="utf-8")
    fake_uvicorn = fake_bin / "uvicorn"
    fake_uvicorn.write_text(
        f'#!/usr/bin/env bash\nprintf "%s" "$*" > "{uvicorn_log}"\n', encoding="utf-8"
    )
    fake_curl.chmod(0o755)
    fake_uvicorn.chmod(0o755)

    env = os.environ.copy()
    for name in ("API_HOST", "API_PORT", "OLLAMA_BASE_URL"):
        env.pop(name, None)
    env.update(
        {
            "NO_BROWSER": "1",
            "PYTHON": shutil.which("python3") or shutil.which("python") or "python3",
            "PATH": f"{fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
        }
    )
    result = subprocess.run(
        ["bash", str(scripts_dir / "start.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    assert "LocalDeploy UI:  http://127.0.0.1:8123/ui" in result.stdout
    assert curl_log.read_text(encoding="utf-8").endswith("http://127.0.0.1:19999/api/tags")
    assert uvicorn_log.read_text(encoding="utf-8") == "api_server:app --host 0.0.0.0 --port 8123"
