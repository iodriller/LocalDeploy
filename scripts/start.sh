#!/usr/bin/env bash
# Local (non-Docker) launcher for macOS/Linux: sets up a venv, installs deps,
# and starts the API + UI. Requires Ollama installed and running separately
# (https://ollama.com). For a zero-setup all-in-one, use `docker compose up`.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  echo "[start] creating virtualenv (.venv) ..."
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

echo "[start] installing dependencies ..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Seed local config from examples if absent (the app also falls back internally).
[ -f .env ] || { [ -f .env.example ] && cp .env.example .env; } || true

HOST="${API_HOST:-127.0.0.1}"
PORT="${API_PORT:-8000}"
BROWSE_HOST="$HOST"
case "$BROWSE_HOST" in 0.0.0.0|::) BROWSE_HOST="127.0.0.1" ;; esac
UI_URL="http://${BROWSE_HOST}:${PORT}/ui"
echo "[start] LocalDeploy UI:  $UI_URL"

# Open the UI once the server answers (set NO_BROWSER=1 to skip).
if [ -z "${NO_BROWSER:-}" ]; then
  opener=""
  command -v xdg-open >/dev/null 2>&1 && opener="xdg-open"
  command -v open >/dev/null 2>&1 && opener="open"
  if [ -n "$opener" ]; then
    (
      for _ in $(seq 1 60); do
        if curl -fsS "http://${BROWSE_HOST}:${PORT}/health" >/dev/null 2>&1; then
          "$opener" "$UI_URL" >/dev/null 2>&1 || true
          break
        fi
        sleep 1
      done
    ) &
  fi
fi

exec uvicorn api_server:app --host "$HOST" --port "$PORT"
