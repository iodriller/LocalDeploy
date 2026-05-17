# Local LLM Deployment for Windows

This project runs a local-only LLM HTTP API for other apps, with Ollama as the default backend and optional llama.cpp / GGUF support for advanced optimization testing.

Target machine:

- ASUS ROG Zephyrus G15 GA503QS
- RTX 3080 Laptop GPU with 8 GB VRAM
- 40 GB RAM
- Windows 10
- No OpenAI, Anthropic, Google cloud, or external inference APIs

The default API server exposes local endpoints for chat, vision, profile listing, request safety estimation, and benchmarks. It only calls localhost backends:

- Ollama: `http://localhost:11434`
- llama.cpp server: `http://localhost:8080`

For YBM and other local OpenAI-compatible clients, the server also exposes:

- `GET /v1/models`
- `POST /v1/chat/completions`

## Why Gemma 3 4B Is The Default

`gemma3:4b` is the practical default for 8 GB VRAM because it has a better chance of staying responsive with multimodal prompts, moderate context, and predictable output limits.

`gemma3:12b` is included for testing, but 12B-class models can be slow, unstable, or fail on 8 GB VRAM depending on quantization, context length, KV cache size, GPU offload, and prompt size. Advertised very large context windows are not realistic for this laptop. Do not attempt 128K context on 8 GB VRAM.

For 12B tests, start with:

1. 2048 or 4096 context
2. 4-bit / INT4 / QAT-style quantization
3. Safe mode enabled
4. Conservative output limits

Use 4B as the production default unless 12B is clearly better for your workload.

## Files

- `api_server.py`: FastAPI server with `/chat`, `/vision`, `/benchmark`, `/estimate`, `/health`, `/models`, `/profiles`, and `/v1/chat/completions`
- `test_models.py`: benchmark and stability test runner
- `install.ps1`: Windows setup helper for Ollama and model pulls
- `config.json`: active local model profiles and limits
- `config.example.json`: profile template
- `.env`: active local environment defaults
- `.env.example`: environment template
- `requirements.txt`: Python dependencies

Project docs:

- [YBM integration](docs/YBM_INTEGRATION.md)
- [GitHub setup](docs/GITHUB_SETUP.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Key Safety Limits

The server rejects oversized requests before sending anything to Ollama or llama.cpp.

Configured limits include:

- Global max prompt characters
- Per-profile max prompt characters
- Per-profile `context_limit`
- Per-profile `safe_context_limit`
- Per-profile `max_output_tokens`
- Global max output tokens
- Max image count
- Max image size
- Backend request timeout

If `safe_mode=true`, the server enforces `safe_context_limit` instead of the full `context_limit`. Safe mode defaults to true.

If a request asks for too many output tokens or too much context, the server returns a validation error. It only clamps when the request explicitly sets `allow_clamp=true`.

## Important Optimization Knobs

- `quantization`: Q4 / INT4 / QAT is preferred for trying 12B on 8 GB VRAM.
- `context_limit` / Ollama `num_ctx`: context length often causes crashes or slowdowns faster than most other settings.
- `max_output_tokens` / Ollama `num_predict`: limits generated output and memory pressure.
- `flash_attention`: useful in llama.cpp builds that support it.
- `gpu_layers`: high values try to offload more layers to the GPU; lower values use more CPU and can be slower but safer.
- `partial CPU offload`: useful when full GPU offload fails.
- `kv_cache_type_k` / `kv_cache_type_v`: Q8 KV cache can reduce KV memory pressure in some setups but may affect speed.
- `safe_mode`: keeps requests inside conservative per-profile limits.
- `max_prompt_chars`: prevents huge prompts from accidentally hanging the backend.

Lower context first before dropping to very low-quality quantization. Avoid Q2/Q3 unless the only goal is fitting a model.

## Setup

Run PowerShell from this folder:

```powershell
cd "C:\for fun\LocalDeploy"
.\install.ps1
```

The installer will:

- Create `config.json` from `config.example.json` if missing
- Create `.env` from `.env.example` if missing
- Check whether Ollama is installed
- Try `winget install Ollama.Ollama` if Ollama is missing and winget exists
- Start or check Ollama at `http://localhost:11434`
- Verify `GET http://localhost:11434/api/tags`
- Pull `gemma3:4b`
- Pull `gemma3:12b`

Optional qwen vision model:

```powershell
.\install.ps1 -PullQwenVision
```

Skip model pulls:

```powershell
.\install.ps1 -SkipModelPulls
```

If winget is unavailable or installation fails, install Ollama manually from the Ollama Windows download page, then rerun the script.

## Python Environment

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Edit Config

Edit `config.json` to change profiles, limits, or GGUF paths.

Default profile:

```json
"default_profile": "gemma3_4b_ollama_safe"
```

For llama.cpp profiles:

1. Install or build llama.cpp separately.
2. Put your GGUF file path in `model_id`.
3. Set `ENABLE_LLAMA_CPP=true` in `.env`.
4. Set the profile `enabled` field to `true`.
5. Start `llama-server` before using the API.

llama.cpp is optional. Ollama mode works without llama.cpp.

## Start The API Server

```powershell
python api_server.py
```

Or:

```powershell
uvicorn api_server:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/health
```

## API Endpoints

- `GET /health`
- `GET /models`
- `GET /profiles`
- `POST /estimate`
- `POST /chat`
- `POST /vision`
- `POST /benchmark`
- `GET /v1/models`
- `POST /v1/chat/completions`

Response fields include:

- `success`
- `backend`
- `profile`
- `model`
- `response`
- `elapsed_seconds`
- `estimated_prompt_chars`
- `context_limit_used`
- `max_output_tokens_used`
- `warning`
- `error`

Experimental profiles return a warning field.

OpenAI-compatible responses follow the usual `choices[0].message.content` shape and include a `localdeploy` metadata object with the selected profile, backend, elapsed time, and safety limits used.

## curl Examples

Health:

```powershell
curl.exe -s http://127.0.0.1:8000/health
```

Chat:

```powershell
curl.exe -s -X POST http://127.0.0.1:8000/chat `
  -H "Content-Type: application/json" `
  -d "{\"profile\":\"gemma3_4b_ollama_safe\",\"prompt\":\"Explain what this local LLM server is doing in 3 bullet points.\",\"safe_mode\":true}"
```

Estimate a request before running it:

```powershell
curl.exe -s -X POST http://127.0.0.1:8000/estimate `
  -H "Content-Type: application/json" `
  -d "{\"profile\":\"gemma3_12b_ollama_safe\",\"prompt\":\"Summarize the local deployment constraints.\",\"max_output_tokens\":768,\"safe_mode\":true}"
```

Benchmark:

```powershell
curl.exe -s -X POST http://127.0.0.1:8000/benchmark `
  -H "Content-Type: application/json" `
  -d "{\"all_profiles\":false,\"profile\":\"gemma3_4b_ollama_safe\",\"safe_mode\":true}"
```

OpenAI-compatible local chat:

```powershell
curl.exe -s -X POST http://127.0.0.1:8000/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"gemma3_4b_ollama_safe\",\"messages\":[{\"role\":\"user\",\"content\":\"Explain what LocalDeploy is doing in 2 sentences.\"}],\"max_tokens\":256}"
```

## PowerShell API Examples

```powershell
$body = @{
  profile = "gemma3_4b_ollama_safe"
  prompt = "You are being used inside a local desktop app. Give a short, practical answer explaining how the app should call your API."
  safe_mode = $true
  max_output_tokens = 512
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8000/chat" -Method Post -ContentType "application/json" -Body $body
```

Vision with base64:

```powershell
$imageBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\image.png"))
$body = @{
  profile = "gemma3_4b_ollama_safe"
  prompt = "Describe the image accurately. Then extract any visible text. Then list any uncertainty."
  images_base64 = @($imageBase64)
  safe_mode = $true
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://127.0.0.1:8000/vision" -Method Post -ContentType "application/json" -Body $body
```

Vision with file upload:

```powershell
curl.exe -s -X POST http://127.0.0.1:8000/vision `
  -F "profile=gemma3_4b_ollama_safe" `
  -F "prompt=Describe the image accurately. Then extract any visible text. Then list any uncertainty." `
  -F "image=@C:\path\image.png"
```

## Run Model Tests

Test the default profile:

```powershell
python test_models.py
```

Test all enabled profiles:

```powershell
python test_models.py --all
```

Test only Ollama Gemma 12B:

```powershell
python test_models.py --profile gemma3_12b_ollama_safe
```

Test with an image:

```powershell
python test_models.py --profile gemma3_4b_ollama_safe --image "C:\path\image.png"
```

Override safe runtime settings:

```powershell
python test_models.py --profile gemma3_12b_ollama_safe --safe-mode true --context-limit 2048 --max-output-tokens 512 --timeout 240
```

`test_models.py` uses the local API server if it is already running. If the server is not running, it calls the configured local backend directly through the same safety validation code.

The benchmark prompts include:

- Basic: "Explain what this local LLM server is doing in 3 bullet points."
- Reasoning: "A laptop can process 18 images per minute. How long will it take to process 153 images? Show your reasoning briefly."
- Coding: "Write a Python function that validates whether a string is valid JSON and returns a tuple of success and error message."
- JSON compliance: "Return only valid JSON with keys: model_capability, strengths, weaknesses, recommended_use. No markdown."
- Local API use case: "You are being used inside a local desktop app. Give a short, practical answer explaining how the app should call your API."
- Long-context: generated locally and capped by the configured safe limits.
- Vision: used only when you pass `--image`.

## llama.cpp Commands

llama.cpp mode is for precise testing of GGUF quantization, context, cache, and GPU offload. It is not required for the basic project.

Set this in `.env` after starting a local llama-server:

```text
ENABLE_LLAMA_CPP=true
LLAMACPP_BASE_URL=http://localhost:8080
```

Enable the profile in `config.json` and update `model_id` to the exact local GGUF path.

Q4_0 QAT safe first attempt:

```cmd
llama-server ^
  -m C:\models\gemma-3-12b-it-qat-Q4_0.gguf ^
  -ngl 99 ^
  -c 4096 ^
  -fa ^
  --host 127.0.0.1 ^
  --port 8080
```

Longer-context experimental command with Q8 KV cache:

```cmd
llama-server ^
  -m C:\models\gemma-3-12b-it-qat-Q4_0.gguf ^
  -ngl 99 ^
  -c 8192 ^
  -fa ^
  --cache-type-k q8_0 ^
  --cache-type-v q8_0 ^
  --host 127.0.0.1 ^
  --port 8080
```

Q4_K_M quality attempt:

```cmd
llama-server ^
  -m C:\models\gemma-3-12b-it-qat-Q4_K_M.gguf ^
  -ngl 99 ^
  -c 2048 ^
  -fa ^
  --host 127.0.0.1 ^
  --port 8080
```

Partial GPU offload fallback:

```cmd
llama-server ^
  -m C:\models\gemma-3-12b-it-qat-Q4_0.gguf ^
  -ngl 30 ^
  -c 4096 ^
  -fa ^
  --host 127.0.0.1 ^
  --port 8080
```

Notes:

- Q4_0 QAT / INT4 is the first 12B attempt for 8 GB VRAM.
- Q4_K_M may be higher quality but tighter on VRAM.
- IQ4_XS can be smaller and easier to fit.
- Q8 KV cache can reduce KV memory pressure in some setups but may affect speed.
- Start with 2048 or 4096 context for 12B.
- Increase to 8192 only after stability testing.
- Do not attempt 128K context on this 8 GB VRAM setup.

## Suggested Testing Strategy

1. Run `gemma3_4b_ollama_safe`.
2. Run `gemma3_12b_ollama_safe`.
3. Compare speed and quality.
4. If 12B is too slow or unstable in Ollama, try llama.cpp Q4_0 QAT.
5. Start with context 2048 or 4096.
6. Try 8192 only after it is stable.
7. Test Q8 KV cache only if longer context is needed.
8. Try Q4_K_M only if quality matters more than memory margin.
9. Use 4B as the production default unless 12B is clearly better.

## Troubleshooting

Ollama not running:

```powershell
ollama serve
Invoke-RestMethod http://localhost:11434/api/tags
```

Model not found:

```powershell
ollama pull gemma3:4b
ollama pull gemma3:12b
```

12B too slow:

- Keep `safe_mode=true`.
- Lower `max_output_tokens`.
- Lower `context_limit` to 2048.
- Use 4B as the default profile.

12B hangs:

- Stop the request.
- Restart Ollama or llama-server.
- Use `gemma3_4b_ollama_safe`.
- Try a lower context and shorter prompt.

Out of memory:

- Lower context first.
- Lower output tokens.
- For llama.cpp, reduce `-ngl`.
- Try IQ4_XS or Q4_0 QAT before lower-quality Q2/Q3.

Prompt too large:

- Check `GLOBAL_MAX_PROMPT_CHARS` in `.env`.
- Check `max_prompt_chars` in `config.json`.
- Use `/estimate` before `/chat`.

Context too large:

- Keep `safe_mode=true`.
- Use `safe_context_limit`.
- Do not request large contexts like 128K on 8 GB VRAM.

Image too large:

- Check `GLOBAL_MAX_IMAGE_MB`.
- Resize the image before sending.
- Keep `GLOBAL_MAX_IMAGES=1` unless you know the model and backend are stable.

llama.cpp server not reachable:

- Confirm `ENABLE_LLAMA_CPP=true`.
- Confirm `LLAMACPP_BASE_URL=http://localhost:8080`.
- Start `llama-server` with `--host 127.0.0.1 --port 8080`.
- Confirm the GGUF path in `config.json` exists.

## Local Privacy

Prompts stay local as long as `OLLAMA_BASE_URL` and `LLAMACPP_BASE_URL` point to localhost endpoints. This project does not use OpenAI APIs, cloud SDKs, or external inference APIs.
