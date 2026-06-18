"""Steps 5-6 - model lifecycle.

Step 5:  POST /models/pull    -> stream `ollama pull`, gated by the fit-check
Step 6:  GET  /system/status  -> served model(s), Ollama health, VRAM
         POST /models/serve    -> warm a model (Ollama keep-alive)
         POST /models/stop      -> unload a model
         POST /models/switch    -> pivot from one model to another

Ollama is driven fully. llama.cpp lifecycle is process-managed by the existing
scripts, so serve/stop return clear guidance instead of spawning processes.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple

import requests
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..backends.llamacpp import llama_health
from ..utils import BackendCallError, require_gpu_only
from . import _ollama
from .fit import FitRequest, fit_check
from .hardware import detect_hardware

router = APIRouter()


def _sse(obj: Dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# A large layer count forces Ollama to offload everything it can to the GPU.
_FORCE_GPU_LAYERS = 999


def _resolve_num_gpu(device: Optional[str], num_gpu: Optional[int]) -> Optional[int]:
    """Map a friendly device choice to an Ollama ``num_gpu`` value.

    - explicit ``num_gpu`` wins (advanced override)
    - "cpu"  -> 0   (force CPU)
    - "gpu"  -> 999 (force max GPU offload)
    - "auto"/None -> None (Ollama decides; identical to prior behaviour)
    """
    if num_gpu is not None:
        return num_gpu
    d = (device or "").strip().lower()
    if d == "cpu":
        return 0
    if d == "gpu":
        return _FORCE_GPU_LAYERS
    return None


def _placement(size: Any, size_vram: Any) -> Dict[str, Any]:
    """Derive a GPU/CPU placement label from total vs VRAM-resident bytes."""
    if isinstance(size, int) and isinstance(size_vram, int) and size > 0:
        pct = round(100 * size_vram / size)
        label = "GPU" if pct >= 99 else ("CPU" if pct <= 1 else "Split")
        return {"gpu_percent": pct, "placement": label}
    return {"gpu_percent": None, "placement": None}


def _resolve_target(
    profile: Optional[str], model: Optional[str], backend: Optional[str]
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """Return (backend, model_id, profile_dict). Raises ValueError on bad input."""
    if profile:
        from api_server import load_config  # lazy: api_server owns config loading

        prof = load_config().get("profiles", {}).get(profile)
        if not prof:
            raise ValueError(f"Unknown profile '{profile}'.")
        return prof.get("backend", "ollama"), prof.get("model_id"), prof
    if not model:
        raise ValueError("Provide 'profile' or 'model'.")
    return (backend or "ollama"), model, {}


# --- Step 6: status ----------------------------------------------------------


@router.get("/system/status")
def system_status() -> Dict[str, Any]:
    running, run_error = _ollama.list_running()
    for m in running:
        m.update(_placement(m.get("size"), m.get("size_vram")))
    hardware = detect_hardware()
    return {
        "success": True,
        "ollama": {"reachable": run_error is None, "running": running, "error": run_error},
        "served_models": [m.get("name") for m in running],
        "hardware": {
            "gpu_available": hardware["gpu_available"],
            "gpus": hardware["gpus"],
            "system": hardware.get("system"),
        },
        "require_gpu_only": require_gpu_only(),
    }


# --- Step 5: pull (streamed, fit-gated) --------------------------------------


class PullRequest(BaseModel):
    model: Optional[str] = None
    profile: Optional[str] = None
    allow_override: bool = False
    free_vram_mb: Optional[int] = None


@router.post("/models/pull")
def models_pull(req: PullRequest):
    try:
        backend, model_id, _ = _resolve_target(req.profile, req.model, "ollama")
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    if backend != "ollama":
        return {
            "success": False,
            "error": "Pull is only supported for Ollama. llama.cpp uses local GGUF files.",
        }
    if require_gpu_only():
        return {"success": False, "error": "GPU-only mode is enabled; refusing Ollama pull."}

    fit = fit_check(FitRequest(profile=req.profile, model_id=model_id, free_vram_mb=req.free_vram_mb))
    # Hard-block only when the model fits *nowhere* (not GPU VRAM, not system RAM).
    # A model that won't fit VRAM but runs on CPU (tier "cpu_only", severity "soft")
    # is allowed through with a note — blocking it would contradict the tiered fit
    # warnings. Fall back to the coarse verdict when severity is absent.
    severity = fit.get("severity")
    hard_block = severity == "hard" if severity is not None else fit.get("verdict") == "WONT_FIT"
    if hard_block and not req.allow_override:
        return {
            "success": False,
            "blocked_by": "fit-check",
            "fit": fit,
            "message": fit.get("headline")
            or "Model is unlikely to fit available VRAM or system RAM. Re-run with allow_override=true to pull anyway.",
        }

    def event_stream():
        start: Dict[str, Any] = {"status": f"starting pull for {model_id}", "fit_verdict": fit.get("verdict")}
        # Surface the soft "won't fit GPU but runs on CPU" case so the pull isn't silent about it.
        if severity == "soft" and fit.get("cpu_deployable"):
            start["note"] = fit.get("headline") or "Won't fit GPU VRAM — will run on CPU (slower)."
        yield _sse(start)
        try:
            for event in _ollama.pull_stream(model_id):
                yield _sse(event)
            yield _sse({"status": "success", "done": True})
        except BackendCallError as exc:
            yield _sse({"error": str(exc)})
        except requests.ConnectionError:
            yield _sse({"error": "Ollama is not running or is unreachable. Start Ollama and retry."})
        except requests.RequestException as exc:
            yield _sse({"error": f"Ollama pull failed: {exc}"})
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --- Step 6: serve / stop / switch -------------------------------------------


class ServeRequest(BaseModel):
    model: Optional[str] = None
    profile: Optional[str] = None
    keep_alive: str = "5m"
    device: Optional[str] = None  # "auto" | "gpu" | "cpu"
    num_gpu: Optional[int] = None  # advanced override (layers offloaded to GPU)


class StopRequest(BaseModel):
    model: Optional[str] = None
    profile: Optional[str] = None


class SwitchRequest(BaseModel):
    to_model: Optional[str] = None
    to_profile: Optional[str] = None
    from_model: Optional[str] = None
    keep_alive: str = "5m"
    device: Optional[str] = None
    num_gpu: Optional[int] = None


def _serve_ollama(model_id: str, keep_alive: str, num_gpu: Optional[int] = None) -> Dict[str, Any]:
    if require_gpu_only():
        return {"success": False, "error": "GPU-only mode is enabled; refusing Ollama serve."}
    try:
        _ollama.load_model(model_id, keep_alive, num_gpu=num_gpu)
    except BackendCallError as exc:
        return {"success": False, "error": str(exc)}
    except requests.ConnectionError:
        return {"success": False, "error": "Ollama is not running or is unreachable. Start Ollama and retry."}
    except requests.Timeout:
        # The load may still finish in the background — guide the user to re-check
        # rather than implying a hard failure (common with large CPU loads).
        return {
            "success": False,
            "timeout": True,
            "error": (
                f"Loading '{model_id}' took too long to respond. Large models "
                "(especially on CPU) can take several minutes — it may still be "
                "loading. Wait a moment, then click Refresh status."
            ),
        }
    except requests.RequestException as exc:
        return {"success": False, "error": f"Failed to load '{model_id}': {exc}"}
    running, _ = _ollama.list_running()
    for m in running:
        m.update(_placement(m.get("size"), m.get("size_vram")))
    target = {0: "CPU", _FORCE_GPU_LAYERS: "GPU"}.get(num_gpu, "auto") if num_gpu is not None else "auto"
    return {
        "success": True,
        "backend": "ollama",
        "served": model_id,
        "running": running,
        "device": target,
        "message": f"'{model_id}' warmed on {target} and kept alive for {keep_alive}.",
    }


def _llamacpp_status_message() -> Dict[str, Any]:
    health = llama_health(os.getenv("LLAMACPP_BASE_URL", "http://localhost:8080"))
    if health.get("reachable"):
        return {"success": True, "backend": "llamacpp", "message": "llama.cpp server is running and serving its GGUF model."}
    return {
        "success": False,
        "backend": "llamacpp",
        "message": "Start the llama.cpp server first (scripts/start_llamacpp.ps1). LocalDeploy does not spawn it automatically.",
    }


@router.post("/models/serve")
def models_serve(req: ServeRequest) -> Dict[str, Any]:
    try:
        backend, model_id, _ = _resolve_target(req.profile, req.model, "ollama")
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    if backend == "ollama":
        return _serve_ollama(model_id, req.keep_alive, _resolve_num_gpu(req.device, req.num_gpu))
    return _llamacpp_status_message()


@router.post("/models/stop")
def models_stop(req: StopRequest) -> Dict[str, Any]:
    try:
        backend, model_id, _ = _resolve_target(req.profile, req.model, "ollama")
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    if backend != "ollama":
        return {
            "success": False,
            "backend": "llamacpp",
            "message": "Stop the llama.cpp server with scripts/stop.ps1; LocalDeploy does not manage that process.",
        }
    try:
        _ollama.unload_model(model_id)
    except BackendCallError as exc:
        return {"success": False, "error": str(exc)}
    except requests.ConnectionError:
        return {"success": False, "error": "Ollama is not running or is unreachable."}
    except requests.RequestException as exc:
        return {"success": False, "error": f"Failed to stop '{model_id}': {exc}"}
    return {"success": True, "backend": "ollama", "stopped": model_id, "message": f"'{model_id}' unloaded."}


class DeleteRequest(BaseModel):
    model: Optional[str] = None
    profile: Optional[str] = None


@router.post("/models/delete")
def models_delete(req: DeleteRequest) -> Dict[str, Any]:
    """Delete a model from disk (frees disk space). Ollama only."""
    try:
        backend, model_id, _ = _resolve_target(req.profile, req.model, "ollama")
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    if backend != "ollama":
        return {
            "success": False,
            "backend": "llamacpp",
            "message": "Delete is Ollama-only. Remove llama.cpp GGUF files manually.",
        }
    try:
        _ollama.delete_model(model_id)
    except BackendCallError as exc:
        return {"success": False, "error": str(exc)}
    except requests.ConnectionError:
        return {"success": False, "error": "Ollama is not running or is unreachable."}
    except requests.RequestException as exc:
        return {"success": False, "error": f"Failed to delete '{model_id}': {exc}"}
    return {"success": True, "deleted": model_id, "message": f"'{model_id}' deleted from disk."}


@router.post("/models/free")
def models_free() -> Dict[str, Any]:
    """Unload all loaded models from memory/VRAM (the 'free memory' reset)."""
    try:
        count, err = _ollama.unload_all()
    except BackendCallError as exc:
        return {"success": False, "error": str(exc)}
    if err is not None:
        return {"success": False, "error": err}
    return {
        "success": True,
        "unloaded": count,
        "message": f"Freed memory: unloaded {count} model(s)." if count else "No models were loaded.",
    }


@router.post("/models/switch")
def models_switch(req: SwitchRequest) -> Dict[str, Any]:
    try:
        backend, to_model, _ = _resolve_target(req.to_profile, req.to_model, "ollama")
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    if backend != "ollama":
        return _llamacpp_status_message()

    unloaded = None
    if req.from_model:
        try:
            _ollama.unload_model(req.from_model)
            unloaded = req.from_model
        except (BackendCallError, requests.RequestException):
            unloaded = None  # best-effort; serving the new model is what matters

    result = _serve_ollama(to_model, req.keep_alive, _resolve_num_gpu(req.device, req.num_gpu))
    result["switched_from"] = unloaded
    if result.get("success"):
        result["message"] = f"Switched to '{to_model}'" + (f" (unloaded '{unloaded}')" if unloaded else "") + "."
    return result
