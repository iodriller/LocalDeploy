# Tests

Offline tests for the full LocalDeploy stack. They run against the in-process FastAPI app and do not require Ollama, llama.cpp, or a GPU.

## Install

```powershell
python -m pip install -r requirements-dev.txt
```

The browser UI smoke tests (`test_ui_playwright.py`) also need a Chromium build,
fetched once. They **skip cleanly** if it's absent, so this step is optional:

```powershell
python -m playwright install chromium
```

## Run

```powershell
pytest
```

Or a single file:

```powershell
pytest tests/test_guardrails.py -v
```

## What's covered

**Core / safety**
- `test_utils.py` — `is_loopback_url`, `get_backend_base_url`, env helpers.
- `test_guardrails.py` — `prepare_request` rejects oversized prompts, honors `allow_clamp`, rejects unknown/disabled profiles, image limits and base64 validation.
- `test_security.py` — opt-in API token (`API_TOKEN` env var); endpoints require the token when set; `/health` and static UI stay open.
- `test_grader_sandbox.py` — restricted code-grader worker (side-effect blocking, resource caps, and timeouts).

**Hardware & fit**
- `test_hardware_cpu_ram.py` — hardware probe reports CPU model, cores, and RAM.
- `test_fit_tiers.py` — tiered soft/hard deployability warnings from `/system/fit-check`.
- `test_device_target.py` — CPU vs GPU deployment target plumbing (`num_gpu` → placement).
- `test_warmup_timeout.py` — device-aware warm-up timeout + graceful load-timeout message.

**Models & registry**
- `test_model_management.py` — delete/free-memory routes.
- `test_web_registry_models.py` — registry + model-lifecycle endpoints (pull, fit, installed list, HF search).
- `test_audit_fixes.py` — Apple Silicon detection + tiered pull gate.

**Benchmark**
- `test_benchmark_graders.py` — all built-in graders accept valid answers and reject bad ones.
- `test_benchmark_registry.py` — question-set validator and streaming run endpoints.
- `test_benchmark_runner.py` — CLI `run_profile` loop regression coverage.
- `test_web_benchmark.py` — benchmark web endpoints (question set, validate, run, export, example).

**Web API & UI**
- `test_api_routes.py` — HTTP-level routes via TestClient (models, profiles, streaming SSE, 501s).
- `test_web_endpoints.py` — web control-plane routes (serve, stop, switch, hardware, status, recommend).
- `test_web_assets.py` — JS parses as valid JavaScript, cache-busting version strings are correct, UI controls have expected IDs.
- `test_web_differentiators.py` — report card export and A/B compare endpoints.
- `test_ui_playwright.py` — **browser** smoke tests: launches the real app and drives `/ui` in
  headless Chromium (tab switching, benchmark run-library per-run delete, clear-history confirm).
  Skips cleanly if Playwright or its browser isn't installed.

**Phase regression**
- `test_phase5_phase6.py` — Phase 5 (HF discovery) and Phase 6 (device-tagged benchmark cards).
- `test_phase_b_report.py` — tok/s in summary, per-category rollup, tok/s in compare.

These exercise the safety and control-plane layers. They are not a substitute for end-to-end testing against a real backend; for that, run `compare_models.py` with a live Ollama or llama.cpp server.
