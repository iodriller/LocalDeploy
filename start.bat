@echo off
rem Double-clickable LocalDeploy launcher for Windows.
rem Runs scripts\start.ps1 with the execution policy bypassed, so it works on a
rem clean machine and on files extracted from a downloaded ZIP (mark-of-the-web
rem would otherwise block the .ps1 under the default policy).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
if errorlevel 1 (
    echo.
    echo LocalDeploy did not start. Read the message above for what to fix.
    pause
)
