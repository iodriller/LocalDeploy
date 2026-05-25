param(
    [switch]$StopOllama
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
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

# Try to stop the API server using the PID file
$pidFile = ".\logs\api_server.pid"
if (Test-Path -LiteralPath $pidFile) {
    $pid = Get-Content -LiteralPath $pidFile -Raw | ForEach-Object { $_.Trim() }
    if ($pid) {
        try {
            Stop-Process -Id $pid -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $pidFile -Force
            Write-Step "Stopped LocalDeploy API (PID: $pid)"
        }
        catch {
            Write-Warning "Could not stop process with PID $pid"
        }
    }
}

# Also try to kill any remaining python processes running api_server.py
$processes = Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*api_server.py*" }
if ($processes) {
    foreach ($proc in $processes) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Write-Step "Stopped additional API server process (PID: $($proc.Id))"
    }
}

# Check if API is still running
if (Test-Http "http://127.0.0.1:8000/health") {
    Write-Warning "LocalDeploy API is still running. You may need to stop it manually."
}
else {
    Write-Host "LocalDeploy API stopped successfully" -ForegroundColor Green
}

$llamaPidFile = ".\logs\llama_server.pid"
if (Test-Path -LiteralPath $llamaPidFile) {
    $llamaPid = Get-Content -LiteralPath $llamaPidFile -Raw | ForEach-Object { $_.Trim() }
    if ($llamaPid) {
        Stop-Process -Id $llamaPid -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $llamaPidFile -Force -ErrorAction SilentlyContinue
        Write-Step "Stopped llama.cpp server (PID: $llamaPid)"
    }
}

Get-Process "llama-server" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    Write-Step "Stopped additional llama.cpp server process (PID: $($_.Id))"
}

# Stop Ollama if requested
if ($StopOllama) {
    $ollama = Get-Process "ollama" -ErrorAction SilentlyContinue
    if ($ollama) {
        Stop-Process -InputObject $ollama -Force -ErrorAction SilentlyContinue
        Write-Step "Stopped Ollama"
    }
    else {
        Write-Host "Ollama is not running" -ForegroundColor Yellow
    }
}
else {
    Write-Host "To also stop Ollama, run: .\scripts\stop.ps1 -StopOllama" -ForegroundColor Cyan
}
