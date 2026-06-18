"""Step 2 - hardware probe.

GET /system/hardware reports the NVIDIA GPU(s) + VRAM, plus CPU model, core
counts, and system RAM. It shells out to ``nvidia-smi`` for the GPU and uses
``psutil`` for CPU/RAM when available, degrading gracefully (never raises) when
neither is present.
"""
from __future__ import annotations

import os
import platform
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


def _cpu_model() -> Optional[str]:
    """Best-effort CPU model name across platforms; None if undeterminable."""
    # Linux: /proc/cpuinfo carries a human-readable "model name".
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip() or None
    except OSError:
        pass
    # macOS / Windows: platform.processor() is usually populated there.
    proc = (platform.processor() or "").strip()
    if proc:
        return proc
    machine = (platform.machine() or "").strip()
    return machine or None


def _cpu_and_memory() -> Dict[str, Any]:
    """CPU core counts + RAM. Uses psutil when present, else stdlib fallbacks."""
    info: Dict[str, Any] = {
        "cpu_model": _cpu_model(),
        "logical_cores": os.cpu_count(),
        "physical_cores": None,
        "ram_total_mb": None,
        "ram_available_mb": None,
    }
    try:
        import psutil  # optional dependency

        info["physical_cores"] = psutil.cpu_count(logical=False)
        if info["logical_cores"] is None:
            info["logical_cores"] = psutil.cpu_count(logical=True)
        vm = psutil.virtual_memory()
        info["ram_total_mb"] = int(vm.total / (1024 * 1024))
        info["ram_available_mb"] = int(vm.available / (1024 * 1024))
    except Exception:
        # psutil absent or probe failed — keep stdlib-derived values, RAM stays None.
        pass
    return info


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
    """Detect GPU + CPU + RAM. Always succeeds; never raises."""
    gpus = _query_nvidia_smi()
    system = _cpu_and_memory()
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
