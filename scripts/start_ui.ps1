param(
    [switch]$SkipInstall
)

# Convenience wrapper: start the API in the background if needed, then open /ui.
$ErrorActionPreference = "Stop"
& "$PSScriptRoot\start.ps1" -OpenUI -SkipInstall:$SkipInstall
