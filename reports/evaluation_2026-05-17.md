# LocalDeploy Evaluation - 2026-05-17

## Setup Status

- Python virtual environment: created at `.venv`.
- Python dependencies: installed from `requirements.txt`.
- Ollama: installed with winget and running locally.
- Local API: running at `http://127.0.0.1:8000`.
- Ollama API: reachable at `http://localhost:11434`.
- llama.cpp: optional and disabled for this run.
- Raw local logs: kept under `logs/` and ignored by Git.
- Committed report logs: stored in `reports/`.

Installer verification:

```text
install.ps1 -SkipModelPulls completed successfully after Ollama installation.
Ollama API responded at http://localhost:11434.
gemma3:4b and gemma3:12b were pulled successfully with direct ollama pull commands.
```

## Pulled Ollama Models

| Model | Size | Parameters | Quantization |
|---|---:|---:|---|
| `gemma3:12b` | 7.59 GiB | 12.2B | Q4_K_M |
| `gemma3:4b` | 3.11 GiB | 4.3B | Q4_K_M |

## YBM Compatibility

YBM's existing OpenAI-compatible provider can use:

```yaml
base_url: "http://127.0.0.1:8000/v1"
model: "gemma3_4b_ollama_safe"
api_key_env: null
```

Compatibility checks used `POST /v1/chat/completions` and validated the `choices[0].message.content` response shape.

| Check | Success | Seconds | JSON Valid | Profile | Context | Max Output |
|---|---:|---:|---:|---|---:|---:|
| `ybm_text_4b` | True | 6.613 | None | `gemma3_4b_ollama_safe` | 4096 | 256 |
| `ybm_json_4b` | True | 3.24 | True | `gemma3_4b_ollama_safe` | 4096 | 256 |
| `ybm_text_12b` | True | 18.316 | None | `gemma3_12b_ollama_safe` | 2048 | 256 |

Actual YBM provider checks imported `agent_control.llm.providers.OpenAICompatibleProvider` from `C:/for fun/YBM` and called LocalDeploy directly.

| Actual YBM Check | Success | Seconds | Result |
|---|---:|---:|---|
| `YBM OpenAICompatibleProvider.generate_text` | True | 3.286 | Potentially, but needs further testing. The LocalDeploy endpoint *could* be usable by YBM  |
| `YBM OpenAICompatibleProvider.generate_structured` | True | 3.487 | {"compatible": true, "endpoint": "http://127.0.0.1:8000/v1/chat/completions", "recommended_profile": "gemma3_4b_ollama_safe", "risk": "low"} |

Result: compatible with YBM for text generation and structured generation. Structured output is improved by LocalDeploy's `/v1` shim, which strips valid JSON out of fenced model responses and gives JSON-schema requests data-object instructions instead of asking the model to echo the schema.

## Full Profile Comparison

The full comparison used conservative `--max-output-tokens 256`.

```text
Test mode: direct backend calls
Safe mode: True
Requested max_output_tokens: 256

Profile: gemma3_4b_ollama_safe
  backend=ollama model=gemma3:4b recommended_8gb=True
  PASS basic: 9.30s, 1117 chars, ~30.03 tok/s
  PASS reasoning: 3.82s, 321 chars, ~21.00 tok/s
  PASS coding: 5.87s, 875 chars, ~37.27 tok/s
  PASS long_context: 5.45s, 793 chars, ~36.35 tok/s
  PASS json_compliance: 5.24s, 805 chars, ~38.39 tok/s
  PASS local_api_use: 4.75s, 645 chars, ~33.91 tok/s
  Summary: works | score=0.837 reliability=100% quality=0.81

Profile: gemma3_12b_ollama_safe
  backend=ollama model=gemma3:12b recommended_8gb=False
  PASS basic: 16.36s, 256 chars, ~3.91 tok/s
  PASS reasoning: 16.33s, 305 chars, ~4.67 tok/s
  PASS coding: 37.87s, 849 chars, ~5.61 tok/s
  PASS long_context: 36.28s, 1020 chars, ~7.03 tok/s
  PASS json_compliance: 38.38s, 1213 chars, ~7.90 tok/s
  PASS local_api_use: 14.47s, 324 chars, ~5.60 tok/s
  Summary: works | score=0.783 reliability=100% quality=0.87

Scoring summary
- gemma3_4b_ollama_safe: score=0.837, status=works, avg=5.74s, tok/s=32.82, failures=none, 8GB=True
- gemma3_12b_ollama_safe: score=0.783, status=works, avg=26.61s, tok/s=5.79, failures=none, 8GB=False

Recommended profile: gemma3_4b_ollama_safe
Reason: it is the safest default when speed and reliability matter on 8 GB VRAM.
```

## HTTP API Path Checks

4B through LocalDeploy API:

```text
Test mode: local API server at http://127.0.0.1:8000
Safe mode: True
Requested max_output_tokens: 64

Profile: gemma3_4b_ollama_safe
  backend=ollama model=gemma3:4b recommended_8gb=True
  PASS basic: 6.96s, 265 chars, ~9.52 tok/s
  PASS reasoning: 3.17s, 226 chars, ~17.81 tok/s
  PASS coding: 3.24s, 240 chars, ~18.51 tok/s
  PASS long_context: 4.01s, 297 chars, ~18.52 tok/s
  PASS json_compliance: 3.29s, 268 chars, ~20.33 tok/s
  PASS local_api_use: 3.38s, 235 chars, ~17.39 tok/s
  Summary: works | score=0.790 reliability=100% quality=0.57

Scoring summary
- gemma3_4b_ollama_safe: score=0.790, status=works, avg=4.01s, tok/s=17.01, failures=none, 8GB=True

Recommended profile: gemma3_4b_ollama_safe
Reason: it is the safest default when speed and reliability matter on 8 GB VRAM.
```

12B through LocalDeploy API:

```text
Test mode: local API server at http://127.0.0.1:8000
Safe mode: True
Requested max_output_tokens: 64

Profile: gemma3_12b_ollama_safe
  backend=ollama model=gemma3:12b recommended_8gb=False
  PASS basic: 14.86s, 260 chars, ~4.37 tok/s
  PASS reasoning: 11.24s, 219 chars, ~4.87 tok/s
  PASS coding: 11.92s, 241 chars, ~5.06 tok/s
  PASS long_context: 14.23s, 279 chars, ~4.90 tok/s
  PASS json_compliance: 11.37s, 300 chars, ~6.60 tok/s
  PASS local_api_use: 11.85s, 270 chars, ~5.70 tok/s
  Summary: works | score=0.740 reliability=100% quality=0.65

Scoring summary
- gemma3_12b_ollama_safe: score=0.740, status=works, avg=12.58s, tok/s=5.25, failures=none, 8GB=False

Recommended profile: gemma3_12b_ollama_safe
Reason: 12B only won because it ran reliably enough for the measured workload.
```

## Evaluation

- `gemma3_4b_ollama_safe` is the best default for YBM on this machine: 100% reliability in the test run, much better speed, and lower VRAM risk.
- `gemma3_12b_ollama_safe` works locally, but is much slower and still marked non-default for 8 GB VRAM. Keep safe mode enabled and use it only when quality gain justifies latency.
- The llama.cpp profiles remain optional. They were not tested because no local GGUF files were configured and `ENABLE_LLAMA_CPP=false`.

## API Health Snapshot

```json
{
  "success": true,
  "server": "ok",
  "config_path": "C:\\for fun\\LocalDeploy\\config.json",
  "default_profile": "gemma3_4b_ollama_safe",
  "ollama": {
    "base_url": "http://localhost:11434",
    "reachable": true,
    "models": [
      "gemma3:12b",
      "gemma3:4b"
    ],
    "error": null
  },
  "llamacpp": {
    "enabled": false
  },
  "limits": {
    "max_prompt_chars": 20000,
    "max_output_tokens": 2048,
    "max_images": 1,
    "max_image_mb": 10.0,
    "request_timeout_seconds": 180,
    "slow_response_seconds": 60
  }
}
```
