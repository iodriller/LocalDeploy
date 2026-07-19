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
from localdeploy.control import models as models_mod
from localdeploy.control import registry

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


def test_check_updates_blank_query_requests_popular_catalog(monkeypatch) -> None:
    seen = []

    def fake_list_hf(query, limit, gguf_only=True):
        seen.append((query, limit, gguf_only))
        return ([{"id": "org/popular-GGUF"}], None)

    monkeypatch.setattr(registry, "_list_hf", fake_list_hf)
    body = client.post(
        "/registry/check-updates",
        json={"queries": [""], "limit": 24, "gguf_only": True},
    ).json()
    assert seen == [("", 24, True)]
    assert body["results"][0]["query"] == ""
    assert body["results"][0]["candidates"][0]["id"] == "org/popular-GGUF"


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


def test_provider_inventory_combines_runtime_metadata_and_benchmark_rate(monkeypatch) -> None:
    monkeypatch.setattr(
        registry._ollama,
        "list_installed",
        lambda: (
            [
                {
                    "name": "gemma3:4b",
                    "digest": "sha256:full",
                    "details": {"family": "gemma3", "parameter_size": "4.3B", "quantization_level": "Q4_K_M"},
                }
            ],
            None,
        ),
    )
    monkeypatch.setattr(registry._ollama, "version", lambda: ("0.12.0", None))
    monkeypatch.setattr(registry._ollama, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(
        registry,
        "_provider_targets",
        lambda: [{"provider": "lmstudio", "base_url": "http://127.0.0.1:1234", "profiles": []}],
    )
    monkeypatch.setattr(
        registry,
        "_generic_inventory",
        lambda target: {
            **target,
            "reachable": True,
            "models": [{"id": "publisher/model", "owned_by": "publisher"}],
            "error": None,
        },
    )
    monkeypatch.setattr(
        registry,
        "_benchmark_rates",
        lambda: {("ollama", "gemma3:4b"): {"tokens_per_second": 42.5, "sample_count": 6}},
    )
    body = client.get("/registry/providers").json()
    assert body["success"] is True
    assert {row["provider"] for row in body["models"]} == {"ollama", "lmstudio"}
    ollama_row = next(row for row in body["models"] if row["provider"] == "ollama")
    assert ollama_row["quant"] == "Q4_K_M"
    assert ollama_row["tokens_per_second"] == 42.5


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


def test_pull_of_unparseable_model_name_is_not_silent() -> None:
    # "llama3:latest" encodes no parameter count, so fit-check can't estimate
    # size at all. That must not be a silent, unwarned pass-through — it must
    # neither hard-block (we don't know it won't fit) nor proceed with zero
    # signal that fit couldn't be verified.
    response = client.post("/models/pull", json={"model": "llama3:latest", "free_vram_mb": 8192})
    assert response.status_code == 200
    text = response.text
    assert "[DONE]" in text
    assert '"note"' in text
    assert "Could not verify VRAM fit" in text or "Could not determine parameter count" in text


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
    calls = []
    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda m: calls.append(("unload", m)) or {})
    monkeypatch.setattr(
        models_mod._ollama,
        "load_model",
        lambda m, k, num_gpu=None: calls.append(("load", m, num_gpu)) or {},
    )
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([], None))
    body = client.post("/models/serve", json={"model": "gemma3:4b", "device": "cpu"}).json()
    assert body["success"] is True
    assert body["device"] == "CPU"
    assert calls == [("unload", "gemma3:4b"), ("load", "gemma3:4b", 0)]


def test_serve_auto_does_not_unload_before_load(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda m: calls.append(("unload", m)) or {})
    monkeypatch.setattr(
        models_mod._ollama,
        "load_model",
        lambda m, k, num_gpu=None: calls.append(("load", m, num_gpu)) or {},
    )
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([], None))
    body = client.post("/models/serve", json={"model": "gemma3:4b", "device": "auto"}).json()
    assert body["success"] is True
    assert calls == [("load", "gemma3:4b", None)]


def test_serve_cpu_warns_but_proceeds_if_ollama_reports_split(monkeypatch) -> None:
    # A device request that Ollama can't fully honor (model too big for pure CPU
    # so it lands on Split) should warn and proceed, not hard-fail: the run stays
    # useful and is labeled with the actual placement.
    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda _m: {})
    monkeypatch.setattr(models_mod._ollama, "load_model", lambda _m, _k, num_gpu=None: {})
    monkeypatch.setattr(
        models_mod._ollama,
        "list_running",
        lambda: ([{"name": "gemma3:4b", "size": 1000, "size_vram": 500}], None),
    )
    body = client.post("/models/serve", json={"model": "gemma3:4b", "device": "cpu"}).json()
    assert body["success"] is True
    assert "Requested CPU" in body["warning"]
    assert "Split" in body["warning"]


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


def test_stop_waits_for_running_inventory_confirmation(monkeypatch) -> None:
    inventories = iter(
        [
            ([{"name": "gemma3:4b"}], None),
            ([{"name": "gemma3:4b"}], None),
            ([], None),
        ]
    )
    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda m: {})
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: next(inventories))
    monkeypatch.setattr(models_mod.time, "sleep", lambda _seconds: None)
    body = client.post("/models/stop", json={"model": "gemma3:4b"}).json()
    assert body["status"] == "unloaded"
    assert body["confirmed"] is True


def test_stop_reports_pending_instead_of_false_success(monkeypatch) -> None:
    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda m: {})
    monkeypatch.setattr(
        models_mod._ollama,
        "list_running",
        lambda: ([{"name": "gemma3:4b"}], None),
    )
    monkeypatch.setattr(models_mod.time, "sleep", lambda _seconds: None)
    body = client.post("/models/stop", json={"model": "gemma3:4b"}).json()
    assert body["success"] is True
    assert body["status"] == "pending"
    assert body["confirmed"] is False


def test_model_name_match_does_not_confuse_parameter_sizes() -> None:
    assert models_mod._matches_model_name("gemma3:latest", "gemma3") is True
    assert models_mod._matches_model_name("gemma3:4b", "gemma3:4b") is True
    assert models_mod._matches_model_name("gemma3:12b", "gemma3:4b") is False


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


def test_switch_clears_monitor_state_for_the_from_model(monkeypatch) -> None:
    # Regression: switch used to unload the "from" model via Ollama without
    # telling monitor.py, leaving a stale _serve_state entry behind forever
    # (no session summary, and it permanently blocks the "only one model
    # loaded" calibration guard for every future /models/stop).
    from localdeploy.control import monitor

    monkeypatch.setattr(models_mod._ollama, "unload_model", lambda m: {})
    monkeypatch.setattr(models_mod._ollama, "load_model", lambda m, k, num_gpu=None: {})
    monkeypatch.setattr(models_mod._ollama, "list_running", lambda: ([{"name": "gemma3:4b"}], None))
    monitor.note_serve("qwen3:8b", "GPU")
    client.post("/models/switch", json={"to_model": "gemma3:4b", "from_model": "qwen3:8b"})
    assert "qwen3:8b" not in monitor._serve_state
    monitor._serve_state.pop("gemma3:4b", None)  # leave module state clean for other tests


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
