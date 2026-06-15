# IMPROVEMENTS.md — analysis & backlog

A tracked, prioritized backlog from a full end-to-end audit of the public-launch surface
(one-command install, the `/ui` web UI, and the control-plane backend). Each item has a stable ID,
a severity, a file reference, the concrete problem, and a suggested fix. Check items off as they
land.

**Guiding constraint:** none of these changes may break the existing, working functionality — the
original CLI, the OpenAI-compatible API, the `/chat` endpoint, and the loopback-only backend guard.
Every fix here is additive or defensive.

**Baseline at time of audit:** `122 tests pass`, ruff clean except two `F401` unused-import
warnings. The audit read the code statically; runtime scenarios (a real `docker compose up`, a live
Ollama) are reasoned from the code, not executed, and are marked *(inferred)* where relevant.

Severity: **BUG** (will misbehave) · **RISK** (fails on some setups / edge cases) ·
**SECURITY** · **UX** · **A11Y** · **NICE** (polish).

Checkbox: `[x]` done · `[~]` partially done (note says what's left) · `[ ]` open.

---

## Priority 0 — do these first (highest impact, low risk to fix)

- [x] **I1 · RISK · `run.sh` `launch()`** — The sudo-fallback only triggers when Docker was *just*
  installed (`DOCKER_JUST_INSTALLED`). A user who already has Docker but whose shell isn't in the
  `docker` group yet (installed earlier, never re-logged in) runs plain `docker compose up` and hits
  a raw `permission denied /var/run/docker.sock`, then a dead terminal. *Fix:* in `launch()`, probe
  `docker info >/dev/null 2>&1` and prefix `sudo` whenever it fails — don't key off the install flag.

- [x] **I2 · RISK · `docker-compose.yml` + `run.sh`** — Host port is hard-bound (`"8000:8000"`); if
  8000 is taken, compose exits with `port is already allocated`, `set -e` aborts, and the user never
  sees the "open localhost:8000" line. `run.sh` also reads `API_PORT` but compose ignores it. *Fix:*
  `ports: ["${API_PORT:-8000}:8000"]`, and have `run.sh` pre-check the port and print a clear "port
  busy, set API_PORT and re-run" message.

- [x] **I3 · RISK · `docker-compose.yml`, `run.sh`, `run.ps1`** — `compose up -d` returns when the
  container *starts*, not when uvicorn is listening. The scripts immediately print the URL (and
  `run.ps1` auto-opens the browser), so the first hit is connection-refused / a blank page. *(inferred)*
  *Fix:* add a `healthcheck` on `/health` to compose, and poll `http://localhost:PORT/health` in the
  scripts (spinner + timeout) before printing/opening the URL. `/health` already exists but nothing
  uses it as a healthcheck.

- [x] **I4 · RISK · `run.sh` / `run.ps1` repo update** — `git pull --ff-only origin main` hard-fails
  on a dirty or diverged existing clone (e.g. the user uncommented the GPU block or set a token), and
  `set -e` then aborts the whole launch — bricking an install that previously worked. *Fix:* make the
  pull non-fatal: `git -C "$DIR" pull --ff-only origin main || warn "Couldn't update; launching
  existing version."`

- [x] **I5 · BUG · `web/app.js` `getJSON` / `postJSON`** — `getJSON` only throws when the response is
  not-ok *and* not JSON, so FastAPI error bodies (`{"detail": ...}`, which are JSON) slip through as
  "success" and callers read fields off an error object. `postJSON` never checks `resp.ok` at all.
  Result: a 500/422 renders as a confusing partial state or a downstream `TypeError` instead of a
  clear error toast. This contradicts the UI.md promise that no action throws when the backend is
  down. *Fix:* on `!resp.ok`, throw with the parsed `detail`/`error`.

- [x] **I6 · RISK · `localdeploy/backends/ollama.py` `stream_ollama`** — The streaming
  `requests.post(..., stream=True)` is never wrapped in `with` and never `.close()`d. If the SSE
  client disconnects mid-generation, the generator stops being driven and the connection leaks until
  GC; repeated cancellations exhaust the pool. (`_ollama.pull_stream` already does this correctly with
  `with`.) *Fix:* wrap in `with requests.post(...) as response:`.

---

## Priority 1 — important correctness & first-run UX

- [ ] **I7 · RISK · `localdeploy/web/recommend.py`** — "Tune for my GPU" (`/system/recommend`) is a
  synchronous blocking request: N fitting profiles × `sample_size` tests × up to 120 s each, all in
  one HTTP call. The client timeout will very likely fire first, and the UI shows only a spinner with
  no progress. *Fix:* stream it via SSE like `/benchmark/run`, or cap total wall-clock, or run as a
  background job with a polled id. (Per-test `try/except` already prevents a 500 — good.)

- [~] **I8 · UX · `web/app.js` `recommendTune` / `runBenchmark`** — Long operations have a spinner but
  no cancel, no fetch timeout, and (for recommend) no elapsed/heartbeat indicator. A user assumes a
  3-minute run is frozen, reloads, and loses state. *Done:* added a live `…Ns` elapsed counter
  (`startElapsed`) to both "Tune for my GPU" and the benchmark summary, so neither looks frozen.
  *Still open:* an explicit cancel button + fetch `AbortController`; pairs with I7's streaming.

- [x] **I9 · RISK · `web/app.js` `streamSSE`** — Only blocks terminated by `\n\n` are parsed; a final
  `data:` line with no trailing blank line left in `buf` after the stream ends is dropped. If a
  benchmark's `run_end` is the last event without a trailing `[DONE]`, the summary never renders and
  **Export card stays disabled** — a finished run looks hung. *(inferred — depends on server framing)*
  *Fix:* flush any remaining complete `data:` line after the read loop; confirm the server always
  emits `[DONE]`.

- [x] **I10 · RISK · `api_server.py` `run_local_request`** — Only `BackendCallError` is caught; an
  unexpected exception (e.g. an unwrapped `json.JSONDecodeError`/`KeyError` from a backend) propagates
  as a raw 500 instead of a graceful `success: false` response. *(inferred)* *Fix:* add a catch-all
  `except Exception` that returns the standard error response.

- [x] **I11 · RISK · `localdeploy/web/recommend.py` `set-default` write** — `config.json` is written
  non-atomically (`path.write_text(...)`) while every other request reads it via `load_config()` with
  no lock; a concurrent reader can see a truncated file → `JSONDecodeError` → 500. *Fix:* write to a
  temp file and `os.replace()` atomically.

- [~] **I12 · UX · first-boot has zero models** — `PULL_MODELS` is commented out, so the very first
  UI view is "No model loaded / No models pulled." Combined with I3 the first impression can be a blank
  page. *Done:* `init()` now auto-loads hardware, status, and the installed-models list so the page
  shows real state on open instead of "Not loaded yet." placeholders; the hint banner already guides
  the pull. *Still open (owner call):* whether to ship a default `PULL_MODELS=gemma3:4b` (auto-download
  ~3 GB on first boot) — left off by default to keep the image lean and the first boot fast.

- [x] **I13 · UX · `web/app.js` `loadProfiles`** — If `/profiles` fails, the dropdowns stay empty and
  Start/Run silently POST `profile: ""`. *Fix:* on empty/failed load, disable Start/Run and show an
  inline "No profiles configured — check the API connection."

---

## Priority 2 — security hardening (within the local-only threat model)

- [ ] **I14 · SECURITY · `benchmark.py` code graders** — The built-in code-category graders
  `exec(compile(tree, ...))` the model's **response** in-process with no sandbox, reachable via
  `/benchmark/run` when no uploaded question set is provided. A model returning code with side effects
  runs it in the server process. (Uploaded question sets use the safe JSON grader registry — not
  affected.) *Fix:* subprocess-isolate the code-grader `exec`, or gate code-category tests behind an
  explicit opt-in flag.

- [x] **I15 · RISK · `api_server.py` auth middleware** — The auth exemption uses
  `path.startswith("/ui")`, a prefix match. No data route matches today, but a future `/ui-config`
  route would silently become unauthenticated. *Fix:* tighten to `path == "/ui" or
  path.startswith("/ui/")`.

- [ ] **I16 · NICE · `api_server.py` `_extract_token`** — Token accepted via `?token=` lands in
  access logs / browser history / `Referer`. It's needed for `EventSource` (which can't set headers),
  so it's likely intentional — *Fix:* add a code comment saying so, or document the logging
  sensitivity. (The `hmac.compare_digest` comparison itself is correct — no timing issue.)

- [ ] **I17 · NICE · `localdeploy/utils.py` `is_loopback_url`** — Name-based allow of `localhost` is
  weaker than the IP allow (`/etc/hosts` could repoint it) and `127.0.0.0/8` other than `.1` is
  rejected. By design for a local tool, but *Fix:* optionally resolve to IP and test
  `ipaddress.ip_address(host).is_loopback`, or document the intentional name-trust.

---

## Priority 3 — accessibility (easy wins)

- [x] **I18 · A11Y · `web/index.html` file uploads** — The upload controls are a `<label>` wrapping a
  `hidden` `<input type="file">`; labels aren't tab-stops and `hidden` inputs aren't focusable, so
  upload / Card A / Card B are unreachable by keyboard. *Fix:* use a visually-hidden (not `hidden`)
  input, or make the label a real button that triggers it.

- [ ] **I19 · A11Y · `web/index.html` tabs** — `role="tablist"/"tab"` are set but panels lack
  `role="tabpanel"`/`aria-labelledby`, and tabs aren't arrow-key operable. *Fix:* add tabpanel roles,
  `aria-controls`, and arrow-key navigation.

- [x] **I20 · A11Y · `web/app.js` toasts** — Error toasts use `aria-live="polite"` and auto-dismiss
  after 5 s, so screen-reader users may miss them. *Fix:* `role="alert"`/`aria-live="assertive"` for
  error toasts and don't auto-dismiss errors (or pause on hover/focus).

- [x] **I21 · A11Y · `web/styles.css`** — `--muted #9aa0a6` on `--panel #171a21` is ~4.0:1, below
  WCAG AA 4.5:1 for the small `0.78rem` field labels. *(estimated, not measured)* *Fix:* lighten
  `--muted` (e.g. `#b0b6bd`) or enlarge label text.

---

## Priority 4 — clarity, jargon, and polish

- [x] **I22 · UX · jargon for non-experts** — "Target free VRAM (MB)", "Keep-alive 5m", "Ollama name",
  "Override fit check", GGUF/quant/KV-cache are unexplained. *Fix:* add `title=`/inline helper text
  ("Keep-alive: how long to keep the model warm in memory"); warn that "Override fit check" may OOM
  the GPU.

- [ ] **I23 · UX · `web/app.js`** — Unrecognized pull/benchmark SSE events are dumped as raw JSON into
  the log/table. *Fix:* render a friendly fallback ("downloading layers…") or suppress unknown events.

- [x] **I24 · UX · `web/app.js` export-enabled state** — `#btn-export` is enabled in `run_end` but not
  reset at the start of a new run, so after an aborted/errored run it can point at a stale result.
  *Fix:* disable export at the start of every `runBenchmark`, re-enable only on success.

- [ ] **I25 · UX · GPU story by platform** — The GPU block documents only the Linux NVIDIA Container
  Toolkit path. Windows needs WSL2 + Docker Desktop GPU; macOS Docker is **CPU-only** (Metal isn't
  exposed to Linux containers) — a major silent perf surprise. *Fix:* add a short "GPU support by
  platform" note, pointing Mac users to native `scripts/start.sh` + native Ollama for Metal.

- [ ] **I26 · UX · README consistency** — The "Already have Docker?" snippet uses foreground
  `docker compose up` while the scripts use `-d`; beginners copying it then "lose" their terminal.
  *Fix:* use `-d` or note it runs in the foreground.

- [ ] **I27 · NICE · macOS bare-machine path** — `run.sh` `die`s if Homebrew is missing and tells the
  user to run `xcode-select --install` for git — so a truly fresh Mac needs two manual steps before
  the "one command" works, despite the README implying zero prerequisites. *Fix:* detect+offer to
  install Homebrew, or soften the macOS claim in the README.

- [ ] **I28 · NICE · `requirements.txt`** — Fully unpinned; a future breaking FastAPI/Pydantic release
  could break an image rebuild with no code change. *Fix:* pin to `>=x,<y` ranges.

- [x] **I29 · NICE · `Dockerfile`** — `EXPOSE 11434` advertises the Ollama port that compose never
  publishes. *Fix:* drop it or comment that it's internal-only.

- [x] **I30 · NICE · ruff `F401`** — Remove unused `import pytest` in `tests/test_benchmark_graders.py`
  and `tests/test_guardrails.py` (auto-fixable with `ruff check --fix`).

---

## Verified-OK (no action — recorded so they're not re-flagged)

- `ENABLE_WEB_UI=false` cleanly removes the router and `/ui` mount; the original endpoints are defined
  unconditionally above the guard — the original API is unaffected.
- When `API_TOKEN` is unset the middleware short-circuits with zero behavioral change and ~one
  `os.getenv` per request; `OPTIONS` preflight is exempt.
- `/benchmark/run` copies `TEST_CASES` via `dataclasses.replace` per request — no cross-request
  mutation of module-level state.
- `nvidia-smi` subprocess calls use timeouts and fully consume output — no handle leak.
- `set-default` correctly refuses to overwrite `config.example.json`.
- The loopback guard is called at every backend URL site found — no bypass path located.

---

*Audit method: three parallel static reviews (install path, UI/UX, backend) cross-checked against the
source; the highest-severity items (I1, I5, I6, I9, I14, I17) were re-verified by reading the exact
code. Not executed here: a live `docker compose up`, a running Ollama, or a real GPU — items marked
*(inferred)* are reasoned from code, not observed.*
