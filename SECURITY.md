# Security Policy

## Threat Model

LocalDeploy is built for a **single local user on a trusted workstation**. The default configuration assumes:

- The API binds to loopback (`127.0.0.1`), not to a LAN address or `0.0.0.0`.
- Only the local user submits requests. An optional `API_TOKEN` is one shared secret, not a user identity or tenant boundary.
- Inference backends are loopback-only. Supported runtimes include Ollama, llama.cpp, LM Studio, vLLM, Docker Model Runner, and configured OpenAI-compatible services.
- Prompts, images, and benchmark outputs are private data; nothing leaves the machine.

The following are **explicitly out of scope** of LocalDeploy's defenses:

- Multi-tenant or shared-host deployments. There is no per-user authentication, isolation, rate limiting, quota accounting, or audit trail.
- Untrusted prompts (e.g., scraped web content fed into the model). No input sanitization beyond pydantic validation and size caps.
- Untrusted model output reaching other systems. Responses are not filtered for PII, credentials, or jailbreak markers.
- Network exposure beyond loopback. There is no TLS server configuration, and a shared bearer token does not make direct internet exposure safe.

LocalDeploy must not be presented or deployed as an internet-facing server. Do not publish it through a public reverse proxy, tunnel, port-forward, or public container port. A production gateway with TLS, real identity, authorization, isolation, rate limiting, and audit logging is a separate system outside this project's security claim.

## Local-Only Boundary

LocalDeploy is designed to call only local inference backends:

- `http://localhost:11434` for Ollama
- loopback ports for llama.cpp and OpenAI-compatible local runtimes

The server rejects non-local backend URLs in code (`localdeploy/utils.py::is_loopback_url`). Do not remove this guard without an explicit security review.

`API_HOST` defaults to `127.0.0.1` in `.env.example`. Keep that default. `API_TOKEN` can reduce accidental access by another local process or trusted-LAN client, but it is a single shared token sent over plain HTTP and is not sufficient for internet exposure.

**Runtime guard:** at startup, `api_server.py` checks whether `API_HOST` resolves to a
non-loopback address. It prints a loud warning even when `API_TOKEN` is set,
because the token adds neither TLS nor user isolation. The legacy
`REQUIRE_TOKEN_ON_LAN=true` switch turns a tokenless non-loopback bind into a
hard failure. Inside Docker the container always binds `0.0.0.0` internally
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
| One shared HTTP token, optional | Loopback-only single-user assumption | Any public exposure, shared host, or need for revocation/accountability per user |
| No TLS | Local loopback HTTP does not cross a network | LAN, tunnel, proxy, or internet exposure |
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
