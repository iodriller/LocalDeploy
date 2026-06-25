param(
    [switch]$Background,
    [switch]$OpenUI,
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

function Read-DotEnv {
    $values = @{}
    $path = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path -LiteralPath $path)) {
        return $values
    }
    foreach ($line in Get-Content -LiteralPath $path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $name, $value = $trimmed.Split("=", 2)
        $values[$name.Trim()] = $value.Trim().Trim('"').Trim("'")
    }
    return $values
}

function Env-Value {
    param(
        [hashtable]$EnvFile,
        [string]$Name,
        [string]$Default = $null
    )
    $processValue = [Environment]::GetEnvironmentVariable($Name)
    if ($processValue) {
        return $processValue
    }
    if ($EnvFile.ContainsKey($Name)) {
        return $EnvFile[$Name]
    }
    return $Default
}

function Get-ApiBaseUrl {
    param([hashtable]$EnvFile)
    $hostName = Env-Value -EnvFile $EnvFile -Name "API_HOST" -Default "127.0.0.1"
    $port = Env-Value -EnvFile $EnvFile -Name "API_PORT" -Default "8000"
    $browserHost = $hostName
    if ($browserHost -eq "0.0.0.0" -or $browserHost -eq "::") {
        $browserHost = "127.0.0.1"
    }
    elseif ($browserHost -eq "::1") {
        $browserHost = "[::1]"
    }
    return "http://${browserHost}:${port}"
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
    param([string]$Url, [int]$Timeout = 30)
    try {
        Invoke-RestMethod -Uri $Url -TimeoutSec $Timeout | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Clear-ZombieOnPort {
    param([string]$Url)
    $uri = [System.Uri]$Url
    $port = $uri.Port
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return }
    $ownerPid = $listener.OwningProcess
    Write-Warning "Port $port is held by PID $ownerPid but not answering HTTP. Killing it before starting a fresh server."
    Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 600
}

if (-not (Test-Path -LiteralPath ".\.env")) {
    Copy-Item -LiteralPath ".\.env.example" -Destination ".\.env"
    Write-Step "Created .env"
}

if (-not (Test-Path -LiteralPath ".\config.json")) {
    Copy-Item -LiteralPath ".\config.example.json" -Destination ".\config.json"
    Write-Step "Created config.json"
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
elseif (-not (Test-Http "http://localhost:11434/api/tags" -Timeout 5)) {
    Write-Step "Starting Ollama"
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

& ".\scripts\start_llamacpp.ps1" -Optional

if (Test-Http $healthUrl -Timeout 5) {
    Write-Step "LocalDeploy API is already running"
}
elseif ($Background) {
    Write-Step "Starting LocalDeploy API in the background"
    Clear-ZombieOnPort $healthUrl
    New-Item -ItemType Directory -Force -Path ".\logs" | Out-Null
    $out = Join-Path (Resolve-Path ".\logs") "api_server.out.log"
    $err = Join-Path (Resolve-Path ".\logs") "api_server.err.log"
    $pythonAbs = if (Test-Path -LiteralPath $python) { (Resolve-Path -LiteralPath $python).Path } else { $python }
    $process = Start-Process -FilePath $pythonAbs -ArgumentList "api_server.py" -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    $process.Id | Set-Content -Path ".\logs\api_server.pid"
    Write-Host "Waiting for server to start" -NoNewline
    $serverReady = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        Write-Host "." -NoNewline
        if (Test-Http $healthUrl -Timeout 30) {
            Write-Host ""
            $serverReady = $true
            break
        }
    }
    Write-Host ""
    if (-not $serverReady) {
        throw "LocalDeploy API did not start at $apiBaseUrl"
    }
}
else {
    Write-Step "Starting LocalDeploy API in the foreground"
    Write-Host "Open another terminal for chat commands, or stop with Ctrl+C."
    & $python api_server.py
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "LocalDeploy is ready:" -ForegroundColor Green
Write-Host "  UI:      $uiUrl"
Write-Host "  API:     $apiBaseUrl"
Write-Host "  Docs:    $docsUrl"
Write-Host "  Open UI: .\scripts\start_ui.ps1  (starts the API if needed, then opens the UI)"
Write-Host "  Chat:    .\scripts\chat.ps1 -Prompt `"How are you?`""
Write-Host "  Bench:   python test_models.py --all --safe-mode true --max-output-tokens 256"

if ($OpenUI) {
    Start-Process $uiUrl
}

if ($OpenDocs) {
    Start-Process $docsUrl
}
