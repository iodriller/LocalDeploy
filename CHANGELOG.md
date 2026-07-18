# Changelog

All notable changes to this project should be documented here.

## 0.4.0 - 2026-07-17

Public-launch release: a chat playground, quant advisor, disk usage tools, durable
benchmark history — and streamlining so the app only ever shows models that are
actually on your machine.

### New features

- **Chat playground tab.** Talk to any pulled model right in the UI — streaming
  tokens over the server's own OpenAI-compatible `/v1/chat/completions`, image
  attachments for vision-capable profiles, an optional system prompt, and a
  Send-becomes-Stop button. The reply meta line separates model-load time
  ("first token X s") from true generation speed (tok/s).
- **Quantization advisor** (Get a model → ⚖ Quant advisor). Fit-checks every
  common GGUF quant (Q2_K → F16) of a model size against your VRAM budget with
  the same estimator as fit checks, and tells you when there's headroom for a
  higher-quality tag than the usual Q4 default (`POST /system/quant-advisor`).
- **Disk usage panel.** The Your models card now shows `N models · X GB on disk`,
  sorts by size / recency / name, and bulk-deletes selected models with a
  freed-gigabytes preview.
- **Opt-in server-side benchmark history.** A toggle on the History tile mirrors
  completed runs to `reports/benchmark-history/` as one JSON file per run
  (`/benchmark/history` endpoints), so results survive the browser and can be
  shared as plain files. Run ids are strictly validated (no path traversal).

### No more phantom models

- **Fresh installs start truly empty.** `load_config()` no longer falls back to
  `config.example.json` when `config.json` is missing (that fallback made a fresh
  clone list a dozen sample profiles for models that were never pulled — most
  visibly on macOS/Linux, where `start.sh` doesn't seed a config). A missing
  config now yields zero profiles; pulling a model auto-creates its profile.
  `config.example.json` remains as field-reference documentation and a test fixture.
- **`.env.example` no longer pins `DEFAULT_MODEL_PROFILE`.** The pinned sample value
  made `/chat` fail with "Unknown profile" on a fresh install until that exact model
  was pulled, even after other models were. The config's `default_profile`
  (auto-set on first pull) is now used.
- **Clear first-run error.** With no profiles configured, `/chat` and friends now say
  "pull a model first" instead of "Unknown profile 'gemma3_4b_ollama_safe'".
- **UI hides profiles whose model isn't on the machine.** The benchmark model picker
  hides them by default behind a "Show N hidden (model not pulled)" toggle, the
  profile dropdowns annotate them, and a new **Remove not-pulled profiles** button
  (Advanced → All run profiles) deletes them in one click. For llama.cpp profiles the
  server now reports whether the GGUF file exists (`model_file_exists` on `/profiles`),
  so dead file paths get the same treatment as un-pulled Ollama models.

### Packaging

- **`pip install localdeploy`.** The project now ships as a proper Python package
  (`pyproject.toml`) with a `localdeploy` console command that serves the API + UI
  and opens the browser. The web UI ships as package data (`localdeploy/web/`,
  moved from the repo-root `web/`).
- **App home for runtime state.** Installed runs keep `.env`, `config.json`,
  `logs/`, and `reports/` in `~/.localdeploy` (override with `LOCALDEPLOY_HOME`);
  source checkouts keep using the repo root, so nothing changes for `git clone`
  users. Docker now mounts that state separately from Ollama's model volume, so
  profiles and benchmark history survive container recreation. `/favicon.ico`
  is now served, so `/docs` page views stop logging 404s.

### Fresh coat of paint

- New logo (`localdeploy/web/logo.svg`), regenerated favicon, and a repo social banner
  (`docs/assets/banner.png`).
- README rebuilt around an animated demo GIF (`docs/assets/demo.gif`), captured by the
  new `scripts/capture_demo_gif.py`; screenshots now ship in dark (default) and light
  themes via `scripts/capture_screenshots.py`.
- The web asset cache-bust test no longer pins the exact `?v=` token, so bumping asset
  versions can't silently break CI.

### Security and reliability

- Updated `python-multipart` to `0.0.31` after dependency auditing found three
  advisories affecting `0.0.28`; CI now audits the pinned runtime requirements.
- Bundled benchmark and chat clients now forward `API_TOKEN`, so enabling auth
  no longer breaks server-initiated benchmarks or local CLI calls.
- The code benchmark worker now validates candidate ASTs, exposes only a small
  builtin/import allowlist, and blocks common filesystem, process, network,
  registry, and native-library operations in addition to its existing timeout.
- Removed the browser-triggered `pip install psutil` endpoint. `psutil` is a
  required dependency, and hardware probe failures now degrade to a read-only
  status instead of modifying the running Python environment.

## 0.3.0 - 2026-07-08

First public release.

### Repo cleanup for public release

- **One start command per platform.** `scripts/start.ps1` (Windows) now opens the UI by default
  (`-NoBrowser` to skip, `-Foreground` for live logs) and `scripts/start.sh` (macOS/Linux) opens
  the UI once the server is healthy. Removed the redundant launchers: `run.ps1` / `run.sh`
  (curl-pipe bootstrappers that auto-installed Docker), `install.ps1` (start.ps1 already creates
  config/venv; model pulls belong in the fit-checked UI), and `scripts/start_ui.ps1` (now the
  default behavior).
- **Renamed** the root model-comparison CLI `test_models.py` → `compare_models.py` (it was never a
  pytest suite; the old name needed a pytest.ini workaround).
- **Docs**: `docs/TERMINAL_TESTING.md` → `docs/CLI.md`; removed the internal `GAPS.md` and
  `docs/VERIFICATION.md` trackers; rewrote the README around a per-platform quick start.
- **License** switched from "all rights reserved" placeholder to MIT.

### Features and fixes

- **Forced CPU/GPU benchmarks now measure the device they ask for.** Previously a
  `device=cpu` (or `gpu`) run only pinned the placement at warm-up; the benchmark's own
  `/chat` calls didn't pass `num_gpu`, so Ollama could silently re-place the model (a CPU
  run drifting onto the GPU). `num_gpu` is now threaded through `/chat` → `execute_test` →
  `iter_run`, so every inference call stays on the requested device end-to-end. Verified:
  `device=cpu` reports `actual_device=cpu`, `device=gpu` reports `gpu`.
- **Fewer spurious "status failed" benchmark rows.** Forcing CPU/GPU used to hard-fail
  whenever Ollama placed the model differently (e.g. a model too big for pure GPU lands on
  Split). It now warns and proceeds, recording the run with its actual placement.
- **Benchmark history/queue management**: per-run delete (×) in the run library, a confirm on
  Clear history, and the ability to dismiss finished/failed queue rows (individually or via
  **Clear finished**); failed rows show their error reason inline. The running-model **Kill**
  button is now **Unload** for consistency.
- **Browser UI smoke tests** (`tests/test_ui_playwright.py`): optional Playwright-driven checks
  that load the real `/ui` in headless Chromium and exercise tab switching and the benchmark
  history controls. They skip cleanly when Playwright or its browser isn't installed; added
  `playwright` to `requirements-dev.txt`.
- **Fixed: the web UI was completely broken** — `web/app.js` contained smart/curly quotes
  (`“ ”`) used as string delimiters in the "Check New Models" function, a `SyntaxError` that
  prevented the *entire* script from parsing (blank/dead UI). Replaced with straight quotes.
  Added a CI step (`node --check web/app.js`) and a pytest guard so a JS parse error can never
  ship silently again (the Python-only suite never loaded the UI before).
- **Benchmark workspace V2**: the benchmark tab is now a local experiment workspace with a
  multi-profile Run Builder, sequential run queue, leaderboard, category heatmap, SVG
  speed/quality scatter, per-test matrix, local browser history, selected-run comparison, and
  report-card import/export. **CPU + GPU** now creates two queued benchmark batches instead of a
  separate special-case button.
- **Benchmark queue UX**: waiting benchmark rows can now be moved up/down or removed before they
  run, while the active run appears only in the main progress panel with elapsed time.
- **Comparison auto-select**: fresh benchmark results now join the existing selected comparison
  set instead of replacing it, so a second run appears next to the first run automatically.
- **Per-test matrix demoted**: the per-test matrix is now collapsed as an advanced diagnostic
  view instead of occupying the main results dashboard by default.
- **Benchmark deployments are temporary**: `/benchmark/run` now unloads each Ollama model after
  its profile finishes, including forced CPU/GPU benchmark deployments. The benchmark tab no
  longer leaves a model served as a side effect.
- Quieter device auto-detect (inline note instead of a per-run toast); the run progress bar is
  now an ARIA `progressbar` so screen readers announce progress.

- **Honest device tag**: the benchmark Device tag now defaults to **Auto (detect)** and is
  resolved from the model's *actual* placement (GPU/CPU/Split via `/system/status`) after the
  run, so report cards can't be silently mislabelled. Manual GPU/CPU stays an override and warns
  if it disagrees with what's detected.
- **Apple Silicon (Metal) detection**: `/system/hardware` now reports Apple Silicon GPUs instead
  of claiming "CPU-only" on a Mac. Unified memory is surfaced as such (no false VRAM figure; fit
  checks use system RAM). NVIDIA still takes precedence where present.
- **Pull fit-gate respects the tiered warnings**: a model that won't fit VRAM but **runs on CPU**
  is no longer blocked behind the override — the gate now hard-blocks only the "fits nowhere"
  (`severity: hard`) case and notes the CPU fallback for the soft case.
- **Tune for my GPU** labels skipped CPU-capable profiles as "CPU-only (skipped for GPU tuning)"
  instead of the misleading "won't fit VRAM".
- **Run table**: detailed benchmark rows now live below the dashboard and can be filtered by
  model, category, pass/fail, and slowest failures.
- **Warm-up robustness**: Deploy/replace actions now show a **live "Loading…Ns" counter** (with a
  "large models on CPU can take a minute" hint when targeting CPU) instead of an apparently
  frozen button. The server's load timeout **scales with the device** (longer for CPU offload)
  and is overridable via `OLLAMA_LOAD_TIMEOUT`; a load timeout returns a clear "it may still be
  loading — click Refresh status" message rather than a generic failure.
- **Decision-grade run results**: the benchmark workspace shows queued model/device variants,
  live progress, **Cancel**, **tok/s** per test, inline failure reasons, response previews,
  leaderboard ranking, per-category heatmap, and per-test matrix views.
- **Report cards & compare carry speed**: exported cards now include `avg_tokens_per_second`,
  a per-test **tok/s** column, and a **By category** table. The A/B compare view adds a
  **tok/s (A → B)** column and aggregate tok/s delta — so a "Qwen/GPU vs Qwen/CPU" diff
  shows the speed difference directly.
- **Better discovery** (Phase 5): "New on Hugging Face" now has a search box, results-per-query
  limit, and GGUF-only toggle — the API already accepted `queries`/`limit`/`gguf_only`, the UI
  now surfaces them. Installed rows show quantization level, parameter size, and modified date.
  HF candidates show download counts and likes alongside the modified date.
- **CPU-vs-GPU benchmark comparison** (Phase 6): the benchmark tab gains a *Device tag*
  (Auto / GPU / CPU) selector. The tag is stored in the exported report card (`device` field) and
  surfaces in the card header (`[GPU]`/`[CPU]` next to the model name in HTML and Markdown). The
  compare view labels runs as `model/GPU` vs `model/CPU` so A/B diffs are unambiguous.
- **Tiered fit warnings** (`/system/fit-check`): adds `tier`/`severity`/`headline`/`cpu_deployable`
  — green (comfortable), yellow (tight, or won't-fit-GPU-but-runs-on-CPU), red (too big anywhere) —
  using system RAM to judge CPU deployability. The coarse `verdict` is unchanged (backward-compatible).
- **Model management**: `POST /models/delete` (remove a model from disk) and `POST /models/free`
  (unload all models from memory/VRAM), with **Delete** buttons per installed model, a **Free memory**
  button, and a **Cancel** button to abort an in-flight pull.
- **Hardware panel now shows CPU + RAM** (`/system/hardware`): CPU model, physical/logical cores,
  and system RAM total/available via `psutil` (graceful fallback when absent).
- **Choose CPU vs GPU at deploy time**: the Serve panel has a *Deploy to* selector (Auto / GPU /
  CPU). Forces Ollama `num_gpu` (0 = CPU, max = GPU); Auto is unchanged from before. `/system/status`
  now labels each running model's placement (GPU / CPU / Split N%). Additive and backwards-compatible.
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
