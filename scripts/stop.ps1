param(
    [switch]$StopOllama
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-ApiPort {
    $port = [Environment]::GetEnvironmentVariable("API_PORT")
    if ($port) { return [int]$port }
    if (-not (Test-Path -LiteralPath ".\.env")) { return 8000 }
    foreach ($line in Get-Content -LiteralPath ".\.env") {
        if ($line.Trim() -match "^API_PORT\s*=\s*(.+)$") {
            return [int]$Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return 8000
}

function Test-Health {
    param([int]$Port)
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Stop-PidFile {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) { return }

    $rawPid = (Get-Content -LiteralPath $Path -Raw).Trim()
    [int]$processId = 0
    if ([int]::TryParse($rawPid, [ref]$processId)) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        Write-Step "Stopped $Label (PID: $processId)"
    }
    else {
        Write-Warning "Ignoring invalid $Label PID file: $rawPid"
    }
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

function Stop-ApiListener {
    param([int]$Port)
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) { return }
    if (-not (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) { return }

    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if (
            $proc -and
            $proc.Name -match "^(python|python3|pythonw|py)(\.exe)?$" -and
            $proc.CommandLine -like "*api_server.py*" -and
            $proc.CommandLine -like "*$ProjectRoot*"
        ) {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Step "Stopped API server listener on port $Port (PID: $($proc.ProcessId))"
        }
    }
}

$apiPort = Get-ApiPort

Stop-PidFile -Path ".\logs\api_server.pid" -Label "LocalDeploy API"
Stop-ApiListener -Port $apiPort

if (Test-Health -Port $apiPort) {
    Write-Warning "LocalDeploy API still responds on port $apiPort. Stop it manually if this checkout owns it."
}
else {
    Write-Host "LocalDeploy API stopped successfully" -ForegroundColor Green
}

Stop-PidFile -Path ".\logs\llama_server.pid" -Label "llama.cpp server"
Get-Process "llama-server" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    Write-Step "Stopped llama.cpp server (PID: $($_.Id))"
}

if ($StopOllama) {
    Get-Process "ollama" -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        Write-Step "Stopped Ollama (PID: $($_.Id))"
    }
}
else {
    Write-Host "Ollama was left running. To stop it too: .\scripts\stop.ps1 -StopOllama" -ForegroundColor Cyan
}
