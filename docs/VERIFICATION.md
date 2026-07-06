# Verification Matrix

Honest, dated record of what has actually been exercised and observed, versus what is
covered only by CI or not yet tested at all. Update a row with a real date and a real
observation — don't mark something verified because it "should work."

## Legend

- ✅ Verified manually — a person ran it and observed the result (see Notes).
- 🤖 CI-covered — exercised automatically on every push/PR, not manually re-verified here.
- ⬜ Not yet verified — no manual run recorded. Treat as unknown, not as broken.

## Platform × path

| Platform | Path | Status | Last verified | Notes |
|---|---|---|---|---|
| Windows 10/11 | Local (non-Docker), `scripts/start.ps1` | ✅ | 2026-07-05 | See "Model lifecycle" below. |
| Windows 10/11 | Docker (`docker compose up`) | 🤖 | — (CI builds + boots the image on Linux runners; not run on Windows Docker Desktop by a person) | CI's `docker-build` job builds the image and waits for `/health`; it does not run on a Windows host. |
| macOS | Docker | ⬜ | — | Not yet run on macOS hardware. |
| Linux | Docker | 🤖 | every push/PR | `ci.yml`'s `docker-build` job: build, boot, `/health`, teardown, on `ubuntu-latest`. |
| Linux | Local (non-Docker) | 🤖 | every push/PR | `ci.yml`'s `tests-linux` job runs the full pytest suite (incl. Playwright UI tests) directly on `ubuntu-latest`, not inside Docker. |
| NVIDIA GPU passthrough (Docker) | — | ⬜ | — | Requires the NVIDIA Container Toolkit and a CUDA-capable host; CI runners don't have a GPU. |

## Model lifecycle (manually verified 2026-07-05, Windows 10, local non-Docker)

Run against a live Ollama instance with several models already pulled, using the
`llama32_3b_ollama` profile (`llama3.2:3b`), via direct HTTP calls to a `scripts/start.ps1`
-launched server (not just the pytest suite, which mocks Ollama in CI).

| Step | Result | Detail |
|---|---|---|
| Pull | ✅ (verified against Ollama directly, not through this profile) | Confirmed pulling a new model streams progress and that cancelling the client connection actually stops the Ollama-side download (see "Pull cancellation" below). |
| Deploy / chat | ✅ | `POST /chat` with `profile=llama32_3b_ollama` returned `success: true` with a valid response after a cold-start model load. |
| Benchmark | ✅ | `POST /benchmark` for the same profile returned `success: true` with per-test timing. |
| Unload | ✅ | `POST /models/free` returned `success: true`, unloaded the loaded model(s). |
| Restart | ✅ | `scripts/stop.ps1` followed by `scripts/start.ps1` cleanly stopped and relaunched the API; `/health` came back healthy. |

### Pull cancellation (manually verified 2026-07-05)

Investigated whether aborting a pull from the browser actually stops the download on the
Ollama side, or just stops the UI from showing progress. Verified by starting a pull of an
uninstalled model through `/models/pull`, closing the client connection after a few progress
events, then watching Ollama's blob directory size for the next 10 seconds:

- Active download: growing at several MB/s.
- After closing the connection: **239 bytes** drifted in the next 10 seconds (noise, not a
  continued download).

Confirms cancellation propagates through FastAPI's streaming response, into the generator's
`with requests.post(...)` cleanup, and closes the upstream connection to Ollama, which Ollama
treats as a cancel signal. No code change was needed here.

## Known-fixed regressions

- **Windows `localhost` DNS latency** — resolving the hostname `localhost` took ~2 seconds
  per call on this machine (Windows falls back from IPv6 to IPv4), versus ~8ms for the
  `127.0.0.1` literal. Every default backend URL (`OLLAMA_BASE_URL`, `LLAMACPP_BASE_URL`) used
  `localhost`, so `/health` alone cost 4-6 seconds per call. Fixed by defaulting to IP literals
  everywhere (`api_server.py`, `localdeploy/utils.py`, `localdeploy/web/_ollama.py`,
  `localdeploy/web/models.py`, `.env.example`, `config.example.json`, `Dockerfile`). Verified:
  `/health` dropped from ~6.1s to ~2.0s (residual 2s is a separate, unrelated llama.cpp
  reachability check delay on this machine when no llama.cpp server is running — not caused by
  DNS, see below).
- **llama.cpp reachability check latency** — when `ENABLE_LLAMA_CPP=true` but no llama.cpp
  server is listening, connecting to the configured port took the full 2-second timeout to
  fail on this machine instead of an instant refusal. This looks environment-specific (a
  firewall or security tool likely drops the connection attempt rather than sending an
  instant RST) rather than a universal code bug, so it was recorded here rather than "fixed"
  by guessing at a shorter timeout that could cause false negatives elsewhere.

## How to add a row

1. Actually run the flow on the platform in question.
2. Record the date, what you ran, and what you observed (paste real output, not a summary).
3. If something failed, leave it failing in this table with the error until it's fixed —
   don't mark it ✅ to make the table look better.
