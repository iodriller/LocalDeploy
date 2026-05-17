param(
    [switch]$SkipModelPulls,
    [switch]$PullQwenVision
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "OK: $Message" -ForegroundColor Green
}

function Get-OllamaCommand {
    $cmd = Get-Command "ollama" -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:ProgramFiles\Ollama\ollama.exe",
        "${env:ProgramFiles(x86)}\Ollama\ollama.exe"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    return $null
}

function Test-OllamaApi {
    try {
        $null = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get -TimeoutSec 5
        return $true
    }
    catch {
        return $false
    }
}

function Copy-IfMissing {
    param(
        [string]$Source,
        [string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Destination)) {
        Copy-Item -LiteralPath $Source -Destination $Destination
        Write-Ok "Created $Destination from $Source"
    }
    else {
        Write-Ok "$Destination already exists"
    }
}

function Pull-OllamaModel {
    param(
        [string]$OllamaExe,
        [string]$Model
    )

    Write-Step "Pulling $Model"
    & $OllamaExe pull $Model
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Failed to pull $Model. You can retry later with: ollama pull $Model"
    }
    else {
        Write-Ok "Pulled $Model"
    }
}

Write-Step "Preparing local config files"
Copy-IfMissing -Source ".\config.example.json" -Destination ".\config.json"
Copy-IfMissing -Source ".\.env.example" -Destination ".\.env"

Write-Step "Checking Ollama installation"
$ollamaExe = Get-OllamaCommand
if (-not $ollamaExe) {
    Write-Warning "Ollama was not found in PATH or common install locations."
    $winget = Get-Command "winget" -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Trying winget install. This should not require Administrator mode for a normal user install."
        winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "winget could not install Ollama automatically."
        }
        $ollamaExe = Get-OllamaCommand
    }
    else {
        Write-Warning "winget is not available on this Windows install."
    }
}

if (-not $ollamaExe) {
    Write-Warning "Install Ollama manually from https://ollama.com/download/windows, then rerun this script."
    Write-Host "The Python project files were still created successfully."
    exit 0
}

Write-Ok "Found Ollama: $ollamaExe"

Write-Step "Checking Ollama local API"
if (-not (Test-OllamaApi)) {
    Write-Host "Ollama is not responding at http://localhost:11434. Starting 'ollama serve' in the background."
    try {
        Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 5
    }
    catch {
        Write-Warning "Could not start Ollama automatically: $($_.Exception.Message)"
    }
}

if (-not (Test-OllamaApi)) {
    Write-Warning "Ollama still is not responding at http://localhost:11434/api/tags."
    Write-Warning "Start Ollama manually, then run: ollama pull gemma3:4b"
    Write-Warning "For 12B testing, also run: ollama pull gemma3:12b"
    exit 0
}

Write-Ok "Ollama API is responding at http://localhost:11434"

if ($SkipModelPulls) {
    Write-Step "Skipping model pulls because -SkipModelPulls was provided"
}
else {
    Pull-OllamaModel -OllamaExe $ollamaExe -Model "gemma3:4b"
    Pull-OllamaModel -OllamaExe $ollamaExe -Model "gemma3:12b"

    # Optional vision comparison model. This is not pulled unless you opt in:
    # .\install.ps1 -PullQwenVision
    if ($PullQwenVision) {
        Pull-OllamaModel -OllamaExe $ollamaExe -Model "qwen2.5vl:7b"
    }
}

Write-Step "Next steps"
Write-Host "Create a Python virtual environment:"
Write-Host "  py -3 -m venv .venv"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python -m pip install -r requirements.txt"
Write-Host ""
Write-Host "Start the API server:"
Write-Host "  python api_server.py"
Write-Host ""
Write-Host "Run benchmarks:"
Write-Host "  python test_models.py --all"
