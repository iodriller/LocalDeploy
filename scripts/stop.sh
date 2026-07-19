#!/usr/bin/env bash
# Stops the LocalDeploy API process recorded by start.sh. Add --ollama to also
# stop the Ollama process that start.sh launched. Unmanaged processes are left alone.
set -euo pipefail

cd "$(dirname "$0")/.."

stop_ollama=false
for argument in "$@"; do
  case "$argument" in
    --ollama) stop_ollama=true ;;
    -h|--help)
      echo "Usage: ./scripts/stop.sh [--ollama]"
      exit 0
      ;;
    *)
      echo "Unknown option: $argument" >&2
      echo "Usage: ./scripts/stop.sh [--ollama]" >&2
      exit 2
      ;;
  esac
done

stop_managed_pid() {
  pid_file=$1
  label=$2
  process_kind=$3

  if [ ! -f "$pid_file" ]; then
    echo "[stop] $label was not started by this launcher."
    return
  fi

  pid=$(cat "$pid_file" 2>/dev/null || true)
  case "$pid" in
    ''|*[!0-9]*)
      echo "[stop] ignoring invalid $label PID file."
      rm -f "$pid_file"
      return
      ;;
  esac

  if ! kill -0 "$pid" 2>/dev/null; then
    echo "[stop] removing stale $label PID file."
    rm -f "$pid_file"
    return
  fi

  if [ -r "/proc/$pid/cmdline" ]; then
    command_line=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  else
    command_line=$(ps -p "$pid" -o command= 2>/dev/null || true)
  fi
  case "$process_kind" in
    api)
      if [[ "$command_line" != *uvicorn* && "$command_line" != *api_server.py* ]]; then
        echo "[stop] PID $pid no longer belongs to LocalDeploy; leaving it alone." >&2
        rm -f "$pid_file"
        return
      fi
      ;;
    ollama)
      if [[ "$command_line" != *ollama* || "$command_line" != *serve* ]]; then
        echo "[stop] PID $pid no longer belongs to a managed Ollama server; leaving it alone." >&2
        rm -f "$pid_file"
        return
      fi
      ;;
  esac

  kill "$pid" 2>/dev/null || true
  for ((attempt = 0; attempt < 20; attempt++)); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.25
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
  echo "[stop] stopped $label (PID $pid)."
}

stop_managed_pid logs/api_server.pid "LocalDeploy API" api

if [ "$stop_ollama" = true ]; then
  stop_managed_pid logs/ollama.pid "Ollama" ollama
else
  echo "[stop] Ollama was left running. Use ./scripts/stop.sh --ollama to stop a process started by this launcher."
fi
