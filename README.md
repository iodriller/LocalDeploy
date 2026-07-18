<p align="center">
  <img src="web/logo.svg" width="96" height="96" alt="LocalDeploy logo" />
</p>

<h1 align="center">LocalDeploy</h1>

<p align="center">
  <strong>Pick, deploy &amp; benchmark the best local AI model for your machine — no guessing required.</strong>
</p>

<p align="center">
  Everything stays on your machine: no cloud inference, no subscriptions, no telemetry.
</p>

<p align="center">
  <a href="https://github.com/iodriller/LocalDeploy/actions/workflows/ci.yml"><img src="https://github.com/iodriller/LocalDeploy/actions/workflows/ci.yml/badge.svg" alt="CI status" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license" /></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Docker-555.svg" alt="Platforms" />
</p>

<p align="center">
  <img src="docs/assets/demo.gif" alt="LocalDeploy demo: hardware detection, curated model picks, fit-checked model list, and the benchmark dashboard" width="820" />
</p>

---

## Why?

Running a local model with [Ollama](https://ollama.com) is easy. Knowing **which** model to run is not:

| Ollama alone | With LocalDeploy |
|---|---|
| You guess whether a model fits your VRAM | Fit-checked before you pull or deploy: fits / tight / won't fit |
| `ollama run` in a terminal | A browser UI: pull, deploy, unload, and switch models with one click |
| No built-in way to compare models | Live benchmarking with a leaderboard, heatmap, and speed-vs-quality view |
| Pick a model by name and hope | Auto-pick ranks your models by accuracy, speed, and VRAM headroom |
| No record of what you tried | Exportable report cards (HTML/JSON) to compare runs later |

LocalDeploy runs *on* Ollama (and optionally llama.cpp) — it doesn't replace it. If you already know exactly which model you want, `ollama run` is enough. LocalDeploy is for the more common case: *"I have this GPU — what should I actually run, and how well does it work?"*

## Quick Start

### Windows

Prerequisites: [Python 3.10+](https://www.python.org/downloads/) and [Ollama](https://ollama.com/download) — `start.ps1` offers to install both via winget if missing.

```powershell
git clone https://github.com/iodriller/localdeploy.git
cd localdeploy
.\scripts\start.ps1
```

That creates `.env`, `config.json`, and a `.venv` on first run, starts Ollama if it's installed, launches the API in the background, and opens the UI at `http://localhost:8000/ui`. Stop with `.\scripts\stop.ps1`.

### macOS / Linux

Prerequisites: Python 3.10+ and [Ollama](https://ollama.com/download) running.

```bash
git clone https://github.com/iodriller/localdeploy.git
cd localdeploy
./scripts/start.sh
```

Runs in the foreground and opens `http://localhost:8000/ui` when ready. Stop with Ctrl+C.

### Docker (bundles Ollama, no Python needed)

```bash
git clone https://github.com/iodriller/localdeploy.git
cd localdeploy
docker compose up --build -d
```

Then open `http://localhost:8000/ui`. Stop with `docker compose down`. The container binds to `127.0.0.1` only; to reach it from other devices see the LAN-mode comments in `docker-compose.yml` and [SECURITY.md](SECURITY.md). For NVIDIA GPU passthrough, uncomment the `deploy.resources` block in `docker-compose.yml` (requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)).

## Your First 3 Minutes

A fresh install starts **empty on purpose** — no sample models, no phantom profiles. Everything you see reflects what's actually on your machine:

1. **Check hardware** — LocalDeploy reads your GPU, VRAM, CPU, and RAM, and shows a fit budget.
2. **Pull a model** — hit *Recommend models for my hardware* for curated fit-checked picks, or pull any Ollama tag (e.g. `gemma3:4b`). Pulls are fit-checked first, so you don't download something too big.
3. **Deploy it** — one click, choosing Auto / GPU / CPU placement. A run profile is created automatically for every model you pull.
4. **Benchmark it** — run it against the built-in 25-test suite to see real accuracy and speed.
5. **Auto-pick** — once a couple of models are pulled, let a preset (Safe Starter / Best Quality / Fast & Low VRAM) rank them for you.

No terminal flags, no VRAM math, no trial-and-error pulls. Full UI guide: [docs/UI.md](docs/UI.md)

<p align="center">
  <img src="docs/screenshots/setup-deploy.png" alt="Setup &amp; Deploy tab: hardware detection, live VRAM, fit budget" width="800" />
</p>

More screenshots (including light theme): [docs/SCREENSHOTS.md](docs/SCREENSHOTS.md)

## Benchmarking

The benchmark tab is a local experiment workspace:

- Runs execute sequentially to avoid VRAM contention, with per-test results streaming in live.
- CPU, GPU, Auto, and CPU + GPU comparison modes for Ollama profiles.
- Leaderboard, category heatmap, speed/quality scatter, and detailed per-test rows.
- Completed runs live in browser `localStorage` — no database — and export as self-contained HTML/JSON report cards you can re-import and compare later.
- Bring your own question set (safe JSON graders, no code execution) or use the built-in suite.

<p align="center">
  <img src="docs/screenshots/benchmark-compare.png" alt="Benchmark &amp; Compare tab: question set, model picker, run history" width="800" />
</p>

## Privacy & Security

- **No telemetry.** The server only talks to local inference backends; backend URLs are enforced loopback-only in code.
- The one internet-touching feature (Hugging Face model search) runs only when you click it. Set `OFFLINE=true` to block it too, and verify with `python scripts/egress_selftest.py`.
- The API binds to `127.0.0.1` by default. Before any LAN exposure, set `API_TOKEN` — see [SECURITY.md](SECURITY.md) for the threat model.

## For Developers

```powershell
.\scripts\start.ps1 -Foreground   # live logs in the terminal
```

- OpenAPI docs at `http://127.0.0.1:8000/docs`.
- **OpenAI-compatible endpoints**: `/v1/chat/completions` (with streaming) and `/v1/models` — point compatible clients at `http://127.0.0.1:8000/v1` with any API key. `/v1/embeddings` is not implemented and returns a clear error pointing at Ollama's native embeddings API.
- Model profiles live in `config.json`. It starts empty and mirrors your machine: pulling a model creates its profile automatically, and the UI can remove profiles whose model is gone. `config.example.json` is a reference for every profile field.
- Terminal chat and model comparison: [docs/CLI.md](docs/CLI.md)
- Full API request schema and limits: [docs/API_OPTIONS.md](docs/API_OPTIONS.md)
- Model catalog with VRAM guidance: [docs/MODELS.md](docs/MODELS.md)
- Tests and local checks: [tests/README.md](tests/README.md) and [CONTRIBUTING.md](CONTRIBUTING.md)

## Contributing

Issues and PRs are welcome — this project favors small, focused changes that keep everything local-first. Start with [CONTRIBUTING.md](CONTRIBUTING.md). Good first contributions: new benchmark questions for the built-in suite, models for the curated starter catalog, and docs fixes.

## License

[MIT](LICENSE)
