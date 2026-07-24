<#
.SYNOPSIS
  build.ps1 — сгенерировать конфиги под AI-агентов из ЕДИНОГО ИСТОЧНИКА core/ (Windows-паритет build.sh).
  Три оси переносимости: AGENTS.md (правила) + SKILL.md (навыки) + MCP (инструменты).
.EXAMPLE
  .\build.ps1            # собрать всё (claude+gemini+codex)
  .\build.ps1 claude     # только Claude Code
.NOTES
  На Windows симлинки требуют прав → GEMINI.md делается копией. Для Cursor/Copilot/… используйте rulesync (npm i -g rulesync).
#>
param([string]$Target = 'all')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Build-Claude {
  Write-Host "[claude] CLAUDE.md (@AGENTS.md) + .claude/skills + .claude/settings.json + .mcp.json"
  Copy-Item 'core\AGENTS.md' 'AGENTS.md' -Force
  Copy-Item 'adapters\claude\CLAUDE.md' 'CLAUDE.md' -Force
  if (Test-Path '.claude\skills') { Remove-Item '.claude\skills' -Recurse -Force }
  New-Item -ItemType Directory -Force -Path '.claude\skills' | Out-Null
  Copy-Item 'core\skills\*' '.claude\skills\' -Recurse -Force
  New-Item -ItemType Directory -Force -Path '.claude' | Out-Null
  Copy-Item 'adapters\claude\settings.json' '.claude\settings.json' -Force
  Copy-Item 'core\mcp\servers.json' '.mcp.json' -Force
}
function Build-Gemini { Write-Host "[gemini] GEMINI.md (копия AGENTS.md)"; Copy-Item 'core\AGENTS.md' 'AGENTS.md' -Force; Copy-Item 'core\AGENTS.md' 'GEMINI.md' -Force }
function Build-Codex  { Write-Host "[codex] AGENTS.md (канон, читается нативно)"; Copy-Item 'core\AGENTS.md' 'AGENTS.md' -Force }
function Build-Rulesync {
  if (Get-Command rulesync -ErrorAction SilentlyContinue) {
    Write-Host "[rulesync] генерация под Cursor/Copilot/Cline/Windsurf/…"
    try { rulesync generate --targets claudecode,cursor,copilot,cline,geminicli,codexcli } catch { Write-Host "[rulesync] пропущено — фолбэк собран" }
  } else { Write-Host "[rulesync] не установлен — фолбэк (Claude/Gemini/Codex) собран. Для остальных: npm i -g rulesync" }
}

switch ($Target) {
  'claude' { Build-Claude }
  'gemini' { Build-Gemini }
  'codex'  { Build-Codex }
  'all'    { Build-Claude; Build-Gemini; Build-Codex; Build-Rulesync }
  default  { Write-Host "usage: .\build.ps1 [all|claude|gemini|codex]"; exit 1 }
}
Write-Host "[ok] build: $Target"
