#!/usr/bin/env bash
# Minimal LocalDeploy usage via curl.
# Requires the API server to be running at http://127.0.0.1:8000.

set -euo pipefail

BASE_URL="${LOCALDEPLOY_BASE_URL:-http://127.0.0.1:8000}"
API_TOKEN="${LOCALDEPLOY_API_TOKEN:-${API_TOKEN:-}}"
AUTH_ARGS=()
if [[ -n "${API_TOKEN}" ]]; then
    AUTH_ARGS=(-H "Authorization: Bearer ${API_TOKEN}")
fi

echo "== /health =="
curl -sS "${AUTH_ARGS[@]}" "${BASE_URL}/health" | head -c 400
echo

echo "== Native /chat =="
curl -sS -X POST "${BASE_URL}/chat" \
    "${AUTH_ARGS[@]}" \
    -H "Content-Type: application/json" \
    -d '{
        "prompt": "Reply with one sentence: what is your role?",
        "safe_mode": true,
        "max_output_tokens": 128
    }'
echo

echo "== OpenAI-compatible /v1/chat/completions =="
curl -sS -X POST "${BASE_URL}/v1/chat/completions" \
    "${AUTH_ARGS[@]}" \
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
    "${AUTH_ARGS[@]}" \
    -H "Content-Type: application/json" \
    -d '{
        "prompt": "Will this prompt fit?",
        "max_output_tokens": 256
    }'
echo
