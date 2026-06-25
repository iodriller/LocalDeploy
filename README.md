<p align="center">
  <img src="web/favicon.png" width="72" height="72" alt="LocalDeploy icon" />
</p>

<h1 align="center">LocalDeploy</h1>

<p align="center">
  <strong>Run, manage, and benchmark local AI models from one browser UI.</strong>
</p>

<p align="center">
  LocalDeploy keeps model serving on your machine: no cloud inference, no subscriptions, and no telemetry.
</p>

---

## What It Does

- **Deploy local models** through Ollama-backed saved profiles.
- **Check hardware and fit** before pulling or deploying a model.
- **Pull models safely** with VRAM-aware warnings and streamed download logs.
- **Benchmark profiles live** with per-test results appearing as each test finishes.
- **Compare runs** with a leaderboard, heatmap, speed/quality view, detailed rows, and exportable report cards.
- **Find a good default** by fit-checking saved profiles and benchmarking candidates against your GPU budget.

Full UI guide: [docs/UI.md](docs/UI.md)

---

## Quick Start

### Windows

Paste this in PowerShell:

```powershell
irm https://raw.githubusercontent.com/iodriller/localdeploy/main/run.ps1 | iex
```

### macOS or Linux

Paste this in a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/iodriller/localdeploy/main/run.sh | bash
```

The installer prepares the app, starts the server, and opens the UI. If it does not open automatically, go to:

```text
http://localhost:8000/ui
```

---

## Existing Clone

From the repo root, start the local API and browser UI:

```powershell
.\scripts\start_ui.ps1
```

Or start the API in the background and open the UI yourself:

```powershell
.\scripts\start.ps1 -Background -OpenUI
```

The default UI URL is:

```text
http://127.0.0.1:8000/ui
```

The launcher honors `API_HOST` and `API_PORT` from `.env`, so custom ports are reflected in the opened URL.

---

## UI Workflow

1. **Check hardware** to detect GPU, VRAM, CPU, RAM, and live memory state.
2. **Review the model fit budget** so scans and pulls use the right GPU target.
3. **Pull a model** by name, or scan saved profiles and Hugging Face GGUF results.
4. **Deploy a saved profile** and keep it warm with a configurable keep-alive.
5. **Run benchmarks** across one or more profiles and devices.
6. **Watch streamed results** update the queue, leaderboard, detailed table, and comparison views as each test completes.
7. **Export report cards** or compare selected runs locally in the browser.

---

## Benchmarking

The benchmark workspace is built for local model decisions:

- Runs execute sequentially to avoid VRAM contention.
- The queue clearly separates queued, deploying, running, finished, failed, and stopped runs.
- Active runs stream test results immediately into the dashboard and detailed table.
- CPU, GPU, Auto, and CPU + GPU modes are supported for Ollama profiles.
- Completed runs are saved in browser `localStorage`; no backend database is required.
- Report cards can be exported as HTML or bundled as JSON for later comparison.

---

## Docker

Already have Docker?

```bash
git clone https://github.com/iodriller/localdeploy.git
cd localdeploy
docker compose up --build -d
```

Then open:

```text
http://localhost:8000/ui
```

To stop or update:

```bash
docker compose down
docker compose pull && docker compose up --build -d
```

---

## GPU Notes

For NVIDIA GPU passthrough in Docker, uncomment the `deploy.resources` block in `docker-compose.yml`.
This requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).

llama.cpp is optional. If `ENABLE_LLAMA_CPP=true` but no enabled llama.cpp profile or GGUF file is configured, the local launcher skips that backend and still starts the API/UI. Start llama.cpp directly when you want that backend to be fatal on misconfiguration:

```powershell
.\scripts\start_llamacpp.ps1
```

---

## Privacy

LocalDeploy has no telemetry. The server only talks to local inference backends unless you manually run the Hugging Face model lookup. Set `OFFLINE=true` to block outbound lookup calls too.

See [SECURITY.md](SECURITY.md) for the threat model.

---

## For Developers

Manual launch without Docker:

```powershell
.\scripts\start.ps1 -Background -OpenUI
```

OpenAPI docs:

```text
http://127.0.0.1:8000/docs
```

OpenAI-compatible endpoints:

- `/v1/chat/completions`
- `/v1/models`

Point compatible clients at `http://127.0.0.1:8000` with any API key.

More API options: [docs/API_OPTIONS.md](docs/API_OPTIONS.md)

Model profiles live in `config.json`, copied from `config.example.json` on first setup. The example config includes Ollama profiles and optional llama.cpp GGUF profiles.

Model catalog with VRAM recommendations: [docs/MODELS.md](docs/MODELS.md)
