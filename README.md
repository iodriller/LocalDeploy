# LocalDeploy

Run AI models on your own machine — no cloud, no subscriptions, nothing leaves your computer.
Pull a model, start it, and benchmark it from a browser tab.

---

## Get started

**macOS or Linux** — paste this in a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/iodriller/localdeploy/main/run.sh | bash
```

**Windows** — paste this in PowerShell:

```powershell
irm https://raw.githubusercontent.com/iodriller/localdeploy/main/run.ps1 | iex
```

That's the whole setup. The script installs Docker if you don't have it, downloads everything,
and starts the server. Then open **http://localhost:8000/ui** in your browser.

---

## What you can do in the UI

- **Check your hardware** — see your GPU, VRAM, and which models will fit
- **Pull a model** — type a name like `gemma3:4b`; the UI blocks pulls that won't fit your VRAM
- **Start / stop / switch** models without touching a config file
- **Run benchmarks** — upload a question set or use the built-in one; export a report card; compare two models
- **Find newer models** — scan Hugging Face for GGUF updates and pull with one click

Full UI guide: [docs/UI.md](docs/UI.md)

---

## Stop and update

```bash
cd ~/localdeploy
docker compose down                                          # stop
docker compose pull && docker compose up --build -d         # update to latest
```

---

## NVIDIA GPU

Uncomment the `deploy.resources` block in `docker-compose.yml` (requires the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)).

---

## Already have Docker?

```bash
git clone https://github.com/iodriller/localdeploy.git
cd localdeploy
docker compose up
```

Then open **http://localhost:8000/ui**.

---

## Privacy

No telemetry. The server only talks to local inference backends. The only outbound call it ever
makes is the optional "Check New Models" lookup to Hugging Face — which you trigger manually.
Set `OFFLINE=true` to block that too.

---

## For developers

<details>
<summary>Manual install without Docker (macOS/Linux, Ollama already running)</summary>

```bash
git clone https://github.com/iodriller/localdeploy.git
cd localdeploy
./scripts/start.sh
```

</details>

<details>
<summary>OpenAI-compatible API</summary>

The server exposes `/v1/chat/completions` and `/v1/models` so any app that talks to OpenAI
works against your local model. Point it at `http://127.0.0.1:8000` with any API key.

Swagger docs: `http://127.0.0.1:8000/docs`

More options: [docs/API_OPTIONS.md](docs/API_OPTIONS.md)

</details>

<details>
<summary>Optional token auth</summary>

By default there is no auth — nothing to set up. If you want it, set `API_TOKEN=<secret>` in
your environment or `docker-compose.yml`. The API then requires the token via the `X-API-Token`
header, `Authorization: Bearer`, or `?token=`. Open the UI once at
`http://localhost:8000/ui?token=<secret>` and it remembers it.

See [SECURITY.md](SECURITY.md) for the full threat model.

</details>

<details>
<summary>Model profiles and config</summary>

Edit `config.json` (copied from `config.example.json`) to enable, disable, or tune model
profiles. The example config includes `gemma3:4b` and `gemma3:12b` Ollama profiles and optional
llama.cpp GGUF profiles.

Model catalog with VRAM recommendations: [docs/MODELS.md](docs/MODELS.md)

</details>
