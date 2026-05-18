# YBM Integration

YBM currently has an `openai_compatible` LLM provider that posts to:

```text
{base_url}/chat/completions
```

LocalDeploy exposes an OpenAI-compatible shim at:

```text
http://127.0.0.1:8000/v1/chat/completions
```

That means YBM should use this base URL:

```text
http://127.0.0.1:8000/v1
```

## Start LocalDeploy

```powershell
cd "C:\for fun\LocalDeploy"
.\.venv\Scripts\Activate.ps1
python api_server.py
```

Check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/v1/models
```

## YBM Config Snippet

Use the profile name as the OpenAI-compatible `model` value. LocalDeploy maps that profile name back to the safe model profile in `config.json`.

```yaml
llm:
  default_profile: "localdeploy_gemma3_4b"
  profiles:
    localdeploy_gemma3_4b:
      provider: "openai_compatible"
      model: "gemma3_4b_ollama_safe"
      base_url: "http://127.0.0.1:8000/v1"
      api_key_env: null
      timeout_seconds: 180
      max_tokens: 1024
      temperature: 0.2
```

For 12B testing:

```yaml
llm:
  default_profile: "localdeploy_gemma3_12b"
  profiles:
    localdeploy_gemma3_12b:
      provider: "openai_compatible"
      model: "gemma3_12b_ollama_safe"
      base_url: "http://127.0.0.1:8000/v1"
      api_key_env: null
      timeout_seconds: 240
      max_tokens: 768
      temperature: 0.2
```

## Structured Output Notes

YBM can request structured output through `response_format`. LocalDeploy passes the request through as a strict JSON instruction appended to the local prompt. Local models may still produce invalid JSON, so benchmark JSON compliance before using a profile for planning workflows.

Recommended YBM path:

1. Use `gemma3_4b_ollama_safe` first.
2. Run YBM planner/classifier tests.
3. Try `gemma3_12b_ollama_safe` only if 4B quality is not enough.
4. Keep 12B safe mode limits conservative.

## Local-Only Guarantee

YBM sends requests to LocalDeploy on `127.0.0.1`. LocalDeploy then calls only localhost Ollama or llama.cpp endpoints. No cloud inference API is used.
