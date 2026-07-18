"""Server-side benchmark history — an opt-in JSON store under reports/.

The benchmark workspace keeps runs in browser localStorage, which dies with
the browser profile. When the user flips the "also store on server" toggle in
the UI, completed runs are POSTed here and saved as one JSON file per run in
``reports/benchmark-history/`` (gitignored, human-readable, trivially
shareable). The endpoints are always mounted; whether anything is written is
the client's choice — nothing is stored unless the UI sends it.

Run ids double as filenames, so they are strictly validated (no separators,
no traversal) and everything else about the run payload is treated as opaque.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_MAX_RUNS = 200
_MAX_RUN_BYTES = 5_000_000  # one run's JSON; far above any real suite
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def _history_dir() -> Path:
    configured = os.getenv("BENCH_HISTORY_DIR")
    if configured:
        return Path(configured)
    from ..utils import app_home

    return app_home() / "reports" / "benchmark-history"


def _safe_path(run_id: str) -> Optional[Path]:
    if not _ID_RE.match(run_id or ""):
        return None
    return _history_dir() / f"{run_id}.json"


def _prune_history(directory: Path, keep: Path) -> int:
    """Keep the newest configured number of history files, including `keep`."""
    files = []
    for file in directory.glob("*.json"):
        try:
            files.append((file == keep, file.stat().st_mtime_ns, file.name, file))
        except OSError:
            continue
    files.sort(reverse=True)
    pruned = 0
    for _is_keep, _mtime, _name, file in files[_MAX_RUNS:]:
        try:
            file.unlink()
            pruned += 1
        except OSError:
            continue
    return pruned


class SaveRunRequest(BaseModel):
    run: Dict[str, Any]


class DeleteRunRequest(BaseModel):
    id: str


@router.get("/benchmark/history")
def list_history() -> Dict[str, Any]:
    directory = _history_dir()
    if not directory.is_dir():
        return {"success": True, "runs": [], "path": str(directory)}
    runs: List[Dict[str, Any]] = []
    for file in directory.glob("*.json"):
        try:
            with file.open("r", encoding="utf-8") as fh:
                run = json.load(fh)
        except (OSError, ValueError):
            continue  # skip unreadable/corrupt entries rather than failing the list
        if isinstance(run, dict):
            runs.append(run)
    runs.sort(key=lambda r: str(r.get("createdAt") or ""), reverse=True)
    return {"success": True, "runs": runs[:_MAX_RUNS], "path": str(directory)}


@router.post("/benchmark/history/save")
def save_run(req: SaveRunRequest) -> Dict[str, Any]:
    run = dict(req.run or {})
    if not isinstance(run.get("tests"), list):
        return {"success": False, "error": "A run record needs a 'tests' list."}
    run_id = str(run.get("id") or f"run-{int(time.time() * 1000)}")
    path = _safe_path(run_id)
    if path is None:
        return {"success": False, "error": f"Invalid run id {run_id!r}."}
    run["id"] = run_id
    payload = json.dumps(run, ensure_ascii=False, indent=2)
    if len(payload.encode("utf-8")) > _MAX_RUN_BYTES:
        return {"success": False, "error": "Run record is too large to store."}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        pruned = _prune_history(path.parent, path)
    except OSError as exc:
        return {"success": False, "error": f"Could not write {path}: {exc}"}
    return {"success": True, "id": run_id, "path": str(path), "pruned": pruned}


@router.post("/benchmark/history/delete")
def delete_run(req: DeleteRunRequest) -> Dict[str, Any]:
    path = _safe_path(req.id)
    if path is None:
        return {"success": False, "error": f"Invalid run id {req.id!r}."}
    if not path.is_file():
        return {"success": False, "error": f"No stored run with id {req.id!r}."}
    try:
        path.unlink()
    except OSError as exc:
        return {"success": False, "error": f"Could not delete {path}: {exc}"}
    return {"success": True, "deleted": req.id}
