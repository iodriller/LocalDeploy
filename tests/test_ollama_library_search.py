"""Tests for the Ollama library search (parser + endpoint offline behavior)."""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.control.registry import parse_ollama_library_search

client = TestClient(app)

# Mirrors the structure of ollama.com/search result <li> blocks (July 2026).
_FIXTURE = """
<li class="flex items-baseline border-b border-neutral-200 py-6">
  <a href="/library/gemma3" class="group w-full">
    <div class="flex flex-col mb-1" title="gemma3">
      <h2><span>gemma3</span></h2>
      <p class="max-w-lg break-words text-neutral-800 text-md">The current, most capable model that runs on a single GPU.</p>
    </div>
    <div class="flex flex-col">
      <div class="flex flex-wrap space-x-2">
        <span class="inline-flex my-1 items-center rounded-md bg-indigo-50 px-2 py-[2px] text-xs font-medium text-indigo-600 sm:text-[13px]">vision</span>
        <span class="inline-flex my-1 items-center rounded-md bg-[#ddf4ff] px-2 py-[2px] text-xs font-medium text-blue-600 sm:text-[13px]">1b</span>
        <span class="inline-flex my-1 items-center rounded-md bg-[#ddf4ff] px-2 py-[2px] text-xs font-medium text-blue-600 sm:text-[13px]">4b</span>
        <span class="inline-flex my-1 items-center rounded-md bg-[#ddf4ff] px-2 py-[2px] text-xs font-medium text-blue-600 sm:text-[13px]">27b</span>
      </div>
      <p class="my-1 flex space-x-5 text-[13px] font-medium text-neutral-500">
        <span class="flex items-center"><svg></svg><span>38.7M</span><span class="hidden sm:flex">&nbsp;Pulls</span></span>
        <span class="flex items-center"><span >2 weeks ago</span></span>
      </p>
    </div>
  </a>
</li>
<li class="flex items-baseline border-b border-neutral-200 py-6">
  <a href="/library/embeddinggemma" class="group w-full">
    <div class="flex flex-col mb-1" title="embeddinggemma">
      <h2><span>embeddinggemma</span></h2>
    </div>
  </a>
</li>
"""


def test_parser_extracts_full_entries():
    results = parse_ollama_library_search(_FIXTURE)
    assert [r["name"] for r in results] == ["gemma3", "embeddinggemma"]
    top = results[0]
    assert top["description"] == "The current, most capable model that runs on a single GPU."
    assert top["sizes"] == ["1b", "4b", "27b"]
    assert top["capabilities"] == ["vision"]
    assert top["pulls"] == "38.7M"
    assert top["updated"] == "2 weeks ago"
    assert top["pullable"] is True
    assert top["pull_name"] == "gemma3"
    assert top["url"] == "https://ollama.com/library/gemma3"


def test_parser_degrades_to_bare_names():
    results = parse_ollama_library_search('<li><a href="/library/mystery"></a></li>')
    assert results == [
        {
            "name": "mystery",
            "provider": "ollama",
            "publisher": "ollama",
            "description": None,
            "sizes": [],
            "capabilities": [],
            "pulls": None,
            "updated": None,
            "pullable": True,
            "pull_name": "mystery",
            "url": "https://ollama.com/library/mystery",
        }
    ]


def test_parser_handles_junk():
    assert parse_ollama_library_search("") == []
    assert parse_ollama_library_search("<html>nothing here</html>") == []


def test_parser_keeps_namespaced_models_and_marks_cloud_only_entries():
    html = """
    <li><a href="/library/acme/coder"><p class="max-w-lg">Community model</p><span class="text-blue-600">7b</span></a></li>
    <li><a href="/library/cloud-model"><span class="text-indigo-600">cloud</span></a></li>
    """
    results = parse_ollama_library_search(html)
    assert results[0]["name"] == "acme/coder"
    assert results[0]["publisher"] == "acme"
    assert results[0]["pull_name"] == "acme/coder"
    assert results[1]["pullable"] is False
    assert results[1]["pull_name"] is None


def test_endpoint_respects_offline_mode(monkeypatch):
    monkeypatch.setenv("OFFLINE", "true")
    body = client.post("/registry/search-ollama-library", json={"query": "gemma"}).json()
    assert body["success"] is True
    assert body["online"] is False
    assert body["results"] == []
    assert "no egress" in body["message"]


def test_endpoint_survives_network_failure(monkeypatch):
    import requests as requests_lib

    def boom(*args, **kwargs):
        raise requests_lib.ConnectionError("nope")

    monkeypatch.setattr(requests_lib, "get", boom)
    monkeypatch.delenv("OFFLINE", raising=False)
    body = client.post("/registry/search-ollama-library", json={"query": "gemma"}).json()
    assert body["success"] is True
    assert body["online"] is False
    assert "Could not reach ollama.com" in body["message"]


# --- unified search (/registry/search-models) --------------------------------


def test_unified_search_respects_offline_mode(monkeypatch):
    monkeypatch.setenv("OFFLINE", "true")
    body = client.post("/registry/search-models", json={"query": "gemma"}).json()
    assert body["success"] is True
    assert body["online"] is False
    assert body["results"] == []
    assert "no egress" in body["message"]


def test_unified_search_merges_sources_and_reports_partial_failures(monkeypatch):
    from localdeploy.control import registry as reg

    monkeypatch.delenv("OFFLINE", raising=False)
    lib_row = {"source": "ollama", "name": "gemma3", "sizes": ["4b"], "capabilities": [],
               "description": None, "pulls": "1M", "updated": None, "pullable": True,
               "pull_name": "gemma3", "url": "https://ollama.com/library/gemma3",
               "provider": "ollama", "publisher": "ollama"}
    monkeypatch.setattr(reg, "_library_rows", lambda query, limit: ([dict(lib_row)], None))
    monkeypatch.setattr(reg, "_hf_rows", lambda query, limit: ([], "Hugging Face unreachable: boom"))
    monkeypatch.setattr(reg, "_modelscope_rows", lambda query, limit: ([], "ModelScope unreachable: boom"))
    body = client.post("/registry/search-models", json={"query": "gemma"}).json()
    assert body["success"] is True
    assert body["online"] is True  # one source is enough
    assert [r["name"] for r in body["results"]] == ["gemma3"]
    assert body["sources"]["ollama"]["online"] is True
    assert body["sources"]["huggingface"]["online"] is False
    assert body["sources"]["modelscope"]["online"] is False
    assert "Partial results" in body["message"]


def test_unified_search_orders_library_before_hf(monkeypatch):
    from localdeploy.control import registry as reg

    monkeypatch.delenv("OFFLINE", raising=False)
    lib = {"source": "ollama", "name": "a-lib", "sizes": [], "capabilities": [], "description": None,
           "pulls": None, "updated": None, "pullable": True, "pull_name": "a-lib",
           "url": "u", "provider": "ollama", "publisher": "ollama"}
    hf = {"source": "huggingface", "name": "z-org/a-hf", "sizes": [], "capabilities": [], "description": None,
          "pulls": None, "updated": None, "pullable": True, "pull_name": "hf.co/z-org/a-hf",
          "url": "u", "provider": "huggingface", "publisher": "z-org"}
    monkeypatch.setattr(reg, "_library_rows", lambda query, limit: ([dict(lib)], None))
    monkeypatch.setattr(reg, "_hf_rows", lambda query, limit: ([dict(hf)], None))
    monkeypatch.setattr(reg, "_modelscope_rows", lambda query, limit: ([], None))
    body = client.post("/registry/search-models", json={"query": "a"}).json()
    assert [r["source"] for r in body["results"]] == ["ollama", "huggingface"]
    assert body["results"][1]["pull_name"] == "hf.co/z-org/a-hf"


def test_catalog_metadata_extracts_size_quant_and_capabilities():
    from localdeploy.control import registry as reg

    assert reg._params_from_name("Qwen/Qwen3.5-4B-Instruct-Q4_K_M-GGUF") == 4
    assert reg._params_from_name("org/tiny-270m-gguf") == pytest.approx(0.27)
    assert reg._quant_from_name("model-4B-Q4_K_M-GGUF") == "Q4_K_M"
    assert "vision" in reg._catalog_capabilities("org/model-vl", [], "image-text-to-text")
    assert "embedding" in reg._catalog_capabilities("org/embed", [], "feature-extraction")


# --- library tags (/registry/library-tags) ------------------------------------

_TAGS_FIXTURE = """
<a href="/library/qwen2.5:latest"><p>4.7GB</p><p>32K</p></a>
<a href="/library/qwen2.5:7b"></a>
<a href="/library/qwen2.5:7b"><p>4.7GB</p><p>32K</p></a>
<a href="/library/qwen2.5:7b-instruct-q5_K_M"><p>5.4GB</p><p>32K</p></a>
"""


def test_tags_parser_prefers_sized_occurrence():
    from localdeploy.control.registry import parse_ollama_library_tags

    tags = parse_ollama_library_tags(_TAGS_FIXTURE)
    by = {t["tag"]: t for t in tags}
    assert by["7b"]["size"] == "4.7GB"  # second occurrence carried the size
    assert by["7b-instruct-q5_K_M"] == {"tag": "7b-instruct-q5_K_M", "size": "5.4GB", "context": "32K"}
    assert [t["tag"] for t in tags] == ["latest", "7b", "7b-instruct-q5_K_M"]


def test_tags_endpoint_offline_and_bad_name(monkeypatch):
    monkeypatch.setenv("OFFLINE", "true")
    body = client.post("/registry/library-tags", json={"model": "qwen2.5:7b"}).json()
    assert body["success"] is True and body["online"] is False
    assert body["family"] == "qwen2.5"
    bad = client.post("/registry/library-tags", json={"model": "###"}).json()
    assert bad["success"] is False


# --- batch fit (/system/fit-batch) ---------------------------------------------


def test_fit_batch_classifies_and_dedupes():
    body = client.post(
        "/system/fit-batch",
        json={"params_b": [0.27, 4, 4, 27, 70], "free_vram_mb": 8192},
    ).json()
    assert body["success"] is True
    by = {item["params_b"]: item for item in body["items"]}
    assert len(body["items"]) == 4  # deduped
    assert by[0.27]["severity"] == "ok"
    assert by[4]["severity"] == "ok"
    assert by[70]["severity"] in ("soft", "hard")  # CPU-only at best on 8 GB
    assert all(item["required_gb"] > 0 for item in body["items"])


def test_fit_batch_rejects_bad_inputs():
    assert client.post("/system/fit-batch", json={"params_b": [4], "context": -1}).status_code == 422
    body = client.post("/system/fit-batch", json={"params_b": [0], "free_vram_mb": 8192}).json()
    assert body["success"] is False
