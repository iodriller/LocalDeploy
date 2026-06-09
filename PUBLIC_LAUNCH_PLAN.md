# LocalDeploy — Public Launch Plan

> A phased, implementation-ready plan to take LocalDeploy public **without disturbing the
> existing project**. Everything below is **additive**: the current FastAPI server, Ollama /
> llama.cpp backends, benchmark engine, profiles, and PowerShell scripts keep working exactly
> as they do today. New capabilities are layered on top behind new files and new endpoints.

Status: **planning document** (no code changed yet). Follow the phases in order; each phase
ships independently and leaves the repo in a working state.

---

## 1. Goal (definition of success)

Turn LocalDeploy from a CLI/API tool into a project a stranger can clone and run in **one
command**, with a **simple two-tab web UI**:

1. **Tab 1 — Serve & Diagnose:** see the model currently being served, its health/VRAM, and
   start / stop / switch ("pivot") the served model.
2. **Tab 2 — Deploy & Benchmark:** upload a structured set of questions, run the benchmark,
   and watch results **stream** in live.

Plus three first-class buttons the user asked for:

- **Check My Hardware** — detect the GPU (e.g. which RTX), total/free VRAM, and report it.
- **Check New Models** — query Hugging Face for newer model versions and offer to pull them.
- **Fit Check (VRAM validation)** — before pulling/serving, verify the model fits the detected
  VRAM and show a clear error if it will not (e.g. *"This won't fit your 8 GB card."*).

Non-functional targets the request called out, mapped to concrete work:

| User phrase | Interpretation | Where addressed |
|---|---|---|
| "super easy to run", "don't want people setting it up" | One-command launch, no manual wiring | Phase 6 |
| "good network deployment" | Works over LAN / remote, optional cloud GPU | Phase 6 + Appendix C |
| "good cash flow" / sustainability | Sponsorship + optional hosted tier (stretch) | Phase 7 |
| "friendly", "easy for the end user" | Web UI, sensible defaults, guardrails already exist | Phases 3–5 |

> **Assumption log (garbled phrases interpreted, not invented):** "UIS load in VastiPio" → a
> loadable web UI, with optional Vast.ai-style cloud-GPU deployment as a stretch goal (Appendix
> C). "be like Brian / primary time" → treated as transcription noise; no action. If any of
> these guesses are wrong, correct them before Phase 1 — the rest of the plan still holds.

---

## 2. What already exists (do not rebuild)

Verified by inspecting the repo on the launch branch:

- **`api_server.py`** — FastAPI app (`title="Local LLM Server"`). Endpoints today:
  `/health`, `/models`, `/v1/models`, `/profiles`, `/estimate`, `/chat`,
  `/v1/chat/completions` (streaming-capable), `/v1/embeddings`, `/vision`, `/benchmark`.
  Swagger at `/docs`. CORS is already wired behind `ENABLE_CORS`. Safety/estimate logic lives
  in `estimate_request_safety()` and `prepare_request()`, and there is a `require_gpu_only()`
  guard.
- **`localdeploy/backends/ollama.py`** and **`llamacpp.py`** — backend adapters. Ollama at
  `:11434`, optional llama.cpp at `:8080`.
- **`benchmark.py` / `test_models.py`** — capability benchmark. Each test is a `TestCase`
  dataclass: `name`, `category`, `prompt`, `grader`, `grader_explainer`, `max_output_tokens`.
  CLI flags: `--profile`, `--all`, `--max-output-tokens`, `--safe-mode`, `--timeout`,
  `--skip-categories`, `--include-categories`. Graders include JSON extraction and numeric
  tolerance helpers.
- **`config.example.json`** — profiles with rich per-model metadata already including
  `recommended_for_8gb_vram`, `context_limit`, `safe_context_limit`, `quantization`,
  `gpu_layers`, `kv_cache_type_k/v`, etc. **This metadata is the backbone of the Fit Check.**
- **PowerShell scripts** (`scripts/start.ps1`, `stop.ps1`, `chat.ps1`, `smoke_test.ps1`),
  **`install.ps1`**, CI in **`.github/workflows/ci.yml`**, tests in **`tests/`**.
- **`CLAUDE.md`** — already present and solid (see §9; no online fetch needed).

**No web UI exists yet** — the two-tab UI is net new. **The `LICENSE` currently reads
"All rights reserved"** — see the launch blocker in §8.

---

## 3. Architecture decision (keep it boring and additive)

**Serve a tiny static web UI directly from the existing FastAPI app.** No Node build, no
second server, no new runtime to install. This best satisfies "super easy to run".

- New package: **`localdeploy/web/`** for the new endpoints (router), and **`web/`** (repo
  root) for static assets (`index.html`, `app.js`, `styles.css` — plain JS or a single small
  CDN-free framework; no bundler).
- Mount in `api_server.py` with **one block guarded by a flag** so existing behavior is
  untouched when disabled:

  ```python
  # additive, opt-in; default on for the public build, off-able via env
  if enable_web_ui():               # reads ENABLE_WEB_UI, default true
      from localdeploy.web import router as web_router
      app.include_router(web_router)
      app.mount("/ui", StaticFiles(directory="web", html=True), name="ui")
  ```

- All new HTTP endpoints live under a **`/system/*`** and **`/registry/*`** prefix so they
  never collide with the existing API. Streaming reuses the pattern already in
  `/v1/chat/completions`.

Why not Gradio/Streamlit? They pull heavy deps and a second process, fighting the
"one-command, super-easy" goal. A static page + JSON endpoints keeps the footprint tiny and
runs anywhere the API already runs.

---

## 4. New endpoints (contract)

All additive. Names chosen to avoid existing routes.

| Method | Path | Purpose | Notes / data source |
|---|---|---|---|
| GET | `/system/hardware` | GPU model, total & free VRAM, driver/CUDA, CPU/RAM | `nvidia-smi --query-gpu=...` or `pynvml`; graceful CPU-only fallback |
| GET | `/system/status` | Currently served model, backend health, live VRAM use | Wraps existing `/health` + Ollama `/api/ps` |
| POST | `/system/fit-check` | Does `{profile or model_id}` fit detected VRAM? | Compares model size + KV-cache estimate vs free VRAM (see Appendix B) |
| POST | `/models/serve` | Load/warm a model (the "start") | Ollama: a tiny warmup call sets keep-alive; llama.cpp: start `llama-server` |
| POST | `/models/stop` | Unload the served model | Ollama: `keep_alive: 0`; llama.cpp: stop process (reuse `stop.ps1` logic) |
| POST | `/models/switch` | Pivot to a different model | stop current → serve new; reports new `/system/status` |
| GET | `/registry/installed` | Models already pulled locally | Ollama `/api/tags` |
| POST | `/registry/check-updates` | Query Hugging Face for newer versions/tags | HF Hub API (network); diff vs installed |
| POST | `/models/pull` | Pull a model, **stream progress** | `ollama pull` stream; gated by a fit-check pass |
| POST | `/benchmark/validate` | Validate an uploaded question set against the schema | Returns per-row errors, no run |
| POST | `/benchmark/run` | Run benchmark, **stream** per-test results | Wraps `benchmark.py` engine (refactor to importable function) |
| GET | `/benchmark/example` | Return the canonical question-set example | Serves Appendix A verbatim |

**Refactor note (small, safe):** extract the per-test execution loop in `benchmark.py` into an
importable generator (e.g. `iter_run(profiles, cases, ...) -> yields result dicts`) so both the
CLI **and** `/benchmark/run` call the same code. The CLI keeps its current behavior; it just
becomes a thin wrapper. This honors "solve the underlying pattern, not one example."

---

## 5. Phased implementation

Each phase is shippable and leaves tests green. Suggested commits in parentheses.

### Phase 0 — Scaffolding & guardrails (½ day)
- Add `localdeploy/web/__init__.py` (empty router), `web/` static dir with a placeholder page,
  and the **flag-guarded mount** in `api_server.py`. Add `enable_web_ui()` to the env helpers.
- Add deps to `requirements.txt`: `huggingface_hub` (registry checks) and optionally `pynvml`
  (hardware). Keep them optional-import so the server still boots if absent.
- Acceptance: with `ENABLE_WEB_UI=false` the server behaves byte-for-byte as today; with it on,
  `/ui` serves the placeholder and `/docs` still lists all original endpoints.

### Phase 1 — Hardware detection + Fit Check (1 day)
- Implement `/system/hardware` (parse `nvidia-smi`; fall back to "no NVIDIA GPU detected").
- Implement `/system/fit-check` using the math in **Appendix B**, reading model/quant/context
  from the profile or a pulled model's metadata.
- Wire **"Check My Hardware"** + inline fit warnings into the UI shell.
- Acceptance: on an 8 GB card, a 12B Q4 long-context profile returns a clear *won't-fit* verdict
  with the numbers shown; a 4B safe profile returns *fits*.

### Phase 2 — Model registry / Hugging Face (1–1.5 days)
- `/registry/installed` (Ollama tags), `/registry/check-updates` (HF Hub `list_models` /
  revision compare), `/models/pull` with streamed progress.
- **Gate `/models/pull` behind a fit-check** so a user can't pull something their card can't run
  without an explicit override.
- Wire **"Check New Models"** button → list of newer versions with a "Pull" action per row.
- Acceptance: clicking the button lists candidate updates; pulling streams progress and the new
  model then appears in `/registry/installed`. Network failures degrade gracefully with a clear
  message (no crash).

### Phase 3 — Tab 1: Serve & Diagnose (1 day)
- `/system/status`, `/models/serve`, `/models/stop`, `/models/switch`.
- UI Tab 1: current model card (name, backend, VRAM in use, health), Start / Stop / Switch
  controls, profile picker.
- Acceptance: starting a model shows it as served in `/system/status`; switching unloads the old
  and loads the new; stopping frees VRAM (observable via `/system/hardware`).

### Phase 4 — Tab 2: Deploy & Benchmark with streaming (1.5 days)
- Refactor `benchmark.py` into the importable generator (see §4). Add `/benchmark/validate`,
  `/benchmark/run` (streamed), `/benchmark/example`.
- UI Tab 2: upload a question set (Appendix A schema), "Validate", "Run", and a live-streaming
  results panel (per-test score, latency, pass/fail) with a summary at the end. **Show the
  example structure right in the UI** (a "Load example" link populating the editor).
- Acceptance: uploading the example set, validating, and running streams each test result and
  ends with an aggregate table; an intentionally malformed row is rejected by `/benchmark/validate`
  with a row-specific message.

### Phase 5 — UI polish & one-screen flow (½–1 day)
- Two clean tabs, empty-state hints, error toasts, dark-friendly CSS, no external CDNs (offline
  friendly). Reuse existing guardrail messages from `estimate_request_safety`.
- Acceptance: a first-time user can go hardware → pull → serve → benchmark without reading docs.

### Phase 6 — "Super easy to run" + network deployment (1 day)
- **`Dockerfile` + `docker-compose.yml`** bundling the API, the UI, and an Ollama service, so
  `docker compose up` is the entire setup. Document GPU passthrough (`--gpus all`).
- **Cross-platform launcher:** keep the PowerShell scripts; add `scripts/start.sh` and a
  top-level `make run` (or `run.py`) so macOS/Linux users get the same one-command start.
- Bind options + a clear note for **LAN/remote** access (`API_HOST=0.0.0.0`, enable CORS), with
  a security caveat (it has no auth — see §8).
- Acceptance: on a clean machine, `docker compose up` (or `./scripts/start.sh`) yields a working
  `/ui` with no manual config editing.

### Phase 7 — Public-repo readiness & sustainability (1 day) — see §8

---

## 6. Two-tab UI sketch

```
┌────────────────────────────────────────────────────────────────────┐
│  LocalDeploy           [ Serve & Diagnose ]   [ Deploy & Benchmark ]│
├────────────────────────────────────────────────────────────────────┤
│  TAB 1: Serve & Diagnose                                            │
│  ┌──────────────── Hardware ───────────────┐  [ Check My Hardware ] │
│  │ GPU: RTX 4060  •  VRAM 8.0 GB (5.9 free) │                       │
│  └──────────────────────────────────────────┘                      │
│  Served model:  gemma3_4b_ollama_safe  •  healthy  •  using 4.1 GB  │
│  Profile ▼ [gemma3_4b_ollama_safe]   [ Start ] [ Stop ] [ Switch ] │
│                                                                     │
│  Models      [ Check New Models ]                                   │
│   • gemma3:4b      installed   (update available → 4b-it-qat) [Pull]│
│   • qwen3vl:8b     not installed     ⚠ won't fit 8 GB              │
└────────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────────┐
│  TAB 2: Deploy & Benchmark                                         │
│  Question set:  [ Load example ]  [ Upload .json ]  [ Validate ]   │
│  ┌─ editor (JSON, Appendix A schema) ──────────────────────────┐   │
│  │ [ { "name": "...", "category": "...", "prompt": "..." } ]   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│  Profile ▼ [gemma3_4b_ollama_safe]      [ Run benchmark ▶ ]         │
│  ── live results (streaming) ───────────────────────────────────   │
│   ✓ planning/triage      score 1.00   1.8 s                        │
│   ✓ classification/spam  score 1.00   0.9 s                        │
│   … summary: 7/8 passed, avg 1.6 s                                 │
└────────────────────────────────────────────────────────────────────┘
```

---

## 7. Effort & sequencing

Roughly **7–9 working days** for Phases 0–6, plus ~1 day for Phase 7. Phases 1–2 (hardware +
registry) are independent and can be built in parallel with Phase 3–4 (serve/benchmark UI) once
Phase 0 lands. Ship after each phase; the repo is always demoable.

---

## 8. Public-launch readiness (Phase 7) — including a blocker

- **🚨 LICENSE BLOCKER:** the current `LICENSE` says **"All rights reserved."** A public repo
  with that license tells the world they may **not** use it. Pick a real OSS license (MIT or
  Apache-2.0 are the usual choices for a tool like this) **before** flipping the repo public,
  and update the `Copyright` line. *This is a decision for the owner — confirm before changing.*
- **No auth on the API:** it binds to localhost today. The moment we document `0.0.0.0` for
  "network deployment", anyone on the LAN can drive the model. Document this loudly; consider an
  optional API-token middleware as a fast-follow.
- **Docs:** add a top "60-second quickstart" to `README.md` (Docker one-liner + screenshot),
  a `web/` UI section, and a short `CONTRIBUTING` refresh. Add UI screenshots/GIF.
- **Repo hygiene:** confirm `.gitignore` already excludes `reports/`, `logs/`, models, `.env`,
  `config.json` (it does). Add issue/PR templates check (present under `.github/`).
- **CI:** extend `.github/workflows/ci.yml` to lint/test the new `localdeploy/web` module and
  validate the example question set against the schema.
- **Sustainability ("good cash flow"):** add `FUNDING.yml` (GitHub Sponsors), a clear README
  "Support" section, and keep an optional **hosted/managed tier** as a documented stretch (do
  not build it for v1). Optional Vast.ai / cloud-GPU deploy recipe lives in Appendix C.

---

## 9. CLAUDE.md

`CLAUDE.md` **already exists** and is well-formed (Working Style, Minimal Implementation,
Holistic Solutions, Accuracy, Local Runtime, Testing, Reporting, Maintenance). **No online
fetch is needed** (the user's request was conditional on it being missing). Recommended light
additions for the public phase, to be made as their own small commit:

- A "Public surface" note: changes to documented endpoints, the question-set schema, or the UI
  contract are user-facing and need a `CHANGELOG.md` entry.
- A reminder that new features must stay **flag-guarded and additive** until a phase is complete.

---

## Appendix A — Structured question-set schema (for upload + the in-UI example)

Mirrors the existing `TestCase` dataclass so uploaded sets feed the real benchmark engine.
Graders that need code (custom `grader` callables) are referenced by **name** from a built-in
grader registry, so uploads stay safe JSON (no arbitrary code execution).

```json
{
  "version": 1,
  "questions": [
    {
      "name": "planning_triage_basic",
      "category": "planning",
      "prompt": "List 3 first steps to triage a service outage. Return a JSON array of strings.",
      "max_output_tokens": 512,
      "grader": { "type": "json_array_min_len", "min": 3 },
      "grader_explainer": "Passes if the model returns a JSON array with at least 3 steps."
    },
    {
      "name": "math_tolerance",
      "category": "reasoning",
      "prompt": "What is 12.5% of 240? Answer with the number only.",
      "max_output_tokens": 32,
      "grader": { "type": "number_within", "expected": 30, "tolerance": 0.5 },
      "grader_explainer": "Passes if the parsed number is within 0.5 of 30."
    }
  ]
}
```

Built-in grader `type`s map to helpers already in `benchmark.py` (`_extract_json`,
`_grade_number`, etc.): `contains_all`, `json_array_min_len`, `number_within`,
`exact_match`, `classification_set`. `/benchmark/validate` checks every row against this schema
and the available grader types before any run.

---

## Appendix B — VRAM fit-check math (transparent, approximate)

Goal: a clear yes/no with the numbers shown, not a black box.

```
required_GB ≈ weights_GB + kv_cache_GB + overhead_GB

weights_GB     ≈ params_B * bytes_per_param(quant)      # e.g. Q4 ≈ 0.5 GB per 1B params
kv_cache_GB    ≈ 2 * n_layers * n_kv_heads * head_dim * context * kv_bytes / 1e9
overhead_GB    ≈ 0.6–1.0 (CUDA context + activations)   # conservative constant

verdict:  required_GB <= free_VRAM_GB ? FITS : WONT_FIT
```

- Pull `params_B`, `quant`, and `context` from the profile (`config.json`) or the model's
  Hugging Face / Ollama metadata. KV bytes come from `kv_cache_type_k/v` already in profiles.
- Always show the breakdown and a margin (e.g. "needs ~6.8 GB, you have 5.9 GB free → won't
  fit"). Offer the obvious knobs that already exist: smaller quant, `safe_context_limit`,
  partial offload profiles.
- Keep it **approximate and honest** (per CLAUDE.md): label it an estimate; the real proof is a
  short warmup, which `/models/serve` performs and reports.

---

## Appendix C — Optional cloud-GPU deployment (stretch, "good network deployment")

For users without a capable local GPU, document (do **not** hard-wire) a recipe to run the same
Docker image on a rented GPU host (Vast.ai / RunPod style): expose the API on the instance,
tunnel or bind with auth, point the UI at it. This is a docs deliverable for after v1, not a
code dependency — the local-first design stays the default.

---

## Quick reference — files this plan adds (none removed)

```
web/                      # static UI (index.html, app.js, styles.css)  [new]
localdeploy/web/          # FastAPI router for new endpoints             [new]
Dockerfile                # one-command run                              [new]
docker-compose.yml        # API + UI + Ollama                           [new]
scripts/start.sh          # cross-platform launcher                     [new]
docs/UI.md                # UI + schema docs                            [new]
.github/FUNDING.yml       # sponsorship                                 [new, Phase 7]
benchmark.py              # small refactor: importable run generator     [edited, behavior-preserving]
api_server.py             # flag-guarded UI mount + include_router       [edited, additive]
requirements.txt          # + huggingface_hub (and optional pynvml)      [edited]
LICENSE                   # change "All rights reserved" → OSS license   [owner decision, Phase 7]
```

---

# Extra additions — high-value differentiators (post-core)

> Reviewed after the core plan. These are **not** filler. Each one (a) is something the obvious
> competitors (Ollama, LM Studio, Jan, GPT4All, Open WebUI) do **not** do well, (b) is built
> almost entirely from machinery this repo **already has** (benchmark engine, profile metadata,
> fit-check, hardware probe), and (c) earns its place on launch day. Build them only **after**
> Phases 0–6 are green; none is on the critical path. Listed in priority order.

### D1 — Shareable, reproducible "Report Cards" (the growth loop)
- **What:** export any benchmark run as a single self-contained file (`.html` + `.md`) that
  bundles **model + exact profile/config + detected hardware + per-test scores + latencies**.
  An "Export / Share card" button in Tab 2.
- **Why it's a differentiator:** nobody else produces *hardware-specific, reproducible* model
  report cards. Every card someone posts ("gemma3:4b on an RTX 4060: 7/8, 1.6 s avg") is both
  honest proof and free distribution that points back to the project. This is the cheapest
  organic-growth lever available and it raises trust at the same time.
- **Built-in A/B compare (subsumes regression-guarding):** drop two cards in to diff them —
  old model vs new model, or quant A vs quant B. This directly answers *"is the Hugging Face
  update actually better, or just newer?"* before a user commits to switching.
- **Minimal build:** a serializer over the existing `benchmark.py` result dicts +
  `/system/hardware`. No new engine. ~1 endpoint (`/benchmark/export`) + a static template.

### D2 — One-click "Tune for my GPU" (kills the config burden)
- **What:** a single button that, given the detected hardware, picks the candidate profiles that
  *fit*, runs a short benchmark subset, and recommends the best by a transparent
  speed × quality × headroom score — then offers to set it as the default.
- **Why it's a differentiator:** every competing tool makes the user *guess* a model/quant/context
  combo and discover failure by OOM or slowness. Turning `config.json` from homework into one
  button is the strongest possible expression of the "super easy, don't make people set it up"
  goal — and it's defensible because it leans on the fit-check + benchmark this repo uniquely has.
- **Minimal build:** orchestration only — loop fit-check over profiles, call the existing
  benchmark generator with a small case subset, rank. 1 endpoint (`/system/recommend`), reuses
  Appendix B math and Phase 4 streaming.

### D3 — Verifiable "truly local" / privacy posture (the trust anchor)
- **What:** an explicit **offline/airplane mode** (`OFFLINE=true`) that blocks all outbound
  network except user-initiated model pulls, a **no-telemetry-ever** promise stated plainly, and
  a one-command **egress self-test** users can run to confirm nothing phones home.
- **Why it's a differentiator:** "local LLM" tools are adopted largely *for* privacy, yet most
  ask you to take it on faith. A claim a user can **verify themselves** is a credibility moat,
  especially for the privacy-conscious / regulated audiences most likely to share a public repo.
- **Minimal build:** one env flag honored at the HTTP-client boundary, a short pytest that asserts
  no egress in offline mode, and a README badge + section. Mostly wiring and docs, little code.

### Deliberately excluded (kept out on purpose, to stay focused)
Considered and **rejected for v1** — each would broaden scope, add heavy deps, or dilute the
"local serving + honest benchmarking" identity: general **RAG / document chat** (a different
product), **multi-user accounts / sharing server**, **in-app fine-tuning or training**, a
**plugin/model marketplace**, and a **mobile app**. The OpenAI-compatible endpoints already in
`api_server.py` cover "use it from my existing app," so no new integration surface is needed.
Revisit only if real post-launch demand appears.
