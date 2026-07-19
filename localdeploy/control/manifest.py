"""Deployment manifests (Release R5) - capture an exact deployment so it can
be exported, inspected, and recreated on another machine.

POST /system/manifest/export    -> assemble a manifest from a saved profile,
                                    pulling together fit/calibration/hardware/
                                    benchmark data that already exists elsewhere
                                    in the app. YAML is the human-readable
                                    default (per the reproducibility plan);
                                    JSON is returned alongside it.
POST /system/manifest/validate  -> "would this manifest's deployment still
                                    work here?" - a compatibility report, no
                                    side effects.
POST /system/manifest/recreate  -> validate, then actually pull/serve to
                                    reproduce it on this machine, streamed.
GET  /system/integration-snippets -> copy-paste snippets for using a served
                                    model from Open WebUI, curl, Python, etc.,
                                    off the existing OpenAI-compatible /v1/*
                                    endpoints. Static templating, no new
                                    serving logic.

Every field is assembled from data this app already computes elsewhere
(fit.py, calibration.py, hardware.py, benchmark history) - this module is
orchestration, not a new estimation engine.
"""
from __future__ import annotations

import json
import platform
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

from . import _ollama, calibration
from .fit import FitRequest, _base_family, _parse_quant, fit_check
from .hardware import detect_hardware
from .models import _matches_model_name, _placement

router = APIRouter()

SCHEMA_VERSION = 1


class ManifestExportRequest(BaseModel):
    profile: Optional[str] = None
    model_id: Optional[str] = None


def _resolve_profile(profile_name: Optional[str], model_id: Optional[str]):
    from api_server import load_config

    config = load_config()
    profiles = config.get("profiles", {})
    if profile_name and profile_name in profiles:
        return profile_name, profiles[profile_name]
    if model_id:
        for name, prof in profiles.items():
            if str(prof.get("model_id")) == model_id:
                return name, prof
    return None, None


def _latest_benchmark_record(model_id: str) -> Optional[Dict[str, Any]]:
    """Best-effort: the most recent saved benchmark-history run that used this
    exact model, richest test record first. Server-side history is opt-in
    (history.py); when nothing is saved, performance/benchmark sections are
    simply omitted rather than guessed."""
    try:
        from .history import _history_dir

        files = sorted(_history_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in files[:200]:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(run, dict):
            continue
        if str(run.get("model_id") or "") != model_id:
            continue
        return run
    return None


def _performance_from_run(run: Dict[str, Any]) -> Dict[str, Any]:
    tests = run.get("tests") or []
    metrics_list = [t.get("metrics") or {} for t in tests if t.get("success")]
    ttft = [m["ttft_ms"] for m in metrics_list if m.get("ttft_ms") is not None]
    prompt_tps = [m["prompt_tokens_per_second"] for m in metrics_list if m.get("prompt_tokens_per_second") is not None]
    gen_tps = [m["tokens_per_second"] for m in metrics_list if m.get("tokens_per_second") is not None]
    perf: Dict[str, Any] = {}
    if ttft:
        perf["first_token_ms"] = round(sum(ttft) / len(ttft), 1)
    if prompt_tps:
        perf["prompt_tokens_per_second"] = round(sum(prompt_tps) / len(prompt_tps), 2)
    if gen_tps:
        perf["generation_tokens_per_second"] = round(sum(gen_tps) / len(gen_tps), 2)
    peak_vram_mb = run.get("peak_vram_mb")
    if peak_vram_mb:
        perf["peak_vram_gb"] = round(peak_vram_mb / 1024.0, 2)
    return perf


def _benchmark_section(run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not run:
        return {}
    tests = run.get("tests") or []
    accs = [t.get("accuracy") for t in tests if isinstance(t.get("accuracy"), (int, float))]
    return {
        "pack": run.get("pack") or run.get("questionSetName") or None,
        "accuracy": round(sum(accs) / len(accs), 3) if accs else None,
        "run_id": run.get("id"),
    }


@router.post("/system/manifest/export")
def manifest_export(req: ManifestExportRequest) -> Dict[str, Any]:
    profile_name, profile = _resolve_profile(req.profile, req.model_id)
    if not profile:
        return {"success": False, "error": "Unknown profile or model_id - pull and deploy it first."}
    model_id = str(profile.get("model_id") or profile_name)
    backend = str(profile.get("backend") or "ollama").lower()
    if backend != "ollama":
        return {"success": False, "error": "Deployment manifests currently support Ollama-backed profiles only."}

    hw = detect_hardware()
    installed, _ = _ollama.list_installed()
    installed_item = next((m for m in installed if _matches_model_name(m.get("name"), model_id)), None)
    version, _ = _ollama.version()
    running, _ = _ollama.list_running()
    running_item = next((m for m in running if _matches_model_name(m.get("name"), model_id)), None)
    placement_observed = None
    if running_item:
        placement_observed = _placement(running_item.get("size"), running_item.get("size_vram")).get("placement")

    context = profile.get("safe_context_limit") or profile.get("context_limit") or 4096
    quant = profile.get("quantization") or (installed_item or {}).get("details", {}).get("quantization_level") or _parse_quant(model_id)

    fit = fit_check(FitRequest(profile=profile_name))
    corr = calibration.get_correction(
        gpu=calibration.gpu_key(hw), runtime="ollama", family=_base_family(model_id), quant=quant, context=context
    )

    bench_run = _latest_benchmark_record(model_id)
    gpus = hw.get("gpus") or []
    best_gpu = gpus[0] if gpus else {}

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "model": {
            "name": model_id,
            "digest": (installed_item or {}).get("digest"),
            "quantization": quant,
            "source": "ollama",
        },
        "runtime": {
            "provider": "ollama",
            "version": version,
            "endpoint": _ollama.base_url(),
        },
        "deployment": {
            "context_length": context,
            "placement_requested": None,  # not persisted per-profile today; best-effort only
            "placement_observed": placement_observed,
            "keep_alive": profile.get("keep_alive", "60m"),
            "flash_attention": profile.get("flash_attention"),
            "kv_cache_type": profile.get("kv_cache_type_k"),
        },
        "hardware": {
            "gpu": best_gpu.get("name"),
            "vram_gb": round((best_gpu.get("vram_total_mb") or 0) / 1024.0, 1) if best_gpu.get("vram_total_mb") else None,
            "cpu": (hw.get("system") or {}).get("cpu_model"),
            "ram_gb": round(((hw.get("system") or {}).get("ram_total_mb") or 0) / 1024.0, 1) or None,
            "operating_system": platform.system(),
        },
        "fit": {
            "estimated_vram_gb": (fit.get("estimate_gb") or {}).get("required") if fit.get("success") else None,
            "observed_vram_gb": corr.get("last_sample_observed_gb"),
            "confidence": fit.get("confidence") if fit.get("success") else None,
        },
        "performance": _performance_from_run(bench_run) if bench_run else {},
        "benchmark": _benchmark_section(bench_run),
    }
    yaml_text = yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False)
    return {"success": True, "manifest": manifest, "yaml": yaml_text, "json": json.dumps(manifest, indent=2)}


# --- validate / recreate ------------------------------------------------------


class ManifestValidateRequest(BaseModel):
    manifest: Dict[str, Any]


def _compatibility_report(manifest: Dict[str, Any]) -> Dict[str, Any]:
    model = manifest.get("model") or {}
    deployment = manifest.get("deployment") or {}
    hardware_m = manifest.get("hardware") or {}
    model_id = str(model.get("name") or "")
    context = deployment.get("context_length") or 4096

    hw = detect_hardware()
    diffs: List[Dict[str, Any]] = []

    # Hardware: does the same context still fit on THIS machine?
    fit = fit_check(FitRequest(model_id=model_id, context=context, quant=model.get("quantization")))
    if fit.get("success"):
        fits_now = fit.get("severity") in ("ok", "soft")
        diffs.append(
            {
                "symbol": "ok" if fits_now else "bad",
                "text": f"Original context ({context}) "
                + ("fits comfortably here." if fit.get("severity") == "ok" else "fits here (tight)." if fits_now else f"will not fit comfortably here: {fit.get('headline')}"),
            }
        )
    else:
        diffs.append({"symbol": "unknown", "text": f"Could not verify fit on this machine: {fit.get('message')}"})

    current_gpus = hw.get("gpus") or []
    current_gpu_name = current_gpus[0].get("name") if current_gpus else None
    if hardware_m.get("gpu") and current_gpu_name and hardware_m["gpu"] != current_gpu_name:
        diffs.append({"symbol": "info", "text": f"Original GPU was {hardware_m['gpu']}; this machine has {current_gpu_name}."})

    # Runtime.
    installed, ollama_err = _ollama.list_installed()
    runtime_version, _ = _ollama.version()
    diffs.append(
        {"symbol": "ok" if ollama_err is None else "bad", "text": "Ollama is reachable." if ollama_err is None else f"Ollama is not reachable: {ollama_err}"}
    )
    if manifest.get("runtime", {}).get("version") and runtime_version and manifest["runtime"]["version"] != runtime_version:
        diffs.append({"symbol": "info", "text": f"Manifest was created with Ollama {manifest['runtime']['version']}; this machine has {runtime_version}."})

    # Model / digest availability.
    exact_digest = next((m for m in installed if model.get("digest") and m.get("digest") == model.get("digest")), None)
    same_name = next((m for m in installed if _matches_model_name(m.get("name"), model_id)), None)
    if exact_digest:
        diffs.append({"symbol": "ok", "text": f"Exact model digest is already installed as '{exact_digest.get('name')}'."})
        model_available = True
    elif same_name:
        diffs.append({"symbol": "info", "text": f"'{model_id}' is installed here, but under a different digest than the manifest recorded."})
        model_available = True
    else:
        diffs.append({"symbol": "info", "text": f"'{model_id}' is not installed here yet - it will need to be pulled."})
        model_available = False

    # Safe substitution: if the original context doesn't fit, does a smaller one?
    substitutions: List[str] = []
    if fit.get("success") and fit.get("severity") not in ("ok", "soft"):
        from .fit import FIT_TABLE_CONTEXTS

        for tier in FIT_TABLE_CONTEXTS:
            if tier >= context:
                continue
            tier_fit = fit_check(FitRequest(model_id=model_id, context=tier, quant=model.get("quantization")))
            if tier_fit.get("success") and tier_fit.get("severity") in ("ok", "soft"):
                substitutions.append(f"Same model fits at {tier} context instead of {context}.")
                break

    hard_blocked = fit.get("success") and fit.get("severity") == "hard"
    return {
        "diffs": diffs,
        "substitutions": substitutions,
        "model_available": model_available,
        "runtime_available": ollama_err is None,
        "can_recreate": not hard_blocked and ollama_err is None,
    }


@router.post("/system/manifest/validate")
def manifest_validate(req: ManifestValidateRequest) -> Dict[str, Any]:
    if not isinstance(req.manifest, dict) or not req.manifest.get("model"):
        return {"success": False, "error": "Invalid manifest: missing 'model' section."}
    report = _compatibility_report(req.manifest)
    return {"success": True, **report}


class ManifestRecreateRequest(BaseModel):
    manifest: Dict[str, Any]
    allow_pull: bool = True


@router.post("/system/manifest/recreate")
def manifest_recreate(req: ManifestRecreateRequest):
    from fastapi.responses import StreamingResponse

    def sse(event: Dict[str, Any]) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    def event_stream():
        if not isinstance(req.manifest, dict) or not req.manifest.get("model"):
            yield sse({"event": "error", "error": "Invalid manifest: missing 'model' section."})
            yield "data: [DONE]\n\n"
            return

        report = _compatibility_report(req.manifest)
        yield sse({"event": "validated", **report})
        if not report["can_recreate"]:
            yield sse({"event": "error", "error": "This manifest cannot be recreated on this machine (see compatibility report)."})
            yield "data: [DONE]\n\n"
            return

        model = req.manifest["model"]
        deployment = req.manifest.get("deployment") or {}
        model_id = str(model["name"])
        context = deployment.get("context_length") or 4096

        if not report["model_available"]:
            if not req.allow_pull:
                yield sse({"event": "error", "error": f"'{model_id}' is not installed and allow_pull is false."})
                yield "data: [DONE]\n\n"
                return
            yield sse({"event": "pull_start", "model": model_id})
            try:
                for progress in _ollama.pull_stream(model_id):
                    yield sse({"event": "pull_progress", **progress})
            except Exception as exc:
                yield sse({"event": "error", "error": f"Pull failed: {exc}"})
                yield "data: [DONE]\n\n"
                return
            yield sse({"event": "pull_end", "model": model_id})

        from ._config import ensure_profile_for_model
        from .models import _serve_ollama

        profile_name, _created, _err = ensure_profile_for_model(model_id)
        yield sse({"event": "serve_start", "model": model_id, "context": context})
        served = _serve_ollama(model_id, deployment.get("keep_alive") or "60m")
        if not served.get("success"):
            yield sse({"event": "error", "error": served.get("error") or "Serve failed."})
            yield "data: [DONE]\n\n"
            return

        running = served.get("running") or []
        match = next((m for m in running if _matches_model_name(m.get("name"), model_id)), None)
        actual_placement = match.get("placement") if match else None
        observed_gb = (match.get("size_vram") or 0) / 1_000_000_000.0 if match else None
        original_fit = req.manifest.get("fit") or {}
        yield sse(
            {
                "event": "recreate_end",
                "profile": profile_name,
                "model": model_id,
                "placement_observed": actual_placement,
                "placement_expected": deployment.get("placement_observed"),
                "observed_vram_gb": round(observed_gb, 2) if observed_gb is not None else None,
                "manifest_observed_vram_gb": original_fit.get("observed_vram_gb"),
                "manifest_estimated_vram_gb": original_fit.get("estimated_vram_gb"),
            }
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --- integration snippets ------------------------------------------------------

_INTEGRATIONS = ["open-webui", "anythingllm", "continue", "cline", "curl", "python", "javascript", "docker-compose"]


def _api_base(request_host: Optional[str] = None) -> str:
    from ..utils import api_client_base_url

    return api_client_base_url()


def _integration_snippet(kind: str, base_url: str, model_id: str, context: int, has_token: bool) -> Dict[str, str]:
    v1 = f"{base_url}/v1"
    api_key_line = "YOUR_API_TOKEN" if has_token else "not-needed"
    if kind == "open-webui":
        return {
            "label": "Open WebUI",
            "snippet": f"Settings -> Connections -> OpenAI API\nBase URL: {v1}\nAPI key: {api_key_line}\nModel: {model_id}",
        }
    if kind == "anythingllm":
        return {
            "label": "AnythingLLM",
            "snippet": f"LLM Provider: Generic OpenAI\nBase URL: {v1}\nAPI Key: {api_key_line}\nChat Model: {model_id}\nMax tokens: {context}",
        }
    if kind == "continue":
        return {
            "label": "Continue (VS Code)",
            "snippet": (
                'Add to ~/.continue/config.json models:\n'
                '{\n  "title": "' + model_id + '",\n  "provider": "openai",\n  "model": "' + model_id + '",\n'
                '  "apiBase": "' + v1 + '",\n  "apiKey": "' + api_key_line + '"\n}'
            ),
        }
    if kind == "cline":
        return {
            "label": "Cline",
            "snippet": f"API Provider: OpenAI Compatible\nBase URL: {v1}\nAPI Key: {api_key_line}\nModel ID: {model_id}",
        }
    if kind == "curl":
        token_flag = ' -H "X-API-Token: YOUR_API_TOKEN"' if has_token else ""
        return {
            "label": "curl",
            "snippet": (
                f'curl {base_url}/v1/chat/completions{token_flag} \\\n'
                f'  -H "Content-Type: application/json" \\\n'
                f'  -d \'{{"model": "{model_id}", "messages": [{{"role": "user", "content": "Hello"}}]}}\''
            ),
        }
    if kind == "python":
        return {
            "label": "Python (openai SDK)",
            "snippet": (
                "from openai import OpenAI\n"
                f'client = OpenAI(base_url="{v1}", api_key="{api_key_line}")\n'
                f'resp = client.chat.completions.create(model="{model_id}", messages=[{{"role": "user", "content": "Hello"}}])\n'
                "print(resp.choices[0].message.content)"
            ),
        }
    if kind == "javascript":
        return {
            "label": "JavaScript (openai SDK)",
            "snippet": (
                "import OpenAI from \"openai\";\n"
                f'const client = new OpenAI({{ baseURL: "{v1}", apiKey: "{api_key_line}" }});\n'
                f'const resp = await client.chat.completions.create({{ model: "{model_id}", messages: [{{ role: "user", content: "Hello" }}] }});\n'
                "console.log(resp.choices[0].message.content);"
            ),
        }
    if kind == "docker-compose":
        return {
            "label": "Docker Compose (external service)",
            "snippet": (
                "services:\n  my-app:\n    environment:\n"
                f'      - OPENAI_BASE_URL={v1}\n      - OPENAI_API_KEY={api_key_line}\n'
                f'      - OPENAI_MODEL={model_id}\n'
                "    extra_hosts:\n      - \"host.docker.internal:host-gateway\"  # if my-app itself runs in Docker"
            ),
        }
    return {"label": kind, "snippet": ""}


@router.get("/system/integration-snippets")
def integration_snippets(model: str, context: int = 8192) -> Dict[str, Any]:
    from ..utils import api_token

    base_url = _api_base()
    has_token = bool(api_token())
    cards = [_integration_snippet(kind, base_url, model, context, has_token) for kind in _INTEGRATIONS]
    return {
        "success": True,
        "base_url": base_url,
        "model": model,
        "context": context,
        "cards": cards,
    }
