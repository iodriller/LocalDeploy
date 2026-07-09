"""Step 4 - model registry.

GET  /registry/installed      -> models pulled locally (Ollama /api/tags)
POST /registry/check-updates  -> newest matching models on Hugging Face

The Hugging Face call is a generic search (newest-first) rather than a
per-model lookup table, so it stays a single mechanism instead of a chain of
special cases. Network/dependency failures degrade to a clear "offline" result.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter
from pydantic import BaseModel

from ..utils import offline_mode
from . import _ollama
from .fit import FitRequest, fit_check

router = APIRouter()


@router.get("/registry/installed")
def registry_installed() -> Dict[str, Any]:
    models, error = _ollama.list_installed()
    return {"success": error is None, "installed": models, "error": error}


class CheckUpdatesRequest(BaseModel):
    queries: Optional[List[str]] = None
    limit: int = 5
    gguf_only: bool = True
    free_vram_mb: Optional[int] = None
    fit_filter: str = "all"  # all | gpu | runnable


def _base_name(name: str) -> str:
    return (name or "").split(":")[0].split("/")[-1]


def _norm(text: str) -> str:
    """Alphanumeric-only lowercase, so 'gemma3' matches 'google/gemma-3-4b-it'."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _derive_queries(installed: List[Dict[str, Any]]) -> List[str]:
    """Turn installed model names into HF search terms (e.g. gemma3:4b -> gemma)."""
    queries: List[str] = []
    for model in installed:
        base = _base_name(model.get("name") or "")
        term = re.sub(r"\d+$", "", base) or base
        if term and term not in queries:
            queries.append(term)
    return queries


def _size_token(text: str) -> Optional[str]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*b\b", (text or "").lower())
    if not match:
        return None
    value = match.group(1).rstrip("0").rstrip(".")
    return f"{value}b"


def _installed_signature(name: str) -> Dict[str, Optional[str]]:
    base = _base_name(name)
    return {"family": _norm(base), "size": _norm(_size_token(name) or "") or None}


def _candidate_matches_installed(candidate_id: str, signatures: List[Dict[str, Optional[str]]]) -> bool:
    hid = _norm(candidate_id)
    for sig in signatures:
        family = sig.get("family")
        if not family or family not in hid:
            continue
        size = sig.get("size")
        if size and size not in hid:
            continue
        return True
    return False


def _list_hf(
    query: str, limit: int, gguf_only: bool = True
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Newest HF models matching `query`. Lazy import; returns (items, error).

    When `gguf_only` is true, the search is filtered to GGUF repos, which Ollama
    can pull directly via `ollama pull hf.co/<id>` — so each candidate carries a
    ready-to-use `pull_name`.
    """
    if offline_mode():
        return None, "offline mode (OFFLINE=true): Hugging Face check skipped — no egress"
    try:
        from huggingface_hub import HfApi
    except Exception as exc:  # pragma: no cover - dependency missing
        return None, f"huggingface_hub unavailable: {exc}"
    try:
        api = HfApi()
        # sort="lastModified" returns most-recent-first; `direction` was removed in
        # huggingface_hub 1.x, so we rely on the default ordering.
        kwargs: Dict[str, Any] = {"search": query, "sort": "lastModified", "limit": limit}
        if gguf_only:
            kwargs["filter"] = "gguf"
        models = api.list_models(**kwargs)
        items: List[Dict[str, Any]] = []
        for model in models:
            mid = getattr(model, "id", None) or getattr(model, "modelId", None)
            downloads = getattr(model, "downloads", None)
            likes = getattr(model, "likes", None)
            if mid and (downloads in (None, 0) or likes in (None, 0)):
                try:
                    info = api.model_info(mid)
                    downloads = getattr(info, "downloads", downloads)
                    likes = getattr(info, "likes", likes)
                except Exception:
                    pass
            items.append(
                {
                    "id": mid,
                    "last_modified": (str(getattr(model, "lastModified", "")) or None),
                    "downloads": downloads,
                    "likes": likes,
                    "gated": getattr(model, "gated", None),
                    # GGUF repos are pullable through Ollama's hf.co/ shortcut.
                    "pullable": bool(gguf_only and mid),
                    "pull_name": f"hf.co/{mid}" if (gguf_only and mid) else None,
                }
            )
        return items, None
    except Exception as exc:
        return None, str(exc)


def _with_fit(item: Dict[str, Any], free_vram_mb: Optional[int]) -> Dict[str, Any]:
    """Attach a best-effort fit estimate for HF search candidates."""
    if not item.get("id"):
        return item
    fit = fit_check(FitRequest(model_id=item["id"], free_vram_mb=free_vram_mb))
    item["fit"] = fit
    return item


def _matches_fit_filter(item: Dict[str, Any], fit_filter: str) -> bool:
    fit_filter = (fit_filter or "all").lower()
    if fit_filter == "all":
        return True
    fit = item.get("fit") or {}
    if not fit.get("success"):
        return False
    if fit_filter == "gpu":
        return fit.get("verdict") == "FITS"
    if fit_filter == "runnable":
        return fit.get("severity") != "hard"
    return True


@router.post("/registry/check-updates")
def check_updates(req: CheckUpdatesRequest) -> Dict[str, Any]:
    installed, _ = _ollama.list_installed()
    queries = req.queries or (_derive_queries(installed) if installed else ["gemma", "qwen"])
    installed_signatures = [
        _installed_signature(m.get("name") or "") for m in installed if m.get("name")
    ]

    results: List[Dict[str, Any]] = []
    last_error: Optional[str] = None
    online = True
    for query in queries:
        items, error = _list_hf(query, req.limit, req.gguf_only)
        if error:
            last_error, online = error, False
            continue
        for item in items or []:
            item["installed_match"] = _candidate_matches_installed(
                item.get("id") or "", installed_signatures
            )
            _with_fit(item, req.free_vram_mb)
        candidates = [
            item for item in (items or []) if _matches_fit_filter(item, req.fit_filter)
        ]
        results.append({"query": query, "candidates": candidates})

    if not results:
        return {
            "success": True,
            "online": False,
            "queries": queries,
            "results": [],
            "message": f"Could not reach Hugging Face: {last_error}" if last_error else "No results.",
        }
    return {
        "success": True,
        "online": online,
        "queries": queries,
        "results": results,
        "message": None if online else f"Partial results; some queries failed: {last_error}",
    }
