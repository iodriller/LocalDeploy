"""Step 4 - model registry.

GET  /registry/installed      -> models pulled locally (Ollama /api/tags)
POST /registry/check-updates  -> matching models from Hugging Face

The Hugging Face call is a generic catalog search (popular-first) rather than a
per-model lookup table, so it stays a single mechanism instead of a chain of
special cases. Network/dependency failures degrade to a clear "offline" result.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
import json
import os
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import APIRouter
from pydantic import BaseModel

from ..backends.openai_compatible import list_openai_models
from ..utils import BackendCallError, get_backend_base_url, offline_mode
from . import _ollama
from .fit import FitRequest, fit_check

router = APIRouter()

_LOCAL_PROVIDER_DEFAULTS = {
    "lmstudio": "http://127.0.0.1:1234",
    "docker": "http://127.0.0.1:12434",
}


@router.get("/registry/installed")
def registry_installed() -> Dict[str, Any]:
    models, error = _ollama.list_installed()
    return {"success": error is None, "installed": models, "error": error}


def _benchmark_rates() -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Index opt-in server benchmark history by (backend, model)."""
    try:
        from .history import _history_dir

        files = list(_history_dir().glob("*.json"))[-200:]
    except OSError:
        return {}
    values: Dict[Tuple[str, str], List[float]] = {}
    counts: Dict[Tuple[str, str], int] = {}
    for path in files:
        try:
            run = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        key = (str(run.get("backend") or ""), str(run.get("model_id") or run.get("profile") or ""))
        rates = []
        for test in run.get("tests") or []:
            metrics = test.get("metrics") or {}
            rate = metrics.get("tokens_per_second") or test.get("approx_tokens_per_second")
            if rate is not None:
                try:
                    rates.append(float(rate))
                except (TypeError, ValueError):
                    pass
        if rates and key[1]:
            values.setdefault(key, []).extend(rates)
            counts[key] = counts.get(key, 0) + 1
    return {
        key: {"tokens_per_second": round(mean(rates), 2), "sample_count": len(rates), "run_count": counts[key]}
        for key, rates in values.items()
    }


def _provider_targets() -> List[Dict[str, Any]]:
    from api_server import load_config

    targets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    config = load_config()
    for profile_name, profile in config.get("profiles", {}).items():
        backend = str(profile.get("backend") or "ollama").lower()
        if backend == "ollama":
            continue
        try:
            base_url = get_backend_base_url(profile, backend)
        except BackendCallError:
            continue
        target = targets.setdefault(
            (backend, base_url), {"provider": backend, "base_url": base_url, "profiles": []}
        )
        target["profiles"].append(profile_name)
    for backend, default in _LOCAL_PROVIDER_DEFAULTS.items():
        targets.setdefault((backend, default), {"provider": backend, "base_url": default, "profiles": []})
    if os.getenv("VLLM_BASE_URL"):
        try:
            base_url = get_backend_base_url({}, "vllm")
            targets.setdefault(("vllm", base_url), {"provider": "vllm", "base_url": base_url, "profiles": []})
        except BackendCallError:
            pass
    return list(targets.values())


def _generic_inventory(target: Dict[str, Any]) -> Dict[str, Any]:
    models, error = list_openai_models(target["base_url"], target["provider"])
    return {**target, "reachable": error is None, "models": models, "error": error}


@router.get("/registry/providers")
def registry_providers() -> Dict[str, Any]:
    """Inventory models exposed by supported loopback inference runtimes."""
    installed, ollama_error = _ollama.list_installed()
    version, _ = _ollama.version()
    try:
        ollama_base_url = _ollama.base_url()
    except BackendCallError:
        ollama_base_url = "http://127.0.0.1:11434"
    providers = [
        {
            "provider": "ollama",
            "base_url": ollama_base_url,
            "reachable": ollama_error is None,
            "version": version,
            "models": installed,
            "error": ollama_error,
        }
    ]
    targets = _provider_targets()
    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as executor:
        providers.extend(executor.map(_generic_inventory, targets))
    rates = _benchmark_rates()
    rows = []
    for provider in providers:
        backend = str(provider["provider"])
        for model in provider.get("models") or []:
            model_id = str(model.get("name") or model.get("id") or "")
            details = model.get("details") if isinstance(model.get("details"), dict) else {}
            measured = rates.get((backend, model_id)) or rates.get((backend, str(model.get("profile") or "")))
            rows.append(
                {
                    "model": model_id,
                    "provider": backend,
                    "publisher": model.get("owned_by") or details.get("family"),
                    "parameters": details.get("parameter_size"),
                    "quant": details.get("quantization_level"),
                    "context": model.get("context_length"),
                    "digest": model.get("digest"),
                    "installed": True,
                    "tokens_per_second": measured.get("tokens_per_second") if measured else None,
                    "benchmark_samples": measured.get("sample_count") if measured else 0,
                    "base_url": provider["base_url"],
                }
            )
    return {"success": True, "providers": providers, "models": rows}


# --- Ollama library search (ollama.com) --------------------------------------
# Ollama does not document a model-library JSON endpoint, so discovery parses
# the official public search page. HTMLParser keeps namespaced community model
# paths intact and degrades cleanly if optional markup changes.


class _OllamaLibraryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, Any]] = []
        self.current: Optional[Dict[str, Any]] = None
        self._description_depth = 0
        self._description_parts: List[str] = []
        self._span_stack: List[Dict[str, Any]] = []
        self._span_texts: List[str] = []

    @staticmethod
    def _classes(attrs: List[Tuple[str, Optional[str]]]) -> str:
        return next((value or "" for key, value in attrs if key == "class"), "")

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attrs_map = dict(attrs)
        if tag == "a" and self.current is None:
            path = urlsplit(attrs_map.get("href") or "").path
            if path.startswith("/library/"):
                name = path[len("/library/") :].strip("/")
                if name:
                    self.current = {"name": name, "sizes": [], "capabilities": []}
                    self._description_parts = []
                    self._span_stack = []
                    self._span_texts = []
        if self.current is None:
            return
        classes = self._classes(attrs)
        if tag == "p" and "max-w-lg" in classes:
            self._description_depth = 1
        elif self._description_depth:
            self._description_depth += 1
        if tag == "span":
            kind = "size" if "text-blue-600" in classes else "capability" if "text-indigo-600" in classes else None
            self._span_stack.append({"kind": kind, "parts": []})

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        if self._description_depth:
            self._description_parts.append(data)
        for span in self._span_stack:
            span["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag == "span" and self._span_stack:
            span = self._span_stack.pop()
            value = " ".join("".join(span["parts"]).split())
            if value:
                self._span_texts.append(value)
                if span["kind"] == "size" and re.fullmatch(r"\d+(?:\.\d+)?[mb]", value.lower()):
                    self.current["sizes"].append(value.lower())
                elif span["kind"] == "capability":
                    self.current["capabilities"].append(value.lower())
        if self._description_depth:
            self._description_depth -= 1
        if tag == "li":
            self._finish_current()

    def _finish_current(self) -> None:
        if self.current is None:
            return
        item = self.current
        description = " ".join("".join(self._description_parts).split()) or None
        span_text = " ".join(self._span_texts)
        pulls_match = re.search(r"([\d.,]+[KMB]?)\s+Pulls\b", span_text, re.I)
        updated_match = re.search(r"\b((?:\d+\s+\w+\s+ago)|yesterday|today)\b", span_text, re.I)
        name = str(item["name"])
        capabilities = list(dict.fromkeys(item["capabilities"]))
        pullable = "cloud" not in capabilities or bool(item["sizes"])
        self.results.append(
            {
                "name": name,
                "provider": "ollama",
                "publisher": name.split("/", 1)[0] if "/" in name else "ollama",
                "description": description,
                "sizes": list(dict.fromkeys(item["sizes"])),
                "capabilities": capabilities,
                "pulls": pulls_match.group(1) if pulls_match else None,
                "updated": updated_match.group(1) if updated_match else None,
                "pullable": pullable,
                "pull_name": name if pullable else None,
                "url": f"https://ollama.com/library/{name}",
            }
        )
        self.current = None
        self._description_depth = 0
        self._description_parts = []
        self._span_stack = []
        self._span_texts = []

    def finish(self) -> List[Dict[str, Any]]:
        self._finish_current()
        return self.results


def parse_ollama_library_search(html: str) -> List[Dict[str, Any]]:
    """Extract model entries from an ollama.com/search results page."""
    parser = _OllamaLibraryParser()
    parser.feed(html or "")
    parsed = parser.finish()
    return list({item["name"]: item for item in parsed}.values())


class LibrarySearchRequest(BaseModel):
    query: str = ""
    limit: int = 24


@router.post("/registry/search-ollama-library")
def search_ollama_library(req: LibrarySearchRequest) -> Dict[str, Any]:
    """Search the public Ollama library (an empty query returns the popular list)."""
    if offline_mode():
        return {
            "success": True,
            "online": False,
            "results": [],
            "message": "offline mode (OFFLINE=true): Ollama library search skipped — no egress",
        }
    import requests

    try:
        resp = requests.get(
            "https://ollama.com/search",
            params={"q": req.query.strip()},
            headers={"User-Agent": "LocalDeploy (+https://github.com/iodriller/LocalDeploy)"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"success": True, "online": False, "results": [], "message": f"Could not reach ollama.com: {exc}"}

    results = parse_ollama_library_search(resp.text)[: max(1, min(req.limit, 50))]
    installed, _ = _ollama.list_installed()
    installed_bases = {(m.get("name") or "").split(":")[0] for m in installed}
    for item in results:
        item["installed_match"] = item["name"] in installed_bases
    message = None
    if not results:
        message = "No results parsed — ollama.com may have changed its page layout. Try the library link directly."
    return {"success": True, "online": True, "results": results, "message": message}


# --- unified remote search (Ollama library + Hugging Face, one query) --------


class UnifiedSearchRequest(BaseModel):
    query: str = ""
    limit: int = 30  # per source


def _library_rows(query: str, limit: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Ollama library results normalized for the unified table."""
    import requests

    try:
        resp = requests.get(
            "https://ollama.com/search",
            params={"q": query},
            headers={"User-Agent": "LocalDeploy (+https://github.com/iodriller/LocalDeploy)"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return [], f"Ollama library unreachable: {exc}"
    rows = parse_ollama_library_search(resp.text)[: max(1, limit)]
    for row in rows:
        row["source"] = "ollama"
    if not rows:
        return [], "No Ollama library results parsed — the page layout may have changed."
    return rows, None


def _hf_rows(query: str, limit: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Hugging Face GGUF results normalized to the same shape as library rows.

    A blank query returns the most-downloaded GGUF repos, so 'search everything'
    works before the user types anything.
    """
    if offline_mode():
        return [], "offline"
    try:
        from huggingface_hub import HfApi
    except Exception as exc:  # pragma: no cover - dependency missing
        return [], f"huggingface_hub unavailable: {exc}"
    try:
        api = HfApi()
        kwargs: Dict[str, Any] = {"filter": "gguf", "limit": max(1, limit)}
        if query:
            kwargs["search"] = query
            kwargs["sort"] = "downloads"
        else:
            kwargs["sort"] = "downloads"
        rows: List[Dict[str, Any]] = []
        for model in api.list_models(**kwargs):
            mid = getattr(model, "id", None) or getattr(model, "modelId", None)
            if not mid:
                continue
            downloads = getattr(model, "downloads", None)
            modified = getattr(model, "lastModified", None)
            rows.append(
                {
                    "source": "huggingface",
                    "name": str(mid),
                    "provider": "huggingface",
                    "publisher": str(mid).split("/", 1)[0] if "/" in str(mid) else None,
                    "description": None,
                    "sizes": [],
                    "capabilities": [],
                    "pulls": _fmt_count(downloads),
                    "updated": str(modified)[:10] if modified else None,
                    "pullable": True,
                    "pull_name": f"hf.co/{mid}",
                    "url": f"https://huggingface.co/{mid}",
                }
            )
        return rows, None
    except Exception as exc:
        return [], f"Hugging Face unreachable: {exc}"


def _fmt_count(value: Any) -> Optional[str]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


@router.post("/registry/search-models")
def search_models(req: UnifiedSearchRequest) -> Dict[str, Any]:
    """One query, every source: the Ollama library and Hugging Face GGUF repos,
    fetched in parallel and returned as one normalized list (source is a field,
    not a user decision)."""
    if offline_mode():
        return {
            "success": True,
            "online": False,
            "results": [],
            "sources": {},
            "message": "offline mode (OFFLINE=true): remote model search skipped — no egress",
        }
    query = req.query.strip()
    limit = max(1, min(req.limit, 50))
    with ThreadPoolExecutor(max_workers=2) as executor:
        lib_future = executor.submit(_library_rows, query, limit)
        hf_future = executor.submit(_hf_rows, query, limit)
        lib_rows, lib_error = lib_future.result()
        hf_rows, hf_error = hf_future.result()

    installed, _ = _ollama.list_installed()
    installed_bases = {(m.get("name") or "").split(":")[0] for m in installed}
    signatures = [_installed_signature(m.get("name") or "") for m in installed if m.get("name")]
    for row in lib_rows:
        row["installed_match"] = row["name"] in installed_bases
    for row in hf_rows:
        row["installed_match"] = _candidate_matches_installed(row["name"], signatures)

    results = lib_rows + hf_rows
    online = not (lib_error and hf_error)
    message = None
    if lib_error and hf_error:
        message = f"No source reachable. {lib_error} / {hf_error}"
    elif lib_error or hf_error:
        message = f"Partial results: {lib_error or hf_error}"
    return {
        "success": True,
        "online": online,
        "results": results,
        "sources": {
            "ollama": {"online": lib_error is None, "count": len(lib_rows), "error": lib_error},
            "huggingface": {"online": hf_error is None, "count": len(hf_rows), "error": hf_error},
        },
        "message": message,
    }


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
        # This endpoint backs a catalog, not an update feed: broad/blank searches
        # should surface established repositories before recent low-signal uploads.
        kwargs: Dict[str, Any] = {"sort": "downloads", "limit": max(1, min(limit, 50)), "full": True}
        if query:
            kwargs["search"] = query
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
                    "provider": "huggingface",
                    "publisher": str(mid).split("/", 1)[0] if mid and "/" in str(mid) else None,
                    "pipeline_tag": getattr(model, "pipeline_tag", None),
                    "tags": list(getattr(model, "tags", None) or []),
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
    queries = req.queries if req.queries is not None else (_derive_queries(installed) if installed else ["gemma", "qwen"])
    queries = [str(query).strip() for query in queries]
    if not queries:
        queries = [""]
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
