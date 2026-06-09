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

router = APIRouter()


@router.get("/registry/installed")
def registry_installed() -> Dict[str, Any]:
    models, error = _ollama.list_installed()
    return {"success": error is None, "installed": models, "error": error}


class CheckUpdatesRequest(BaseModel):
    queries: Optional[List[str]] = None
    limit: int = 5


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


def _list_hf(query: str, limit: int) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Newest HF models matching `query`. Lazy import; returns (items, error)."""
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
        models = api.list_models(search=query, sort="lastModified", limit=limit)
        items: List[Dict[str, Any]] = []
        for model in models:
            items.append(
                {
                    "id": getattr(model, "id", None) or getattr(model, "modelId", None),
                    "last_modified": (str(getattr(model, "lastModified", "")) or None),
                    "downloads": getattr(model, "downloads", None),
                    "likes": getattr(model, "likes", None),
                    "gated": getattr(model, "gated", None),
                }
            )
        return items, None
    except Exception as exc:
        return None, str(exc)


@router.post("/registry/check-updates")
def check_updates(req: CheckUpdatesRequest) -> Dict[str, Any]:
    installed, _ = _ollama.list_installed()
    queries = req.queries or (_derive_queries(installed) if installed else ["gemma", "qwen"])
    installed_bases = {_norm(_base_name(m.get("name") or "")) for m in installed if m.get("name")}
    installed_bases.discard("")

    results: List[Dict[str, Any]] = []
    last_error: Optional[str] = None
    online = True
    for query in queries:
        items, error = _list_hf(query, req.limit)
        if error:
            last_error, online = error, False
            continue
        for item in items or []:
            hid = _norm(item.get("id") or "")
            item["installed_match"] = any(base in hid for base in installed_bases)
        results.append({"query": query, "candidates": items or []})

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
