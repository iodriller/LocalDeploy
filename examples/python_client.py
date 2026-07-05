"""Minimal Python client examples for LocalDeploy.

Run the API first:
    .\\scripts\\start.ps1

Then:
    python examples/python_client.py
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict

import requests

BASE_URL = os.getenv("LOCALDEPLOY_BASE_URL", "http://127.0.0.1:8000")


def list_profiles() -> Dict[str, Any]:
    response = requests.get(f"{BASE_URL}/profiles", timeout=10)
    response.raise_for_status()
    return response.json()


def chat(prompt: str, max_output_tokens: int = 128) -> str:
    payload = {
        "prompt": prompt,
        "safe_mode": True,
        "max_output_tokens": max_output_tokens,
    }
    response = requests.post(f"{BASE_URL}/chat", json=payload, timeout=180)
    response.raise_for_status()
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"LocalDeploy error: {data.get('error')}")
    return str(data.get("response") or "")


def openai_sdk_chat(prompt: str) -> str:
    """Use the official openai SDK against LocalDeploy's OpenAI-compatible endpoint.

    Install the SDK first: pip install openai
    """
    try:
        from openai import OpenAI
    except ImportError:
        return "(openai SDK not installed; pip install openai)"

    client = OpenAI(base_url=f"{BASE_URL}/v1", api_key="not-used-locally")
    response = client.chat.completions.create(
        model="gemma3_4b_ollama_safe",
        messages=[
            {"role": "system", "content": "You are concise."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=128,
    )
    return response.choices[0].message.content or ""


def main() -> int:
    try:
        profiles = list_profiles()
    except requests.RequestException as exc:
        print(f"Could not reach LocalDeploy at {BASE_URL}: {exc}")
        print("Start it with: .\\scripts\\start.ps1")
        return 1

    print(f"Default profile: {profiles.get('default_profile')}")
    enabled = [name for name, p in profiles.get("profiles", {}).items() if p.get("enabled")]
    print(f"Enabled profiles: {', '.join(enabled) or '(none)'}")
    print()

    print("Native /chat ->")
    print(chat("Reply with one short sentence: what is your role?"))
    print()

    print("OpenAI SDK ->")
    print(openai_sdk_chat("Name three small open-weight LLMs."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
