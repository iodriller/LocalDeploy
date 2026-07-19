"""Community benchmark sharing - local-only groundwork (Release R8).

LocalDeploy's whole premise is "no telemetry" (see README Privacy & Security).
A real community dataset needs a hosted server, a moderation/abuse story, and
a data-retention policy - an infrastructure and product decision this repo
has not made, not something to wire up silently inside a feature PR. Both
endpoints below are honest about that: they compute and preview exactly what
*would* be shared, and "export" saves that anonymized snapshot to a local
file - there is no submission call anywhere in this module, because there is
nowhere to submit to yet.

POST /system/community/preview  -> the anonymized payload, never persisted
POST /system/community/export   -> the same payload, saved to
                                    reports/community-contributions/ locally
"""
from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from .. import __version__

router = APIRouter()

SCHEMA_VERSION = 1

# Fields explicitly never copied into the shared payload, no matter what the
# input card contains - listed here (not just in a docstring) so it doubles
# as documentation of the promise and is easy to audit.
EXCLUDED_FIELDS = [
    "username", "computer name", "IP address",
    "model prompts", "model responses", "response previews",
    "filesystem paths", "API keys", "local profile name",
]

_TEST_NUMERIC_FIELDS = ("name", "category", "success", "accuracy", "elapsed_seconds", "approx_tokens_per_second")


def _os_category() -> str:
    return {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(platform.system(), platform.system())


def _round_or_none(value: Any, digits: int = 1) -> Optional[float]:
    try:
        return round(float(value), digits) if value is not None else None
    except (TypeError, ValueError):
        return None


def _anonymize_test(test: Dict[str, Any]) -> Dict[str, Any]:
    """Numeric result fields only - never response_preview or error text,
    which can contain fragments of the user's actual prompts/outputs."""
    out = {k: test.get(k) for k in _TEST_NUMERIC_FIELDS}
    metrics = test.get("metrics") or {}
    numeric_metrics = {
        k: metrics.get(k)
        for k in ("ttft_ms", "tokens_per_second", "prompt_tokens_per_second")
        if metrics.get(k) is not None
    }
    if numeric_metrics:
        out["metrics"] = numeric_metrics
    return out


def anonymize_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """Whitelist-copy a report-card-shaped payload (report.py's build_card
    output, or an equivalent run record) down to what the community-sharing
    plan defines as safe to share. Everything not explicitly copied here is
    dropped - this is a whitelist, not a blocklist, by design."""
    hardware = card.get("hardware") or {}
    provenance = card.get("provenance") or {}
    prov_hardware = provenance.get("hardware") or {}
    gpus = prov_hardware.get("gpus") or hardware.get("gpus") or []
    best_gpu = gpus[0] if gpus else {}
    system = prov_hardware.get("system") or {}

    profile_name = card.get("profile")
    profile_provenance = (provenance.get("profiles") or {}).get(profile_name, {}) if profile_name else {}

    summary = card.get("summary") or {}
    tests = [_anonymize_test(t) for t in (card.get("tests") or [])]

    return {
        "schema_version": SCHEMA_VERSION,
        "hardware": {
            "gpu": best_gpu.get("name") or hardware.get("gpu"),
            "vram_gb": _round_or_none((best_gpu.get("vram_total_mb") or hardware.get("vram_total_mb") or 0) / 1024.0) or None,
            "cpu": system.get("cpu_model"),
            "ram_gb": _round_or_none((system.get("ram_total_mb") or 0) / 1024.0) or None,
            "os_category": _os_category(),
        },
        "runtime": {
            "provider": profile_provenance.get("backend") or "ollama",
            "version": profile_provenance.get("backend_version"),
        },
        "model": {
            "id": card.get("model_id"),
            "digest": profile_provenance.get("model_digest"),
            "quantization": profile_provenance.get("quant"),
            "context": profile_provenance.get("context"),
        },
        "device": card.get("device"),
        "performance": {
            "avg_accuracy": summary.get("avg_accuracy"),
            "avg_latency_s": summary.get("avg_latency_s"),
            "avg_tokens_per_second": summary.get("avg_tokens_per_second"),
            "avg_ttft_ms": summary.get("avg_ttft_ms"),
            "peak_vram_mb": card.get("peak_vram_mb"),
        },
        "tests": tests,
        "repetitions": card.get("repetitions") or 1,
        "localdeploy_version": __version__,
    }


class CommunityShareRequest(BaseModel):
    card: Dict[str, Any]


@router.post("/system/community/preview")
def community_preview(req: CommunityShareRequest) -> Dict[str, Any]:
    return {
        "success": True,
        "would_share": anonymize_card(req.card),
        "excluded_fields": EXCLUDED_FIELDS,
        "note": (
            "Preview only - nothing is sent anywhere. LocalDeploy does not have a "
            "community server yet, so there is currently no submit action; "
            "'Save locally' keeps this anonymized snapshot on disk for later."
        ),
    }


def _contributions_dir() -> Path:
    from ..utils import app_home

    return app_home() / "reports" / "community-contributions"


@router.post("/system/community/export")
def community_export(req: CommunityShareRequest) -> Dict[str, Any]:
    payload = anonymize_card(req.card)
    try:
        directory = _contributions_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"contribution-{int(time.time() * 1000)}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return {"success": False, "error": f"Could not save locally: {exc}"}
    return {
        "success": True,
        "path": str(path),
        "would_share": payload,
        "note": "Saved locally only - not transmitted anywhere. LocalDeploy has no community server yet.",
    }
