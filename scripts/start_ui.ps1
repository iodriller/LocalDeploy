param(
    [switch]$SkipInstall
)

# Starts the LocalDeploy web UI. The UI is served by the API, so this ensures
# the API is up (starting it in the background if needed) and then opens the
# browser at /ui. Run scripts\start.ps1 if you want the API in the foreground.

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
    $browserHost = $hostName
    if ($browserHost -eq "0.0.0.0" -or $browserHost -eq "::") {
        $browserHost = "127.0.0.1"
    }
    elseif ($browserHost -eq "::1") {
        $browserHost = "[::1]"
    }
    return "http://${browserHost}:${port}"
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

$envFile = Read-DotEnv
$apiBaseUrl = Get-ApiBaseUrl -EnvFile $envFile
$healthUrl = "$apiBaseUrl/health"
$uiUrl = "$apiBaseUrl/ui"

if (Test-Http $healthUrl) {
    Write-Step "API already running — opening the UI"
}
else {
    Write-Step "API not running — starting it in the background"
    & "$PSScriptRoot\start.ps1" -Background -SkipInstall:$SkipInstall
    if (-not (Test-Http $healthUrl)) {
        throw "LocalDeploy API did not start at $apiBaseUrl"
    }
}

Write-Host "Opening $uiUrl" -ForegroundColor Green
Start-Process $uiUrl
