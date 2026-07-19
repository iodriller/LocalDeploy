#!/usr/bin/env bash
# Local (non-Docker) launcher for macOS/Linux: sets up a venv, installs deps,
# and starts the API + UI. Requires Ollama installed and running separately
# (https://ollama.com). For a zero-setup all-in-one, use `docker compose up`.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: Python 3 was not found." >&2
  echo "  macOS:          brew install python  (or https://www.python.org/downloads/)" >&2
  echo "  Debian/Ubuntu:  sudo apt install python3 python3-venv" >&2
  echo "  Fedora:         sudo dnf install python3" >&2
  echo "Then re-run this script." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "ERROR: Python 3.10+ is required; found $("$PYTHON" --version 2>&1)." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "[start] creating virtualenv (.venv) ..."
  "$PYTHON" -m venv .venv || {
    echo "ERROR: could not create a virtualenv. On Debian/Ubuntu: sudo apt install python3-venv" >&2
    exit 1
  }
fi
# shellcheck disable=SC1091
. .venv/bin/activate

# Reinstall only when requirements.txt changed since the last install, so a
# git pull that adds a dependency can't leave the venv broken.
REQ_HASH="$( (sha256sum requirements.txt 2>/dev/null || shasum -a 256 requirements.txt) | cut -d' ' -f1 )"
if [ "$(cat .venv/requirements.sha256 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "[start] installing dependencies (first run can take a minute) ..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt || {
    echo "ERROR: dependency install failed. Check your internet connection and re-run." >&2
    exit 1
  }
  echo "$REQ_HASH" > .venv/requirements.sha256
fi

# Seed local environment defaults if absent. The live config starts empty and
# is created when the first model profile is saved.
[ -f .env ] || { [ -f .env.example ] && cp .env.example .env; } || true

# Match the Python server's dotenv behavior without sourcing .env as shell code.
# Explicit shell values still win over the file, just as they do in start.ps1.
IFS=$'\t' read -r HOST PORT OLLAMA_URL < <(
  "$PYTHON" - "${API_HOST:-}" "${API_PORT:-}" "${OLLAMA_BASE_URL:-}" <<'PY'
import sys

from dotenv import dotenv_values

values = dotenv_values(".env")
requested = (
    (sys.argv[1], "API_HOST", "127.0.0.1"),
    (sys.argv[2], "API_PORT", "8000"),
    (sys.argv[3], "OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
)
resolved = []
for explicit, name, default in requested:
    value = explicit or values.get(name) or default
    if any(character in value for character in "\t\r\n"):
        raise SystemExit(f"Invalid control character in {name}")
    resolved.append(value)
resolved[2] = resolved[2].rstrip("/")
print("\t".join(resolved))
PY
)

if ! command -v ollama >/dev/null 2>&1 && ! curl -fsS --max-time 2 "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
  echo "[start] NOTE: Ollama was not found. Install it from https://ollama.com/download -"
  echo "        the UI will start anyway and shows Ollama's status on the Setup tab."
fi

BROWSE_HOST="$HOST"
case "$BROWSE_HOST" in
  0.0.0.0|::) BROWSE_HOST="127.0.0.1" ;;
  ::1) BROWSE_HOST="[::1]" ;;
esac
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
