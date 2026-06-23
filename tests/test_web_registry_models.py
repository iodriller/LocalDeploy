"""Tests for the registry + model-lifecycle endpoints (Steps 4-6).

CI has no Ollama, no GPU, and no network, so these assert the graceful-failure
contract (every route returns 200 with a clear payload, never a 500) and use
monkeypatching for the Hugging Face and Ollama-pull paths.
"""
from __future__ import annotations

import pytest
import requests

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.web import models as models_mod
from localdeploy.web import registry

client = TestClient(app)


@pytest.fixture(autouse=True)
def _simulate_ollama_down(monkeypatch) -> None:
    """Keep default tests independent from a real Ollama service on localhost."""

    error = "Ollama is not reachable at http://localhost:11434. Start Ollama and retry."

    def list_installed():
        return [], error

    def list_running():
        return [], error

    def load_model(*_args, **_kwargs):
        raise requests.ConnectionError(error)

    def unload_model(*_args, **_kwargs):
        raise requests.ConnectionError(error)

    def pull_stream(*_args, **_kwargs):
        raise requests.ConnectionError(error)

    monkeypatch.setattr(registry._ollama, "list_installed", list_installed)
    monkeypatch.setattr(models_mod._ollama, "list_running", list_running)
    monkeypatch.setattr(models_mod._ollama, "load_model", load_model)
    monkeypatch.setattr(models_mod._ollama, "unload_model", unload_model)
    monkeypatch.setattr(models_mod._ollama, "pull_stream", pull_stream)


# --- Step 4: registry --------------------------------------------------------


def test_registry_installed_graceful_when_ollama_down() -> None:
    body = client.get("/registry/installed").json()
    # Ollama is not running in CI: endpoint still returns 200 with a clear error.
    assert "installed" in body
    assert isinstance(body["installed"], list)
    assert body["success"] is False
    assert body["error"]


def test_check_updates_with_mocked_hf(monkeypatch) -> None:
    def fake_list_hf(query, limit, gguf_only=True):
        return ([{"id": f"org/{query}-new", "last_modified": "2026-01-01"}], None)

    monkeypatch.setattr(registry, "_list_hf", fake_list_hf)
    body = client.post("/registry/check-updates", json={"queries": ["gemma"], "limit": 3}).json()
    assert body["success"] is True
    assert body["online"] is True
    assert body["results"][0]["candidates"][0]["id"] == "org/gemma-new"
    assert "installed_match" in body["results"][0]["candidates"][0]


def test_check_updates_offline_is_graceful(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_list_hf", lambda query, limit, gguf_only=True: (None, "network down"))
    body = client.post("/registry/check-updates", json={"queries": ["gemma"]}).json()
    assert body["success"] is True
    assert body["online"] is False
    assert "Hugging Face" in body["message"]


def test_list_hf_marks_gguf_repos_pullable(monkeypatch) -> None:
    import huggingface_hub

    class FakeModel:
        def __init__(self, mid):
            self.id = mid
            self.lastModified = "2026-01-01"
            self.downloads = 1
            self.likes = 1
            self.gated = False

    monkeypatch.setattr(
        huggingface_hub.HfApi, "list_models", lambda self, **kw: [FakeModel("TheBloke/Foo-GGUF")]
    )
    items, err = registry._list_hf("foo", 5, gguf_only=True)
    assert err is None
    assert items[0]["pullable"] is True
    assert items[0]["pull_name"] == "hf.co/TheBloke/Foo-GGUF"


def test_list_hf_enriches_missing_or_zero_stats(monkeypatch) -> None:
    import huggingface_hub

    class FakeSearchModel:
        id = "TheBloke/Foo-GGUF"
        lastModified = "2026-01-01"
        downloads = 0
        likes = 0
        gated = False

    class FakeInfo:
        downloads = 42
        likes = 7

    monkeypatch.setattr(
        huggingface_hub.HfApi, "list_models", lambda self, **kw: [FakeSearchModel()]
    )
    monkeypatch.setattr(huggingface_hub.HfApi, "model_info", lambda self, mid: FakeInfo())

    items, err = registry._list_hf("foo", 5, gguf_only=True)
    assert err is None
    assert items[0]["downloads"] == 42
    assert items[0]["likes"] == 7


def test_check_updates_surfaces_pull_name(monkeypatch) -> None:
    monkeypatch.setattr(
        registry,
        "_list_hf",
        lambda q, limit, gguf_only=True: ([{"id": "x/y-GGUF", "pullable": True, "pull_name": "hf.co/x/y-GGUF"}], None),
    )
    body = client.post("/registry/check-updates", json={"queries": ["x"]}).json()
    candidate = body["results"][0]["candidates"][0]
    assert candidate["pull_name"] == "hf.co/x/y-GGUF"


# --- Step 5: pull ------------------------------------------------------------


def test_pull_blocked_by_fit_check() -> None:
    # 70B against 8 GB must be refused before any network call.
    body = client.post(
        "/models/pull",
        json={"model": "llama3:70b", "free_vram_mb": 8192},
    ).json()
    assert body["success"] is False
    assert body["blocked_by"] == "fit-check"
    assert body["fit"]["verdict"] == "WONT_FIT"


def test_pull_streams_and_terminates() -> None:
    # Small model passes the fit gate; with no Ollama the stream reports an error
    # event and still terminates cleanly with [DONE].
    response = client.post("/models/pull", json={"model": "gemma3:4b", "free_vram_mb": 8192})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "[DONE]" in response.text


def test_pull_requires_a_target() -> None:
    body = client.post("/models/pull", json={}).json()
    assert body["success"] is False
    assert "profile" in body["error"]


# --- Step 6: serve / stop / switch / status ----------------------------------


def test_system_status_graceful() -> None:
    body = client.get("/system/status").json()
    assert body["success"] is True
    assert "served_models" in body
    assert body["ollama"]["reachable"] is False  # no Ollama in CI
    assert "hardware" in body


def test_serve_graceful_when_ollama_down() -> None:
    body = client.post("/models/serve", json={"model": "gemma3:4b"}).json()
    assert body["success"] is False
    assert body["error"]


def test_stop_graceful_when_ollama_down() -> None:
    body = client.post("/models/stop", json={"model": "gemma3:4b"}).json()
    assert body["success"] is False
    assert body["error"]


def test_switch_graceful_when_ollama_down() -> None:
    body = client.post(
        "/models/switch",
        json={"to_model": "gemma3:4b", "from_model": "qwen3:8b"},
    ).json()
    assert body["success"] is False


def test_serve_unknown_profile() -> None:
    body = client.post("/models/serve", json={"profile": "nope"}).json()
    assert body["success"] is False
    assert "Unknown profile" in body["error"]


# --- success paths (simulate Ollama/HF reachable via patching) ---------------
# Existing tests only cover the offline branch; these exercise the parsing of a
# healthy backend so a response-shape bug cannot hide in the success path.


def test_status_success_path(monkeypatch) -> None:
    monkeypatch.setattr(
        models_mod._ollama,
        "list_running",
        lambda: ([{"name": "gemma3:4b", "size_vram": 4_000_000_000}], None),
    )
    body = client.get("/system/status").json()
    assert body["success"] is True
    assert body["ollama"]["reachable"] is True
    assert body["served_models"] == ["gemma3:4b"]


def test_serve_success_path(monkeypatch) -> None:
    calls = {}
    monkeypatch.setattr(
        models_mod._ollama,
        "load_model",
        lambda m, k, num_gpu=None: calls.update(model=m, keep_alive=k, num_gpu=num_gpu) or {},
    )
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([{"name": "gemma3:4b"}], None))
    body = client.post("/models/serve", json={"model": "gemma3:4b", "keep_alive": "10m"}).json()
    assert body["success"] is True
    assert body["served"] == "gemma3:4b"
    # Default (no device) must leave num_gpu unset -> Ollama auto.
    assert calls == {"model": "gemma3:4b", "keep_alive": "10m", "num_gpu": None}


def test_serve_cpu_device_forces_num_gpu_zero(monkeypatch) -> None:
    calls = {}
    monkeypatch.setattr(
        models_mod._ollama,
        "load_model",
        lambda m, k, num_gpu=None: calls.update(num_gpu=num_gpu) or {},
    )
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([], None))
    body = client.post("/models/serve", json={"model": "gemma3:4b", "device": "cpu"}).json()
    assert body["success"] is True
    assert body["device"] == "CPU"
    assert calls["num_gpu"] == 0


def test_serve_default_keep_alive_is_sixty_minutes(monkeypatch) -> None:
    calls = {}
    monkeypatch.setattr(
        models_mod._ollama,
        "load_model",
        lambda m, k, num_gpu=None: calls.update(model=m, keep_alive=k, num_gpu=num_gpu) or {},
    )
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([], None))
    body = client.post("/models/serve", json={"model": "gemma3:4b"}).json()
    assert body["success"] is True
    assert calls["keep_alive"] == "60m"


def test_stop_success_path(monkeypatch) -> None:
    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda m: {})
    body = client.post("/models/stop", json={"model": "gemma3:4b"}).json()
    assert body["success"] is True
    assert body["stopped"] == "gemma3:4b"


def test_switch_success_path(monkeypatch) -> None:
    unloaded = []
    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda m: unloaded.append(m) or {})
    monkeypatch.setattr(models_mod._ollama, "load_model", lambda m, k, num_gpu=None: {})
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([{"name": "gemma3:4b"}], None))
    body = client.post(
        "/models/switch", json={"to_model": "gemma3:4b", "from_model": "qwen3:8b"}
    ).json()
    assert body["success"] is True
    assert body["switched_from"] == "qwen3:8b"
    assert unloaded == ["qwen3:8b"]


def test_pull_success_path_streams_events(monkeypatch) -> None:
    def fake_pull(model):
        yield {"status": "pulling manifest"}
        yield {"status": "downloading", "completed": 5, "total": 10}
        yield {"status": "verifying"}

    monkeypatch.setattr(models_mod._ollama, "pull_stream", fake_pull)
    response = client.post("/models/pull", json={"model": "gemma3:4b", "free_vram_mb": 8192})
    assert response.status_code == 200
    text = response.text
    assert "pulling manifest" in text
    assert "downloading" in text
    assert '"status": "success"' in text
    assert "[DONE]" in text


def test_check_updates_derives_queries_from_installed(monkeypatch) -> None:
    monkeypatch.setattr(
        models_mod._ollama, "list_installed", lambda: ([{"name": "gemma3:4b"}], None)
    )
    # registry imports _ollama as its own reference; patch there too.
    monkeypatch.setattr(registry._ollama, "list_installed", lambda: ([{"name": "gemma3:4b"}], None))
    monkeypatch.setattr(
        registry, "_list_hf", lambda q, limit, gguf_only=True: ([{"id": f"google/{q}-3-4b-it"}], None)
    )
    body = client.post("/registry/check-updates", json={}).json()
    assert body["success"] is True
    assert body["queries"] == ["gemma"]  # gemma3 -> gemma
    candidate = body["results"][0]["candidates"][0]
    assert candidate["id"] == "google/gemma-3-4b-it"
    # normalized matching: installed 'gemma3:4b' should flag the hyphenated HF id
    assert candidate["installed_match"] is True
