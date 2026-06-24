# LocalDeploy Web UI

A lightweight control panel served by the API server itself at **`http://<host>:<port>/ui`**
(default `http://127.0.0.1:8000/ui`). It is plain static HTML/CSS/JS — no build step, no extra
runtime, no external/CDN assets — so it runs anywhere the API runs and works fully offline.

The UI is **opt-out**: set `ENABLE_WEB_UI=false` to disable it (the API behaves exactly as it did
before the UI existed).

## Launching an existing clone

From a local checkout on Windows:

```powershell
.\scripts\start.ps1 -Background -OpenUI
```

That starts the API in the background, waits for `/health`, and opens `/ui`. It uses `API_HOST`
and `API_PORT` from `.env`; if `API_HOST=0.0.0.0`, the browser URL uses `127.0.0.1`.

The UI does not require llama.cpp. During normal API/UI startup, incomplete optional llama.cpp
configuration is skipped with a warning so Ollama-backed profiles and diagnostics remain usable.
Run `.\scripts\start_llamacpp.ps1` directly when you are intentionally bringing up a GGUF profile
and want missing server/model paths to fail fast.

## First-time flow

A newcomer can go end-to-end without reading anything else:

1. **Check My Hardware** — detects your GPU and free VRAM (or reports CPU-only).
2. **Manual pull by model name** — type an Ollama name (e.g. `gemma3:4b`) and pull it; progress
   streams live. The pull is **fit-checked** first and blocked only for hard "fits nowhere"
   warnings unless **Warn only; pull anyway** is checked.
3. **Deploy** — load the model into memory with an Ollama keep-alive.
4. **Benchmark & Compare tab** — load the example question set, **Validate**, then **Run**.

## Tab 1 — Setup & Deploy

| Control | What it does | Endpoint |
|---|---|---|
| Check My Hardware | GPU name + VRAM (NVIDIA) or **Apple Silicon (Metal, unified memory)**, **CPU model, cores, and system RAM** | `GET /system/hardware` |
| Refresh status | Loaded model(s), Ollama health, VRAM, **GPU/CPU placement** | `GET /system/status` |
| Deploy to (Auto/GPU/CPU) | Force where the model runs (`num_gpu`: 0 = CPU, max = GPU) | `POST /models/serve` |
| Deploy / unload / replace | Load / unload / replace the selected profile | `POST /models/{serve,stop,switch}` |
| Pull / Cancel | Download an Ollama model, streamed, fit-gated; Cancel aborts an in-flight pull | `POST /models/pull` |
| Fit check (per model) | Tiered estimate: green (comfortable), yellow (tight / CPU-only), red (won't fit) | `POST /system/fit-check` |
| Delete | Remove a model from disk (frees space) | `POST /models/delete` |
| Free memory | Unload all models from memory/VRAM | `POST /models/free` |
| Check New Models | Newer matching models on Hugging Face | `POST /registry/check-updates` |
| Refresh installed | Models already pulled locally | `GET /registry/installed` |

The **Target free VRAM (MB)** field is auto-filled from the hardware probe and is used by both the
fit check and the pull gate. You can override it to test against a different card.

## Tab 2 — Benchmark & Compare

The benchmark tab is a local experiment workspace. It keeps run records in browser
`localStorage` under `localdeploy.benchmarkRuns.v1`; there is no backend database.

- **Question set** is open by default. Leave the editor empty to use the built-in LocalDeploy
  suite, or use **Use LocalDeploy test bench** to load the built-in JSON into the editor for
  inspection/editing. **Validate** checks custom JSON against the schema and grader registry
  (`POST /benchmark/validate`).
- **Run Builder** replaces the old single-profile form. Select one or more saved profiles as
  chips, review the built-in/custom test-set summary, choose **Auto**, **CPU**, **GPU**, or
  **CPU + GPU**, then click **Run benchmark suite**.
- **Run queue** creates one row per model/device variant and runs sequentially by default to avoid
  VRAM contention. Each queued row shows waiting/deploying/running/complete/failed state, current
  test progress, elapsed progress, and supports cancellation. Waiting rows can be moved up/down
  or removed before they run; the active run is shown only in the larger progress panel.
- **Benchmark device** controls placement for each queued run. **Auto** leaves the current/default
  Ollama placement alone. **CPU** and **GPU** unload and reload the model before benchmarking so
  the measurement matches the requested target. **CPU + GPU** is implemented as two queued batches:
  one with `device=cpu`, one with `device=gpu`. If Ollama reports a different known placement after
  reload, the benchmark fails cleanly instead of recording a mislabeled run.
- Benchmark deployments are temporary. After each benchmarked Ollama profile finishes, the server
  unloads the benchmark model so the benchmark tab does not become a permanent deployment action.
- **Results Dashboard** is the main analysis surface after runs finish:
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
- **Compare selected** replaces the old two-slot compare form. Select 2-4 current or imported
  runs, pin a baseline, and compare deltas for pass count, accuracy, latency, and tok/s. The
  response detail drawer can show the same test's model outputs side by side. Fresh benchmark
  results are automatically added to the selected comparison set when there are prior runs.

## Tune for my GPU (Tab 1)

**Recommended setup → Tune for my GPU** fit-checks your profiles, runs a short benchmark on the
ones that fit, and ranks them by accuracy × speed × VRAM headroom, highlighting the winner
(`POST /system/recommend`). Requires the API + Ollama running.

## Optional token auth

By default the API has no auth. If the server sets `API_TOKEN`, open the UI once at
`/ui?token=<secret>` — the token is stored locally and sent on every request (`X-API-Token`). If a
request is rejected (401), the UI prompts you for the token and remembers it.

## Offline mode

Set `OFFLINE=true` to block all outbound internet calls (the Hugging Face check is skipped). The
UI surfaces this in the "Check New Models" result. Verify with `python scripts/egress_selftest.py`.

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
