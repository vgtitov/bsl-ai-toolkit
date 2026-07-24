<#
.SYNOPSIS
  build.ps1 - generate per-agent configs from the single source core/ (Windows parity of build.sh).
  Three portability axes: AGENTS.md (rules) + SKILL.md (skills) + MCP (tools).
.EXAMPLE
  .\build.ps1            # build all (claude+gemini+codex)
  .\build.ps1 claude     # Claude Code only
.NOTES
  On Windows symlinks need privileges, so GEMINI.md is a copy. For Cursor/Copilot/etc use rulesync (npm i -g rulesync).
#>
param([string]$Target = 'all')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Build-Claude {
  Write-Host '[claude] CLAUDE.md (@AGENTS.md) + .claude/skills + .claude/settings.json + .mcp.json'
  Copy-Item 'core\AGENTS.md' 'AGENTS.md' -Force
  Copy-Item 'adapters\claude\CLAUDE.md' 'CLAUDE.md' -Force
  if (Test-Path '.claude\skills') { Remove-Item '.claude\skills' -Recurse -Force }
  New-Item -ItemType Directory -Force -Path '.claude\skills' | Out-Null
  Copy-Item 'core\skills\*' '.claude\skills' -Recurse -Force
  New-Item -ItemType Directory -Force -Path '.claude' | Out-Null
  Copy-Item 'adapters\claude\settings.json' '.claude\settings.json' -Force
  Copy-Item 'core\mcp\servers.json' '.mcp.json' -Force
}
function Build-Gemini { Write-Host '[gemini] GEMINI.md (copy of AGENTS.md)'; Copy-Item 'core\AGENTS.md' 'AGENTS.md' -Force; Copy-Item 'core\AGENTS.md' 'GEMINI.md' -Force }
function Build-Codex  { Write-Host '[codex] AGENTS.md (canonical, read natively)'; Copy-Item 'core\AGENTS.md' 'AGENTS.md' -Force }
function Build-Rulesync {
  if (Get-Command rulesync -ErrorAction SilentlyContinue) {
    Write-Host '[rulesync] generating for Cursor/Copilot/Cline/Windsurf/...'
    try { rulesync generate --targets claudecode,cursor,copilot,cline,geminicli,codexcli } catch { Write-Host '[rulesync] skipped - fallback already built' }
  } else { Write-Host '[rulesync] not installed - fallback (Claude/Gemini/Codex) built. For others: npm i -g rulesync' }
}

switch ($Target) {
  'claude' { Build-Claude }
  'gemini' { Build-Gemini }
  'codex'  { Build-Codex }
  'all'    { Build-Claude; Build-Gemini; Build-Codex; Build-Rulesync }
  default  { Write-Host 'usage: .\build.ps1 [all|claude|gemini|codex]'; exit 1 }
}
Write-Host "[ok] build: $Target"
