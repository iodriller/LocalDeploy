"""Tests for the ModelScope discovery source and the GGUF import endpoints
(local file -> llama.cpp profile; direct URL -> download + `ollama create`).

Mirrors the conventions in test_web_registry_models.py / test_ollama_library_search.py:
CI has no Ollama, no GPU, and no network, so these monkeypatch the network/Ollama
edges and assert the graceful-failure contract (200 with a clear payload).
"""
from __future__ import annotations

import hashlib

import pytest
import requests as requests_lib

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.control import models as models_mod
from localdeploy.control import registry as reg

client = TestClient(app)


# --- ModelScope discovery (registry.py) ---------------------------------------


class _FakeJsonResponse:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code

    def raise_for_status(self):
        if not self.ok:
            raise requests_lib.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_modelscope_list_gguf_files_excludes_mmproj_and_imatrix_and_sorts_by_size(monkeypatch):
    payload = {
        "Data": {
            "Files": [
                {"Name": "model-Q8_0.gguf", "Size": 900, "Type": "blob"},
                {"Name": "model-Q4_K_M.gguf", "Size": 500, "Type": "blob"},
                {"Name": "mmproj-F16.gguf", "Size": 100, "Type": "blob"},
                {"Name": "model.imatrix.gguf", "Size": 10, "Type": "blob"},
                {"Name": "README.md", "Size": 1, "Type": "blob"},
                {"Name": "model-Q4_K_M.gguf", "Size": 500, "Type": "tree"},  # not a blob - excluded
            ]
        }
    }
    monkeypatch.setattr(requests_lib, "get", lambda *a, **kw: _FakeJsonResponse(payload))
    files, err = reg._modelscope_list_gguf_files("org/model-GGUF")
    assert err is None
    assert [f["name"] for f in files] == ["model-Q4_K_M.gguf", "model-Q8_0.gguf"]


def test_modelscope_list_gguf_files_reports_http_error(monkeypatch):
    monkeypatch.setattr(requests_lib, "get", lambda *a, **kw: _FakeJsonResponse({}, ok=False, status_code=404))
    files, err = reg._modelscope_list_gguf_files("org/missing")
    assert files == []
    assert "404" in err


def test_curate_gguf_files_caps_to_preferred_quants_when_many_present():
    # unsloth-style repos ship 20+ files (every IQ/dynamic variant); without
    # curation each becomes its own catalog row, and a single repo dominates
    # a results page with near-duplicate entries.
    obscure = ["IQ1_S", "IQ1_M", "IQ2_XXS", "Q2_K", "Q2_K_XL", "IQ3_XXS", "IQ4_XS", "Q4_K_XL", "Q8_K_XL"]
    preferred = ["Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"]
    files = [{"name": f"model-{q}.gguf", "size": 1} for q in obscure + preferred]
    curated = reg._curate_gguf_files(files)
    assert {reg._quant_from_name(f["name"]) for f in curated} == set(preferred)
    assert len(curated) == len(preferred)


def test_curate_gguf_files_falls_back_to_full_list_when_none_preferred():
    files = [{"name": "model-IQ1_S.gguf", "size": 1}, {"name": "model-IQ2_M.gguf", "size": 2}]
    curated = reg._curate_gguf_files(files)
    assert curated == files


def test_quant_from_name_recognizes_f16_bf16_f32():
    assert reg._quant_from_name("model-F16.gguf") == "F16"
    assert reg._quant_from_name("model-BF16.gguf") == "BF16"
    assert reg._quant_from_name("model-F32.gguf") == "F32"


def test_quant_from_name_recognizes_period_separated_quant():
    # Some ModelScope repos separate the quant with '.' instead of '-'/'_'
    # (e.g. "Qwen3.5-27B.Q3_K_M.gguf"); without this every file in that repo
    # got no quant label at all and rendered as an indistinguishable row.
    assert reg._quant_from_name("Qwen3.5-27B.Q3_K_M.gguf") == "Q3_K_M"


def test_modelscope_repo_row_rounds_params_b_and_keeps_it_stable_across_variants():
    repo = {
        "id": "org/model-GGUF", "downloads": 10, "likes": 1, "last_modified": "2026-01-01",
        "params": 9_197_093_888, "tags": ["library:gguf"], "tasks": [], "description": "",
    }
    files = [{"name": "model-Q4_K_M.gguf", "size": 1}, {"name": "model-Q8_0.gguf", "size": 2}]
    row = reg._modelscope_repo_row(repo, files)
    for variant in row["variants"]:
        assert variant["params_b"] == 9.2
        # a value with this few decimals never overflows a narrow "Parameters"
        # table column the way "9.197093888" did.
        assert len(str(variant["params_b"])) <= 4


def test_modelscope_rows_filters_to_gguf_tagged_repos_and_builds_variants(monkeypatch):
    payload = {
        "success": True,
        "data": {
            "models": [
                {
                    "id": "unsloth/Qwen3-4B-GGUF",
                    "downloads": 1000,
                    "likes": 10,
                    "last_modified": "2026-05-01T00:00:00Z",
                    "params": 4_000_000_000,
                    "tags": ["library:gguf"],
                    "tasks": ["text-generation"],
                    "description": "",
                },
                {
                    "id": "org/not-gguf",
                    "downloads": 5,
                    "likes": 1,
                    "last_modified": None,
                    "params": 0,
                    "tags": ["library:safetensors"],
                    "tasks": [],
                    "description": "",
                },
            ]
        },
    }
    monkeypatch.setattr(requests_lib, "get", lambda *a, **kw: _FakeJsonResponse(payload))
    monkeypatch.setattr(
        reg,
        "_modelscope_list_gguf_files",
        lambda repo_id: (
            [{"name": "Qwen3-4B-Q4_K_M.gguf", "size": 123}, {"name": "Qwen3-4B-Q8_0.gguf", "size": 456}],
            None,
        ),
    )
    rows, err = reg._modelscope_rows("qwen3", 5)
    assert err is None
    assert len(rows) == 1  # the non-gguf-tagged repo is filtered out
    row = rows[0]
    assert row["source"] == "modelscope"
    assert row["provider"] == "modelscope"
    assert row["pullable"] is True
    assert len(row["variants"]) == 2
    assert row["variants"][0]["pull_name"] == "modelscope.cn/unsloth/Qwen3-4B-GGUF:Qwen3-4B-Q4_K_M.gguf"
    assert row["variants"][0]["quant"] == "Q4_K_M"
    assert row["variants"][0]["params_b"] == pytest.approx(4.0)


def test_modelscope_rows_repo_without_resolved_files_still_returned(monkeypatch):
    payload = {
        "success": True,
        "data": {
            "models": [
                {
                    "id": "org/big-GGUF",
                    "downloads": 1,
                    "likes": 0,
                    "last_modified": None,
                    "params": 0,
                    "tags": ["library:gguf"],
                    "tasks": [],
                    "description": None,
                }
            ]
        },
    }
    monkeypatch.setattr(requests_lib, "get", lambda *a, **kw: _FakeJsonResponse(payload))
    monkeypatch.setattr(reg, "_modelscope_list_gguf_files", lambda repo_id: ([], "boom"))
    rows, err = reg._modelscope_rows("x", 5)
    assert err is None
    assert len(rows) == 1
    assert rows[0]["pullable"] is False
    assert rows[0]["pull_name"] is None
    assert rows[0]["url"] == "https://modelscope.cn/models/org/big-GGUF"


def test_modelscope_rows_offline(monkeypatch):
    monkeypatch.setenv("OFFLINE", "true")
    rows, err = reg._modelscope_rows("x", 5)
    assert rows == []
    assert err == "offline"


def test_modelscope_rows_network_failure_is_graceful(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)

    def boom(*a, **kw):
        raise requests_lib.ConnectionError("nope")

    monkeypatch.setattr(requests_lib, "get", boom)
    rows, err = reg._modelscope_rows("x", 5)
    assert rows == []
    assert "unreachable" in err


def test_search_models_includes_modelscope_source(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setattr(reg, "_library_rows", lambda query, limit: ([], None))
    monkeypatch.setattr(reg, "_hf_rows", lambda query, limit: ([], None))
    ms_row = {
        "source": "modelscope", "name": "org/model-GGUF", "provider": "modelscope", "publisher": "org",
        "description": None, "family": "org/model-GGUF", "sizes": [], "capabilities": [], "pulls": None,
        "popularity": None, "updated": None, "pullable": True,
        "pull_name": "modelscope.cn/org/model-GGUF:model-Q4_K_M.gguf",
        "url": "https://modelscope.cn/models/org/model-GGUF",
        "variants": [{"label": "Q4_K_M", "params_b": 4.0, "pull_name": "modelscope.cn/org/model-GGUF:model-Q4_K_M.gguf",
                      "quant": "Q4_K_M", "download_bytes": 123, "context": None}],
    }
    monkeypatch.setattr(reg, "_modelscope_rows", lambda query, limit: ([dict(ms_row)], None))
    body = client.post("/registry/search-models", json={"query": "model"}).json()
    assert body["success"] is True
    assert body["sources"]["modelscope"] == {"online": True, "count": 1, "error": None}
    assert [r["source"] for r in body["results"]] == ["modelscope"]


# --- /system/check-local-gguf --------------------------------------------------


def test_check_local_gguf_empty_path():
    body = client.post("/system/check-local-gguf", json={"path": ""}).json()
    assert body["success"] is False
    assert "Enter a file path" in body["error"]


def test_check_local_gguf_wrong_extension(tmp_path):
    body = client.post("/system/check-local-gguf", json={"path": str(tmp_path / "model.bin")}).json()
    assert body["success"] is False
    assert ".gguf" in body["error"]


def test_check_local_gguf_file_not_found(tmp_path):
    body = client.post("/system/check-local-gguf", json={"path": str(tmp_path / "missing.gguf")}).json()
    assert body["success"] is False
    assert body["exists"] is False


def test_check_local_gguf_success(tmp_path):
    f = tmp_path / "model.gguf"
    f.write_bytes(b"x" * 4096)
    body = client.post("/system/check-local-gguf", json={"path": str(f)}).json()
    assert body["success"] is True
    assert body["exists"] is True
    assert body["size_bytes"] == 4096


def test_check_local_gguf_strips_surrounding_quotes(tmp_path):
    f = tmp_path / "model.gguf"
    f.write_bytes(b"x")
    body = client.post("/system/check-local-gguf", json={"path": f'"{f}"'}).json()
    assert body["success"] is True


# --- /models/import-url ---------------------------------------------------------


def test_import_url_rejects_non_http_scheme():
    body = client.post("/models/import-url", json={"url": "ftp://example.com/model.gguf"}).json()
    assert body["success"] is False
    assert "http://" in body["error"]


def test_import_url_rejects_non_gguf_url():
    body = client.post("/models/import-url", json={"url": "https://example.com/model.bin"}).json()
    assert body["success"] is False
    assert ".gguf" in body["error"]


def test_import_url_offline_mode(monkeypatch):
    monkeypatch.setenv("OFFLINE", "true")
    body = client.post("/models/import-url", json={"url": "https://example.com/model.gguf"}).json()
    assert body["success"] is False
    assert "offline" in body["error"].lower()


def test_import_url_gpu_only_mode_blocks(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setattr(models_mod, "require_gpu_only", lambda: True)
    body = client.post("/models/import-url", json={"url": "https://example.com/model.gguf"}).json()
    assert body["success"] is False
    assert "GPU-only" in body["error"]


class _FakeDownloadResponse:
    def __init__(self, chunks, ok=True, status_code=200, headers=None):
        self._chunks = chunks
        self.ok = ok
        self.status_code = status_code
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=None):
        yield from self._chunks


def test_import_url_streams_full_success_path(monkeypatch, tmp_path):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setattr(models_mod, "require_gpu_only", lambda: False)
    monkeypatch.setattr(models_mod, "app_home", lambda: tmp_path)

    content = b"fake gguf bytes"
    digest = hashlib.sha256(content).hexdigest()

    def fake_get(url, stream=True, timeout=None):
        return _FakeDownloadResponse([content], headers={"content-length": str(len(content))})

    monkeypatch.setattr(models_mod.requests, "get", fake_get)
    monkeypatch.setattr(models_mod._ollama, "blob_exists", lambda d: False)

    pushed = {}

    def fake_push_blob(path, d):
        pushed["path"] = path
        pushed["digest"] = d
        assert path.exists()

    monkeypatch.setattr(models_mod._ollama, "push_blob", fake_push_blob)
    monkeypatch.setattr(
        models_mod._ollama, "create_stream", lambda model, files: iter([{"status": "success"}])
    )
    monkeypatch.setattr(
        models_mod, "ensure_profile_for_model", lambda model_id: ("myprofile", True, None)
    )

    response = client.post("/models/import-url", json={"url": "https://example.com/dir/mymodel.gguf"})
    assert response.status_code == 200
    text = response.text
    assert '"model": "mymodel"' in text
    assert '"status": "success"' in text
    assert '"profile": "myprofile"' in text
    assert "[DONE]" in text
    assert pushed["digest"] == digest
    # the temp download must be cleaned up regardless of outcome
    assert not pushed["path"].exists()


def test_import_url_download_http_error(monkeypatch, tmp_path):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setattr(models_mod, "require_gpu_only", lambda: False)
    monkeypatch.setattr(models_mod, "app_home", lambda: tmp_path)
    monkeypatch.setattr(
        models_mod.requests, "get", lambda url, stream=True, timeout=None: _FakeDownloadResponse([], ok=False, status_code=404)
    )
    response = client.post("/models/import-url", json={"url": "https://example.com/model.gguf"})
    assert response.status_code == 200
    assert "404" in response.text
    assert "[DONE]" in response.text


def test_import_url_download_request_error_is_streamed(monkeypatch, tmp_path):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setattr(models_mod, "require_gpu_only", lambda: False)
    monkeypatch.setattr(models_mod, "app_home", lambda: tmp_path)

    def boom(url, stream=True, timeout=None):
        raise requests_lib.RequestException("bad url")

    monkeypatch.setattr(models_mod.requests, "get", boom)
    response = client.post("/models/import-url", json={"url": "https://example.com/model.gguf"})
    assert response.status_code == 200
    assert "Download failed" in response.text
    assert "[DONE]" in response.text


def test_import_url_malformed_content_length_still_downloads(monkeypatch, tmp_path):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setattr(models_mod, "require_gpu_only", lambda: False)
    monkeypatch.setattr(models_mod, "app_home", lambda: tmp_path)
    monkeypatch.setattr(
        models_mod.requests,
        "get",
        lambda url, stream=True, timeout=None: _FakeDownloadResponse([b"data"], headers={"content-length": "unknown"}),
    )
    monkeypatch.setattr(models_mod._ollama, "blob_exists", lambda d: True)
    monkeypatch.setattr(models_mod._ollama, "create_stream", lambda model, files: iter([{"status": "success"}]))
    monkeypatch.setattr(models_mod, "ensure_profile_for_model", lambda model_id: ("myprofile", True, None))
    response = client.post("/models/import-url", json={"url": "https://example.com/model.gguf"})
    assert response.status_code == 200
    assert '"status": "success"' in response.text
    assert "[DONE]" in response.text


def test_import_url_size_over_limit_rejected(monkeypatch, tmp_path):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setattr(models_mod, "require_gpu_only", lambda: False)
    monkeypatch.setattr(models_mod, "app_home", lambda: tmp_path)
    monkeypatch.setattr(models_mod, "_MAX_IMPORT_GB", 0.000001)  # 1000-byte cap
    monkeypatch.setattr(
        models_mod.requests,
        "get",
        lambda url, stream=True, timeout=None: _FakeDownloadResponse([b"x" * 2000], headers={"content-length": "2000"}),
    )
    response = client.post("/models/import-url", json={"url": "https://example.com/model.gguf"})
    assert "import limit" in response.text


def test_import_url_ollama_unreachable_during_blob_push(monkeypatch, tmp_path):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setattr(models_mod, "require_gpu_only", lambda: False)
    monkeypatch.setattr(models_mod, "app_home", lambda: tmp_path)
    monkeypatch.setattr(
        models_mod.requests,
        "get",
        lambda url, stream=True, timeout=None: _FakeDownloadResponse([b"data"], headers={"content-length": "4"}),
    )
    monkeypatch.setattr(models_mod._ollama, "blob_exists", lambda d: False)

    def boom(path, d):
        raise requests_lib.ConnectionError("nope")

    monkeypatch.setattr(models_mod._ollama, "push_blob", boom)
    response = client.post("/models/import-url", json={"url": "https://example.com/model.gguf"})
    assert "not running or is unreachable" in response.text
