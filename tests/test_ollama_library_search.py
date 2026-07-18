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
