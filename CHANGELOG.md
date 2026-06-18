# Changelog

All notable changes to this project should be documented here.

## Unreleased

- **Tiered fit warnings** (`/system/fit-check`): adds `tier`/`severity`/`headline`/`cpu_deployable`
  — green (comfortable), yellow (tight, or won't-fit-GPU-but-runs-on-CPU), red (too big anywhere) —
  using system RAM to judge CPU deployability. The coarse `verdict` is unchanged (backward-compatible).
- **Model management**: `POST /models/delete` (remove a model from disk) and `POST /models/free`
  (unload all models from memory/VRAM), with **Delete** buttons per installed model, a **Free memory**
  button, and a **Cancel** button to abort an in-flight pull.
- **Hardware panel now shows CPU + RAM** (`/system/hardware`): CPU model, physical/logical cores,
  and system RAM total/available via `psutil` (graceful fallback when absent). See `docs/ROADMAP.md`.
- **Choose CPU vs GPU at deploy time**: the Serve panel has a *Deploy to* selector (Auto / GPU /
  CPU). Forces Ollama `num_gpu` (0 = CPU, max = GPU); Auto is unchanged from before. `/system/status`
  now labels each running model's placement (GPU / CPU / Split N%). Additive and backwards-compatible.
- Added `run.sh` (macOS/Linux) and `run.ps1` (Windows) one-command launchers: detect and install
  Docker if absent (via `get.docker.com` on Linux, Homebrew cask on macOS, winget on Windows),
  clone or update the repo, and start `docker compose up` — no prerequisites needed.

- Added **opt-in token auth**: set `API_TOKEN` to require it (`X-API-Token` /
  `Authorization: Bearer` / `?token=`); no auth and zero overhead when unset.
- "Check New Models" now filters to GGUF repos and offers a one-click **Pull** via Ollama's
  `hf.co/<id>` shortcut.
- Added an optional **web UI** at `/ui` (two tabs: Serve & Diagnose, Deploy & Benchmark);
  static, no build step, gated by `ENABLE_WEB_UI` (default on). See `docs/UI.md`.
- Added a control-plane API: `/system/hardware`, `/system/fit-check`, `/system/status`,
  `/system/recommend`, `/system/set-default`, `/registry/installed`, `/registry/check-updates`,
  `/models/pull`, `/models/serve`, `/models/stop`, `/models/switch`.
- Added benchmark-over-HTTP: `/benchmark/example`, `/benchmark/validate`, `/benchmark/run`
  (streamed), plus shareable report cards (`/benchmark/export`) and A/B compare
  (`/benchmark/compare`). `benchmark.py` gained an importable `execute_test`/`iter_run` and a
  JSON-safe grader registry shared by the CLI and the API.
- Added one-command Docker run (`Dockerfile`, `docker-compose.yml`) bundling Ollama + the API/UI,
  plus a `scripts/start.sh` launcher.
- Added a verifiable offline mode (`OFFLINE=true`) and `scripts/egress_selftest.py`.
- The original CLI, OpenAI-compatible API, and loopback-only backend guard are unchanged.

## 0.2.0 - 2026-05-16

- Added OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoints for local clients.
- Added GitHub project hygiene files, CI validation, issue templates, and contribution/security docs.
- Added terminal chat helper and local benchmark documentation.

## 0.1.0 - 2026-05-16

- Initial local Ollama-first deployment server.
- Added optional llama.cpp profile support.
- Added safe model profiles for Gemma 3 4B and Gemma 3 12B testing.
- Added benchmark runner and Windows install helper.
