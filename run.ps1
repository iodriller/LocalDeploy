# LocalDeploy one-command launcher for Windows (PowerShell).
# Usage (run from PowerShell — no prerequisites needed):
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
#   irm https://raw.githubusercontent.com/iodriller/localdeploy/main/run.ps1 | iex
#
# What it does:
#   1. Installs Docker Desktop via winget if it is not already present.
#   2. Clones or updates the repo into %USERPROFILE%\localdeploy (skipped
#      when running from inside an existing clone).
#   3. Runs `docker compose up --build -d` and opens the UI in a browser.

param(
    [string]$InstallDir = "$env:USERPROFILE\localdeploy",
    [string]$Port       = "8000"
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/iodriller/localdeploy.git"

function Info  { param($m) Write-Host "[localdeploy] $m" -ForegroundColor Cyan }
function Ok    { param($m) Write-Host "[localdeploy] $m" -ForegroundColor Green }
function Warn  { param($m) Write-Host "[localdeploy] $m" -ForegroundColor Yellow }
function Die   { param($m) Write-Host "[localdeploy] ERROR: $m" -ForegroundColor Red; exit 1 }

# ── 1. Ensure Docker Desktop is installed ──────────────────────────────────────
function Ensure-Docker {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Ok "Docker already installed: $(docker --version)"
        return
    }

    # Try winget first (available on Windows 10 1809+ and Windows 11)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Info "Docker not found — installing Docker Desktop via winget ..."
        winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
        Info "Docker Desktop installed. Starting it now — this may take a minute ..."
        Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe" -ErrorAction SilentlyContinue
    } else {
        Die "Docker is not installed and winget is not available.`nInstall Docker Desktop from https://docs.docker.com/desktop/windows/install/ and re-run this script."
    }

    # Wait up to 90 s for the daemon to be ready
    $tries = 0
    while ($true) {
        try { docker info 2>$null | Out-Null; break } catch {}
        $tries++
        if ($tries -ge 45) { Die "Docker Desktop did not start within 90 s. Open it manually and re-run." }
        Start-Sleep -Seconds 2
    }
    Ok "Docker Desktop running."
}

# ── 2. Ensure docker compose is available ──────────────────────────────────────
function Ensure-Compose {
    try { docker compose version 2>$null | Out-Null; return } catch {}
    Die "Docker Compose (v2) is not available. Ensure Docker Desktop is up to date."
}

# ── 3. Get the repo ────────────────────────────────────────────────────────────
function Ensure-Repo {
    # If we are already inside a clone, use it
    if ((Test-Path ".\docker-compose.yml") -and (Test-Path ".\Dockerfile")) {
        $script:InstallDir = (Get-Location).Path
        Info "Running from existing repo at $($script:InstallDir)."
        return
    }
    if (Test-Path "$InstallDir\.git") {
        Info "Updating existing repo at $InstallDir ..."
        git -C $InstallDir pull --ff-only origin main
    } else {
        Info "Cloning repo into $InstallDir ..."
        git clone --depth 1 $RepoUrl $InstallDir
    }
}

# ── 4. Launch ──────────────────────────────────────────────────────────────────
function Launch {
    Set-Location $InstallDir
    Info "Building and starting LocalDeploy (this takes ~1 min on first run) ..."
    docker compose up --build -d
    $url = "http://localhost:${Port}/ui"
    Ok ""
    Ok "LocalDeploy is running!"
    Ok "  Web UI   ->  $url"
    Ok "  API docs ->  http://localhost:${Port}/docs"
    Ok ""
    Ok "To follow logs:  docker compose logs -f"
    Ok "To stop:         docker compose down"
    Start-Process $url
}

# ── main ───────────────────────────────────────────────────────────────────────
Ensure-Docker
Ensure-Compose
Ensure-Repo
Launch
