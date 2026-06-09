"""Step 2 - hardware probe.

GET /system/hardware reports the NVIDIA GPU(s), VRAM totals, and basic CPU info.
It shells out to ``nvidia-smi`` (no extra Python dependency) and degrades
gracefully to a CPU-only payload when no GPU/driver is present.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

router = APIRouter()

_NVIDIA_SMI_FIELDS = "name,memory.total,memory.free,memory.used,driver_version"


def _to_int(value: str) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _query_nvidia_smi() -> Optional[List[Dict[str, Any]]]:
    """Return a list of GPU dicts, or None when nvidia-smi is unavailable/fails."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        completed = subprocess.run(
            [exe, f"--query-gpu={_NVIDIA_SMI_FIELDS}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None

    gpus: List[Dict[str, Any]] = []
    for line in completed.stdout.strip().splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue
        name, total, free, used, driver = parts[:5]
        gpus.append(
            {
                "name": name or None,
                "vram_total_mb": _to_int(total),
                "vram_free_mb": _to_int(free),
                "vram_used_mb": _to_int(used),
                "driver_version": driver or None,
            }
        )
    return gpus or None


def detect_hardware() -> Dict[str, Any]:
    """Detect GPU + basic CPU info. Always succeeds; never raises."""
    gpus = _query_nvidia_smi()
    system = {"logical_cores": os.cpu_count()}
    if not gpus:
        return {
            "success": True,
            "gpu_available": False,
            "gpus": [],
            "system": system,
            "message": (
                "No NVIDIA GPU detected (nvidia-smi unavailable). "
                "CPU-only inference works but is much slower."
            ),
        }
    return {
        "success": True,
        "gpu_available": True,
        "gpus": gpus,
        "system": system,
        "message": None,
    }


@router.get("/system/hardware")
def system_hardware() -> Dict[str, Any]:
    return detect_hardware()
