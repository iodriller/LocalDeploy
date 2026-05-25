param(
    [switch]$Background,
    [switch]$OpenDocs,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-Ollama {
    $cmd = Get-Command "ollama" -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    $candidate = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path -LiteralPath $candidate) {
        return $candidate
    }
    return $null
}

function Test-Http {
    param([string]$Url)
    try {
        Invoke-RestMethod -Uri $Url -TimeoutSec 5 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath ".\.env")) {
    Copy-Item -LiteralPath ".\.env.example" -Destination ".\.env"
    Write-Step "Created .env"
}

if (-not (Test-Path -LiteralPath ".\config.json")) {
    Copy-Item -LiteralPath ".\config.example.json" -Destination ".\config.json"
    Write-Step "Created config.json"
}

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    if ($SkipInstall) {
        $python = "python"
    }
    else {
        Write-Step "Creating Python virtual environment"
        python -m venv .venv
        $python = ".\.venv\Scripts\python.exe"
        & $python -m pip install --upgrade pip
        & $python -m pip install -r requirements.txt
    }
}

$ollama = Find-Ollama
if (-not $ollama) {
    Write-Warning "Ollama was not found. Run .\install.ps1 first or install Ollama manually."
}
elseif (-not (Test-Http "http://localhost:11434/api/tags")) {
    Write-Step "Starting Ollama"
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

& ".\scripts\start_llamacpp.ps1" -SkipIfDisabled

if (Test-Http "http://127.0.0.1:8000/health") {
    Write-Step "LocalDeploy API is already running"
}
elseif ($Background) {
    Write-Step "Starting LocalDeploy API in the background"
    New-Item -ItemType Directory -Force -Path ".\logs" | Out-Null
    $out = Join-Path (Resolve-Path ".\logs") "api_server.out.log"
    $err = Join-Path (Resolve-Path ".\logs") "api_server.err.log"
    $process = Start-Process -FilePath $python -ArgumentList "api_server.py" -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    $process.Id | Set-Content -Path ".\logs\api_server.pid"
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Http "http://127.0.0.1:8000/health") {
            break
        }
    }
}
else {
    Write-Step "Starting LocalDeploy API in the foreground"
    Write-Host "Open another terminal for chat commands, or stop with Ctrl+C."
    & $python api_server.py
    exit $LASTEXITCODE
}

if (-not (Test-Http "http://127.0.0.1:8000/health")) {
    throw "LocalDeploy API did not start at http://127.0.0.1:8000"
}

Write-Host ""
Write-Host "LocalDeploy is ready:" -ForegroundColor Green
Write-Host "  API:     http://127.0.0.1:8000"
Write-Host "  Docs:    http://127.0.0.1:8000/docs"
Write-Host "  Chat:    .\scripts\chat.ps1 -Prompt `"How are you?`""
Write-Host "  Bench:   python test_models.py --all --safe-mode true --max-output-tokens 256"

if ($OpenDocs) {
    Start-Process "http://127.0.0.1:8000/docs"
}
