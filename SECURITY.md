# Security Policy

## Threat Model

LocalDeploy is built for a **single local user on a trusted workstation**. The default configuration assumes:

- The API binds to loopback (`127.0.0.1`), not to a LAN address or `0.0.0.0`.
- Only the local user submits requests; there is no authentication on the HTTP API.
- The backend services (Ollama at `:11434`, llama.cpp at `:8080`) are themselves loopback-only.
- Prompts, images, and benchmark outputs are private data; nothing leaves the machine.

The following are **explicitly out of scope** of LocalDeploy's defenses:

- Multi-tenant or shared-host deployments. There is no per-user rate limiting, quota accounting, or auth layer.
- Untrusted prompts (e.g., scraped web content fed into the model). No input sanitization beyond pydantic validation and size caps.
- Untrusted model output reaching other systems. Responses are not filtered for PII, credentials, or jailbreak markers.
- Network exposure beyond loopback. Binding to a non-loopback address removes the only meaningful access control.

If your use case violates any of these assumptions, treat LocalDeploy as a starting point and add the missing controls (rate limiting, auth, output filtering, transport security) before exposing it.

## Local-Only Boundary

LocalDeploy is designed to call only local inference backends:

- `http://localhost:11434` for Ollama
- `http://localhost:8080` for llama.cpp

The server rejects non-local backend URLs in code (`localdeploy/utils.py::is_loopback_url`). Do not remove this guard without an explicit security review.

`API_HOST` defaults to `127.0.0.1` in `.env.example`. If you change it, document why and pair it with at least:

- A firewall rule limiting access to known clients.
- `API_TOKEN` set, so the control-plane isn't wide open (see below).
- TLS termination in front of the API.

**Runtime guard:** at startup, `api_server.py` checks whether `API_HOST` resolves to a
non-loopback address with no `API_TOKEN` set. If so it prints a loud warning
listing the exposed control-plane endpoints. Set `REQUIRE_TOKEN_ON_LAN=true` to
turn that warning into a hard failure (the server refuses to start) instead of
just logging it. Inside Docker the container always binds `0.0.0.0` internally
by design — what actually controls exposure is the *host* port mapping in
`docker-compose.yml` — so the warning there is informational unless you also
opened that host port to your LAN.

## Sensitive Files

Do not commit:

- `.env`
- `config.json`
- local GGUF / `.safetensors` / model files
- benchmark outputs containing private prompts
- screenshots or uploaded images from private workflows

The `.gitignore` already excludes `logs/`, `reports/`, `.env`, `config.json`, and model files. Verify with `git check-ignore -v <path>` before committing anything new in those directories.

## Known Gaps and Risk Acceptance

These are deliberate trade-offs for the single-user, local-only design. They become real risks the moment those assumptions stop holding:

| Gap | Why it exists | When it becomes risky |
|---|---|---|
| No HTTP auth *by default* | Loopback-only assumption; opt-in `API_TOKEN` available | Binding to LAN without setting `API_TOKEN`, or exposing via tunnel / shared host |
| No rate limiting | Single-user workload | More than one client / shared host / a misbehaving script loop |
| No concurrency cap on backend calls | 8 GB VRAM serves ~1 in-flight request anyway | If you increase VRAM and add parallel callers |
| No prompt-injection filter | Operator-supplied prompts assumed trusted | Feeding the model with untrusted external content |
| No output PII/jailbreak filter | Outputs stay on the machine | Piping responses to logs, tickets, or other systems |
| Backend error bodies surfaced in API errors | Helpful for debugging locally | Could leak local filesystem paths if exposed |
| Built-in code benchmarks execute model answers in a restricted child process | Functional grading needs to run candidate functions; imports/builtins and common file/process/network operations are blocked, with time and POSIX resource caps | This is defense in depth, not a hardened VM boundary; do not run adversarial models or prompts under a privileged OS account |
| `/benchmark` runs every enabled profile | Diagnostic tool, deliberate | Unauthenticated callers could exhaust GPU |
| Web control-plane (`/models/pull`, `/models/serve`, `/models/delete`, `/models/free`, `/system/recommend`, `/system/set-default`) | Convenience for the local operator | Unauthenticated callers could fill disk (pulls), **delete installed models** (`/models/delete`), unload models (`/models/free`), run benchmarks, or rewrite `config.json`. Set `API_TOKEN`, disable with `ENABLE_WEB_UI=false`, or keep the bind on loopback |

## Reporting

Report security issues privately via GitHub's **Security → Report a vulnerability** on this repository rather than a public issue. Do not include private prompts, images, tokens, or model files in reports or public issues.
