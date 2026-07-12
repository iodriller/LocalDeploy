# API Options For External Clients

External clients can call either the native LocalDeploy API or the OpenAI-compatible API.

## Native `/chat`

```json
{
  "profile": "gemma3_4b_ollama_safe",
  "prompt": "Explain the tradeoff in one paragraph.",
  "system_prompt": "You are concise.",
  "temperature": 0.2,
  "top_p": 0.9,
  "repeat_penalty": 1.1,
  "max_output_tokens": 256,
  "context_limit": 4096,
  "safe_mode": true,
  "allow_clamp": false,
  "timeout_seconds": 180
}
```

The native `/chat` endpoint always returns a single JSON object. For streaming, use the OpenAI-compatible endpoint below with `stream: true`.

Important fields:

- `profile`: selects a configured profile from `config.json`.
- `prompt`: user text.
- `system_prompt`: optional behavior instruction.
- `max_output_tokens`: maps to Ollama `num_predict`.
- `context_limit`: maps to Ollama `num_ctx`.
- `safe_mode`: when true, caps context at the profile `safe_context_limit`.
- `allow_clamp`: when false, oversized requests return a clear error; when true, the server clamps to the configured maximum.
- `temperature`, `top_p`, `repeat_penalty`: sampling controls.
- `timeout_seconds`: per-request timeout.
- `response_format`: optional `json_object` or `json_schema` constraint. LocalDeploy forwards it to Ollama/llama.cpp native constrained generation when supported.

Request options cannot bypass server safety limits. To allow larger context, edit `config.json` first and raise the selected profile's `context_limit`, `safe_context_limit`, and `max_prompt_chars` deliberately.

## OpenAI-Compatible `/v1/chat/completions`

```json
{
  "model": "gemma3_4b_ollama_safe",
  "messages": [
    { "role": "system", "content": "You are concise." },
    { "role": "user", "content": "Explain the tradeoff in one paragraph." }
  ],
  "max_tokens": 256,
  "temperature": 0.2,
  "top_p": 0.9,
  "context_limit": 4096,
  "safe_mode": true,
  "allow_clamp": false
}
```

`model` can be either a profile name such as `gemma3_4b_ollama_safe` or a backend model id such as `gemma3:4b`. The server still applies the matching profile's safety limits.

### Streaming

Set `"stream": true` to receive a Server-Sent Events stream in OpenAI chunk format:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

Streaming is token-by-token for Ollama profiles. For llama.cpp profiles the full response is emitted as a single content delta — same wire format, but no progressive output.

### Structured output

Pass OpenAI-style `response_format` to request JSON or schema-constrained output. LocalDeploy keeps a short schema
instruction in the prompt and also forwards the constraint to the backend: Ollama receives its native `format` value,
while llama.cpp receives `response_format` on `/v1/chat/completions`. Clients must still parse and validate the result;
application-level retry or rejection remains necessary if a backend/model returns semantically invalid data.

## Context Testing

The API defaults are intentionally conservative. Use `compare_models.py` for normal comparisons. For probing larger contexts, increase limits in your local `config.json` or test directly against Ollama with `num_ctx` before changing API defaults.

## Limits Resolution Order

For every request the server computes effective limits by combining three sources, in this order. The final value is always the minimum of the request, the profile, and the global cap — a request can never bypass a server-side cap.

1. **Global caps** from environment variables (read into `get_global_limits` in `api_server.py`):
   - `GLOBAL_MAX_PROMPT_CHARS` (default 20000)
   - `GLOBAL_MAX_OUTPUT_TOKENS` (default 2048)
   - `GLOBAL_MAX_IMAGES` (default 8; hard server ceiling)
   - `GLOBAL_MAX_IMAGE_MB` (default 10)
   - `REQUEST_TIMEOUT_SECONDS` (default 180)
   - `SLOW_RESPONSE_SECONDS` (default 60)

2. **Profile limits** from `config.json` for the selected profile:
   - `max_prompt_chars`, `max_output_tokens`, `max_images`, `context_limit`, `safe_context_limit`, `timeout_seconds`, `slow_response_seconds`.

3. **Request fields** supplied by the caller:
   - `context_limit`, `max_output_tokens`, `timeout_seconds`, `safe_mode`, `allow_clamp`.

### How the effective value is chosen

| Field | Effective value |
|---|---|
| `max_prompt_chars` | `min(profile.max_prompt_chars, GLOBAL_MAX_PROMPT_CHARS)` — request cannot raise it. |
| `context_limit` | `safe_mode=true`: `min(profile.context_limit, profile.safe_context_limit, request.context_limit)`. `safe_mode=false`: `min(profile.context_limit, request.context_limit)`. |
| `max_output_tokens` | `min(profile.max_output_tokens, GLOBAL_MAX_OUTPUT_TOKENS, request.max_output_tokens)`. |
| `timeout_seconds` | `request.timeout_seconds` if set, else `profile.timeout_seconds`, else `REQUEST_TIMEOUT_SECONDS`. |
| `images` | `len(images) > min(profile.max_images, GLOBAL_MAX_IMAGES)` → reject. Profiles without `max_images` retain the legacy limit of 1. Each image: size ≤ `GLOBAL_MAX_IMAGE_MB`. |
| sampling params (`temperature`, `top_p`, `repeat_penalty`) | `request` value if provided, else profile default. No global cap. |

When the request exceeds a cap and `allow_clamp` is `false` (default), the server returns an error rather than silently shrinking. With `allow_clamp=true` the server clamps down to the allowed value and proceeds.

### Default profile resolution

The profile used when the request omits `profile` is resolved in this order:
1. `request.profile` if present
2. `DEFAULT_MODEL_PROFILE` env var
3. `default_profile` in `config.json`
4. Hard fallback: `gemma3_4b_ollama_safe`

The OpenAI-compatible endpoint additionally maps `model` to a profile by matching either a profile name or any profile's `model_id`.

### Where to change limits

- **Raise a single profile's ceilings**: edit `config.json` and bump the relevant `*_limit` fields.
- **Raise the server-wide ceiling for everyone**: edit `.env` (or process env) for `GLOBAL_*` vars.
- **Loosen at request time**: set `safe_mode=false` and/or `allow_clamp=true`. Neither can bypass global caps.
