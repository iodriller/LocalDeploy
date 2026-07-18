from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


from localdeploy.utils import api_auth_headers, api_client_base_url, app_home

APP_DIR = Path(__file__).resolve().parent
APP_HOME = app_home()
load_dotenv(APP_HOME / ".env")


def default_base_url() -> str:
    return api_client_base_url()


def server_health(base_url: str) -> Optional[Dict[str, Any]]:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/health", headers=api_auth_headers(), timeout=5)
        if response.ok:
            return dict(response.json())
    except requests.RequestException:
        return None
    return None


def start_server(base_url: str) -> Optional[subprocess.Popen[Any]]:
    if server_health(base_url):
        return None

    python_exe = APP_DIR / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)

    logs_dir = APP_HOME / "logs"
    logs_dir.mkdir(exist_ok=True)
    stdout = (logs_dir / "chat_cli_api_server.out.log").open("a", encoding="utf-8")
    stderr = (logs_dir / "chat_cli_api_server.err.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [str(python_exe), "api_server.py"],
        cwd=APP_DIR,
        stdout=stdout,
        stderr=stderr,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    for _ in range(40):
        time.sleep(0.5)
        if server_health(base_url):
            return process
    return process


def print_profiles(base_url: str) -> int:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/profiles", headers=api_auth_headers(), timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        print(f"Could not load profiles from {base_url}: {exc}")
        return 1

    print(f"Default profile: {data.get('default_profile')}")
    print()
    for name, profile in data.get("profiles", {}).items():
        status = "enabled" if profile.get("enabled") else "disabled"
        recommended = profile.get("recommended_for_8gb_vram")
        model_id = profile.get("model_id")
        backend = profile.get("backend")
        print(f"- {name}: {status}, {backend}, {model_id}, 8GB={recommended}")
    return 0


def send_chat(
    base_url: str,
    profile: str,
    prompt: str,
    max_output_tokens: int,
    temperature: Optional[float],
    timeout: int,
    raw: bool = False,
) -> int:
    payload: Dict[str, Any] = {
        "profile": profile,
        "prompt": prompt,
        "safe_mode": True,
        "max_output_tokens": max_output_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    started = time.perf_counter()
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/chat",
            json=payload,
            headers=api_auth_headers(),
            timeout=timeout,
        )
    except requests.Timeout:
        print(f"Request timed out after {timeout} seconds.")
        return 1
    except requests.ConnectionError:
        print(f"LocalDeploy is not reachable at {base_url}. Start it with: .\\scripts\\start.ps1")
        return 1

    elapsed = time.perf_counter() - started
    try:
        data = response.json()
    except ValueError:
        print(f"Server returned non-JSON HTTP {response.status_code}:")
        print(response.text[:1000])
        return 1

    if raw:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0 if data.get("success") else 1

    if not data.get("success"):
        print(f"Error: {data.get('error')}")
        if data.get("warning"):
            print(f"Warning: {data.get('warning')}")
        return 1

    print()
    print(data.get("response", ""))
    print()
    print(
        f"[{data.get('profile')} | {data.get('model')} | "
        f"{data.get('elapsed_seconds', round(elapsed, 3))}s | "
        f"ctx={data.get('context_limit_used')} | out={data.get('max_output_tokens_used')}]"
    )
    if data.get("warning"):
        print(f"Warning: {data['warning']}")
    return 0


def repl(args: argparse.Namespace) -> int:
    print("LocalDeploy terminal chat")
    print("Type a prompt and press Enter. Commands: :quit, :profiles, :profile NAME, :tokens N, :raw, :help")
    print(f"Server: {args.base_url}")
    print(f"Profile: {args.profile or '(server default)'}")
    print()

    profile = args.profile
    max_tokens = args.max_output_tokens
    raw = args.raw

    while True:
        try:
            prompt = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not prompt:
            continue
        if prompt in {":quit", ":exit", "quit", "exit"}:
            return 0
        if prompt == ":help":
            print("Commands: :quit, :profiles, :profile NAME, :tokens N, :raw")
            continue
        if prompt == ":profiles":
            print_profiles(args.base_url)
            continue
        if prompt.startswith(":profile "):
            profile = prompt.split(" ", 1)[1].strip()
            print(f"Profile set to {profile}")
            continue
        if prompt.startswith(":tokens "):
            try:
                max_tokens = int(prompt.split(" ", 1)[1].strip())
            except ValueError:
                print("Usage: :tokens 256")
                continue
            print(f"max_output_tokens set to {max_tokens}")
            continue
        if prompt == ":raw":
            raw = not raw
            print(f"Raw JSON output: {raw}")
            continue

        send_chat(
            args.base_url,
            profile,
            prompt,
            max_tokens,
            args.temperature,
            args.timeout,
            raw,
        )
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Simple terminal chat client for LocalDeploy.")
    parser.add_argument("--base-url", default=default_base_url(), help="LocalDeploy API base URL.")
    parser.add_argument(
        "--profile",
        default=os.getenv("DEFAULT_MODEL_PROFILE"),
        help="Profile name from config.json. If omitted, the server selects its configured default.",
    )
    parser.add_argument("--prompt", help="One-shot prompt. If omitted, starts interactive chat.")
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--raw", action="store_true", help="Print full JSON response.")
    parser.add_argument("--profiles", action="store_true", help="List configured profiles and exit.")
    parser.add_argument("--start-server", action="store_true", help="Start api_server.py if it is not already running.")
    args = parser.parse_args()

    if args.start_server:
        start_server(args.base_url)

    if args.profiles:
        return print_profiles(args.base_url)

    if not server_health(args.base_url):
        print(f"LocalDeploy is not reachable at {args.base_url}.")
        print("Start it with:")
        print("  .\\.venv\\Scripts\\python.exe api_server.py")
        print("Or rerun this client with --start-server.")
        return 1

    if args.prompt:
        return send_chat(
            args.base_url,
            args.profile,
            args.prompt,
            args.max_output_tokens,
            args.temperature,
            args.timeout,
            args.raw,
        )
    return repl(args)


if __name__ == "__main__":
    raise SystemExit(main())
