"""Tests for the startup LAN-exposure guard (check_lan_exposure in api_server.py).

The guard runs at module import time, so it can't be exercised by importing
api_server directly in-process (it already ran once, for this test process's
own loopback default). Instead each case launches a fresh subprocess so the
module-level check runs under controlled env vars.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROBE = (
    "import os\n"
    "os.environ.setdefault('CONFIG_PATH', 'config.example.json')\n"
    "os.environ.setdefault('DEFAULT_MODEL_PROFILE', 'gemma3_4b_ollama_safe')\n"
    "import api_server\n"
    "print('IMPORT_OK')\n"
)


def _run(env_overrides: dict) -> subprocess.CompletedProcess:
    import os as _os

    env = dict(_os.environ)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_loopback_host_no_warning():
    result = _run({"API_HOST": "127.0.0.1", "API_TOKEN": "", "REQUIRE_TOKEN_ON_LAN": ""})
    assert result.returncode == 0
    assert "IMPORT_OK" in result.stdout
    assert "WARNING" not in result.stderr


def test_non_loopback_host_without_token_warns_but_starts():
    result = _run({"API_HOST": "0.0.0.0", "API_TOKEN": "", "REQUIRE_TOKEN_ON_LAN": ""})
    assert result.returncode == 0
    assert "IMPORT_OK" in result.stdout
    assert "WARNING" in result.stderr
    assert "API_TOKEN" in result.stderr


def test_non_loopback_host_with_token_stays_quiet():
    result = _run({"API_HOST": "0.0.0.0", "API_TOKEN": "secret", "REQUIRE_TOKEN_ON_LAN": ""})
    assert result.returncode == 0
    assert "IMPORT_OK" in result.stdout
    assert "WARNING" not in result.stderr


def test_require_token_on_lan_hard_fails_without_token():
    result = _run({"API_HOST": "0.0.0.0", "API_TOKEN": "", "REQUIRE_TOKEN_ON_LAN": "true"})
    assert result.returncode != 0
    assert "IMPORT_OK" not in result.stdout
    assert "Refusing to start" in result.stderr


def test_require_token_on_lan_allows_with_token():
    result = _run({"API_HOST": "0.0.0.0", "API_TOKEN": "secret", "REQUIRE_TOKEN_ON_LAN": "true"})
    assert result.returncode == 0
    assert "IMPORT_OK" in result.stdout
