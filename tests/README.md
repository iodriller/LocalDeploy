# Tests

Offline tests for guardrails, utilities, and HTTP routes. They run against the in-process FastAPI app and do not require Ollama or llama.cpp.

## Install

```powershell
python -m pip install -r requirements-dev.txt
```

## Run

```powershell
pytest
```

Or a single file:

```powershell
pytest tests/test_guardrails.py -v
```

## What's covered

- `tests/test_utils.py` — `is_loopback_url`, `get_backend_base_url`, env helpers.
- `tests/test_guardrails.py` — `prepare_request` rejects oversized prompts, honors `allow_clamp`, rejects unknown/disabled profiles, image limits and base64 validation.
- `tests/test_api_routes.py` — `/v1/embeddings` returns 501, `/v1/models` and `/profiles` shape, streaming endpoint emits SSE on validation error, native `ChatRequest` schema has no `stream` field.

These exercise the safety layer. They are not a substitute for end-to-end testing against a real backend; for that, run `test_models.py` with a live Ollama or llama.cpp server.
