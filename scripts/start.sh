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
echo "[start] LocalDeploy UI:  http://${HOST}:${PORT}/ui"
exec uvicorn api_server:app --host "$HOST" --port "$PORT"
