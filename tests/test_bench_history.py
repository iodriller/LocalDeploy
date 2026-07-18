"""Tests for the opt-in server-side benchmark history (reports/benchmark-history)."""
from __future__ import annotations

import json

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.control import history as history_routes

client = TestClient(app)

_RUN = {
    "id": "run-abc123",
    "createdAt": "2026-07-17T00:00:00.000Z",
    "profile": "gemma3_4b_ollama_safe",
    "modelId": "gemma3:4b",
    "tests": [{"name": "t1", "category": "math", "success": True, "accuracy": 1.0}],
    "summary": {"tests": 1, "passed": 1},
}


@pytest.fixture()
def history_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("BENCH_HISTORY_DIR", str(tmp_path / "history"))
    return tmp_path / "history"


def test_empty_history_lists_cleanly(history_dir):
    body = client.get("/benchmark/history").json()
    assert body["success"] is True
    assert body["runs"] == []


def test_save_list_delete_roundtrip(history_dir):
    saved = client.post("/benchmark/history/save", json={"run": _RUN}).json()
    assert saved["success"] is True
    assert saved["id"] == "run-abc123"
    assert (history_dir / "run-abc123.json").is_file()

    listed = client.get("/benchmark/history").json()
    assert [r["id"] for r in listed["runs"]] == ["run-abc123"]
    assert listed["runs"][0]["profile"] == "gemma3_4b_ollama_safe"

    deleted = client.post("/benchmark/history/delete", json={"id": "run-abc123"}).json()
    assert deleted["success"] is True
    assert not (history_dir / "run-abc123.json").exists()


def test_save_generates_id_when_missing(history_dir):
    run = dict(_RUN)
    run.pop("id")
    body = client.post("/benchmark/history/save", json={"run": run}).json()
    assert body["success"] is True
    assert body["id"]
    assert (history_dir / f"{body['id']}.json").is_file()


def test_rejects_traversal_and_junk_ids(history_dir):
    for bad in ("../evil", "a/b", "a\\b", ".hidden", "x" * 100):
        run = {**_RUN, "id": bad}
        body = client.post("/benchmark/history/save", json={"run": run}).json()
        assert body["success"] is False, bad
        deleted = client.post("/benchmark/history/delete", json={"id": bad}).json()
        assert deleted["success"] is False, bad
    # An empty id is not junk on save (one is generated), but delete needs a real one.
    assert client.post("/benchmark/history/delete", json={"id": ""}).json()["success"] is False
    # Nothing escaped into the parent tmp dir.
    assert not (history_dir.parent / "evil.json").exists()


def test_rejects_run_without_tests(history_dir):
    body = client.post("/benchmark/history/save", json={"run": {"id": "run-x"}}).json()
    assert body["success"] is False
    assert "tests" in body["error"]


def test_list_skips_corrupt_files_and_sorts_newest_first(history_dir):
    history_dir.mkdir(parents=True)
    (history_dir / "corrupt.json").write_text("{not json", encoding="utf-8")
    for i, ts in enumerate(["2026-01-01", "2026-03-01", "2026-02-01"]):
        run = {**_RUN, "id": f"run-{i}", "createdAt": ts}
        (history_dir / f"run-{i}.json").write_text(json.dumps(run), encoding="utf-8")
    body = client.get("/benchmark/history").json()
    assert body["success"] is True
    assert [r["id"] for r in body["runs"]] == ["run-1", "run-2", "run-0"]


def test_delete_missing_run_reports_error(history_dir):
    body = client.post("/benchmark/history/delete", json={"id": "run-nope"}).json()
    assert body["success"] is False
    assert "No stored run" in body["error"]


def test_save_prunes_history_to_retention_limit(history_dir, monkeypatch):
    monkeypatch.setattr(history_routes, "_MAX_RUNS", 2)
    for run_id in ("run-oldest", "run-middle", "run-newest"):
        run = {**_RUN, "id": run_id}
        body = client.post("/benchmark/history/save", json={"run": run}).json()
        assert body["success"] is True

    files = list(history_dir.glob("*.json"))
    assert len(files) == 2
    assert (history_dir / "run-newest.json").is_file()
    assert client.get("/benchmark/history").json()["runs"]
