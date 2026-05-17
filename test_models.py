from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from api_server import get_global_limits, load_config, run_local_request


APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")


BASIC_PROMPT = "Explain what this local LLM server is doing in 3 bullet points."
REASONING_PROMPT = (
    "A laptop can process 18 images per minute. How long will it take to process 153 images? "
    "Show your reasoning briefly."
)
CODING_PROMPT = (
    "Write a Python function that validates whether a string is valid JSON and returns a tuple "
    "of success and error message."
)
JSON_PROMPT = "Return only valid JSON with keys: model_capability, strengths, weaknesses, recommended_use. No markdown."
LOCAL_API_PROMPT = (
    "You are being used inside a local desktop app. Give a short, practical answer explaining how the app should call your API."
)
VISION_PROMPT = "Describe the image accurately. Then extract any visible text. Then list any uncertainty."


def parse_bool(value: str) -> bool:
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes", "y", "on"}:
        return True
    if lowered in {"false", "0", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("Expected true or false.")


def server_base_url() -> str:
    host = os.getenv("API_HOST", "127.0.0.1")
    port = os.getenv("API_PORT", "8000")
    return f"http://{host}:{port}"


def is_server_running() -> bool:
    try:
        response = requests.get(f"{server_base_url()}/health", timeout=10)
        return response.ok
    except requests.RequestException:
        return False


def enabled_profiles(config: Dict[str, Any]) -> List[str]:
    return [name for name, profile in config.get("profiles", {}).items() if profile.get("enabled", False)]


def prompt_limit_for_profile(profile: Dict[str, Any]) -> int:
    global_limit = int(get_global_limits()["max_prompt_chars"])
    profile_limit = int(profile.get("max_prompt_chars") or global_limit)
    return min(global_limit, profile_limit)


def make_long_context_prompt(profile: Dict[str, Any]) -> str:
    limit = prompt_limit_for_profile(profile)
    prefix = "Summarize this synthetic local deployment test document in 5 concise bullets.\n\n"
    suffix = "\n\nFocus on the deployment purpose, safety limits, model profiles, and benchmark strategy."
    section = (
        "Section {n}: This Windows local LLM deployment runs only against localhost. "
        "It uses Ollama as the default backend, keeps Gemma 3 4B as the stable profile, "
        "tests Gemma 3 12B with conservative context, and supports optional llama.cpp GGUF profiles "
        "for quantization, GPU layer, KV cache, and context experiments.\n"
    )
    budget = max(500, min(limit - len(prefix) - len(suffix) - 50, 9000))
    document = ""
    n = 1
    while len(document) < budget:
        document += section.format(n=n)
        n += 1
    document = document[:budget]
    return f"{prefix}{document}{suffix}"


def test_prompts_for_profile(profile: Dict[str, Any]) -> List[Tuple[str, str]]:
    return [
        ("basic", BASIC_PROMPT),
        ("reasoning", REASONING_PROMPT),
        ("coding", CODING_PROMPT),
        ("long_context", make_long_context_prompt(profile)),
        ("json_compliance", JSON_PROMPT),
        ("local_api_use", LOCAL_API_PROMPT),
    ]


def image_to_base64(path: Path) -> Tuple[Optional[str], Optional[str]]:
    if not path.exists():
        return None, f"Invalid image path: {path}"
    if not path.is_file():
        return None, f"Image path is not a file: {path}"
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii"), None
    except OSError as exc:
        return None, f"Could not read image file '{path}': {exc}"


def call_server(endpoint: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    url = f"{server_base_url()}/{endpoint.lstrip('/')}"
    try:
        response = requests.post(url, json=payload, timeout=timeout + 10)
    except requests.Timeout:
        return {
            "success": False,
            "elapsed_seconds": timeout,
            "error": f"Local API server request timed out after {timeout} seconds.",
        }
    except requests.ConnectionError:
        return {
            "success": False,
            "elapsed_seconds": 0,
            "error": f"Local API server is not reachable at {server_base_url()}.",
        }
    try:
        data = response.json()
    except ValueError:
        return {
            "success": False,
            "elapsed_seconds": 0,
            "error": f"Server returned non-JSON HTTP {response.status_code}: {response.text[:500]}",
        }
    if not response.ok:
        return {
            "success": False,
            "elapsed_seconds": data.get("elapsed_seconds", 0),
            "error": data.get("detail") or data.get("error") or f"HTTP {response.status_code}",
        }
    return data


def call_model(use_server: bool, endpoint: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    if use_server:
        return call_server(endpoint, payload, timeout)
    return run_local_request("vision" if endpoint == "vision" else "chat", payload)


def response_text(result: Dict[str, Any]) -> str:
    value = result.get("response")
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def strict_json_ok(text: str) -> bool:
    try:
        json.loads(text.strip())
        return True
    except Exception:
        return False


def quality_score(test_name: str, text: str) -> float:
    lowered = text.lower()
    if not text.strip():
        return 0.0
    if test_name == "json_compliance":
        return 1.0 if strict_json_ok(text) else 0.2
    if test_name == "reasoning":
        if "8.5" in lowered or ("8" in lowered and "30" in lowered):
            return 1.0
        return 0.5 if "153" in lowered and "18" in lowered else 0.2
    if test_name == "coding":
        score = 0.0
        score += 0.35 if "json.loads" in lowered else 0.0
        score += 0.25 if "except" in lowered else 0.0
        score += 0.2 if "tuple" in lowered or "return true" in lowered else 0.0
        score += 0.2 if "error" in lowered else 0.0
        return min(1.0, score)
    if test_name == "local_api_use":
        score = 0.0
        score += 0.35 if "post" in lowered else 0.0
        score += 0.35 if "/chat" in lowered or "/vision" in lowered or "http" in lowered else 0.0
        score += 0.3 if "json" in lowered else 0.0
        return min(1.0, score)
    if test_name == "vision":
        return 1.0 if len(text) > 80 else 0.5
    return 1.0 if len(text) > 80 else 0.6


def classify_error(error: str) -> str:
    lowered = error.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "too large" in lowered or "exceeds configured limit" in lowered or "limit" in lowered:
        return "exceeded limits"
    if "not running" in lowered or "unreachable" in lowered or "not reachable" in lowered:
        return "backend unavailable"
    if "not available" in lowered or "model not found" in lowered or "pull" in lowered:
        return "model not pulled"
    if "gguf file path not found" in lowered:
        return "gguf path missing"
    if "memory" in lowered or "cuda" in lowered or "out of memory" in lowered:
        return "memory failure"
    return "failed"


def suitability_score(value: Any) -> float:
    if value is True:
        return 1.0
    if value == "fallback":
        return 0.55
    if value == "experimental":
        return 0.45
    if value is False:
        return 0.35
    return 0.5


def summarize_profile(profile_name: str, profile: Dict[str, Any], tests: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(tests)
    successes = [item for item in tests if item["success"]]
    failures = [item for item in tests if not item["success"]]
    reliability = len(successes) / total if total else 0.0
    elapsed_values = [item["elapsed_seconds"] for item in successes if item.get("elapsed_seconds") is not None]
    avg_elapsed = statistics.mean(elapsed_values) if elapsed_values else None
    slow_threshold = int(profile.get("slow_response_seconds") or os.getenv("SLOW_RESPONSE_SECONDS", "60"))
    speed = 0.0
    if avg_elapsed is not None:
        speed = min(1.0, slow_threshold / max(avg_elapsed, 0.001))
    quality = statistics.mean([item["quality_score"] for item in successes]) if successes else 0.0
    json_tests = [item for item in tests if item["name"] == "json_compliance"]
    json_compliance = 1.0 if json_tests and json_tests[0].get("json_ok") else 0.0
    vision_tests = [item for item in tests if item["name"] == "vision"]
    vision_score = 1.0 if vision_tests and vision_tests[0]["success"] else (0.5 if not vision_tests else 0.0)
    suitability = suitability_score(profile.get("recommended_for_8gb_vram"))
    score = (
        reliability * 0.35
        + speed * 0.20
        + quality * 0.20
        + json_compliance * 0.10
        + vision_score * 0.05
        + suitability * 0.10
    )
    failure_types = sorted({item["classification"] for item in failures})
    status = "works" if reliability >= 0.8 else "unstable" if reliability > 0 else "fails"
    if avg_elapsed is not None and avg_elapsed >= slow_threshold:
        status = f"{status}, too slow"
    if any(item["classification"] == "exceeded limits" for item in failures):
        status = f"{status}, exceeded limits"
    return {
        "profile": profile_name,
        "status": status,
        "score": score,
        "reliability": reliability,
        "average_elapsed_seconds": avg_elapsed,
        "average_tokens_per_second": statistics.mean(
            [item["approx_tokens_per_second"] for item in successes if item["approx_tokens_per_second"] is not None]
        )
        if successes
        else None,
        "quality": quality,
        "json_compliance": json_compliance,
        "vision_score": vision_score,
        "suitability": suitability,
        "failure_types": failure_types,
        "recommended_for_8gb_vram": profile.get("recommended_for_8gb_vram"),
    }


def recommend_profile(summaries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    viable = [item for item in summaries if item.get("reliability", 0.0) > 0.0]
    if not viable:
        return None
    ranked = sorted(viable, key=lambda item: item["score"], reverse=True)
    best = ranked[0]
    four_b = next((item for item in viable if item["profile"] == "gemma3_4b_ollama_safe"), None)
    if four_b and four_b["reliability"] >= 0.8:
        best_elapsed = best.get("average_elapsed_seconds")
        four_elapsed = four_b.get("average_elapsed_seconds")
        best_is_much_slower = (
            best_elapsed is not None
            and four_elapsed is not None
            and best_elapsed > max(four_elapsed * 2.0, four_elapsed + 20)
        )
        if best["profile"] != four_b["profile"] and (best["score"] < four_b["score"] + 0.15 or best_is_much_slower):
            return four_b
    return best


def print_test_line(profile_name: str, item: Dict[str, Any]) -> None:
    if item["success"]:
        tps = item["approx_tokens_per_second"]
        tps_text = f", ~{tps:.2f} tok/s" if tps is not None else ""
        print(f"  PASS {item['name']}: {item['elapsed_seconds']:.2f}s, {item['response_length']} chars{tps_text}")
    else:
        print(f"  FAIL {item['name']}: {item['classification']} - {item['error']}")


def build_payload(
    profile_name: str,
    prompt: str,
    args: argparse.Namespace,
    images_base64: Optional[List[str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "profile": profile_name,
        "prompt": prompt,
        "safe_mode": args.safe_mode,
        "allow_clamp": False,
        "stream": False,
    }
    if args.max_output_tokens is not None:
        payload["max_output_tokens"] = args.max_output_tokens
    if args.context_limit is not None:
        payload["context_limit"] = args.context_limit
    if args.timeout is not None:
        payload["timeout_seconds"] = args.timeout
    if images_base64:
        payload["images_base64"] = images_base64
    return payload


def run_profile_tests(
    profile_name: str,
    profile: Dict[str, Any],
    args: argparse.Namespace,
    use_server: bool,
    image_base64: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    print(f"\nProfile: {profile_name}")
    print(f"  backend={profile.get('backend')} model={profile.get('model_id')} recommended_8gb={profile.get('recommended_for_8gb_vram')}")
    tests: List[Dict[str, Any]] = []
    timeout = args.timeout or int(profile.get("timeout_seconds") or os.getenv("REQUEST_TIMEOUT_SECONDS", "180"))

    prompt_tests = test_prompts_for_profile(profile)
    for test_name, prompt in prompt_tests:
        endpoint = "chat"
        payload = build_payload(profile_name, prompt, args)
        started = time.perf_counter()
        result = call_model(use_server, endpoint, payload, timeout)
        elapsed = float(result.get("elapsed_seconds") or (time.perf_counter() - started))
        text = response_text(result)
        approx_tps = ((len(text) / 4) / elapsed) if result.get("success") and elapsed > 0 else None
        item = {
            "name": test_name,
            "success": bool(result.get("success")),
            "elapsed_seconds": elapsed,
            "response_length": len(text),
            "approx_tokens_per_second": approx_tps,
            "quality_score": quality_score(test_name, text) if result.get("success") else 0.0,
            "json_ok": strict_json_ok(text) if test_name == "json_compliance" and result.get("success") else False,
            "classification": "ok" if result.get("success") else classify_error(str(result.get("error") or "")),
            "error": result.get("error"),
            "warning": result.get("warning"),
        }
        tests.append(item)
        print_test_line(profile_name, item)

    if image_base64:
        payload = build_payload(profile_name, VISION_PROMPT, args, [image_base64])
        started = time.perf_counter()
        result = call_model(use_server, "vision", payload, timeout)
        elapsed = float(result.get("elapsed_seconds") or (time.perf_counter() - started))
        text = response_text(result)
        approx_tps = ((len(text) / 4) / elapsed) if result.get("success") and elapsed > 0 else None
        item = {
            "name": "vision",
            "success": bool(result.get("success")),
            "elapsed_seconds": elapsed,
            "response_length": len(text),
            "approx_tokens_per_second": approx_tps,
            "quality_score": quality_score("vision", text) if result.get("success") else 0.0,
            "json_ok": False,
            "classification": "ok" if result.get("success") else classify_error(str(result.get("error") or "")),
            "error": result.get("error"),
            "warning": result.get("warning"),
        }
        tests.append(item)
        print_test_line(profile_name, item)

    summary = summarize_profile(profile_name, profile, tests)
    print(
        f"  Summary: {summary['status']} | score={summary['score']:.3f} "
        f"reliability={summary['reliability']:.0%} quality={summary['quality']:.2f}"
    )
    return tests, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local Ollama and optional llama.cpp model profiles.")
    parser.add_argument("--profile", help="Profile name from config.json.")
    parser.add_argument("--all", action="store_true", help="Test all enabled profiles.")
    parser.add_argument("--image", help="Optional image path for a vision test.")
    parser.add_argument("--safe-mode", type=parse_bool, default=True, help="true/false. Defaults to true.")
    parser.add_argument("--max-output-tokens", type=int, help="Requested output token cap.")
    parser.add_argument("--context-limit", type=int, help="Requested context limit.")
    parser.add_argument("--timeout", type=int, help="Request timeout seconds.")
    args = parser.parse_args()

    try:
        config = load_config()
    except Exception as exc:
        print(f"Could not load config.json: {exc}")
        return 2

    profiles = config.get("profiles", {})
    if args.all:
        profile_names = enabled_profiles(config)
    else:
        profile_names = [args.profile or os.getenv("DEFAULT_MODEL_PROFILE") or config.get("default_profile")]

    if not profile_names:
        print("No profiles selected. Enable at least one profile in config.json or pass --profile.")
        return 2

    missing = [name for name in profile_names if name not in profiles]
    if missing:
        print(f"Unknown profile(s): {', '.join(missing)}")
        return 2

    image_base64: Optional[str] = None
    if args.image:
        image_base64, image_error = image_to_base64(Path(args.image))
        if image_error:
            print(image_error)
            return 2

    use_server = is_server_running()
    mode = f"local API server at {server_base_url()}" if use_server else "direct backend calls"
    print(f"Test mode: {mode}")
    print(f"Safe mode: {args.safe_mode}")
    if args.max_output_tokens:
        print(f"Requested max_output_tokens: {args.max_output_tokens}")
    if args.context_limit:
        print(f"Requested context_limit: {args.context_limit}")

    all_summaries: List[Dict[str, Any]] = []
    for profile_name in profile_names:
        _, summary = run_profile_tests(profile_name, profiles[profile_name], args, use_server, image_base64)
        all_summaries.append(summary)

    print("\nScoring summary")
    for summary in sorted(all_summaries, key=lambda item: item["score"], reverse=True):
        avg = summary["average_elapsed_seconds"]
        avg_text = f"{avg:.2f}s" if avg is not None else "n/a"
        tps = summary["average_tokens_per_second"]
        tps_text = f"{tps:.2f}" if tps is not None else "n/a"
        failures = ", ".join(summary["failure_types"]) if summary["failure_types"] else "none"
        print(
            f"- {summary['profile']}: score={summary['score']:.3f}, status={summary['status']}, "
            f"avg={avg_text}, tok/s={tps_text}, failures={failures}, "
            f"8GB={summary['recommended_for_8gb_vram']}"
        )

    recommended = recommend_profile(all_summaries)
    if recommended:
        print(f"\nRecommended profile: {recommended['profile']}")
        if recommended["profile"] == "gemma3_4b_ollama_safe":
            print("Reason: it is the safest default when speed and reliability matter on 8 GB VRAM.")
        elif "12b" in recommended["profile"]:
            print("Reason: 12B only won because it ran reliably enough for the measured workload.")
    else:
        print("\nRecommended profile: none")
        print("Reason: no tested profile completed successfully. Check Ollama, pulled models, or enabled backends.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
