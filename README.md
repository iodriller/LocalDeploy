# LocalDeploy

LocalDeploy is a small Windows-friendly project for running local LLMs behind a local HTTP API, then comparing model profiles for speed, quality, stability, and memory safety.

It defaults to Ollama and can optionally call a local llama.cpp server for GGUF experiments. It does not use cloud inference APIs.

## One-command quickstart — nothing to pre-install

**macOS / Linux** (installs Docker automatically if needed, then launches):

```bash
curl -fsSL https://raw.githubusercontent.com/iodriller/localdeploy/main/run.sh | sh
```

**Windows** (PowerShell — installs Docker Desktop via winget if needed, then launches):

```powershell
irm https://raw.githubusercontent.com/iodriller/localdeploy/main/run.ps1 | iex
```

Both scripts clone the repo, build the image, and open **http://localhost:8000/ui** — no
prerequisites beyond a terminal. The image bundles Ollama + the API + the web UI together.

From the UI you can check your hardware, pull a model, start it, and run a benchmark — no config
editing required.

**If you already have Docker installed**, you can skip the install script entirely:

```bash
git clone https://github.com/iodriller/localdeploy.git && cd localdeploy
docker compose up
```

To use your NVIDIA GPU, uncomment the `deploy.resources` block in `docker-compose.yml` (needs
the NVIDIA Container Toolkit). To pre-pull a model on first boot, set `PULL_MODELS=gemma3:4b`
in the compose file.

On macOS/Linux without Docker (Ollama installed separately), `./scripts/start.sh` does the venv
setup and starts the same server.

## What It Provides

- **Web UI** at `/ui` — a two-tab control panel (serve/diagnose + benchmark); see [docs/UI.md](docs/UI.md)

- Local FastAPI server for chat, vision, estimates, profiles, and benchmarks
- Ollama backend at `http://localhost:11434`
- Optional llama.cpp backend at `http://localhost:8080`
- OpenAI-compatible local endpoints for apps that already speak `/v1/chat/completions`
- Safe request limits for prompt size, context, output tokens, image count, image size, and timeouts
- Terminal chat helper for quick manual testing
- Benchmark script for comparing enabled profiles

## Files

- `api_server.py`: local HTTP API server
- `chat_cli.py`: simple terminal chat client
- `test_models.py`: profile benchmark runner
- `install.ps1`: Windows setup helper for Ollama and default model pulls
- `config.example.json`: example model profiles and safety limits
- `.env.example`: example environment settings
- `scripts/chat.ps1`: PowerShell wrapper for terminal chat
- `scripts/smoke_test.ps1`: lightweight validation script

Local files such as `.env`, `config.json`, `logs/`, `reports/`, virtual environments, and model files are ignored by Git.

## Quick Start

```powershell
git clone <repo-url> LocalDeploy
cd LocalDeploy

py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

Copy-Item .env.example .env
Copy-Item config.example.json config.json
.\install.ps1
```

Start the API:

```powershell
.\scripts\start.ps1 -Background
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Run the server in the foreground if you want to see logs:

```powershell
.\scripts\start.ps1
```

## Easy Terminal Testing

One-shot prompt:

```powershell
.\scripts\chat.ps1 -Prompt "How are you?"
```

Interactive chat:

```powershell
.\scripts\chat.ps1
```

Useful interactive commands:

```text
:profiles
:profile gemma3_12b_ollama_safe
:tokens 512
:raw
:quit
```

List configured profiles:

```powershell
.\scripts\chat.ps1 -Profiles
```

## API Examples

Minimal `/chat` request:

```powershell
$body = @{
  profile = "gemma3_4b_ollama_safe"
  prompt = "Explain what this local deployment server does in 3 bullets."
  safe_mode = $true
  max_output_tokens = 256
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8000/chat" -Method Post -ContentType "application/json" -Body $body
```

OpenAI-compatible local request:

```powershell
$body = @{
  model = "gemma3_4b_ollama_safe"
  messages = @(
    @{ role = "user"; content = "Say hello from the local model." }
  )
  max_tokens = 128
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/chat/completions" -Method Post -ContentType "application/json" -Body $body
```

More request options are documented in [docs/API_OPTIONS.md](docs/API_OPTIONS.md).

Swagger UI is available at:

```text
http://127.0.0.1:8000/docs
```

Do not send Swagger placeholder values such as `"profile": "string"` or `max_output_tokens: 0`. Use a real profile name or omit optional fields.

## Benchmarks

Compare enabled profiles:

```powershell
python test_models.py --all --safe-mode true --max-output-tokens 256
```

Test one profile:

```powershell
python test_models.py --profile gemma3_4b_ollama_safe
```

Generated logs and reports should stay local. The repository ignores `logs/`, `reports/`, and benchmark output files.

## Model Profiles

Edit `config.json` to enable, disable, or tune model profiles. The example config includes:

- `gemma3_4b_ollama_safe`
- `gemma3_12b_ollama_safe`
- optional llama.cpp / GGUF 12B profiles for quantization and context testing

For smaller GPUs, start with lower context limits and smaller output limits. Larger models may work but can be slower or memory constrained.

See [docs/MODELS.md](docs/MODELS.md) for a full catalog of recommended models, quantizations, and KV-cache settings tuned for 8 GB VRAM.

## llama.cpp Optional Mode

llama.cpp is not required for Ollama usage. Enable it only after starting a local `llama-server` and setting:

```text
ENABLE_LLAMA_CPP=true
LLAMACPP_BASE_URL=http://localhost:8080
```

Example:

```cmd
llama-server ^
  -m C:\models\model.gguf ^
  -ngl 99 ^
  -c 4096 ^
  -fa ^
  --host 127.0.0.1 ^
  --port 8080
```

## Web UI

A static control panel is served at `/ui` (no build step, no external assets). It wires up
hardware detection, VRAM fit-checks, model pull/serve/switch, Hugging Face update checks, and the
benchmark runner over the existing HTTP API. Disable it with `ENABLE_WEB_UI=false`. Full guide:
[docs/UI.md](docs/UI.md).

## Privacy — verifiably local

LocalDeploy has **no telemetry**. It only talks to your local inference backends; the single
outbound internet call the server process makes is the optional "Check New Models" lookup to
Hugging Face, which you trigger explicitly. Set `OFFLINE=true` to block that too — then the
**LocalDeploy server process** makes no outbound internet connections at all. (A model pull is
fetched by the separate Ollama daemon, not the server process, so pulling a new model still
reaches the internet by design — that action is always user-initiated.)

You don't have to take that on faith. The bundled self-test installs a socket guard that fails if
the app tries to reach any non-loopback address in offline mode:

```bash
python scripts/egress_selftest.py   # prints OFFLINE_SELFTEST_PASS
```

## Network access and security

By default the server binds to `127.0.0.1` (local machine only). To reach it from other machines
on your LAN, set `API_HOST=0.0.0.0` (the Docker image already does this inside the container, and
publishes port 8000).

**Optional token auth (opt-in, zero overhead).** By default there is no auth — nothing to set up.
If you want it, set `API_TOKEN=<secret>` and the HTTP API will require that token (via the
`X-API-Token` header, `Authorization: Bearer <token>`, or `?token=<token>`). The UI stays open so
it can load; open it once at `http://host:8000/ui?token=<secret>` and it remembers the token. This
is also exactly how an OpenAI-style client authenticates (`api_key`), so existing clients work
unchanged.

> Even with a token, only expose `0.0.0.0` on a trusted network — a shared secret is not a
> substitute for transport security. The server always refuses non-loopback *backend* URLs (it will
> not send prompts to a remote inference host) — see `SECURITY.md`.

## Local-Only Notes

The server only calls localhost inference backends. Keep `.env`, `config.json`, model files, logs, and benchmark reports out of Git unless you intentionally sanitize them first.
