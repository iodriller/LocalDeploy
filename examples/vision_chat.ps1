param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [string]$Prompt = "Describe the image, then list any visible text.",
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [int]$MaxOutputTokens = 256
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ImagePath)) {
    throw "Image not found: $ImagePath"
}

$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $ImagePath))
$b64 = [System.Convert]::ToBase64String($bytes)

$body = @{
    prompt = $Prompt
    images_base64 = @($b64)
    max_output_tokens = $MaxOutputTokens
    safe_mode = $true
} | ConvertTo-Json -Depth 5

$response = Invoke-RestMethod -Uri "$BaseUrl/vision" -Method Post -ContentType "application/json" -Body $body
if (-not $response.success) {
    Write-Error "LocalDeploy error: $($response.error)"
    exit 1
}

Write-Host $response.response
Write-Host ""
Write-Host "[$($response.profile) | $($response.model) | $($response.elapsed_seconds)s]" -ForegroundColor Cyan
