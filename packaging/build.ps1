# Build the Windows LocalDeploy tray executable (Release R7, Phase A).
#
# Usage:
#   .\packaging\build.ps1
#
# Requires the packaging extras (pyinstaller, pystray, pillow):
#   pip install -e ".[packaging]"
#
# Output: dist\LocalDeploy\LocalDeploy.exe
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $repoRoot
try {
    python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing packaging dependencies (pyinstaller, pystray, pillow)..."
        python -m pip install -e ".[packaging]"
    }
    pyinstaller packaging/localdeploy.spec --distpath dist --workpath build --noconfirm
    Write-Host "`nBuilt: $repoRoot\dist\LocalDeploy\LocalDeploy.exe"
    Write-Host "Not done: code signing, an installer wrapper (NSIS/MSI), auto-start-at-login. See docs/ROADMAP.md P0.1 Phase B."
} finally {
    Pop-Location
}
