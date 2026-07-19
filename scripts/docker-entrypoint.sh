#!/usr/bin/env bash
# Starts Ollama and the LocalDeploy API+UI inside one container. They talk over
# localhost, so the server's loopback-only backend guard stays intact.
set -euo pipefail

echo "[entrypoint] starting ollama serve ..."
ollama serve &
OLLAMA_PID=$!

# Wait for Ollama to answer before starting the API.
for i in $(seq 1 60); do
  if ollama list >/dev/null 2>&1; then
    echo "[entrypoint] ollama is ready"
    break
  fi
  if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
    echo "[entrypoint] ollama exited unexpectedly" >&2
    exit 1
  fi
  sleep 1
done

# Optionally pre-pull models listed in PULL_MODELS (space-separated).
if [ -n "${PULL_MODELS:-}" ]; then
  for model in $PULL_MODELS; do
    echo "[entrypoint] pulling $model ..."
    ollama pull "$model" || echo "[entrypoint] WARN: could not pull $model"
  done
fi

echo "[entrypoint] starting API + UI on ${API_HOST:-0.0.0.0}:${API_PORT:-8000} (UI at /ui)"
exec uvicorn api_server:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
