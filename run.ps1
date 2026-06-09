# LocalDeploy one-command launcher for Windows (PowerShell).
# Usage (run from PowerShell — no prerequisites needed):
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
#   irm https://raw.githubusercontent.com/iodriller/localdeploy/main/run.ps1 | iex
#
# What it does:
#   1. Installs git via winget if absent.
#   2. Installs Docker Desktop via winget if absent.
#   3. Clones or updates the repo into %USERPROFILE%\localdeploy (skipped
#      when running from inside an existing clone).
#   4. Runs `docker compose up --build -d` and opens the UI in a browser.

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

# winget is available on Windows 10 1809+ and Windows 11.
function Assert-Winget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Die "winget is not available on this machine.`nInstall the App Installer from the Microsoft Store (https://aka.ms/getwinget) then re-run."
    }
}

# ── 1. Ensure git is installed ──────────────────────────────────────────────────
function Ensure-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) { return }
    Assert-Winget
    Info "git not found — installing via winget ..."
    winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
    # Reload PATH so git is visible in this session without reopening the terminal.
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Die "git was installed but is not on PATH yet. Open a new terminal and re-run."
    }
    Ok "git installed."
}

# ── 2. Ensure Docker Desktop is installed ──────────────────────────────────────
function Ensure-Docker {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Ok "Docker already installed: $(docker --version)"
        return
    }
    Assert-Winget
    Info "Docker not found — installing Docker Desktop via winget ..."
    winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
    Info "Starting Docker Desktop — this may take a minute ..."
    $desktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $desktop) { Start-Process $desktop } else { Start-Process "Docker Desktop" -ErrorAction SilentlyContinue }

    # Wait up to 90 s for the daemon to respond.
    $tries = 0
    while ($true) {
        try { docker info 2>$null | Out-Null; break } catch {}
        $tries++
        if ($tries -ge 45) { Die "Docker Desktop did not start within 90 s. Open it manually then re-run." }
        Start-Sleep -Seconds 2
    }
    Ok "Docker Desktop running."
}

# ── 3. Ensure docker compose is available ──────────────────────────────────────
function Ensure-Compose {
    try { docker compose version 2>$null | Out-Null; return } catch {}
    Die "Docker Compose (v2) is not available. Ensure Docker Desktop is up to date (Settings → Software Updates)."
}

# ── 4. Get the repo ────────────────────────────────────────────────────────────
function Ensure-Repo {
    # Running from inside an existing clone — use it as-is.
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

# ── 5. Launch ──────────────────────────────────────────────────────────────────
function Launch {
    Set-Location $InstallDir
    Info "Building and starting LocalDeploy (takes ~1 min on first run) ..."
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
Ensure-Git
Ensure-Docker
Ensure-Compose
Ensure-Repo
Launch
