param(
    [string]$Prompt,
    [string]$Profile = "gemma3_4b_ollama_safe",
    [int]$MaxOutputTokens = 256,
    [switch]$Raw,
    [switch]$Profiles,
    [switch]$StartServer
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$Python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$argsList = @("chat_cli.py", "--profile", $Profile, "--max-output-tokens", [string]$MaxOutputTokens)
if ($Prompt) {
    $argsList += @("--prompt", $Prompt)
}
if ($Raw) {
    $argsList += "--raw"
}
if ($Profiles) {
    $argsList += "--profiles"
}
if ($StartServer) {
    $argsList += "--start-server"
}

& $Python @argsList
