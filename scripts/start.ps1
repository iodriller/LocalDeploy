# Starts LocalDeploy: creates .env/config.json/.venv when missing, starts
# Ollama if installed, launches the API in the background, and opens the UI.
#   -Foreground   run the API in this terminal with live logs (no browser)
#   -NoBrowser    start in the background without opening the UI
param(
    [switch]$Foreground,
    [switch]$NoBrowser,
    [switch]$SkipInstall,
    [switch]$SkipLlamaCpp
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Read-DotEnv {
    $values = @{}
    if (-not (Test-Path -LiteralPath ".\.env")) { return $values }
    foreach ($line in Get-Content -LiteralPath ".\.env") {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
        $name, $value = $trimmed.Split("=", 2)
        $values[$name.Trim()] = $value.Trim().Trim('"').Trim("'")
    }
    return $values
}

function Env-Value {
    param([hashtable]$EnvFile, [string]$Name, [string]$Default = $null)
    $processValue = [Environment]::GetEnvironmentVariable($Name)
    if ($processValue) { return $processValue }
    if ($EnvFile.ContainsKey($Name)) { return $EnvFile[$Name] }
    return $Default
}

function Get-ApiBaseUrl {
    param([hashtable]$EnvFile)
    $hostName = Env-Value -EnvFile $EnvFile -Name "API_HOST" -Default "127.0.0.1"
    $port = Env-Value -EnvFile $EnvFile -Name "API_PORT" -Default "8000"
    if ($hostName -eq "0.0.0.0" -or $hostName -eq "::") { $hostName = "127.0.0.1" }
    elseif ($hostName -eq "::1") { $hostName = "[::1]" }
    return "http://${hostName}:${port}"
}

function Test-Http {
    param([string]$Url, [int]$Timeout = 5)
    try {
        Invoke-RestMethod -Uri $Url -TimeoutSec $Timeout | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Find-Ollama {
    $cmd = Get-Command "ollama" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidate = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    return $null
}

function Find-Python {
    foreach ($cmd in @("python", "python3", "py")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) {
            try {
                # Run in a child process so the Windows Store stub's stderr
                # does not trigger $ErrorActionPreference = "Stop"
                $ver = & $cmd --version 2>&1
                if ("$ver" -match "Python \d") {
                    return $cmd
                }
            }
            catch {
                # Store stub or broken install - skip this candidate
            }
        }
    }
    return $null
}

function Install-PythonGuide {
    Write-Host ""
    Write-Host "Python is required but was not found on this system." -ForegroundColor Yellow
    Write-Host ""

    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget) {
        Write-Host "Choose an installation method:" -ForegroundColor Cyan
        Write-Host "  [1] Install automatically via winget (recommended)"
        Write-Host "  [2] Open python.org download page"
        Write-Host "  [Q] Quit and install manually"
        Write-Host ""
        $choice = Read-Host "Your choice"
    }
    else {
        Write-Host "winget not available. Choose how to install Python:" -ForegroundColor Cyan
        Write-Host "  [1] Open python.org download page"
        Write-Host "  [Q] Quit"
        Write-Host ""
        $choice = Read-Host "Your choice"
        # remap so option 1 opens the browser
        if ($choice.Trim() -eq "1") { $choice = "2" }
    }

    switch ($choice.Trim().ToUpper()) {
        "1" {
            Write-Step "Installing Python 3.12 via winget..."
            # Pipe to Out-Host so winget output goes to the console only and does
            # not get captured as part of this function's return value.
            winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements | Out-Host
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "winget exited with code $LASTEXITCODE. Python may not have installed correctly."
            }
            # Refresh PATH so the new Python binary is visible in this session
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("PATH", "User")
            $pyCmd = Find-Python
            if (-not $pyCmd) {
                Write-Host ""
                Write-Host "Python was installed but is not yet visible in PATH." -ForegroundColor Yellow
                Write-Host "Please close this terminal and re-run start.ps1." -ForegroundColor Yellow
                exit 0
            }
            return $pyCmd
        }
        "2" {
            Start-Process "https://www.python.org/downloads/"
            Write-Host ""
            Write-Host "Opening python.org in your browser." -ForegroundColor Cyan
            Write-Host "After installing Python, re-run this script." -ForegroundColor Yellow
            exit 0
        }
        default {
            Write-Host ""
            Write-Host "Exiting. Install Python from https://www.python.org/downloads/ then re-run this script." -ForegroundColor Yellow
            exit 0
        }
    }
}

function Install-OllamaGuide {
    Write-Host ""
    Write-Host "Ollama is required to pull and serve models, but was not found on this system." -ForegroundColor Yellow
    Write-Host ""

    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget) {
        Write-Host "Choose an installation method:" -ForegroundColor Cyan
        Write-Host "  [1] Install automatically via winget (recommended)"
        Write-Host "  [2] Open ollama.com download page"
        Write-Host "  [Q] Skip for now (you won't be able to pull or serve models)"
        Write-Host ""
        $choice = Read-Host "Your choice"
    }
    else {
        Write-Host "winget not available. Choose how to install Ollama:" -ForegroundColor Cyan
        Write-Host "  [1] Open ollama.com download page"
        Write-Host "  [Q] Skip for now"
        Write-Host ""
        $choice = Read-Host "Your choice"
        # remap so option 1 opens the browser
        if ($choice.Trim() -eq "1") { $choice = "2" }
    }

    switch ($choice.Trim().ToUpper()) {
        "1" {
            Write-Step "Installing Ollama via winget..."
            winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements | Out-Host
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "winget exited with code $LASTEXITCODE. Ollama may not have installed correctly."
            }
            # Refresh PATH so the new ollama binary is visible in this session
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("PATH", "User")
            $found = Find-Ollama
            if (-not $found) {
                Write-Host ""
                Write-Host "Ollama was installed but is not yet visible in PATH." -ForegroundColor Yellow
                Write-Host "Close this terminal and re-run start.ps1 to pick it up." -ForegroundColor Yellow
                return $null
            }
            return $found
        }
        "2" {
            Start-Process "https://ollama.com/download"
            Write-Host ""
            Write-Host "Opening ollama.com in your browser." -ForegroundColor Cyan
            Write-Host "After installing Ollama, re-run this script." -ForegroundColor Yellow
            return $null
        }
        default {
            Write-Host ""
            Write-Host "Skipping Ollama install. Pulling and serving models will fail until it's installed." -ForegroundColor Yellow
            return $null
        }
    }
}

function Clear-ZombieOnPort {
    param([string]$Url)
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) { return }
    $port = ([System.Uri]$Url).Port
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return }
    Write-Warning "Port $port is held by PID $($listener.OwningProcess) but not answering HTTP. Stopping it before startup."
    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 600
}

if (-not (Test-Path -LiteralPath ".\.env")) {
    Copy-Item -LiteralPath ".\.env.example" -Destination ".\.env"
    Write-Step "Created .env"
}

if (-not (Test-Path -LiteralPath ".\config.json")) {
    # Seed a MINIMAL config.json rather than copying the example's dozen sample
    # profiles. config.json is a live mirror of what the user actually pulls:
    # pulling a model auto-creates its profile, so a fresh install should start
    # empty instead of listing models the user hasn't downloaded.
    $minimalConfig = @'
{
  "version": 1,
  "default_profile": null,
  "global_defaults": {
    "safe_mode": true,
    "stream": false,
    "require_gpu_only": false
  },
  "profiles": {}
}
'@
    Set-Content -LiteralPath ".\config.json" -Value $minimalConfig -Encoding utf8
    Write-Step "Created config.json (empty - profiles are created as you pull models)"
}

$envFile = Read-DotEnv
$apiBaseUrl = Get-ApiBaseUrl -EnvFile $envFile
$healthUrl = "$apiBaseUrl/health"
$uiUrl = "$apiBaseUrl/ui"
$docsUrl = "$apiBaseUrl/docs"

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    if ($SkipInstall) {
        $python = "python"
    }
    else {
        $pyCmd = Find-Python
        if (-not $pyCmd) {
            $pyCmd = Install-PythonGuide
        }
        Write-Step "Creating Python virtual environment"
        & $pyCmd -m venv .venv
        if (-not (Test-Path -LiteralPath $python)) {
            throw "Virtual environment creation failed. Verify your Python installation and try again."
        }
        & $python -m pip install --upgrade pip
        & $python -m pip install -r requirements.txt
    }
}

$ollama = Find-Ollama
if (-not $ollama -and -not $SkipInstall) {
    $ollama = Install-OllamaGuide
}
if (-not $ollama) {
    Write-Warning "Ollama was not found. Install it from https://ollama.com/download (or: winget install Ollama.Ollama), then re-run this script."
}
elseif (-not (Test-Http "http://localhost:11434/api/tags" -Timeout 5)) {
    Write-Step "Starting Ollama"
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

if (-not $SkipLlamaCpp) {
    & "$PSScriptRoot\start_llamacpp.ps1" -Optional
}

if (Test-Http $healthUrl -Timeout 5) {
    Write-Step "LocalDeploy API is already running"
}
elseif ($Foreground) {
    Write-Step "Starting LocalDeploy API in the foreground"
    Write-Host "Stop with Ctrl+C. Open another terminal for chat commands."
    & $python api_server.py
    exit $LASTEXITCODE
}
else {
    Write-Step "Starting LocalDeploy API in the background"
    Clear-ZombieOnPort $healthUrl
    New-Item -ItemType Directory -Force -Path ".\logs" | Out-Null
    $out = Join-Path (Resolve-Path ".\logs") "api_server.out.log"
    $err = Join-Path (Resolve-Path ".\logs") "api_server.err.log"
    $pythonAbs = if (Test-Path -LiteralPath $python) { (Resolve-Path -LiteralPath $python).Path } else { $python }
    $process = Start-Process `
        -FilePath $pythonAbs `
        -ArgumentList "api_server.py" `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput $out `
        -RedirectStandardError $err
    $process.Id | Set-Content -LiteralPath ".\logs\api_server.pid"

    Write-Host "Waiting for server to start" -NoNewline
    $serverReady = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        Write-Host "." -NoNewline
        if (Test-Http $healthUrl -Timeout 30) {
            $serverReady = $true
            break
        }
    }
    Write-Host ""

    if (-not $serverReady) {
        throw "LocalDeploy API did not start at $apiBaseUrl. Check logs\api_server.err.log."
    }
}

Write-Host ""
Write-Host "LocalDeploy is ready:" -ForegroundColor Green
Write-Host "  UI:      $uiUrl"
Write-Host "  API:     $apiBaseUrl"
Write-Host "  Docs:    $docsUrl"
Write-Host "  Stop:    .\scripts\stop.ps1"
Write-Host "  Chat:    .\scripts\chat.ps1 -Prompt `"How are you?`""
Write-Host "  Logs:    Get-Content .\logs\api_server.err.log -Wait"

if (-not $NoBrowser) {
    Start-Process $uiUrl
}
