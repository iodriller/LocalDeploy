# Model Catalog

Reference for picking and configuring profiles. The target hardware throughout is a single NVIDIA RTX 3080 with 8 GB VRAM. All numbers below are rough estimates from public benchmarks and community testing; verify with `test_models.py --all` on your machine before relying on them.

VRAM figures include weights plus KV cache at the listed context. Token-per-second figures are for generation (decode) on a 3080-class card with the weights fully GPU-resident; values drop sharply once partial CPU offload kicks in.

## Measured results on this hardware (v2)

These numbers come from running `benchmark.py` against the local API on an **RTX 3080 Laptop (8 GB)** via Ollama on **17 profiles × 25 tests** spanning planning, classification, code, math, structured (basic JSON), and structured_hard (YBM-style pydantic schemas: classification, plan orchestration, approval gate, multi-task extraction). Peak VRAM includes ~1.8 GB baseline for the Windows desktop. Reports: `reports/benchmark_2026-05-27T012222Z.md` (base), `…T014432Z.md` (reasoning-fix), `…T020524Z.md` (hard JSON).

### Leaderboard

| # | Profile | Model | n | Overall | Plan | Cls | Code | Math | Struct | **Hard** | Avg s | Peak VRAM |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | **`qwen3vl_8b_ollama`** | `qwen3-vl:8b-instruct` | 25 | **0.84** | 0.93 | **1.00** | 0.93 | 0.40 | 1.00 | 0.88 | 13.2s | 7372 MB |
| 2 | `qwen25_7b_q5km_ollama` | `qwen2.5:7b-instruct-q5_K_M` | 25 | 0.79 | 0.89 | 0.60 | 0.93 | 0.60 | 1.00 | 0.86 | **7.0s** | 7108 MB |
| 2 | `gemma3_12b_qat_ollama` | `gemma3:12b-it-qat` | 25 | 0.79 | 0.87 | 0.80 | 0.93 | 0.40 | 1.00 | 0.86 | 34.9s | 7460 MB |
| 2 | `qwen25_7b_ollama` | `qwen2.5:7b` | 25 | 0.79 | 0.86 | 0.60 | 0.93 | **0.60** | 1.00 | 0.86 | **5.4s** | 6323 MB |
| 5 | `qwen3vl_4b_ollama` | `qwen3-vl:4b-instruct` | 25 | 0.78 | 0.92 | 0.80 | 0.93 | 0.40 | 1.00 | 0.75 | 6.3s | 7350 MB |
| 6 | `gemma3_4b_ollama_safe` | `gemma3:4b` | 25 | 0.77 | 0.73 | **1.00** | 0.93 | 0.20 | 1.00 | 0.84 | 5.6s | 6184 MB |
| 6 | `gemma3_12b_ollama_safe` | `gemma3:12b` | 25 | 0.77 | 0.73 | 0.80 | 0.93 | 0.40 | 1.00 | 0.82 | 37.1s | 7391 MB |
| 8 | `qwen25_coder_7b_ollama` | `qwen2.5-coder:7b` | 25 | 0.75 | 0.87 | 0.40 | 0.93 | 0.60 | 1.00 | 0.86 | **5.4s** | 6538 MB |
| 9 | `qwen25vl_7b_ollama` | `qwen2.5vl:7b` | 25 | 0.72 | 0.89 | 0.60 | 0.93 | 0.40 | 1.00 | 0.68 | 31.1s | 7299 MB |
| 9 | **`qwen3_8b_ollama`**¹ | `qwen3:8b` | 25 | 0.72 | 0.64 | **1.00** | 0.88 | 0.00 | 1.00 | **0.91** 👑 | 7.9s | 7097 MB |
| 11 | `qwen25vl_3b_ollama` | `qwen2.5vl:3b` | 25 | 0.70 | 0.88 | 0.80 | 0.90 | 0.20 | 0.79 | 0.80 | 19.1s | **1567 MB** |
| 12 | `granite33_8b_ollama` | `granite3.3:8b` | 25 | 0.67 | 0.93 | 0.60 | 0.95 | 0.00 | 1.00 | 0.80 | 11.4s | 6714 MB |
| 13 | `llama31_8b_ollama` | `llama3.1:8b` | 25 | 0.66 | 0.72 | 0.60 | 0.93 | 0.00 | 1.00 | **0.89** | 8.0s | 7484 MB |
| 14 | `llama32_3b_ollama` | `llama3.2:3b` | 25 | 0.64 | 0.93 | 0.60 | 0.95 | 0.00 | 0.96 | 0.66 | **4.0s** ⚡ | 6770 MB |
| 15 | `mistral_7b_ollama` | `mistral:7b` | 25 | 0.60 | 0.90 | 0.40 | 0.84 | 0.00 | 1.00 | 0.73 | 6.7s | 7435 MB |
| 16 | `phi4_mini_ollama` | `phi4-mini` | 25 | 0.58 | 0.65 | 0.20 | 0.88 | 0.20 | 0.95 | 0.80 | 5.5s | 7185 MB |
| 17 | **`deepseek_r1_distill_qwen_7b_ollama`**¹ | `deepseek-r1:7b` | 25 | 0.51 | 0.73 | 0.20 | **1.00** | 0.00 | 0.65 | 0.76 | 6.9s | 6943 MB |

¹ Reasoning models — benchmark uses `think: false` via the Ollama backend so the final answer is graded instead of the chain-of-thought. Math accuracy is 0 *because* thinking is disabled; with thinking on these models score well on math but their `content` field is empty (Ollama puts reasoning in a separate `thinking` field) so the rest of the benchmark would break.

### What "Hard" means

The **`structured_hard`** category is 4 tests modeled directly on YBM's pydantic schemas:

- **`json_intent_classify`** — Route a user message to one of 14 enum routes; output a nested `MessageClassification` + `OrchestrationIntent` object.
- **`json_plan_orchestration`** — Generate a `PlanModel` with enum-validated `required_capabilities` and a hard cross-field constraint: each step's capabilities must be a subset of the plan's top-level capabilities.
- **`json_approval_decision`** — Conditional-field test: `confirmation_prompt` must be non-null iff `needs_user_confirmation=true`; also requires cautious decision (reject/clarify) on a destructive action.
- **`json_multi_task_extract`** — Split a paragraph into 2 separate task objects with correct trigger.kind (`watch` vs `schedule`) plus preserve a negative constraint.

These actually separate the models. The easy `structured` category had everyone at ~1.00; on hard JSON the spread is **0.66 (llama3.2:3b) → 0.91 (qwen3:8b)**.

### Headline findings

1. **`qwen3-vl:8b` is the best all-rounder** at 0.84 overall, perfect classification, top-tier on every category. Use it as the default profile unless you specifically need raw speed.
2. **`qwen3:8b` is the structured-output champion at 0.91 hard-JSON**, but only with `think: false` set. Without it Ollama returns empty content (the chain-of-thought lives in a separate `thinking` field). Configured in this repo's `config.json`.
3. **`llama3.1:8b` is the sleeper pick for JSON pipelines** — 0.89 hard JSON (#2 overall) despite only 0.66 overall. If you don't need math, it's competitive with the top.
4. **QAT beats default Q4 on Gemma 12B**: `gemma3:12b-it-qat` scores 0.79 vs `gemma3:12b`'s 0.77, same speed. Always use the QAT tag for Gemma.
5. **Q5_K_M ≈ Q4 default on Qwen2.5 7B** — same overall (0.79 vs 0.79), Q5 adds ~2s latency, picks up `math_determinant` but loses `cls_bug_severity`. Not worth the bigger VRAM footprint on its own.
6. **Math is universally bad without thinking.** Llama-family + Granite + Mistral all score **0.00** on the math category. Only Qwen 2.5 (text and coder) breaks 0.40 (0.60 each). For arithmetic, use `deepseek_r1_distill_qwen_7b_ollama` with `think: true` and grade the post-`</think>` answer.
7. **Small can be fast.** `llama3.2:3b` averages **4.0s per test** — the fastest profile — at acceptable accuracy (0.64). `qwen2.5vl:3b` peaks at just **1.57 GB VRAM**, leaving the GPU free for parallel work.

### Per-use-case verdicts

| Use case | Pick |
|---|---|
| General default (vision + text) | **`qwen3vl_8b_ollama`** — 0.84 overall, every category ≥ 0.88 except math |
| Fastest text generalist | `qwen25_7b_ollama` — 0.79 at 5.4s avg |
| Pure structured JSON (planning, classification, extraction) | **`qwen3_8b_ollama`** with `think:false` — 0.91 hard JSON, 1.00 classification |
| JSON pipelines without math | `llama31_8b_ollama` — 0.89 hard JSON, 1.00 structured, 0.93 code |
| Coding | `qwen25_coder_7b_ollama` — 0.93 code, 5.4s, also 0.86 hard JSON |
| Math / arithmetic | `qwen25_7b_ollama` (0.60 math) — Qwen models are the only family that doesn't 0 out |
| Fastest at all costs | `llama32_3b_ollama` — 4.0s avg, 0.95 code, 0.93 planning |
| Minimum VRAM footprint | `qwen25vl_3b_ollama` — 1.57 GB peak |
| Quality with depth (cost: slow) | `gemma3_12b_qat_ollama` — 0.79 but 35s avg |
| Reasoning / chain-of-thought | `deepseek_r1_distill_qwen_7b_ollama` (with `think:true`, post-process `<think>` tags) |

### Profiles to avoid (or be careful with)

- **Default `qwen3:8b` without `think:false`** → empty responses (thinking field is separate from content in Ollama). Already configured here.
- **`gemma3:12b` default Q4 over QAT** → ~2% worse for same speed and VRAM. Pull `gemma3:12b-it-qat` instead.
- **`qwen2.5vl:7b`** → superseded by `qwen3-vl:8b-instruct`; 0.72 vs 0.84.
- **`mistral:7b`** → outdated, weak on math and classification; better Qwen alternatives at every size.

### How to reproduce

```powershell
# Pull whichever models you want to test
ollama pull qwen3:8b
ollama pull llama3.1:8b
# ... etc

# Edit config.json to enable the profiles
# Start the API server on an unused port
$env:API_PORT = "8011"
.\scripts\start.ps1

# Run the full battery
python benchmark.py --timeout 300
```

Targeted runs:

```powershell
# Only the new YBM-style hard JSON tests on all enabled profiles
python benchmark.py --include-categories structured_hard

# Skip categories you don't care about
python benchmark.py --skip-categories math
```

### How to extend the benchmark

To pull additional models and add them to the next run:

```powershell
ollama pull qwen2.5-coder:7b
ollama pull qwen3:8b
ollama pull llama3.1:8b
ollama pull phi4-mini
ollama pull deepseek-r1:7b
ollama pull mistral:7b
```

Then flip `enabled: true` for the matching profile in `config.json` and rerun:

```powershell
python benchmark.py --timeout 240
```

Reports land in `reports/benchmark_<timestamp>.json` and `.md`.



## Profiles in `config.example.json`

All new profiles ship **disabled**. To use one: edit `config.json`, flip `enabled: true`, run the `ollama pull` command (or set the GGUF `model_id` path for llama.cpp), then restart the API.

### Text profiles

| Profile name | Backend | `model_id` | Pull / setup |
|---|---|---|---|
| `gemma3_4b_ollama_safe` | ollama | `gemma3:4b` | `ollama pull gemma3:4b` |
| `gemma3_12b_ollama_safe` | ollama | `gemma3:12b` | `ollama pull gemma3:12b` |
| `qwen25_coder_7b_ollama` | ollama | `qwen2.5-coder:7b` | `ollama pull qwen2.5-coder:7b` |
| `qwen3_8b_ollama` | ollama | `qwen3:8b` | `ollama pull qwen3:8b` |
| `llama31_8b_ollama` | ollama | `llama3.1:8b` | `ollama pull llama3.1:8b` |
| `phi4_mini_ollama` | ollama | `phi4-mini` | `ollama pull phi4-mini` |
| `deepseek_r1_distill_qwen_7b_ollama` | ollama | `deepseek-r1:7b` | `ollama pull deepseek-r1:7b` |
| `mistral_7b_ollama` | ollama | `mistral:7b` | `ollama pull mistral:7b` |
| `qwen3_8b_gguf_q4km_kvq8` | llamacpp | GGUF file | Download bartowski Q4_K_M, set `model_id` path |
| `gemma3_4b_gguf_q4_gpu` | llamacpp | GGUF file | Download Gemma 3 4B Q4_K_M, set `model_id` path |
| `gemma3_12b_gguf_q4_safe` | llamacpp | GGUF file | Download Gemma 3 12B Q4_K_M, set `model_id` path |

### Vision profiles

| Profile name | Backend | `model_id` | Pull / setup |
|---|---|---|---|
| `qwen3vl_8b_ollama` | ollama | `qwen3-vl:8b-instruct` | `ollama pull qwen3-vl:8b-instruct` |
| `qwen3vl_4b_ollama` | ollama | `qwen3-vl:4b-instruct` | `ollama pull qwen3-vl:4b-instruct` |
| `gemma3_12b_ollama_safe` | ollama | `gemma3:12b` | Marked `recommended_for_8gb_vram: "fallback"` — vision works but ~4–8 t/s with partial offload |

### Llama.cpp GGUF tuning

All `llamacpp` profiles ship with `flash_attention: true` and `kv_cache_type_k/v: q8_0` so KV cache memory is roughly halved with negligible quality loss. The `gemma3_12b_gguf_q4_longer_context` profile keeps `q8_0` and pushes context to 8K; experimental.

## How to read this table

- **VRAM @ ctx**: approximate steady-state VRAM with `-fa on` and `q8_0` KV cache. Numbers without KV quantization are 10–30% higher.
- **t/s**: generation tokens per second, single user, no batching.
- **Quant**: recommended GGUF quantization. `Q4_K_M` is the safe default; `IQ4_XS` is for fitting bigger models or longer contexts; `Q5_K_M`/`Q6_K` only if you have headroom.
- **Backend**: `ollama` is easiest; `llamacpp` exposes more knobs (KV cache type, batch sizes, draft model).

## Text Models (8 GB VRAM)

| Model | Params | Quant | VRAM @ 4K | VRAM @ 8K | t/s | Best for |
|---|---|---|---|---|---|---|
| **Qwen2.5-Coder 7B** | 7B | Q4_K_M | ~5.0 GB | ~5.5 GB | 40–55 | Code generation and refactors (HumanEval ~76) |
| **Qwen3 8B** | 8B | Q4_K_M (KV q8_0) | ~5.5 GB | ~6.2 GB | 35–50 | Reasoning, math, general Q&A |
| **Llama 3.3 8B** | 8B | Q4_K_M | ~5.5 GB | ~6.2 GB | 35–50 | Generalist; strong MMLU (~73) |
| **Mistral Small 3 7B** | 7B | Q4_K_M | ~5.0 GB | ~5.5 GB | 45–60 | Speed-bound chat |
| **Phi-4-mini** | 3.8B | Q5_K_M | ~3.0 GB | ~3.3 GB | 70–100 | Lowest latency; surprisingly strong on math |
| **DeepSeek-R1-Distill-Qwen-7B** | 7B | Q4_K_M | ~5.0 GB | ~5.5 GB | 30–45 | Local chain-of-thought reasoning |
| **Gemma 3 4B (it, QAT)** | 4B | Q4_0_QAT | ~3.2 GB | ~3.6 GB | 60–85 | Safe default; multimodal-capable variant exists |
| **Gemma 3 12B (it, QAT)** | 12B | IQ4_XS | ~7.2 GB | OOM-risk | 8–18 | Larger context only if you trim KV; otherwise expect partial CPU offload |

## Vision-Language Models

| Model | Params | Quant | VRAM @ 4K | t/s | Notes |
|---|---|---|---|---|---|
| **Qwen3-VL 8B (instruct)** | 8B | Ollama default | ~6.0 GB | 25–40 | Best small VL in 2026; strong OCR and MathVista |
| **Qwen3-VL 4B (instruct)** | 4B | Ollama default | ~3.5 GB | 50–70 | Fast OCR / UI screenshots |
| **Qwen2.5-VL 7B** | 7B | Ollama default | ~5.5 GB | 30–45 | Still strong; older than Qwen3-VL |
| **Gemma 3 4B (it)** | 4B | Q4_0_QAT | ~3.5 GB | 50–70 | Lightweight multimodal |
| **Gemma 3 12B (it)** | 12B | IQ4_XS | tight on 8 GB | 4–10 | Quality > speed; expect heavy CPU offload |

## Quantization Cheat Sheet

| Quant | Bits/weight (eff.) | Quality loss vs FP16 | When to use |
|---|---|---|---|
| `Q8_0` | 8.5 | Negligible | Reference quality, models ≤3B |
| `Q6_K` | ~6.5 | Near-lossless | 7B fits if you have headroom |
| `Q5_K_M` | ~5.5 | Imperceptible for most tasks | When 4B–7B has VRAM left |
| `Q4_K_M` | ~4.89 | Small | Default for 7B–8B on 8 GB |
| `IQ4_XS` | ~4.46 | Slightly more sensitive than Q4_K_M | Fitting larger models / longer contexts |
| `Q4_0_QAT` | ~4.5 | Best 4-bit for Gemma 3 (QAT-trained) | Always prefer over plain Q4 on Gemma 3 |
| `Q3_K_M` | ~3.9 | Visible degradation | Only when nothing else fits |

For Llama 3.3 and Qwen3, prefer `bartowski` GGUFs; for Gemma 3, prefer the official QAT releases.

## KV Cache Quantization

A major VRAM lever on 8 GB cards. Configured per profile via `kv_cache_type_k` / `kv_cache_type_v` in `config.json` and exposed by `llama-server` as `-ctk` / `-ctv`.

| Setting | KV memory vs f16 | Quality risk | Notes |
|---|---|---|---|
| `f16` (default) | 1.00× | — | Baseline. |
| `q8_0` | ~0.50× | Negligible on most tasks | Frees 1–3 GB on a 7B model at 16K. Requires `-fa on`. |
| `q4_0` | ~0.25× | Watchable on long reasoning | Use when stretching to 32K+ context. Requires `-fa on`. |

`-fa on` (flash attention) is a hard prerequisite for KV cache quantization. Without it, llama.cpp must dequantize the cache on every attention step and you lose any speed benefit.

## Recommended Profile Set for an 8 GB RTX 3080

A reasonable lineup:

- **Default (general)**: Qwen3 8B Q4_K_M with KV `q8_0`, ctx 8192.
- **Code**: Qwen2.5-Coder 7B Q4_K_M, ctx 8192.
- **Vision**: Qwen3-VL 8B (Ollama) for quality, Qwen3-VL 4B for speed.
- **Speed**: Phi-4-mini Q5_K_M, ctx 8192.
- **Reasoning**: DeepSeek-R1-Distill-Qwen-7B Q4_K_M, ctx 4096–8192.
- **Fallback**: Gemma 3 4B QAT — safe and small.

## Inference Engines

For a single 8 GB GPU and single user, **llama.cpp** and **Ollama** are the right choice. Other engines were considered:

- **vLLM** — fastest under concurrency but uses 20–30% more VRAM per model than llama.cpp at the same quant; meant for multi-user serving.
- **ExLlamaV3 (EXL3)** — strong low-bit quants and high t/s on Ampere; worth considering only if you want to push 13B+ models onto the card.
- **TGI** — moved to maintenance mode in 2026; do not start new projects on it.

## Sources

- [Best LLM Models for 8GB VRAM in 2026 — inferencerig.com](https://inferencerig.com/models/best-llm-models-for-8gb-vram-in-2026-tested-and-ranked/)
- [Llama.cpp GGUF Quantization Guide 2026 — decodesfuture.com](https://www.decodesfuture.com/articles/llama-cpp-gguf-quantization-guide-2026)
- [Choosing a GGUF Model: K-Quants, I-Quants — kaitchup.substack.com](https://kaitchup.substack.com/p/choosing-a-gguf-model-k-quants-i)
- [llama.cpp server README — GitHub](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [Best Small Language Models 2026 — localaimaster.com](https://localaimaster.com/blog/small-language-models-guide-2026)
- [Best Vision Models You Can Run Locally — InsiderLLM](https://insiderllm.com/guides/vision-models-locally/)
