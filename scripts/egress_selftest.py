#!/usr/bin/env python3
"""Verifiable offline self-test.

Runs the typical UI/control-plane actions with OFFLINE=true while a socket guard
blocks every connection to a non-loopback address. If the app tries to phone
home, the guard records it and the test fails. LocalDeploy has no telemetry, so
this should always pass.

Usage:  python scripts/egress_selftest.py
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["OFFLINE"] = "true"
os.environ.setdefault("CONFIG_PATH", str(ROOT / "config.example.json"))

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}
_blocked: list = []
_real_connect = socket.socket.connect


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if str(host) not in _LOOPBACK:
        _blocked.append(address)
        raise OSError(f"egress blocked to {address} (offline self-test)")
    return _real_connect(self, address)


socket.socket.connect = _guarded_connect  # type: ignore[assignment]


def main() -> int:
    # Import after the guard is installed.
    from localdeploy.control import fit, hardware, registry
    from localdeploy.control.fit import FitRequest
    from localdeploy.control.registry import CheckUpdatesRequest

    # Local-only actions: must not touch the internet.
    hardware.detect_hardware()
    fit.fit_check(FitRequest(profile="gemma3_4b_ollama_safe", free_vram_mb=8192))

    # The one internet-egress action; in offline mode it must be skipped (no socket).
    result = registry.check_updates(CheckUpdatesRequest(queries=["gemma"]))

    online = result.get("online")
    print(f"check-updates online flag: {online}")
    print(f"non-loopback egress attempts: {_blocked or 'none'}")

    ok = (not _blocked) and (online is False)
    print("OFFLINE_SELFTEST_PASS" if ok else "OFFLINE_SELFTEST_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
