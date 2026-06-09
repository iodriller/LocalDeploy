#!/usr/bin/env bash
# LocalDeploy one-command launcher.
# Usage (fresh machine — nothing pre-installed):
#   curl -fsSL https://raw.githubusercontent.com/iodriller/localdeploy/main/run.sh | bash
#
# What it does:
#   1. Installs git if absent (Linux only; macOS ships it).
#   2. Installs Docker if absent (Linux via get.docker.com; macOS via Homebrew cask).
#   3. Installs Docker Compose v2 plugin if absent (Linux only; comes with Docker Desktop on macOS).
#   4. Clones or updates the repo into ~/localdeploy (skipped when running from inside a clone).
#   5. Runs `docker compose up --build -d` and prints the UI URL.
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

# Detect the Linux package manager (returns: apt | yum | dnf | apk | unknown)
linux_pkg_manager() {
  if   command -v apt-get &>/dev/null; then echo apt
  elif command -v dnf     &>/dev/null; then echo dnf
  elif command -v yum     &>/dev/null; then echo yum
  elif command -v apk     &>/dev/null; then echo apk
  else echo unknown
  fi
}

# ── 1. Ensure git is installed ──────────────────────────────────────────────────
ensure_git() {
  if command -v git &>/dev/null; then
    return
  fi

  local os
  os=$(os_type)

  if [ "$os" = "linux" ]; then
    info "git not found — installing ..."
    case "$(linux_pkg_manager)" in
      apt) sudo apt-get update -qq && sudo apt-get install -y git ;;
      dnf) sudo dnf install -y git ;;
      yum) sudo yum install -y git ;;
      apk) sudo apk add --no-cache git ;;
      *)   die "Cannot install git — unknown package manager. Install git manually and re-run." ;;
    esac
    ok "git installed."
  elif [ "$os" = "macos" ]; then
    # macOS always ships git via Xcode Command Line Tools; running any git command
    # triggers the install prompt automatically.  We can't do it non-interactively here.
    die "git is not installed. Run: xcode-select --install   then re-run this script."
  fi
}

# ── 2. Ensure Docker is installed ──────────────────────────────────────────────
ensure_docker() {
  if command -v docker &>/dev/null; then
    ok "Docker already installed: $(docker --version)"
    return
  fi

  local os
  os=$(os_type)

  if [ "$os" = "linux" ]; then
    info "Docker not found — installing via get.docker.com ..."
    curl -fsSL https://get.docker.com | sh
    # Add the current user to the docker group so sudo is not needed later.
    if ! id -nG "$USER" 2>/dev/null | grep -qw docker; then
      warn "Adding $USER to the docker group."
      sudo usermod -aG docker "$USER" 2>/dev/null || true
    fi
    ok "Docker Engine installed."

  elif [ "$os" = "macos" ]; then
    if command -v brew &>/dev/null; then
      info "Docker not found — installing Docker Desktop via Homebrew ..."
      brew install --cask docker
      info "Starting Docker Desktop (this may take a moment) ..."
      open -a Docker
      # Wait up to 60 s for the daemon.
      local tries=0
      while ! docker info &>/dev/null 2>&1; do
        tries=$((tries + 1))
        [ $tries -ge 30 ] && die "Docker Desktop did not start within 60 s. Open it manually then re-run."
        sleep 2
      done
      ok "Docker Desktop running."
    else
      die "Docker is not installed and Homebrew is not available. Install Docker Desktop from https://docs.docker.com/desktop/mac/install/ then re-run."
    fi

  else
    die "Unsupported OS '$(uname -s)'. Install Docker manually (https://docs.docker.com/get-docker/) then re-run."
  fi
}

# ── 3. Ensure Docker Compose v2 is available ──────────────────────────────────
ensure_compose() {
  if docker compose version &>/dev/null 2>&1; then
    return
  fi

  # On Linux, get.docker.com installs the Engine but not the Compose plugin —
  # install it explicitly via the package manager.
  if [ "$(os_type)" = "linux" ]; then
    info "Docker Compose v2 plugin not found — installing ..."
    case "$(linux_pkg_manager)" in
      apt)
        sudo apt-get update -qq
        sudo apt-get install -y docker-compose-plugin
        ;;
      dnf) sudo dnf install -y docker-compose-plugin ;;
      yum) sudo yum install -y docker-compose-plugin ;;
      apk)
        # Alpine: Compose v2 ships as a separate package
        sudo apk add --no-cache docker-compose ;;
      *)
        # Last resort: install the standalone binary from GitHub releases
        info "Package manager unknown — installing docker compose binary ..."
        COMPOSE_VER=$(curl -fsSL https://api.github.com/repos/docker/compose/releases/latest \
          | grep '"tag_name"' | sed 's/.*"v\([^"]*\)".*/\1/')
        sudo curl -fsSL \
          "https://github.com/docker/compose/releases/download/v${COMPOSE_VER}/docker-compose-$(uname -s)-$(uname -m)" \
          -o /usr/local/lib/docker/cli-plugins/docker-compose
        sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
        ;;
    esac
    ok "Docker Compose v2 installed."
    return
  fi

  # Fallback: docker-compose v1 standalone (legacy systems)
  if command -v docker-compose &>/dev/null; then
    warn "Using docker-compose v1 — consider upgrading."
    COMPOSE_CMD="docker-compose"
    return
  fi

  die "Docker Compose is not available. Install it from https://docs.docker.com/compose/install/ then re-run."
}

# ── 4. Get the repo ────────────────────────────────────────────────────────────
ensure_repo() {
  # If we are already inside a clone, use it as-is.
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

# ── 5. Launch ──────────────────────────────────────────────────────────────────
launch() {
  cd "$INSTALL_DIR"
  info "Building and starting LocalDeploy (takes ~1 min on first run) ..."
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

ensure_git
ensure_docker
ensure_compose
ensure_repo
launch
