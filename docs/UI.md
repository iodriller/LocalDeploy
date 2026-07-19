# LocalDeploy Web UI

A lightweight control panel served by the API server itself at **`http://<host>:<port>/ui`**
(default `http://127.0.0.1:8000/ui`). It is plain static HTML/CSS/JS — no build step, no extra
runtime, no external/CDN assets — so it runs anywhere the API runs and works fully offline.

The UI is **opt-out**: set `ENABLE_WEB_UI=false` to disable it (the API behaves exactly as it did
before the UI existed).

## Launching

Installed via pip/pipx, one command serves the API + UI and opens the browser
(`--no-browser`, `--host`, `--port` to override; state lives in `~/.localdeploy`
or `LOCALDEPLOY_HOME`):

```bash
localdeploy
```

From a local checkout on Windows:

```powershell
.\scripts\start.ps1
```

That starts the API in the background if needed, waits for `/health`, and opens `/ui`.
For API-only startup without a browser, run:

```powershell
.\scripts\start.ps1 -NoBrowser
```

For foreground logs while developing:

```powershell
.\scripts\start.ps1 -Foreground
```

The launcher uses `API_HOST` and `API_PORT` from `.env`; if `API_HOST=0.0.0.0`, the browser URL
uses `127.0.0.1`.

The UI does not require llama.cpp. During normal API/UI startup, incomplete optional llama.cpp
configuration is skipped with a warning so Ollama-backed profiles and diagnostics remain usable.
Run `.\scripts\start_llamacpp.ps1` directly when you are intentionally bringing up a GGUF profile
and want missing server/model paths to fail fast.

## First-time flow

A newcomer can go end-to-end without reading anything else:

1. **Check hardware** — detects your GPU and free VRAM (or reports CPU-only).
2. **Manual pull by model name** — type an Ollama name (e.g. `gemma3:4b`) and pull it; progress
   streams live. The pull is **fit-checked** first and blocked only for hard "fits nowhere"
   warnings unless **Warn only; pull anyway** is checked.
3. **Deploy a profile** — load the model into memory with an Ollama keep-alive.
4. **Chat tab** — talk to the model you just deployed, streaming, right in the browser.
5. **Monitor tab** — watch live VRAM, throughput, and request history while it serves.
6. **Benchmark & Compare tab** — load the example question set, **Validate**, then **Run**.

## Tab 1 — Setup & Deploy

| Control | What it does | Endpoint |
|---|---|---|
| Check hardware | NVIDIA/AMD/Intel/Apple GPU inventory, compatible VRAM pools, CPU, cores, and RAM | `GET /system/hardware` |
| Refresh status | Loaded model(s), Ollama health, VRAM, **GPU/CPU placement** | `GET /system/status` |
| Deploy to (Auto/GPU/CPU) | Force where the model runs (`num_gpu`: 0 = CPU, max = GPU) | `POST /models/serve` |
| Deploy / unload / replace | Load / unload / replace the selected profile | `POST /models/{serve,stop,switch}` |
| Pull / Cancel | Download an Ollama model, streamed, fit-gated; Cancel aborts an in-flight pull | `POST /models/pull` |
| Fit check (per model) | Tiered estimate: green (comfortable), yellow (tight / CPU-only), red (won't fit) | `POST /system/fit-check` |
| Delete | Remove a model from disk (frees space) | `POST /models/delete` |
| Free memory | Unload all models from memory/VRAM | `POST /models/free` |
| Search Hugging Face | Newer or matching GGUF models on Hugging Face | `POST /registry/check-updates` |
| Refresh providers | Models from local Ollama/OpenAI-compatible runtimes with params, quant, context, and saved tok/s | `GET /registry/providers` |
| Refresh installed | Models already pulled locally | `GET /registry/installed` |

The **Model fit budget** is auto-filled from the hardware probe and is used by installed-model
badges, saved-profile scans, Hugging Face search, fit checks, and the pull gate. You can override it
to test against a different card or current free VRAM.

`config.json` mirrors what is actually on your machine: pulling a model auto-creates its profile.
Profiles whose model is gone (never pulled, or deleted outside the UI) are annotated everywhere and
can be removed in one click with **Advanced → All run profiles → Remove not-pulled profiles**. For
llama.cpp profiles the server checks whether the GGUF file still exists on disk.

**Recommended models** (Get a model → ★ Recommended, the default segment) is the guided path for
"which model should I install for what I'm actually doing?" Pick a use case (general assistant,
coding, document analysis, structured extraction, reasoning, vision, multilingual, tool calling), a
priority (balanced, best quality, fastest, lowest memory, longest context), and an expected context
size, then **Recommend models for me** returns up to three fit-checked, labeled picks — Recommended,
Faster, Higher quality — each with quality tier, estimated VRAM/headroom, published context window,
and a confidence level (`POST /registry/recommend`). Every "why this model?" reason is tagged by
provenance — **estimated** (this app's formula), **published** (the model's own spec), or **measured
here** (from your own saved benchmark runs for that model) — so a recommendation never quietly mixes
a guess with a fact. **Download and start** pulls (if needed) and deploys the pick in one action.

**Model catalog** (Get a model → ⌕ Model catalog) inventories every model your local
runtimes expose (Ollama, LM Studio, vLLM, Docker Model Runner, llama.cpp, OpenAI-compatible
servers) with search, runtime/size filters, sorting (including measured tok/s from your own
saved benchmark runs), and pagination. Reachability chips explain how to enable each runtime;
one click creates a run profile for any listed model (`GET /registry/providers`).

The catalog's size chips are colored by fit against your detected hardware (green fits
the GPU, yellow tight/CPU-only, red won't fit; one batched `POST /system/fit-batch`
per search), and Hugging Face rows carry a row-level fit badge when the repo name
encodes a parameter count.

**Quant advisor** (Get a model → ⚖ Quant advisor) fit-checks every common GGUF quantization
(Q2_K → F16) of one model size against your budget using the same estimator as fit checks, and says
when there's headroom for a higher-quality tag than the usual Q4 default
(`POST /system/quant-advisor`). Each quant row's **Pull it** column shows the family's real published
tag with its true download size (fetched from ollama.com via
`POST /registry/library-tags`); quants with no published tag say so, and an expandable
list offers every published tag as a one-click pull.

**Disk usage** lives in the Your models card: a `N models · X GB on disk` summary, a sort control
(largest / recently updated / name), and per-row checkboxes that reveal a bulk **Delete selected**
bar with the total gigabytes being freed.

**Deployment manifests.** Every running model's card in **Currently serving** has two actions:
**Export deployment** downloads a self-contained YAML/JSON manifest (model digest, quant, runtime,
hardware, fit estimate, measured performance from saved benchmark history —
`POST /system/manifest/export`), and **Use elsewhere** shows copy-paste configuration for Open
WebUI, AnythingLLM, Continue, Cline, curl, Python, JavaScript, and Docker Compose, generated from
that model's live endpoint (`GET /system/integration-snippets`). The **Deployment manifest**
section further down the tab accepts a pasted or uploaded manifest JSON: **Check compatibility**
reports whether it will fit and run here — exact digest match, a different but present version,
or not installed at all, plus a smaller-context suggestion when the original doesn't fit
(`POST /system/manifest/validate`) — and **Recreate deployment** pulls/serves it, then reports the
placement and VRAM actually observed here next to what the manifest recorded
(`POST /system/manifest/recreate`).

## Tab — Chat playground

A full chat surface over the server's own OpenAI-compatible endpoint
(`POST /v1/chat/completions` with `stream: true`) — pick a profile, type, and tokens render as they
arrive. Conversation state lives in the page (nothing is stored); **Clear** resets it.

- The profile picker is the same annotated list as everywhere else, so a not-pulled profile is
  labeled before you try it. The first message to a cold model includes Ollama's load time; the
  reply's meta line separates that out (`first token X s`) from the generation speed (`tok/s`).
- **Images** can be attached to a message when the selected profile is marked vision-capable
  (`supports_vision` — editable via Edit tuning). Attachments preview as thumbnails and are sent as
  data-URI `image_url` parts, the same shape any OpenAI client would use.
- An optional **system prompt** (⚙ System in the header) is sent with every turn.
- The welcome screen offers clickable suggestion prompts; replies render fenced code
  blocks with a language tag and copy button; each reply's meta line shows 🕒 elapsed,
  first-token latency, and ⚡ tok/s.
- Every *served* model's card (Setup & Deploy → Currently serving) shows the full API
  endpoint with ⧉ copy buttons for the URL and a ready-to-run curl.
- **Enter** sends, **Shift+Enter** inserts a newline, and the Send button becomes **Stop** while a
  reply is streaming.

## Tab 2 — Benchmark & Compare

The benchmark tab is a local experiment workspace. Its primary run history lives in
browser `localStorage` under `localdeploy.benchmarkRuns.v1`; there is no backend database.

Optionally, the **Also store on server** toggle (History tile) mirrors completed runs to
`reports/benchmark-history/` as one JSON file per run (`/benchmark/history` endpoints) — so
history survives the browser and can be shared or inspected as plain files. Turning the toggle on
pushes this browser's existing runs and pulls any runs stored by other browsers; deleting a run in
the UI also deletes its server copy.

- **Question set** is open by default. Leave the editor empty to use the built-in LocalDeploy
  suite, or use **Use LocalDeploy test bench** to load the built-in JSON into the editor for
  inspection/editing. **Validate** checks custom JSON against the schema and grader registry
  (`POST /benchmark/validate`).
- **Benchmark runner** replaces the old single-profile form. Select one or more saved profiles as
  chips, review the built-in/custom test-set summary, choose **Auto**, **CPU**, **GPU**, or
  **CPU + GPU**, choose 1-10 repetitions, then click **Run benchmark suite**. Profiles whose model isn't on the machine are
  hidden by default behind a "Show N hidden (model not pulled)" toggle, so the picker only offers
  models that can actually run.
- **Run queue** creates one row per model/device variant and runs sequentially by default to avoid
  VRAM contention. Each row shows queued/deploying/running/finished/failed/stopped state, current
  test progress, elapsed time, and a distinct visual treatment for active versus finished work.
  Waiting rows can be moved up/down or removed before they run; finished rows can be dismissed
  individually or all at once with **Clear finished**, and failed rows show their error reason
  inline. The larger active-run panel mirrors the running item and includes a **Stop** button that
  ends just that run and continues the queue (vs. the global **Cancel** that stops the whole queue).
- **Benchmark device** controls placement for each queued run. **Auto** leaves the current/default
  Ollama placement alone. **CPU** and **GPU** reload the model on that device *and* pin the same
  `num_gpu` on every inference call, so the measured run stays on the requested device end-to-end
  (not just at warm-up). **CPU + GPU** is implemented as two queued batches: one with `device=cpu`,
  one with `device=gpu`. If Ollama can't fully honor the request (e.g. a model too big for pure GPU
  lands on **Split**), the run still proceeds and is labeled with the *actual* placement reported by
  `/system/status` — so nothing is mislabeled and a reasonable device choice doesn't fail outright.
- Benchmark deployments are temporary. After each benchmarked Ollama profile finishes, the server
  unloads the benchmark model so the benchmark tab does not become a permanent deployment action.
- Each run records Ollama version, full model digest, quant, context, initial warm/cold state,
  LocalDeploy version, and the complete hardware snapshot. Repetitions add latency, accuracy, and
  token-rate variance; native backend tok/s is used when available.
- **Results Dashboard** is the main analysis surface as soon as results start streaming:
  - Leaderboard sorted by pass count, average accuracy, then average latency.
  - Winner badges for most accurate, fastest, and best tokens/second.
  - Category heatmap with accessible red/yellow/green accuracy cells.
  - SVG speed/quality scatter rendered locally with no chart dependency.
  - Collapsed advanced per-test matrix for pass/fail and accuracy across selected runs.
- **Detailed results** remains below the dashboard and adds filters for model, category, pass/fail,
  and slowest results. Rows still show latency, tok/s, accuracy, failure reason, warning, and an
  expandable response preview.

### Question-set schema

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
    }
  ]
}
```

Graders are selected by `type` from a fixed registry (uploads stay safe JSON — no code execution):

| Grader `type` | Fields | Passes when |
|---|---|---|
| `contains_all` | `keywords`, `case_sensitive?` | fraction of keywords present (1.0 = all) |
| `json_array_min_len` | `min` | response is a JSON array with ≥ `min` items |
| `number_within` | `expected`, `tolerance?` | a parsed number is within tolerance |
| `exact_match` | `expected`, `case_sensitive?` | trimmed response equals `expected` |
| `classification_set` | `expected` (list) | the response's label set equals `expected` |

## Report cards & comparison (Tab 2)

- **Export run** downloads a self-contained `.html` report card for the active run: model,
  hardware, requested/actual device tag, per-test scores (latency, **tok/s**, accuracy), and
  category summary. The card embeds JSON so it stays reproducible and re-importable
  (`POST /benchmark/export`).
- **Export selected** downloads one card for a single selected run or a `.json` bundle when
  multiple runs are selected. Bundles use `kind: "localdeploy.run_bundle"`.
- **Import card(s)** accepts exported `.html` cards and `.json` bundles. Imported runs appear in
  the same local run library as fresh benchmark results.
- **Managing history**: each run in the library has an **×** to remove just that run; **Clear
  history** wipes them all (with a confirm, since it also drops imported cards). **Select all** /
  **Deselect all** drive which runs feed the dashboard and comparison.
- **Compare selected** replaces the old two-slot compare form. Select 2 or more current or imported
  runs, pin a baseline, and compare deltas for pass count, accuracy, latency, and tok/s. The
  response detail drawer can show the same test's model outputs side by side. Fresh benchmark
  results are automatically added to the selected comparison set when there are prior runs.

## Tab — Monitor

Live view of what's happening after a model is loaded and while it's serving requests
(`GET /system/monitor`, polled every 5 seconds while the tab is open). Before anything is deployed
it explains what to do instead of showing an empty table.

- **Overview**: current VRAM used/total, GPU utilization, RAM used/total, and CPU utilization, plus
  short rolling charts (VRAM %, GPU utilization %, generation tok/s) built from samples taken while
  this tab is open — history does not accumulate in the background when nobody is watching.
- **Loaded models**: one card per running model with placement, VRAM in use, uptime, recent tok/s,
  time-to-first-token, and request/failure counts, plus a **Stop** action.
- **Recent requests**: the last 50 calls with timestamp, model, source (chat/benchmark/API),
  success, prompt/output token counts, TTFT, tok/s, and latency. **Numeric metadata only** — prompts
  and responses are never recorded here, matching the no-telemetry stance for the whole app.
- **Alerts**: sustained VRAM pressure (>95% for 3+ minutes), a model running on a different device
  than requested, generation speed well below its own recent median, and multiple backend calls
  that are genuinely in flight at the same time.
- **Session summaries**: when a model is stopped, a summary (peak VRAM, median tok/s, median TTFT,
  request/failure counts, uptime) is saved to `reports/monitor-sessions/`. Calibration uses Ollama's
  model-specific VRAM allocation at load time; whole-machine session peaks remain diagnostic only.

## Auto-pick a profile (Tab 1)

**Auto-pick a profile → Find best fit** fit-checks enabled saved profiles, runs a short benchmark
on candidates that can answer, and ranks them by accuracy, speed, and VRAM headroom. It recommends a
saved profile but does not download models or search Hugging Face (`POST /system/recommend`).
Requires the API + Ollama running.

## Optional token auth

By default the API has no auth. If the server sets `API_TOKEN`, open the UI once at
`/ui?token=<secret>` — the token is stored locally and sent on every request (`X-API-Token`). If a
request is rejected (401), the UI prompts you for the token and remembers it.

This is one shared local token over HTTP. There is no TLS, per-user identity, or tenant isolation.
Keep LocalDeploy on loopback and do not expose it through a public tunnel or internet-facing proxy.

## Update check

A chip in the top bar (`GET /system/update-check`) compares the running version against this
project's GitHub releases once per page load and shows itself only when a newer version exists —
click it to open the release. No data about your machine is sent; see Offline mode below to
disable it.

## Offline mode

Set `OFFLINE=true` to block all outbound internet calls (the Hugging Face/Ollama-library search and
the update check are both skipped). The UI surfaces this in the search result. Verify with
`python scripts/egress_selftest.py`.

## Keyboard shortcuts

- **Enter** in the Pull field — start the pull.
- **⌘/Ctrl + Enter** in the question editor — run the benchmark.

## Notes

- Streaming endpoints (`/models/pull`, `/benchmark/run`) use Server-Sent Events; the UI reads the
  stream incrementally and renders progress as it arrives.
- All errors surface as toasts (bottom-right) and inline result messages; no action throws an
  unhandled error if Ollama or the network is down.
- The benchmark run calls the server's own `/chat` over `API_HOST:API_PORT`, so the API must be
  reachable at its configured address (the same assumption the CLI `benchmark.py` makes).
