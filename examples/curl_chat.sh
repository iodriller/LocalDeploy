#!/usr/bin/env bash
# Minimal LocalDeploy usage via curl.
# Requires the API server to be running at http://127.0.0.1:8000.

set -euo pipefail

BASE_URL="${LOCALDEPLOY_BASE_URL:-http://127.0.0.1:8000}"

echo "== /health =="
curl -sS "${BASE_URL}/health" | head -c 400
echo

echo "== Native /chat =="
curl -sS -X POST "${BASE_URL}/chat" \
    -H "Content-Type: application/json" \
    -d '{
        "prompt": "Reply with one sentence: what is your role?",
        "safe_mode": true,
        "max_output_tokens": 128
    }'
echo

echo "== OpenAI-compatible /v1/chat/completions =="
curl -sS -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{
        "messages": [
            {"role": "system", "content": "You are concise."},
            {"role": "user", "content": "Name three small open-weight LLMs."}
        ],
        "max_tokens": 128
    }'
echo

echo "== /estimate (no model call) =="
curl -sS -X POST "${BASE_URL}/estimate" \
    -H "Content-Type: application/json" \
    -d '{
        "prompt": "Will this prompt fit?",
        "max_output_tokens": 256
    }'
echo
