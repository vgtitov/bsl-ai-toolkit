@echo off
REM Wrapper for set_token.ps1 that bypasses PowerShell execution policy.
REM Fresh Windows defaults to Restricted and blocks .ps1 ("running scripts is disabled").
REM This .cmd runs the script with -ExecutionPolicy Bypass for this process only (system policy unchanged).
REM Run: double-click, or  scripts\set_token.cmd   (args are passed through)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set_token.ps1" %*
