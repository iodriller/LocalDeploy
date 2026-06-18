# ROADMAP.md — CPU/GPU deployment & model-management plan

A focused improvement plan turning LocalDeploy into the full "launch → see my hardware → pick a
model → deploy to CPU **or** GPU → test → clean up" journey. Derived from a code-grounded audit
(see `IMPROVEMENTS.md` for the earlier launch/UI backlog).

**Guiding rule (non-negotiable):** every change is **additive and non-breaking**. New optional
parameters default to today's behaviour; the original CLI, OpenAI-compatible API, `/chat`, the
loopback-only backend guard, and `ENABLE_WEB_UI=false` parity all stay intact.

Status legend: **[ ]** planned · **[~]** in progress · **[x]** done.

---

## Where the journey breaks today (audit summary)

| Journey step | Today | Gap |
|---|---|---|
| Launch (one command) | ✅ `run.sh` / `run.ps1`, `/ui` | — |
| Pull with progress | ✅ SSE-streamed, fit-gated | — |
| See online model list | 🟡 live HF GGUF search, auto-derived queries | no search box |
| **See my hardware** | 🔴 GPU+VRAM (NVIDIA only); **no CPU model, cores, or RAM** | `hardware.py:66` |
| **Soft/hard warnings** | 🔴 binary `FITS`/`WONT_FIT`, VRAM-only | `fit.py:172` |
| **Choose CPU vs GPU** | 🔴 no `num_gpu`; Ollama-default only | backends send no GPU opts |
| Installed list | 🟡 name+size (quant/date returned, not shown) | `app.js:468` |
| Deploy / serve | ✅ Ollama keep-alive | no device choice |
| **Wipe from disk** | 🔴 `/api/delete` never called | no route/UI |
| **Free stuck memory / cancel** | 🔴 only `keep_alive:0`; no cancel | no controls |
| **Test CPU vs GPU & compare** | 🔴 runs not tagged by device | blocked by device choice |

The enabling facts: Ollama already supports `num_gpu` (0 = CPU, N = GPU layers) and `/api/delete`.
This is **additive plumbing, not a rewrite**.

---

## Phase 1 — Full hardware visibility  **[x]**
*Foundation: everything downstream needs CPU/RAM numbers.*

- Extend `GET /system/hardware` with **CPU model, physical + logical cores, RAM total/available**
  via `psutil` (graceful fallback to `os.cpu_count()` when absent — never hard-fails).
- UI: hardware panel shows GPU(s)+VRAM **and** CPU + cores + RAM; top-bar chip gains RAM.
- Files: `localdeploy/web/hardware.py`, `web/app.js`, `web/index.html`, `requirements.txt`.
- Caveat surfaced in UI: inside Docker, RAM reflects host/cgroup limits.

## Phase 2 — CPU vs GPU deployment target  **[x]**
*The core ask: deploy a model to CPU or GPU on purpose.*

- Add an optional Ollama `num_gpu` option (`None`=Auto/today, `0`=CPU, large=force GPU) and a
  friendly **device** selector (Auto / GPU / CPU) on serve.
- Plumb device through `/models/serve` and `/models/switch` (load the model with the chosen
  placement). Keep the chat-time passthrough wired in `options_payload` for Phase 6.
- `/system/status` enriches each running model with a **placement** label (GPU / CPU / Split N%)
  computed from `size` vs `size_vram`.
- Files: `localdeploy/backends/ollama.py`, `localdeploy/web/_ollama.py`,
  `localdeploy/web/models.py`, `web/app.js`, `web/index.html`. Default (no device) = today exactly.

## Phase 3 — Tiered soft/hard deployability warnings  **[ ]**
*Needs Phase 1 RAM.*

- Fit-check tiers: **Comfortable (green)** · **Tight (yellow, soft)** · **Won't fit GPU but runs on
  CPU+RAM (yellow, "slower")** · **Won't fit anywhere (red, hard)** — using RAM for the CPU path.
- Keep `FITS`/`WONT_FIT`/`UNKNOWN` as compatibility aliases so nothing that reads them breaks.
- Files: `localdeploy/web/fit.py`, `web/app.js`.

## Phase 4 — Cleanup, free-memory, cancel  **[ ]**
*The wipe/reset/cancel safety net.*

- `POST /models/delete` → Ollama `/api/delete` (loopback-guarded) + per-row **Delete** with confirm.
- **Free memory / unload all** button (unload every running model).
- **Cancel pull** via client `AbortController` + server `request.is_disconnected()` check.
- Files: `localdeploy/web/_ollama.py`, `localdeploy/web/models.py`, `web/app.js`, `web/index.html`.

## Phase 5 — Better discovery  **[ ]**

- Search box + limit + GGUF toggle on "Check New Models" (API already accepts `queries`).
- Surface quant / size / modified / downloads / likes in installed + HF lists (data already returned).
- Files: `web/app.js`, `web/index.html`.

## Phase 6 — CPU-vs-GPU comparison  **[ ]**

- Tag benchmark runs + report cards with the **device target** from Phase 2 so the existing Compare
  view diffs "Qwen/CPU" vs "Qwen/GPU"; optional one-click "run both, compare".
- Files: `benchmark.py`, `localdeploy/web/bench.py`, `localdeploy/web/report.py`, `web/app.js`.

---

## Verification per phase

- Unit tests for new endpoints/fields; existing suite stays green.
- `ENABLE_WEB_UI=false` still removes all web routes; loopback guard unchanged.
- Manual UI smoke against a live Ollama where a backend is available (noted as unverified otherwise).
