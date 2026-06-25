<#
.SYNOPSIS
  Bootstrap окружения Claude Code для разработки 1С. Идемпотентный. Org-agnostic.
.EXAMPLE
  .\onboard.ps1
  .\onboard.ps1 -SrcDir C:\1c-src
#>
param(
  [string]$WorkDir = (Get-Location).Path,
  [string]$SrcDir  = $(if ($env:ONEC_SRC_DIR) { $env:ONEC_SRC_DIR } else { "$env:USERPROFILE\erp-src" })
)
$ErrorActionPreference = 'Stop'
function Say($m){ Write-Host "`n== $m ==" -ForegroundColor Cyan }
function Ok($m){ Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [!] $m" -ForegroundColor Yellow }

$TeamDir   = Split-Path -Parent $PSScriptRoot
$SkillsDst = Join-Path $env:USERPROFILE '.claude\skills'

Say "1/6 Пререквизиты"
foreach ($c in 'git','uv','java','claude','rg') {
  if (Get-Command $c -ErrorAction SilentlyContinue) { Ok $c } else { Warn "$c не найден (доустанови)" }
}

Say "2/6 Скиллы -> ~/.claude/skills"
New-Item -ItemType Directory -Force -Path $SkillsDst | Out-Null
foreach ($s in '1c-dev','1c-analyst') {
  $src = Join-Path $TeamDir "skills\$s"
  if (Test-Path $src) { Copy-Item $src $SkillsDst -Recurse -Force; Ok "скилл $s" } else { Warn "нет $src" }
}

Say "3/6 Каталог исходников 1С: $SrcDir"
New-Item -ItemType Directory -Force -Path $SrcDir | Out-Null
Warn "Склонируй СВОИ репозитории конфигураций/расширений в $SrcDir (MCP подхватит их авто)."

Say "4/6 Деплой MCP чтения кода"
Copy-Item (Join-Path $TeamDir 'mcp\erp_mcp.py') (Join-Path $SrcDir 'erp_mcp.py') -Force
Ok "erp_mcp.py -> $SrcDir"

Say "5/7 Профиль .mcp.json и CLAUDE.md -> $WorkDir"
Copy-Item (Join-Path $TeamDir 'mcp\dev.mcp.json') (Join-Path $WorkDir '.mcp.json') -Force
Copy-Item (Join-Path $TeamDir 'CLAUDE.md') (Join-Path $WorkDir 'CLAUDE.md') -Force
Ok "разложены профиль и правила"
# Источник erp-1c: local по умолчанию; central — если задан ERP1C_URL и центр доступен (подключение — set_token, см. ниже).
if (Get-Command uv -ErrorAction SilentlyContinue) {
  $erpMode = if ($env:ERP1C_MODE) { $env:ERP1C_MODE } else { 'auto' }
  try { uv run (Join-Path $TeamDir 'scripts\switch_erp.py') --mode $erpMode --workdir $WorkDir; Ok "switch_erp ($erpMode)" }
  catch { Warn "switch_erp пропущен" }
} else { Warn "uv не найден — switch_erp пропущен (erp-1c останется локальным)" }

Say "6/7 Переменные окружения (User)"
[Environment]::SetEnvironmentVariable('ONEC_SRC_DIR', $SrcDir, 'User'); Ok "ONEC_SRC_DIR=$SrcDir (дефолт у каждого свой; хочешь другой — задай ONEC_SRC_DIR заранее или -SrcDir)"
# Подключение к ЦЕНТРАЛЬНОМУ erp-1c (если в вашей команде он развёрнут): URL и токен выдаёт тимлид.
Warn "Центральный erp-1c (если есть) — подключение одной командой (токен у тимлида):"
@"
  .\scripts\set_token.ps1                  # спросит URL/токен, поставит ERP1C_URL/ERP1C_TOKEN, напечатает блок для Claude Code
  # затем switch_erp сам уйдёт в central (--mode auto). Секрет — только в env, не в файл/git.
"@ | Write-Host

Say "7/8 Платформа 1С и BSL-инструменты (поиск + скачивание свободных jar'ов)"
if (Get-Command uv -ErrorAction SilentlyContinue) {
  try { uv run (Join-Path $TeamDir 'scripts\detect_tools.py') --install; Ok "detect_tools: платформа/BSL найдены/скачаны, env прописаны (мост BSL_LS_MCP — из mcp\bsl_ls_mcp.py)" }
  catch { Warn "detect_tools пропущен — запусти: uv run scripts\detect_tools.py --install" }
} else { Warn "uv не найден — позже: python scripts\detect_tools.py --install" }

Say "8/8 Git: коммиты без соавторства Claude (commit-msg хук)"
if (Get-Command uv -ErrorAction SilentlyContinue) {
  try { uv run (Join-Path $TeamDir 'scripts\install_git_hooks.py'); Ok "commit-msg хук поставлен" }
  catch { Warn "install_git_hooks пропущен — поставь вручную: uv run scripts\install_git_hooks.py" }
} else { Warn "uv не найден — поставь хук вручную: python scripts\install_git_hooks.py" }

Say "Готово. Ручные шаги"
@"
[ ] Проставь версии под свою конфигурацию в CLAUDE.md (платформа, режим совместимости, БСП, библиотеки).
[ ] Заполни skills/1c-dev/references/conventions-template.md под свой проект (слои, префикс, точки расширения).
[ ] BSL-инструменты: detect_tools уже нашёл/скачал платформу/jar'ы и прописал env. Что помечено [нет] — доложи и повтори uv run scripts\detect_tools.py (мост BSL_LS_MCP берётся из репо mcp\bsl_ls_mcp.py).
[ ] Git-идентичность площадки и токен push — см. docs/git.md.
[ ] Перезапусти Claude Code в $WorkDir -> подтверди MCP-серверы.
[ ] Self-test: спроси erp-1c (find_object) — ответ по коду из $SrcDir (или из центра, если подключён).
"@ | Write-Host
