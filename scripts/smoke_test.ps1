param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [switch]$RequireServer
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

Write-Host "Checking Python syntax"
python -m py_compile api_server.py test_models.py

Write-Host "Checking JSON examples"
python -m json.tool config.example.json > $null

Write-Host "Checking PowerShell installer parse"
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path .\install.ps1), [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_.Message }
    exit 1
}

Write-Host "Checking API import and safety validation"
@'
from api_server import run_local_request
result = run_local_request("chat", {"profile": "gemma3_4b_ollama_safe", "prompt": "x" * 25000})
assert result["success"] is False
assert "Prompt is too large" in result["error"]
print("validation OK")
'@ | python -

try {
    $health = Invoke-RestMethod -Uri "$BaseUrl/health" -Method Get -TimeoutSec 3
    if ($health.server -ne "ok") {
        throw "Unexpected health response"
    }
    Invoke-RestMethod -Uri "$BaseUrl/v1/models" -Method Get -TimeoutSec 3 | Out-Null
    Write-Host "HTTP smoke test OK at $BaseUrl"
}
catch {
    if ($RequireServer) {
        throw
    }
    Write-Host "API server is not running at $BaseUrl; skipped HTTP smoke test"
}
