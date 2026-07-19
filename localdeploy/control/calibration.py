"""Estimated-vs-observed VRAM calibration (Release R2).

Every successful Ollama warm-up (see ``models._serve_ollama``) knows two
numbers for the same deployment: what ``fit.py`` predicted before load, and
what Ollama actually placed on the GPU (``size_vram`` from ``/api/ps``). This
module stores that pair, keyed by the configuration it applies to, and turns
repeated samples into a correction factor future fit checks can apply.

Storage: one JSON file at ``app_home()/calibration.json`` (schema_version 1),
gitignored and machine-local like config.json/history. Writes are atomic
(reuses ``_config.write_config_atomic``) so a crash mid-write can't corrupt it.

Nothing here is silent: ``get_correction`` always reports ``sample_count`` and
``factor`` so a caller can show "calibrated +7.7% from 5 samples on this GPU"
instead of quietly nudging a number.
"""
from __future__ import annotations

import re
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._config import write_config_atomic
from .hardware import detect_hardware

_SCHEMA_VERSION = 1
_MAX_SAMPLES_PER_KEY = 50  # oldest samples drop off; recent hardware/driver state matters most
# Guards the load-mutate-save sequence in record_sample: two concurrent serves
# (e.g. two models warming up close together) would otherwise both read the
# same on-disk state and the second write would silently clobber the first.
_write_lock = threading.Lock()

# Context is bucketed (not stored exact) so "4096 vs 4100" doesn't fragment
# calibration into useless singleton buckets. Buckets mirror fit-table tiers.
_CONTEXT_BUCKETS = [4096, 8192, 16384, 32768, 65536, 131072]


def _context_bucket(context: Optional[int]) -> int:
    context = int(context or 4096)
    for bucket in _CONTEXT_BUCKETS:
        if context <= bucket:
            return bucket
    return _CONTEXT_BUCKETS[-1]


def gpu_key(hw: Optional[Dict[str, Any]] = None) -> str:
    """A stable-ish identifier for 'the GPU pool fit checks are budgeting against'.

    Uses the best compatible pool's vendor+backend+name (not free/total VRAM,
    which changes sample to sample). Falls back to 'cpu' when no GPU was
    detected, so CPU-only calibration still groups sensibly.
    """
    hw = hw or detect_hardware()
    gpus = hw.get("gpus") or []
    if not gpus:
        return "cpu"
    gpu = gpus[0]
    name = re.sub(r"[^a-z0-9]+", "-", str(gpu.get("name") or "gpu").lower()).strip("-")
    return f"{gpu.get('vendor') or '?'}:{gpu.get('backend') or '?'}:{name}"


def sample_key(gpu: str, runtime: str, family: Optional[str], quant: Optional[str], context: Optional[int]) -> str:
    fam = (family or "unknown").lower()
    q = (quant or "default").lower()
    return f"{gpu}|{runtime}|{fam}|{q}|{_context_bucket(context)}"


def _store_path() -> Path:
    from ..utils import app_home

    return app_home() / "calibration.json"


def _load() -> Dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {"schema_version": _SCHEMA_VERSION, "samples": {}}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": _SCHEMA_VERSION, "samples": {}}
    if not isinstance(data, dict) or not isinstance(data.get("samples"), dict):
        return {"schema_version": _SCHEMA_VERSION, "samples": {}}
    return data


def _save(data: Dict[str, Any]) -> Optional[str]:
    data["schema_version"] = _SCHEMA_VERSION
    return write_config_atomic(data, _store_path())


def record_sample(
    *,
    gpu: str,
    runtime: str,
    family: Optional[str],
    quant: Optional[str],
    context: Optional[int],
    estimated_gb: float,
    observed_gb: float,
) -> Dict[str, Any]:
    """Append one (estimated, observed) pair. Best-effort: never raises.

    Returns the sample recorded plus the key it landed under, or an
    ``error`` string when the store couldn't be written.
    """
    key = sample_key(gpu, runtime, family, quant, context)
    try:
        with _write_lock:
            data = _load()
            samples: List[Dict[str, Any]] = data["samples"].setdefault(key, [])
            samples.append(
                {
                    "ts": time.time(),
                    "estimated_gb": round(float(estimated_gb), 3),
                    "observed_gb": round(float(observed_gb), 3),
                }
            )
            del samples[:-_MAX_SAMPLES_PER_KEY]
            err = _save(data)
        if err:
            return {"key": key, "error": err}
        return {"key": key, "sample_count": len(samples)}
    except Exception as exc:  # calibration is a bonus signal, never a hard dependency
        return {"key": key, "error": str(exc)}


def _confidence_for(sample_count: int) -> str:
    if sample_count >= 10:
        return "high"
    if sample_count >= 3:
        return "medium"
    if sample_count >= 1:
        return "low"
    return "none"


def get_correction(
    *, gpu: str, runtime: str, family: Optional[str], quant: Optional[str], context: Optional[int]
) -> Dict[str, Any]:
    """Median observed/estimated ratio for this exact key. No cross-key blending -
    materially different configurations are never averaged together."""
    key = sample_key(gpu, runtime, family, quant, context)
    data = _load()
    samples = data["samples"].get(key) or []
    if not samples:
        return {"key": key, "applied": False, "factor": 1.0, "sample_count": 0, "confidence": "none"}
    ratios = [
        s["observed_gb"] / s["estimated_gb"]
        for s in samples
        if isinstance(s.get("estimated_gb"), (int, float)) and s.get("estimated_gb", 0) > 0
    ]
    if not ratios:
        return {"key": key, "applied": False, "factor": 1.0, "sample_count": 0, "confidence": "none"}
    factor = statistics.median(ratios)
    return {
        "key": key,
        "applied": True,
        "factor": round(factor, 4),
        "sample_count": len(ratios),
        "confidence": _confidence_for(len(ratios)),
        "last_sample_estimated_gb": samples[-1]["estimated_gb"],
        "last_sample_observed_gb": samples[-1]["observed_gb"],
    }


def stats() -> Dict[str, Any]:
    """Overall calibration coverage: how many keys have data, and the mean
    absolute prediction error across every recorded sample (the estimator
    self-grading metric)."""
    data = _load()
    all_samples: List[Dict[str, Any]] = []
    for samples in data["samples"].values():
        all_samples.extend(samples)
    if not all_samples:
        return {"keys": 0, "samples": 0, "mean_abs_error_pct": None}
    errors_pct = [
        abs(s["observed_gb"] - s["estimated_gb"]) / s["estimated_gb"] * 100
        for s in all_samples
        if s.get("estimated_gb")
    ]
    return {
        "keys": len(data["samples"]),
        "samples": len(all_samples),
        "mean_abs_error_pct": round(statistics.mean(errors_pct), 2) if errors_pct else None,
    }
