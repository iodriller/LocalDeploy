# LocalDeploy Web UI

A lightweight control panel served by the API server itself at **`http://<host>:<port>/ui`**
(default `http://127.0.0.1:8000/ui`). It is plain static HTML/CSS/JS — no build step, no extra
runtime, no external/CDN assets — so it runs anywhere the API runs and works fully offline.

The UI is **opt-out**: set `ENABLE_WEB_UI=false` to disable it (the API behaves exactly as it did
before the UI existed).

## First-time flow

A newcomer can go end-to-end without reading anything else:

1. **Check My Hardware** — detects your GPU and free VRAM (or reports CPU-only).
2. **Pull a model** — type an Ollama name (e.g. `gemma3:4b`) and pull it; progress streams live.
   The pull is **fit-checked** first and blocked if it won't fit your VRAM (override available).
3. **Start** — warm the model into memory (Ollama keep-alive).
4. **Deploy & Benchmark tab** — load the example question set, **Validate**, then **Run**.

## Tab 1 — Serve & Diagnose

| Control | What it does | Endpoint |
|---|---|---|
| Check My Hardware | GPU name, VRAM total/free/used, driver, cores | `GET /system/hardware` |
| Refresh status | Currently loaded model(s), Ollama health, VRAM in use | `GET /system/status` |
| Start / Stop / Switch | Warm / unload / pivot the selected profile | `POST /models/{serve,stop,switch}` |
| Pull | Download an Ollama model, streamed, fit-gated | `POST /models/pull` |
| Fit check (per model) | Estimate whether a model fits your VRAM | `POST /system/fit-check` |
| Check New Models | Newer matching models on Hugging Face | `POST /registry/check-updates` |
| Refresh installed | Models already pulled locally | `GET /registry/installed` |

The **Target free VRAM (MB)** field is auto-filled from the hardware probe and is used by both the
fit check and the pull gate. You can override it to test against a different card.

## Tab 2 — Deploy & Benchmark

- **Load example** populates the editor with a canonical question set (`GET /benchmark/example`).
- **Upload .json** loads a set from disk; **Validate** checks it against the schema and grader
  registry (`POST /benchmark/validate`) and reports per-row errors.
- **Run** streams per-test results into the table and ends with a summary
  (`POST /benchmark/run`, Server-Sent Events). Leave the editor empty to run the built-in
  capability suite instead of an uploaded set.

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
