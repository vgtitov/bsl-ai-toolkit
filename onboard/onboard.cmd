@echo off
REM Wrapper for onboard.ps1 that bypasses PowerShell execution policy (fresh Windows blocks .ps1).
REM Run: onboard\onboard.cmd   (args are passed through to onboard.ps1, e.g. -SrcDir C:\1c-src)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0onboard.ps1" %*
