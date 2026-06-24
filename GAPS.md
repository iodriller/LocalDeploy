# GAPS.md — known gaps, limitations, and unverified areas

A living, honest record of what is **not** fully done or proven on the public-launch feature set
(the `/ui` web UI, the control-plane API, Docker, and the report-card / tune / offline
differentiators). The original CLI, OpenAI-compatible API, and loopback-only backend guard are
unchanged and not in scope here.

Status legend: **Open** (worth doing) · **By design** (intentional trade-off) ·
**Unverified** (couldn't prove in this environment) · **Owner action** (a human decision).

Last reviewed against `main` at commit `e4451c7` (Benchmark workspace V2, device-aware deploy, and UI fixes).

---

## 1. Fixed since the first gap analysis (for the record)

These were found and resolved before this document:

- Self-call host normalized (`0.0.0.0`/`::` → `127.0.0.1`) in `benchmark.api_base_url()`.
- Report-card HTML round-trip now un-escapes `&lt;`/`&gt;`/`&amp;` correctly in `app.js`.
- `POST /system/set-default` implemented (the missing half of "Tune for my GPU"), guarded so it
  cannot overwrite `config.example.json`.
- `recommend` wraps per-test execution so a failure degrades instead of 500-ing.
- Installed-model list auto-runs the fit check per row.
- CHANGELOG, SECURITY, and the README offline claim updated.

Verification at time of writing: **213 tests pass** (expanded with benchmark V2, device-aware deploy,
warmup-timeout, and model-management suites). Ruff clean, offline egress self-test passes,
UI verified end-to-end via headless DOM (incl. SSE streaming, export, compare, recommend,
set-default).

---

## 2. Feature gaps vs. the original vision

### 2.1 Hugging Face → pull bridge — **Done (for GGUF repos)**
"Check New Models" now filters to **GGUF** repos (`filter="gguf"`) and gives each candidate a
**Pull** button that pulls it via Ollama's `hf.co/<id>` shortcut (fit-checked like any pull).
Remaining limitation: non-GGUF repos (e.g. safetensors-only `google/gemma-3-4b-it`) are still
link-only, because Ollama can't pull those directly. *Impact: the common GGUF case is one click;
safetensors-only repos remain manual.*

### 2.2 `recommend` is synchronous (no progress streaming) — **Open**
`POST /system/recommend` runs several models × N tests in one blocking request; the UI just shows a
spinner with no per-step progress. For many enabled profiles on a slow box this can take a while and
could hit proxy/idle timeouts. `/benchmark/run` streams; `recommend` does not. *Impact: UX on long
runs; a larger change to stream it.*

### 2.3 `recommend` benchmarks fit-`UNKNOWN` profiles — **By design / inherent**
When VRAM can't be determined (CPU-only host, or no target VRAM provided), fit-check returns
`UNKNOWN`, which is **not** skipped — so models that may not fit still get benchmarked. Without VRAM
data there's no safe way to pre-filter. *Impact: wasted time on a host with no detectable GPU.*

### 2.4 llama.cpp serve/stop don't manage the process — **By design**
`/models/serve` and `/models/stop` fully drive Ollama, but for the llama.cpp backend they only
health-check and return guidance to the existing `scripts/start_llamacpp.ps1` / `stop.ps1`.
Spawning/killing a `llama-server` process from the API was judged fragile and platform-specific, so
it's intentionally omitted. *Impact: llama.cpp lifecycle is manual.*

### 2.5 Per-row pull from "Discover on Hugging Face" — **Done**
GGUF candidates now have a per-row **Pull** button via Ollama's `hf.co/<id>` shortcut (same
fit-gate as a manual pull). Non-GGUF repos (safetensors-only) remain link-only because Ollama
cannot pull them directly.

---

## 3. Robustness / minor

- **Auto fit-check fan-out** — refreshing the installed list fires one `/system/fit-check` per
  model in parallel, each shelling out to `nvidia-smi`. Fine for a handful of models; could be
  batched if someone has dozens. *(Minor.)*
- **`set-default` materializes `config.json`** — first call writes a full `config.json` seeded from
  the loaded config (which may be the example). Intended, but it does create the file. *(Expected.)*
- **`detect_hardware()` runs `nvidia-smi` every call** — `/system/status` and `/system/fit-check`
  re-probe each time; not cached. Negligible locally; could be cached if status is polled hard.
- **Report-card round-trip is exact for normal data**; the earlier `<`/`>` corruption is fixed.
  Adversarial content is HTML-escaped on render, so there's no injection — only display fidelity
  was ever at risk, and that's resolved.

---

## 4. Security posture — **Opt-in auth available; off by default**

The control-plane is unauthenticated **by default** (zero overhead, matching the loopback-only
assumption). Opt-in auth now exists: set `API_TOKEN=<secret>` and every data endpoint requires it
(via `X-API-Token`, `Authorization: Bearer`, or `?token=`); the static UI and `/health` stay open so
the page can load and remember the token. This is the recommended control before any LAN exposure
(`API_HOST=0.0.0.0`). Remaining caveats: a shared token is **not** transport security (use a tunnel/
TLS for real exposure), and there's still no rate limiting or per-user accounting. Other mitigations:
keep the bind on loopback, or `ENABLE_WEB_UI=false`. The backend loopback guard (no prompts to remote
inference hosts) is always enforced.

---

## 5. Unverified in this environment — **Unverified**

These are coded and statically checked but could not be executed here:

- **Docker build & run** — no Docker daemon was available. `docker compose config` validates and
  the shell scripts pass `bash -n`, but the image was never built or run. The `apt`/`pip`/
  `huggingface_hub` install, the entrypoint's `ollama serve` + readiness loop, port publishing, and
  NVIDIA GPU passthrough are all **unproven**. First real `docker compose up` on a host with a
  daemon is the test.
- **Live model paths** — a real `ollama pull` completing, real benchmark scores against a served
  model, real serve/stop/switch VRAM changes, and a GPU-backed fit-check were all exercised against
  a **backend-down** server (graceful/streaming paths only). No Ollama or GPU was present.
- **`scripts/egress_selftest.py`** passes locally but is **not wired into CI**.

---

## 6. Test / doc housekeeping — **Open (low)**

- `scripts/egress_selftest.py` could be added as a CI step to lock in the offline guarantee.
- `tests/README.md` lists only the original three test files; it doesn't mention the newer
  `test_web_*`, `test_benchmark_*`, `test_model_management`, `test_warmup_timeout`, and
  `test_device_target` suites (213 tests total as of `e4451c7`).

---

## 7. Owner actions before going fully public — **Owner action**

- **LICENSE** still reads "All rights reserved" — a public repo needs a real OSS license
  (MIT/Apache-2.0) and an updated copyright line. *This is a deliberate human decision, untouched.*
- **Repo visibility** flip (private → public) is the owner's to perform.
- Consider adding **auth** (see §4) if the UI will ever be reachable beyond loopback/trusted LAN.
- Consider a **GitHub Sponsors / FUNDING.yml** and UI screenshots/GIF for the launch (plan
  Phase 7 / differentiator notes).

---

## 8. Explicitly out of scope (chosen, not missed)

From the launch plan's "deliberately excluded" list, intentionally **not** built for v1: general
RAG / document chat, multi-user accounts, in-app fine-tuning/training, a plugin/model marketplace,
and a mobile app. The OpenAI-compatible endpoints already cover "use it from my own app."
