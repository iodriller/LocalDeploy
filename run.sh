#!/usr/bin/env bash
# LocalDeploy one-command launcher.
# Usage (fresh machine — nothing pre-installed):
#   curl -fsSL https://raw.githubusercontent.com/iodriller/localdeploy/main/run.sh | bash
set -euo pipefail

REPO_URL="https://github.com/iodriller/localdeploy.git"
INSTALL_DIR="${LOCALDEPLOY_DIR:-$HOME/localdeploy}"
PORT="${API_PORT:-8000}"
DOCKER_JUST_INSTALLED=false

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

linux_pkg_manager() {
  if   command -v apt-get &>/dev/null; then echo apt
  elif command -v dnf     &>/dev/null; then echo dnf
  elif command -v yum     &>/dev/null; then echo yum
  elif command -v apk     &>/dev/null; then echo apk
  else echo unknown
  fi
}

# Use sudo only when not already root
maybe_sudo() { [ "$(id -u)" -eq 0 ] && "$@" || sudo "$@"; }

# ── 1. Ensure git ──────────────────────────────────────────────────────────────
ensure_git() {
  command -v git &>/dev/null && return

  if [ "$(os_type)" = "linux" ]; then
    info "git not found — installing ..."
    case "$(linux_pkg_manager)" in
      apt) maybe_sudo apt-get update -qq && maybe_sudo apt-get install -y git ;;
      dnf) maybe_sudo dnf install -y git ;;
      yum) maybe_sudo yum install -y git ;;
      apk) maybe_sudo apk add --no-cache git ;;
      *)   die "Cannot install git — unknown package manager. Install git manually then re-run." ;;
    esac
    ok "git installed."
  else
    # macOS: running any git command triggers the Xcode CLI tools install prompt.
    die "git is not installed. Run:  xcode-select --install  then re-run this script."
  fi
}

# ── 2. Ensure Docker ───────────────────────────────────────────────────────────
ensure_docker() {
  if command -v docker &>/dev/null; then
    ok "Docker found: $(docker --version)"
    return
  fi

  local os
  os=$(os_type)

  if [ "$os" = "linux" ]; then
    info "Docker not found — installing via get.docker.com ..."
    curl -fsSL https://get.docker.com | sh
    # Ensure the daemon is running.
    maybe_sudo systemctl enable --now docker 2>/dev/null || \
      maybe_sudo service docker start 2>/dev/null || true
    # Add the current user to the docker group (takes effect on next login,
    # but we will use sudo for this first run — see launch()).
    local cur_user
    cur_user=$(id -un)
    if ! id -nG "$cur_user" 2>/dev/null | grep -qw docker; then
      maybe_sudo usermod -aG docker "$cur_user" 2>/dev/null || true
    fi
    DOCKER_JUST_INSTALLED=true
    ok "Docker Engine installed."

  elif [ "$os" = "macos" ]; then
    if command -v brew &>/dev/null; then
      info "Docker not found — installing Docker Desktop via Homebrew ..."
      brew install --cask docker
      info "Starting Docker Desktop (this may take a moment) ..."
      open -a Docker
      local tries=0
      while ! docker info &>/dev/null 2>&1; do
        tries=$((tries + 1))
        [ $tries -ge 30 ] && die "Docker Desktop did not start within 60 s. Open it manually then re-run."
        sleep 2
      done
      ok "Docker Desktop running."
    else
      die "Docker is not installed and Homebrew is not available.
Install Docker Desktop from https://docs.docker.com/desktop/mac/install/ then re-run."
    fi

  else
    die "Unsupported OS '$(uname -s)'. Install Docker from https://docs.docker.com/get-docker/ then re-run."
  fi
}

# ── 3. Ensure Docker Compose v2 ───────────────────────────────────────────────
ensure_compose() {
  docker compose version &>/dev/null 2>&1 && return

  if [ "$(os_type)" = "linux" ]; then
    info "Docker Compose v2 not found — installing ..."
    case "$(linux_pkg_manager)" in
      apt)
        maybe_sudo apt-get update -qq
        maybe_sudo apt-get install -y docker-compose-plugin
        ;;
      dnf) maybe_sudo dnf install -y docker-compose-plugin ;;
      yum) maybe_sudo yum install -y docker-compose-plugin ;;
      apk) maybe_sudo apk add --no-cache docker-cli-compose ;;
      *)
        # Last resort: pull the binary directly from GitHub releases.
        info "Falling back to GitHub release binary ..."
        local ver
        ver=$(curl -fsSL https://api.github.com/repos/docker/compose/releases/latest \
              | grep '"tag_name"' | sed 's/.*"v\([^"]*\)".*/\1/')
        local dest=/usr/local/lib/docker/cli-plugins
        maybe_sudo mkdir -p "$dest"
        maybe_sudo curl -fsSL \
          "https://github.com/docker/compose/releases/download/v${ver}/docker-compose-$(uname -s)-$(uname -m)" \
          -o "$dest/docker-compose"
        maybe_sudo chmod +x "$dest/docker-compose"
        ;;
    esac
    ok "Docker Compose v2 installed."
    return
  fi

  # Legacy fallback: docker-compose v1 standalone
  if command -v docker-compose &>/dev/null; then
    warn "Using docker-compose v1 — consider upgrading."
    COMPOSE_CMD="docker-compose"
    return
  fi

  die "Docker Compose is not available. Install it from https://docs.docker.com/compose/install/ then re-run."
}

# ── 4. Get the repo ────────────────────────────────────────────────────────────
ensure_repo() {
  # Already inside a clone — use it.
  if [ -f "$(pwd)/docker-compose.yml" ] && [ -f "$(pwd)/Dockerfile" ]; then
    INSTALL_DIR="$(pwd)"
    info "Running from existing repo at $INSTALL_DIR."
    return
  fi

  if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating repo at $INSTALL_DIR ..."
    # Non-fatal: a dirty/diverged local clone (e.g. user edited docker-compose.yml)
    # must never block launching the version they already have.
    git -C "$INSTALL_DIR" pull --ff-only origin main 2>/dev/null \
      || warn "Couldn't fast-forward (local changes?) — launching the existing version."
  else
    info "Cloning repo into $INSTALL_DIR ..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
}

# ── 5. Launch ──────────────────────────────────────────────────────────────────
launch() {
  cd "$INSTALL_DIR"

  # Decide whether we need sudo by actually probing the daemon — not by guessing
  # from whether we just installed Docker. This also covers the common case of a
  # pre-installed Docker whose user isn't in the `docker` group yet.
  local compose_cmd="${COMPOSE_CMD:-docker compose}"
  if ! docker info >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null || [ "$(id -u)" -eq 0 ]; then
      compose_cmd="sudo $compose_cmd"
    else
      warn "Docker needs elevated access (your user isn't in the 'docker' group yet)."
      warn "You may be prompted for your password. Re-login later to drop the sudo requirement."
      compose_cmd="sudo $compose_cmd"
    fi
  fi

  # Warn if the target port is already taken, with a clear remedy.
  if command -v bash >/dev/null 2>&1 && (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
    exec 3>&- 2>/dev/null || true
    die "Port ${PORT} is already in use. Free it, or re-run with a different port:
    API_PORT=8001 curl -fsSL https://raw.githubusercontent.com/iodriller/localdeploy/main/run.sh | bash"
  fi

  info "Building and starting LocalDeploy (first run downloads ~2-4 GB; can take several minutes) ..."
  API_PORT="$PORT" $compose_cmd up --build -d

  # Wait for the API to actually answer before declaring success / printing the URL.
  info "Waiting for the server to come up ..."
  local tries=0
  until curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ $tries -ge 60 ]; then
      warn "Server didn't answer on /health within 2 min. It may still be pulling a model or building."
      warn "Check progress with:  cd $INSTALL_DIR && docker compose logs -f"
      break
    fi
    sleep 2
  done

  ok ""
  ok "LocalDeploy is running!"
  ok "  Open this in your browser:  http://localhost:${PORT}/ui"
  ok ""
  ok "  Logs:    cd $INSTALL_DIR && docker compose logs -f"
  ok "  Stop:    cd $INSTALL_DIR && docker compose down"
  ok "  Update:  cd $INSTALL_DIR && docker compose pull && docker compose up --build -d"
}

# ── main ───────────────────────────────────────────────────────────────────────
COMPOSE_CMD="docker compose"

ensure_git
ensure_docker
ensure_compose
ensure_repo
launch
