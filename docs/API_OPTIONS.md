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
  "stream": false,
  "timeout_seconds": 180
}
```

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

## Context Testing

The API defaults are intentionally conservative. Use `test_models.py` for normal comparisons. For probing larger contexts, increase limits in your local `config.json` or test directly against Ollama with `num_ctx` before changing API defaults.
