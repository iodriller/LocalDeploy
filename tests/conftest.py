"""Pytest fixtures and environment setup for LocalDeploy tests.

These tests run against the in-process FastAPI app and the pure-Python
guardrail code. They do not require a running Ollama or llama.cpp server.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Project-relative env setup must happen before api_server is imported.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_PATH", str(PROJECT_ROOT / "config.example.json"))
os.environ.setdefault("DEFAULT_MODEL_PROFILE", "gemma3_4b_ollama_safe")
os.environ.setdefault("REQUIRE_GPU_ONLY", "false")
os.environ.setdefault("ENABLE_LLAMA_CPP", "false")
os.environ.setdefault("ENABLE_CORS", "false")

# Make the project importable regardless of how pytest is invoked.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
