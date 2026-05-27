from __future__ import annotations

import base64
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from localdeploy.backends.llamacpp import call_llamacpp, llama_health
from localdeploy.backends.ollama import call_ollama, ollama_models, stream_ollama
from localdeploy.utils import (
    BackendCallError,
    env_bool,
    env_float,
    env_int,
    get_backend_base_url,
    model_dump_compat,
    model_validate_compat,
    require_gpu_only,
)


APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")


class ChatRequest(BaseModel):
    profile: Optional[str] = None
    model: Optional[str] = None
    backend: Optional[str] = None
    prompt: str = ""
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    repeat_penalty: Optional[float] = None
    max_output_tokens: Optional[int] = None
    context_limit: Optional[int] = None
    safe_mode: bool = True
    allow_clamp: bool = False
    timeout_seconds: Optional[int] = None


class VisionRequest(ChatRequest):
    images_base64: Optional[List[str]] = None


class BenchmarkRequest(ChatRequest):
    all_profiles: bool = False
    prompts: Optional[List[str]] = None
    image_path: Optional[str] = None
    images_base64: Optional[List[str]] = None


class EstimateRequest(VisionRequest):
    pass


class OpenAIChatMessage(BaseModel):
    role: str
    content: Any


class OpenAIChatCompletionRequest(BaseModel):
    model: str = Field(default="gemma3_4b_ollama_safe")
    messages: List[OpenAIChatMessage]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    stream: bool = False
    response_format: Optional[Dict[str, Any]] = None
    profile: Optional[str] = None
    context_limit: Optional[int] = None
    safe_mode: bool = True
    allow_clamp: bool = False
    timeout_seconds: Optional[int] = None
    repeat_penalty: Optional[float] = None


class LocalLLMResponse(BaseModel):
    success: bool
    backend: Optional[str] = None
    profile: Optional[str] = None
    model: Optional[str] = None
    response: Optional[Any] = None
    elapsed_seconds: float = 0.0
    estimated_prompt_chars: int = 0
    estimated_prompt_tokens: int = 0
    context_limit_used: Optional[int] = None
    max_output_tokens_used: Optional[int] = None
    warning: Optional[str] = None
    error: Optional[str] = None


def get_config_path() -> Path:
    configured = os.getenv("CONFIG_PATH", "config.json")
    path = Path(configured)
    if not path.is_absolute():
        path = APP_DIR / path
    return path


def load_config() -> Dict[str, Any]:
    path = get_config_path()
    if not path.exists():
        path = APP_DIR / "config.example.json"
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_global_limits() -> Dict[str, Any]:
    return {
        "max_prompt_chars": env_int("GLOBAL_MAX_PROMPT_CHARS", 20000),
        "max_output_tokens": env_int("GLOBAL_MAX_OUTPUT_TOKENS", 2048),
        "max_images": env_int("GLOBAL_MAX_IMAGES", 1),
        "max_image_mb": env_float("GLOBAL_MAX_IMAGE_MB", 10.0),
        "request_timeout_seconds": env_int("REQUEST_TIMEOUT_SECONDS", 180),
        "slow_response_seconds": env_int("SLOW_RESPONSE_SECONDS", 60),
    }


def profile_warning(profile: Dict[str, Any]) -> Optional[str]:
    recommended = profile.get("recommended_for_8gb_vram")
    notes = str(profile.get("notes", "")).lower()
    name = profile.get("name", "selected profile")
    if recommended == "experimental" or "experimental" in notes:
        return f"{name} is experimental on 8 GB VRAM; benchmark stability before relying on it."
    if recommended == "fallback":
        return f"{name} is a fallback profile and may be much slower due to partial CPU offload."
    if recommended is False:
        return f"{name} is not the recommended 8 GB VRAM default; use safe mode and conservative context."
    return None


def estimate_tokens_from_chars(char_count: int) -> int:
    return max(1, math.ceil(char_count / 4))


def normalize_images(images: Optional[Any]) -> List[str]:
    if images is None:
        return []
    if isinstance(images, str):
        return [images]
    return [str(item) for item in images if item is not None]


def extract_openai_content(content: Any) -> Tuple[str, List[str]]:
    if isinstance(content, str):
        return content, []

    if not isinstance(content, list):
        return str(content), []

    text_parts: List[str] = []
    images: List[str] = []
    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
            continue
        if not isinstance(item, dict):
            text_parts.append(str(item))
            continue

        item_type = item.get("type")
        if item_type == "text":
            text_parts.append(str(item.get("text", "")))
        elif item_type == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                url = str(image_url.get("url", ""))
            else:
                url = str(image_url or "")
            if url.startswith("data:") and "," in url:
                images.append(url)
        elif item_type == "input_text":
            text_parts.append(str(item.get("text", "")))
        elif item_type == "input_image":
            image_value = str(item.get("image_url") or item.get("image_base64") or "")
            if image_value:
                images.append(image_value)

    return "\n".join(part for part in text_parts if part), images


def clean_base64_image(value: str) -> str:
    cleaned = "".join(str(value).split())
    if cleaned.startswith("data:") and "," in cleaned:
        cleaned = cleaned.split(",", 1)[1]
    return cleaned


def decoded_image_size_bytes(value: str) -> int:
    cleaned = clean_base64_image(value)
    try:
        return len(base64.b64decode(cleaned, validate=True))
    except Exception as exc:
        raise ValueError("Image input is not valid base64.") from exc


def clamp_or_error(
    requested: int,
    allowed: int,
    field_name: str,
    allow_clamp: bool,
    errors: List[str],
) -> int:
    if requested <= 0:
        errors.append(f"{field_name} must be greater than zero.")
        return requested
    if requested > allowed:
        if allow_clamp:
            return allowed
        errors.append(f"{field_name} {requested} exceeds configured limit {allowed}. Set allow_clamp=true to clamp safely.")
    return requested


def resolve_profile(
    config: Dict[str, Any],
    request_data: Dict[str, Any],
    require_enabled: bool = True,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    profiles = config.get("profiles", {})
    profile_name = (
        request_data.get("profile")
        or os.getenv("DEFAULT_MODEL_PROFILE")
        or config.get("default_profile")
        or "gemma3_4b_ollama_safe"
    )
    if profile_name not in profiles:
        return None, None, f"Unknown profile '{profile_name}'. Check config.json."

    profile = dict(profiles[profile_name])
    profile["name"] = profile.get("name") or profile_name

    if require_enabled and not profile.get("enabled", False):
        return profile_name, profile, f"Profile '{profile_name}' is disabled in config.json."

    requested_backend = request_data.get("backend")
    if requested_backend:
        backend = str(requested_backend).strip().lower()
        if backend not in {"ollama", "llamacpp"}:
            return profile_name, profile, "backend must be 'ollama' or 'llamacpp'."
        profile["backend"] = backend
        if backend == "ollama":
            profile["base_url"] = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        else:
            profile["base_url"] = os.getenv("LLAMACPP_BASE_URL", "http://localhost:8080")

    requested_model = request_data.get("model")
    if requested_model:
        profile["model_id"] = str(requested_model)

    backend = str(profile.get("backend", "")).lower()
    if backend not in {"ollama", "llamacpp"}:
        return profile_name, profile, f"Unsupported backend '{backend}' in profile '{profile_name}'."

    if require_gpu_only():
        gpu_layers = str(profile.get("gpu_layers") or "").strip().lower()
        if backend != "llamacpp":
            return profile_name, profile, (
                f"GPU-only mode is enabled, but profile '{profile_name}' uses backend '{backend}'. "
                "Select a llama.cpp profile with gpu_layers=all."
            )
        if gpu_layers != "all":
            return profile_name, profile, (
                f"GPU-only mode is enabled, but profile '{profile_name}' has gpu_layers={profile.get('gpu_layers')!r}. "
                "Set gpu_layers to 'all' so startup fails instead of falling back to CPU."
            )

    if backend == "llamacpp" and not env_bool("ENABLE_LLAMA_CPP", False):
        return (
            profile_name,
            profile,
            "llama.cpp backend is disabled. Set ENABLE_LLAMA_CPP=true after starting a local llama-server.",
        )

    try:
        profile["base_url"] = get_backend_base_url(profile, backend)
    except BackendCallError as exc:
        return profile_name, profile, str(exc)

    return profile_name, profile, None


def profile_for_openai_model(config: Dict[str, Any], model_name: str) -> Tuple[str, Optional[str]]:
    profiles = config.get("profiles", {})
    if model_name in profiles:
        return model_name, None

    for profile_name, profile in profiles.items():
        if str(profile.get("model_id")) == model_name:
            return profile_name, None

    default_profile = (
        os.getenv("DEFAULT_MODEL_PROFILE")
        or config.get("default_profile")
        or "gemma3_4b_ollama_safe"
    )
    return default_profile, model_name


def openai_request_to_local_payload(request_model: OpenAIChatCompletionRequest) -> Tuple[Dict[str, Any], bool]:
    config = load_config()
    requested_profile, model_override = profile_for_openai_model(config, request_model.profile or request_model.model)
    system_parts: List[str] = []
    conversation_parts: List[str] = []
    images: List[str] = []

    for message in request_model.messages:
        role = message.role.lower()
        text, message_images = extract_openai_content(message.content)
        images.extend(message_images)
        if role == "system":
            if text:
                system_parts.append(text)
        else:
            if text:
                conversation_parts.append(f"{role}: {text}" if role != "user" else text)

    prompt = "\n\n".join(conversation_parts).strip()
    if request_model.response_format:
        prompt = f"{prompt}\n\n{response_format_instruction(request_model.response_format)}".strip()

    payload: Dict[str, Any] = {
        "profile": requested_profile,
        "prompt": prompt,
        "system_prompt": "\n\n".join(system_parts).strip() or None,
        "temperature": request_model.temperature,
        "top_p": request_model.top_p,
        "repeat_penalty": request_model.repeat_penalty,
        "max_output_tokens": request_model.max_tokens or request_model.max_completion_tokens,
        "context_limit": request_model.context_limit,
        "safe_mode": request_model.safe_mode,
        "allow_clamp": request_model.allow_clamp,
        "timeout_seconds": request_model.timeout_seconds,
    }
    if model_override:
        payload["model"] = model_override
    if images:
        payload["images_base64"] = images
    return payload, bool(images)


def response_format_instruction(response_format: Dict[str, Any]) -> str:
    format_type = response_format.get("type")
    if format_type == "json_schema":
        json_schema = response_format.get("json_schema") or {}
        schema = json_schema.get("schema") or {}
        required = schema.get("required") or []
        properties = schema.get("properties") or {}
        property_lines = []
        for key, details in properties.items():
            if isinstance(details, dict):
                value_type = details.get("type", "value")
                description = details.get("description")
                suffix = f" - {description}" if description else ""
                property_lines.append(f"- {key}: {value_type}{suffix}")
            else:
                property_lines.append(f"- {key}")
        return (
            "Return only valid JSON. Do not use markdown or code fences. "
            "Return a data object that conforms to this schema; do not return the schema itself. "
            f"Required keys: {', '.join(required) if required else 'use the schema properties'}. "
            f"Properties:\n{chr(10).join(property_lines) if property_lines else json.dumps(properties, ensure_ascii=False)}"
        )
    if format_type == "json_object":
        return "Return only one valid JSON object. Do not use markdown or code fences."
    return (
        "Return only valid JSON when possible. Do not use markdown or code fences. "
        f"Response format request: {json.dumps(response_format, ensure_ascii=False)}"
    )


def prepare_request(
    kind: str,
    request_data: Dict[str, Any],
    require_enabled: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    config = load_config()
    limits = get_global_limits()
    errors: List[str] = []

    model_cls = VisionRequest if kind == "vision" else ChatRequest
    if kind == "benchmark":
        model_cls = BenchmarkRequest
    if kind == "estimate":
        model_cls = EstimateRequest

    try:
        request_model = model_validate_compat(model_cls, request_data)
    except Exception as exc:
        return None, make_error_response(error=f"Invalid request schema: {exc}")

    data = model_dump_compat(request_model)
    data["images_base64"] = normalize_images(data.get("images_base64"))
    profile_name, profile, profile_error = resolve_profile(config, data, require_enabled=require_enabled)
    if profile_error:
        return None, make_error_response(
            error=profile_error,
            backend=profile.get("backend") if profile else data.get("backend"),
            profile=profile_name,
            model=profile.get("model_id") if profile else data.get("model"),
            warning=profile_warning(profile) if profile else None,
        )

    assert profile is not None
    backend = str(profile.get("backend")).lower()
    prompt = data.get("prompt") or ""
    system_prompt = data.get("system_prompt") or ""
    estimated_prompt_chars = len(prompt) + len(system_prompt)
    estimated_prompt_tokens = estimate_tokens_from_chars(estimated_prompt_chars)
    allow_clamp = bool(data.get("allow_clamp", False))
    safe_mode = bool(data.get("safe_mode", True))

    profile_prompt_limit = int(profile.get("max_prompt_chars") or limits["max_prompt_chars"])
    prompt_limit = min(profile_prompt_limit, int(limits["max_prompt_chars"]))
    if estimated_prompt_chars > prompt_limit:
        errors.append(
            f"Prompt is too large: {estimated_prompt_chars} characters exceeds configured limit {prompt_limit}."
        )

    profile_context_limit = int(profile.get("context_limit") or 2048)
    profile_safe_context_limit = int(profile.get("safe_context_limit") or profile_context_limit)
    allowed_context = min(profile_context_limit, profile_safe_context_limit) if safe_mode else profile_context_limit
    requested_context = int(data.get("context_limit") or allowed_context)
    context_limit_used = clamp_or_error(
        requested_context,
        allowed_context,
        "context_limit",
        allow_clamp,
        errors,
    )

    profile_output_limit = int(profile.get("max_output_tokens") or limits["max_output_tokens"])
    allowed_output = min(profile_output_limit, int(limits["max_output_tokens"]))
    requested_output = int(data.get("max_output_tokens") or allowed_output)
    max_output_tokens_used = clamp_or_error(
        requested_output,
        allowed_output,
        "max_output_tokens",
        allow_clamp,
        errors,
    )

    images = data.get("images_base64") or []
    if images:
        if not profile.get("supports_vision", False):
            errors.append(f"Profile '{profile_name}' does not support vision requests.")
        if len(images) > int(limits["max_images"]):
            errors.append(f"Too many images: {len(images)} exceeds configured limit {limits['max_images']}.")
        max_image_bytes = float(limits["max_image_mb"]) * 1024 * 1024
        cleaned_images: List[str] = []
        for idx, image in enumerate(images, start=1):
            try:
                image_size = decoded_image_size_bytes(image)
            except ValueError as exc:
                errors.append(f"Image {idx}: {exc}")
                continue
            if image_size > max_image_bytes:
                errors.append(
                    f"Image {idx} is too large: {image_size / (1024 * 1024):.2f} MB exceeds "
                    f"configured limit {limits['max_image_mb']} MB."
                )
            cleaned_images.append(clean_base64_image(image))
        data["images_base64"] = cleaned_images

    timeout_seconds = int(data.get("timeout_seconds") or profile.get("timeout_seconds") or limits["request_timeout_seconds"])
    if timeout_seconds <= 0:
        errors.append("timeout_seconds must be greater than zero.")

    if errors:
        return None, make_error_response(
            error=" ".join(errors),
            backend=backend,
            profile=profile_name,
            model=str(profile.get("model_id")),
            estimated_prompt_chars=estimated_prompt_chars,
            estimated_prompt_tokens=estimated_prompt_tokens,
            context_limit_used=context_limit_used,
            max_output_tokens_used=max_output_tokens_used,
            warning=profile_warning(profile),
        )

    prepared = {
        "kind": kind,
        "profile_name": profile_name,
        "profile": profile,
        "backend": backend,
        "model": str(profile.get("model_id")),
        "prompt": prompt,
        "system_prompt": system_prompt,
        "temperature": data.get("temperature") if data.get("temperature") is not None else profile.get("temperature"),
        "top_p": data.get("top_p") if data.get("top_p") is not None else profile.get("top_p"),
        "repeat_penalty": data.get("repeat_penalty")
        if data.get("repeat_penalty") is not None
        else profile.get("repeat_penalty"),
        "context_limit_used": context_limit_used,
        "max_output_tokens_used": max_output_tokens_used,
        "timeout_seconds": timeout_seconds,
        "estimated_prompt_chars": estimated_prompt_chars,
        "estimated_prompt_tokens": estimated_prompt_tokens,
        "images_base64": data.get("images_base64") or [],
        "safe_mode": safe_mode,
        "warning": profile_warning(profile),
        "slow_response_seconds": int(profile.get("slow_response_seconds") or limits["slow_response_seconds"]),
    }
    return prepared, None


def make_error_response(
    error: str,
    backend: Optional[str] = None,
    profile: Optional[str] = None,
    model: Optional[str] = None,
    elapsed_seconds: float = 0.0,
    estimated_prompt_chars: int = 0,
    estimated_prompt_tokens: int = 0,
    context_limit_used: Optional[int] = None,
    max_output_tokens_used: Optional[int] = None,
    warning: Optional[str] = None,
) -> Dict[str, Any]:
    return model_dump_compat(
        LocalLLMResponse(
            success=False,
            backend=backend,
            profile=profile,
            model=model,
            response=None,
            elapsed_seconds=round(elapsed_seconds, 3),
            estimated_prompt_chars=estimated_prompt_chars,
            estimated_prompt_tokens=estimated_prompt_tokens,
            context_limit_used=context_limit_used,
            max_output_tokens_used=max_output_tokens_used,
            warning=warning,
            error=error,
        )
    )


def make_success_response(prepared: Dict[str, Any], content: Any, elapsed_seconds: float) -> Dict[str, Any]:
    warning = prepared.get("warning")
    if elapsed_seconds >= prepared.get("slow_response_seconds", 60):
        slow_warning = f"Response exceeded slow threshold of {prepared.get('slow_response_seconds')} seconds."
        warning = f"{warning} {slow_warning}" if warning else slow_warning
    return model_dump_compat(
        LocalLLMResponse(
            success=True,
            backend=prepared["backend"],
            profile=prepared["profile_name"],
            model=prepared["model"],
            response=content,
            elapsed_seconds=round(elapsed_seconds, 3),
            estimated_prompt_chars=prepared["estimated_prompt_chars"],
            estimated_prompt_tokens=prepared["estimated_prompt_tokens"],
            context_limit_used=prepared["context_limit_used"],
            max_output_tokens_used=prepared["max_output_tokens_used"],
            warning=warning,
            error=None,
        )
    )


def run_local_request(kind: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
    prepared, error_response = prepare_request(kind, request_data, require_enabled=True)
    if error_response:
        return error_response
    assert prepared is not None

    start = time.perf_counter()
    try:
        if prepared["backend"] == "ollama":
            content = call_ollama(prepared)
        elif prepared["backend"] == "llamacpp":
            content = call_llamacpp(prepared)
        else:
            raise BackendCallError(f"Unsupported backend '{prepared['backend']}'.")
    except BackendCallError as exc:
        elapsed = time.perf_counter() - start
        return make_error_response(
            error=str(exc),
            backend=prepared["backend"],
            profile=prepared["profile_name"],
            model=prepared["model"],
            elapsed_seconds=elapsed,
            estimated_prompt_chars=prepared["estimated_prompt_chars"],
            estimated_prompt_tokens=prepared["estimated_prompt_tokens"],
            context_limit_used=prepared["context_limit_used"],
            max_output_tokens_used=prepared["max_output_tokens_used"],
            warning=prepared.get("warning"),
        )
    elapsed = time.perf_counter() - start
    return make_success_response(prepared, content, elapsed)


def estimate_request_safety(request_data: Dict[str, Any]) -> Dict[str, Any]:
    prepared, error_response = prepare_request("estimate", request_data, require_enabled=False)
    if error_response:
        error_response["response"] = {
            "likely_safe": False,
            "errors": [error_response.get("error")],
            "warnings": [error_response.get("warning")] if error_response.get("warning") else [],
        }
        return error_response

    assert prepared is not None
    warnings: List[str] = []
    profile = prepared["profile"]
    limits = get_global_limits()
    profile_prompt_limit = min(int(profile.get("max_prompt_chars") or limits["max_prompt_chars"]), limits["max_prompt_chars"])
    prompt_ratio = prepared["estimated_prompt_chars"] / max(1, profile_prompt_limit)
    if prompt_ratio >= 0.8:
        warnings.append("Prompt is close to the configured character limit.")

    estimated_total_tokens = prepared["estimated_prompt_tokens"] + prepared["max_output_tokens_used"]
    if estimated_total_tokens > prepared["context_limit_used"]:
        warnings.append(
            "Estimated prompt plus output tokens may exceed the selected context. Tokenization is approximate; lower prompt size or output tokens if generation fails."
        )
    if prepared.get("warning"):
        warnings.append(prepared["warning"])

    response = {
        "likely_safe": True,
        "errors": [],
        "warnings": warnings,
        "selected_profile": prepared["profile_name"],
        "backend": prepared["backend"],
        "model": prepared["model"],
        "safe_mode": prepared["safe_mode"],
        "estimated_prompt_chars": prepared["estimated_prompt_chars"],
        "estimated_prompt_tokens": prepared["estimated_prompt_tokens"],
        "estimated_total_tokens_with_output": estimated_total_tokens,
        "context_limit_used": prepared["context_limit_used"],
        "max_output_tokens_used": prepared["max_output_tokens_used"],
        "global_limits": limits,
    }
    return make_success_response(prepared, response, 0.0)


def benchmark_prompts() -> List[str]:
    return [
        "Explain what this local LLM server is doing in 3 bullet points.",
        "A laptop can process 18 images per minute. How long will it take to process 153 images? Show your reasoning briefly.",
        "Return only valid JSON with keys: model_capability, strengths, weaknesses, recommended_use. No markdown.",
    ]


def run_benchmark(request_data: Dict[str, Any]) -> Dict[str, Any]:
    config = load_config()
    request_model = model_validate_compat(BenchmarkRequest, request_data)
    data = model_dump_compat(request_model)
    prompts = data.get("prompts") or ([data["prompt"]] if data.get("prompt") else benchmark_prompts())
    if data.get("all_profiles"):
        profile_names = [
            name for name, profile in config.get("profiles", {}).items() if profile.get("enabled", False)
        ]
    else:
        profile_names = [
            data.get("profile")
            or os.getenv("DEFAULT_MODEL_PROFILE")
            or config.get("default_profile")
            or "gemma3_4b_ollama_safe"
        ]

    if not profile_names:
        return make_error_response(error="No enabled profiles are available in config.json.", profile="all")

    started = time.perf_counter()
    results: List[Dict[str, Any]] = []
    for profile_name in profile_names:
        profile_results: List[Dict[str, Any]] = []
        for prompt in prompts:
            call_data = dict(data)
            call_data["profile"] = profile_name
            call_data["prompt"] = prompt
            call_data["images_base64"] = data.get("images_base64") or []
            result = run_local_request("vision" if call_data["images_base64"] else "chat", call_data)
            profile_results.append(
                {
                    "prompt": prompt[:120],
                    "success": result.get("success"),
                    "elapsed_seconds": result.get("elapsed_seconds"),
                    "response_length": len(str(result.get("response") or "")),
                    "warning": result.get("warning"),
                    "error": result.get("error"),
                }
            )
        successes = sum(1 for item in profile_results if item["success"])
        failures = len(profile_results) - successes
        elapsed_values = [float(item["elapsed_seconds"] or 0.0) for item in profile_results if item["success"]]
        average_elapsed = sum(elapsed_values) / len(elapsed_values) if elapsed_values else None
        results.append(
            {
                "profile": profile_name,
                "successes": successes,
                "failures": failures,
                "average_elapsed_seconds": round(average_elapsed, 3) if average_elapsed is not None else None,
                "tests": profile_results,
            }
        )

    elapsed = time.perf_counter() - started
    response = {
        "profiles_tested": profile_names,
        "results": results,
    }
    top_backend = "mixed" if len(profile_names) > 1 else None
    return model_dump_compat(
        LocalLLMResponse(
            success=any(item["successes"] > 0 for item in results),
            backend=top_backend,
            profile="all" if len(profile_names) > 1 else profile_names[0],
            model=None,
            response=response,
            elapsed_seconds=round(elapsed, 3),
            estimated_prompt_chars=sum(len(prompt) for prompt in prompts),
            estimated_prompt_tokens=estimate_tokens_from_chars(sum(len(prompt) for prompt in prompts)),
            context_limit_used=None,
            max_output_tokens_used=data.get("max_output_tokens"),
            warning=None,
            error=None,
        )
    )


def sanitize_profiles(config: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for name, profile in config.get("profiles", {}).items():
        item = dict(profile)
        item["warning"] = profile_warning(item)
        sanitized[name] = item
    return sanitized


def openai_error_status(error: str) -> int:
    lowered = error.lower()
    if (
        "too large" in lowered
        or "exceeds configured limit" in lowered
        or "invalid" in lowered
        or "streaming is not implemented" in lowered
    ):
        return 400
    if "not running" in lowered or "unreachable" in lowered or "not reachable" in lowered:
        return 503
    if "not available" in lowered or "not found" in lowered or "disabled" in lowered:
        return 404
    return 502


def openai_error_response(result: Dict[str, Any]) -> JSONResponse:
    error = str(result.get("error") or "LocalDeploy request failed.")
    return JSONResponse(
        status_code=openai_error_status(error),
        content={
            "error": {
                "message": error,
                "type": "localdeploy_error",
                "param": None,
                "code": "localdeploy_request_failed",
            },
            "localdeploy": {
                "backend": result.get("backend"),
                "profile": result.get("profile"),
                "model": result.get("model"),
                "warning": result.get("warning"),
                "context_limit_used": result.get("context_limit_used"),
                "max_output_tokens_used": result.get("max_output_tokens_used"),
            },
        },
    )


def openai_sse_chunk(completion_id: str, model_name: str, delta: Dict[str, Any], finish_reason: Optional[str]) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def openai_sse_error(completion_id: str, model_name: str, message: str) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
        "error": {
            "message": message,
            "type": "localdeploy_error",
            "code": "localdeploy_request_failed",
        },
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def openai_stream_response(request_model: OpenAIChatCompletionRequest) -> StreamingResponse:
    completion_id = f"chatcmpl-localdeploy-{int(time.time() * 1000)}"
    model_name = request_model.model

    payload, has_images = openai_request_to_local_payload(request_model)
    prepared, error_response = prepare_request("vision" if has_images else "chat", payload, require_enabled=True)

    def emit() -> Iterator[str]:
        if error_response:
            yield openai_sse_error(completion_id, model_name, str(error_response.get("error") or "LocalDeploy request failed."))
            yield "data: [DONE]\n\n"
            return
        assert prepared is not None

        if prepared["backend"] != "ollama":
            try:
                content = call_llamacpp(prepared)
            except BackendCallError as exc:
                yield openai_sse_error(completion_id, model_name, str(exc))
                yield "data: [DONE]\n\n"
                return
            yield openai_sse_chunk(completion_id, model_name, {"role": "assistant"}, None)
            if content:
                yield openai_sse_chunk(completion_id, model_name, {"content": content}, None)
            yield openai_sse_chunk(completion_id, model_name, {}, "stop")
            yield "data: [DONE]\n\n"
            return

        yield openai_sse_chunk(completion_id, model_name, {"role": "assistant"}, None)
        try:
            for chunk in stream_ollama(prepared):
                if chunk:
                    yield openai_sse_chunk(completion_id, model_name, {"content": chunk}, None)
        except BackendCallError as exc:
            yield openai_sse_error(completion_id, model_name, str(exc))
            yield "data: [DONE]\n\n"
            return
        yield openai_sse_chunk(completion_id, model_name, {}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(emit(), media_type="text/event-stream")


def openai_chat_completion_response(request_model: OpenAIChatCompletionRequest, result: Dict[str, Any]) -> Dict[str, Any]:
    content = str(result.get("response") or "")
    if request_model.response_format:
        content = normalize_structured_content(content)
    prompt_tokens = int(result.get("estimated_prompt_tokens") or estimate_tokens_from_chars(result.get("estimated_prompt_chars") or 0))
    completion_tokens = estimate_tokens_from_chars(len(content)) if content else 0
    return {
        "id": f"chatcmpl-localdeploy-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request_model.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "localdeploy": {
            "backend": result.get("backend"),
            "profile": result.get("profile"),
            "model": result.get("model"),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "warning": result.get("warning"),
            "context_limit_used": result.get("context_limit_used"),
            "max_output_tokens_used": result.get("max_output_tokens_used"),
        },
    }


def normalize_structured_content(content: str) -> str:
    stripped = content.strip()
    fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.IGNORECASE | re.DOTALL)
    candidates = [fence_match.group(1).strip()] if fence_match else []
    candidates.append(stripped)

    object_match = re.search(r"(\{.*\})", stripped, flags=re.DOTALL)
    if object_match:
        candidates.append(object_match.group(1).strip())
    array_match = re.search(r"(\[.*\])", stripped, flags=re.DOTALL)
    if array_match:
        candidates.append(array_match.group(1).strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        return json.dumps(parsed, ensure_ascii=False)
    return content


app = FastAPI(title="Local LLM Server", version="1.0.0")

if env_bool("ENABLE_CORS", False):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1", "http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health")
def health() -> Dict[str, Any]:
    config = load_config()
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    if require_gpu_only():
        models, ollama_error = [], "disabled by GPU-only mode"
        ollama_reachable = False
    else:
        models, ollama_error = ollama_models(ollama_base)
        ollama_reachable = ollama_error is None
    llama_base = os.getenv("LLAMACPP_BASE_URL", "http://localhost:8080")
    llama_status = llama_health(llama_base) if env_bool("ENABLE_LLAMA_CPP", False) else {"enabled": False}
    return {
        "success": True,
        "server": "ok",
        "config_path": str(get_config_path()),
        "default_profile": os.getenv("DEFAULT_MODEL_PROFILE") or config.get("default_profile"),
        "require_gpu_only": require_gpu_only(),
        "ollama": {
            "base_url": ollama_base,
            "reachable": ollama_reachable,
            "models": models,
            "error": ollama_error,
        },
        "llamacpp": llama_status,
        "limits": get_global_limits(),
    }


@app.get("/models")
def models() -> Dict[str, Any]:
    config = load_config()
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    if require_gpu_only():
        models_available, ollama_error = [], "disabled by GPU-only mode"
    else:
        models_available, ollama_error = ollama_models(ollama_base)
    llama_profiles = [
        {
            "profile": name,
            "model_id": profile.get("model_id"),
            "enabled": profile.get("enabled", False),
            "recommended_for_8gb_vram": profile.get("recommended_for_8gb_vram"),
            "quantization": profile.get("quantization"),
        }
        for name, profile in config.get("profiles", {}).items()
        if profile.get("backend") == "llamacpp"
    ]
    return {
        "success": True,
        "ollama": {
            "base_url": ollama_base,
            "models": models_available,
            "error": ollama_error,
        },
        "llamacpp_profiles": llama_profiles,
    }


@app.get("/v1/models")
def openai_models() -> Dict[str, Any]:
    config = load_config()
    data = []
    for name, profile in config.get("profiles", {}).items():
        if not profile.get("enabled", False):
            continue
        data.append(
            {
                "id": name,
                "object": "model",
                "created": 0,
                "owned_by": "localdeploy",
                "localdeploy": {
                    "backend": profile.get("backend"),
                    "model_id": profile.get("model_id"),
                    "supports_vision": profile.get("supports_vision"),
                    "recommended_for_8gb_vram": profile.get("recommended_for_8gb_vram"),
                    "warning": profile_warning(profile),
                },
            }
        )
    return {"object": "list", "data": data}


@app.get("/profiles")
def profiles() -> Dict[str, Any]:
    config = load_config()
    return {
        "success": True,
        "default_profile": os.getenv("DEFAULT_MODEL_PROFILE") or config.get("default_profile"),
        "profiles": sanitize_profiles(config),
    }


@app.post("/estimate", response_model=LocalLLMResponse)
def estimate(request: EstimateRequest) -> Dict[str, Any]:
    return estimate_request_safety(model_dump_compat(request))


@app.post("/chat", response_model=LocalLLMResponse)
def chat(request: ChatRequest) -> Dict[str, Any]:
    return run_local_request("chat", model_dump_compat(request))


@app.post("/v1/chat/completions", response_model=None)
def openai_chat_completions(request: OpenAIChatCompletionRequest) -> Any:
    if request.stream:
        return openai_stream_response(request)
    payload, has_images = openai_request_to_local_payload(request)
    result = run_local_request("vision" if has_images else "chat", payload)
    if not result.get("success"):
        return openai_error_response(result)
    return openai_chat_completion_response(request, result)


@app.post("/v1/embeddings")
def openai_embeddings(_request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "message": (
                    "LocalDeploy does not implement /v1/embeddings. "
                    "Call Ollama directly at POST http://localhost:11434/api/embeddings, "
                    "or run a dedicated embedding server."
                ),
                "type": "localdeploy_not_implemented",
                "code": "embeddings_not_implemented",
            }
        },
    )


def parse_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        if lowered.isdigit():
            try:
                return int(lowered)
            except ValueError:
                return value
        try:
            return float(value)
        except ValueError:
            return value
    return value


@app.post("/vision", response_model=LocalLLMResponse)
async def vision(request: Request) -> Dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            data: Dict[str, Any] = {}
            images: List[str] = []
            for key, value in form.multi_items():
                if hasattr(value, "read"):
                    file_bytes = await value.read()
                    images.append(base64.b64encode(file_bytes).decode("ascii"))
                elif key == "images_base64":
                    images.append(str(value))
                else:
                    data[key] = parse_scalar(value)
            data["images_base64"] = images
        else:
            data = await request.json()
    except Exception as exc:
        return make_error_response(error=f"Invalid vision request body: {exc}")

    return run_local_request("vision", data)


@app.post("/benchmark", response_model=LocalLLMResponse)
def benchmark(request: BenchmarkRequest) -> Dict[str, Any]:
    return run_benchmark(model_dump_compat(request))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_server:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=env_int("API_PORT", 8000),
        reload=False,
    )
