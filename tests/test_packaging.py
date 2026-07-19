"""Packaging guards: app-home resolution, CLI entry point, version consistency."""
from __future__ import annotations

from pathlib import Path

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
        "CHANGELOG.md has no section for localdeploy.__version__ — "
        "bump both together."
    )


def test_docker_persists_localdeploy_runtime_state():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "LOCALDEPLOY_HOME=/data/localdeploy" in dockerfile
    assert "localdeploy-data:/data/localdeploy" in compose
    assert "localdeploy-data:" in compose
