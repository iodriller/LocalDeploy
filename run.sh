#!/usr/bin/env bash
# LocalDeploy one-command launcher.
# Usage (fresh machine, nothing installed):
#   curl -fsSL https://raw.githubusercontent.com/iodriller/localdeploy/main/run.sh | sh
#
# What it does:
#   1. Installs Docker if it is not already present (Linux via get.docker.com,
#      macOS via Homebrew cask — both only when needed).
#   2. Clones or updates the repo into ~/localdeploy (skipped when running
#      from inside a clone).
#   3. Runs `docker compose up --build -d` and prints the UI URL.
set -euo pipefail

REPO_URL="https://github.com/iodriller/localdeploy.git"
INSTALL_DIR="${LOCALDEPLOY_DIR:-$HOME/localdeploy}"
PORT="${API_PORT:-8000}"

# ── helpers ────────────────────────────────────────────────────────────────────
info()  { printf '\033[0;34m[localdeploy]\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m[localdeploy]\033[0m %s\n' "$*"; }
warn()  { printf '\033[0;33m[localdeploy]\033[0m %s\n' "$*"; }
die()   { printf '\033[0;31m[localdeploy] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

os_type() {
  case "$(uname -s)" in
    Linux*)  echo linux ;;
    Darwin*) echo macos ;;
    *)       echo unknown ;;
  esac
}

# ── 1. Ensure Docker is installed ──────────────────────────────────────────────
ensure_docker() {
  if command -v docker &>/dev/null; then
    ok "Docker already installed: $(docker --version)"
    return
  fi

  local os
  os=$(os_type)

  if [ "$os" = "linux" ]; then
    info "Docker not found — installing via get.docker.com ..."
    # Convenience script is the official single-command install for Linux.
    curl -fsSL https://get.docker.com | sh
    # On most distros the current user needs to be in the docker group.
    if id -nG "$USER" | grep -qw docker; then
      : # already in group
    else
      warn "Adding $USER to the docker group — you may need to log out and back in if this fails."
      sudo usermod -aG docker "$USER" 2>/dev/null || true
    fi
    ok "Docker installed."

  elif [ "$os" = "macos" ]; then
    if command -v brew &>/dev/null; then
      info "Docker not found — installing Docker Desktop via Homebrew ..."
      brew install --cask docker
      info "Starting Docker Desktop (this may take a moment) ..."
      open -a Docker
      # Wait for the daemon to become responsive (up to 60 s).
      local tries=0
      while ! docker info &>/dev/null 2>&1; do
        tries=$((tries + 1))
        [ $tries -ge 30 ] && die "Docker Desktop did not start within 60 s. Open it manually and re-run this script."
        sleep 2
      done
      ok "Docker Desktop running."
    else
      die "Docker is not installed and Homebrew is not available. Install Docker Desktop from https://docs.docker.com/desktop/mac/install/ then re-run this script."
    fi

  else
    die "Unsupported OS '$(uname -s)'. Install Docker manually (https://docs.docker.com/get-docker/) then re-run this script."
  fi
}

# ── 2. Ensure `docker compose` (v2 plugin) is available ───────────────────────
ensure_compose() {
  if docker compose version &>/dev/null 2>&1; then
    return
  fi
  # Fallback: docker-compose v1 standalone (older Linux setups)
  if command -v docker-compose &>/dev/null; then
    warn "Using docker-compose v1 — consider upgrading to Docker Compose v2."
    COMPOSE_CMD="docker-compose"
    return
  fi
  die "Docker Compose is not available. Install it from https://docs.docker.com/compose/install/ then re-run this script."
}

# ── 3. Get the repo ────────────────────────────────────────────────────────────
ensure_repo() {
  # If we are already inside a clone (the script was run locally), use it.
  if [ -f "$(pwd)/docker-compose.yml" ] && [ -f "$(pwd)/Dockerfile" ]; then
    INSTALL_DIR="$(pwd)"
    info "Running from existing repo at $INSTALL_DIR."
    return
  fi

  if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing repo at $INSTALL_DIR ..."
    git -C "$INSTALL_DIR" pull --ff-only origin main
  else
    info "Cloning repo into $INSTALL_DIR ..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
}

# ── 4. Launch ──────────────────────────────────────────────────────────────────
launch() {
  cd "$INSTALL_DIR"
  info "Building and starting LocalDeploy (this takes ~1 min on first run) ..."
  ${COMPOSE_CMD:-docker compose} up --build -d
  ok ""
  ok "LocalDeploy is running!"
  ok "  Web UI   →  http://localhost:${PORT}/ui"
  ok "  API docs →  http://localhost:${PORT}/docs"
  ok ""
  ok "To follow logs:  docker compose logs -f   (in $INSTALL_DIR)"
  ok "To stop:         docker compose down       (in $INSTALL_DIR)"
}

# ── main ───────────────────────────────────────────────────────────────────────
COMPOSE_CMD="docker compose"

ensure_docker
ensure_compose
ensure_repo
launch
