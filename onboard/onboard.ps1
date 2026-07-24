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
# generic: доедет всё, что лежит в skills/ (1c-dev, 1c-analyst, 1c-metadata, будущие).
# Скилл перекладывается ЦЕЛИКОМ (удалённые upstream файлы не остаются), но references/local/
# — слой ЛОКАЛИЗАЦИИ (docs/SKILL_LOCALIZATION.md) — сохраняется.
$skillDirs = Get-ChildItem -Path (Join-Path $TeamDir 'core\skills') -Directory -ErrorAction SilentlyContinue
if ($skillDirs) {
  foreach ($d in $skillDirs) {
    $dst = Join-Path $SkillsDst $d.Name
    $local = Join-Path $dst 'references\local'
    $keep = $null
    if (Test-Path $local) {
      $keep = Join-Path ([System.IO.Path]::GetTempPath()) ("skill-local-" + [System.Guid]::NewGuid().ToString('N'))
      Copy-Item $local $keep -Recurse -Force
    }
    if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
    Copy-Item $d.FullName $SkillsDst -Recurse -Force
    if ($keep) {
      New-Item -ItemType Directory -Force -Path (Join-Path $dst 'references') | Out-Null
      Copy-Item $keep (Join-Path $dst 'references\local') -Recurse -Force
      Remove-Item $keep -Recurse -Force
    }
    Ok "скилл $($d.Name)"
  }
} else { Warn "в $TeamDir\core\skills нет скиллов" }

Say "3/6 Каталог исходников 1С: $SrcDir"
New-Item -ItemType Directory -Force -Path $SrcDir | Out-Null
Warn "Склонируй СВОИ репозитории конфигураций/расширений в $SrcDir (MCP подхватит их авто)."

Say "4/6 Деплой MCP чтения кода"
Copy-Item (Join-Path $TeamDir 'mcp\onec_mcp.py') (Join-Path $SrcDir 'onec_mcp.py') -Force
Ok "onec_mcp.py -> $SrcDir"
$legacyEngine = Join-Path $SrcDir 'erp_mcp.py'   # ренейм erp->onec: убрать старый движок (иначе путается с onec_mcp.py)
if (Test-Path $legacyEngine) { Remove-Item $legacyEngine -Force; Ok "удалён легаси erp_mcp.py" }

Say "5/7 Сборка из core/ + профиль .mcp.json, CLAUDE.md, AGENTS.md -> $WorkDir"
Copy-Item (Join-Path $TeamDir '.mcp.json') (Join-Path $WorkDir '.mcp.json') -Force
Copy-Item (Join-Path $TeamDir 'AGENTS.md') (Join-Path $WorkDir 'AGENTS.md') -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $TeamDir 'CLAUDE.md') (Join-Path $WorkDir 'CLAUDE.md') -Force
Ok "разложены профиль и правила"
# Источник onec-code: local по умолчанию; central — если задан ONEC_MCP_URL и центр доступен (подключение — set_token, см. ниже).
if (Get-Command uv -ErrorAction SilentlyContinue) {
  $erpMode = if ($env:ONEC_MCP_MODE) { $env:ONEC_MCP_MODE } else { 'auto' }
  try { uv run (Join-Path $TeamDir 'scripts\switch_source.py') --mode $erpMode --workdir $WorkDir; Ok "switch_source ($erpMode)" }
  catch { Warn "switch_source пропущен" }
} else { Warn "uv не найден — switch_source пропущен (onec-code останется локальным)" }

Say "6/7 Переменные окружения (User)"
[Environment]::SetEnvironmentVariable('ONEC_SRC_DIR', $SrcDir, 'User'); Ok "ONEC_SRC_DIR=$SrcDir (дефолт у каждого свой; хочешь другой — задай ONEC_SRC_DIR заранее или -SrcDir)"
Warn "Все переменные — в .env.example (скопируй в .env и заполни). Полная таблица: docs/mcp-deploy-and-use.md."
Warn "Для onec-metadata (bin\1c-meta, правки метаданных по SSH): опц. ONEC_1CV8_BIN, ONEC_REMOTE_WORKDIR; нужны ssh/scp/tar (Windows 10+ — из коробки)."
# Подключение к ЦЕНТРАЛЬНОМУ onec-code (если в вашей команде он развёрнут): URL и токен выдаёт тимлид.
Warn "Центральный onec-code (если есть) — подключение одной командой (токен у тимлида):"
@"
  .\scripts\set_token.ps1                  # спросит URL/токен, поставит ONEC_MCP_URL/ONEC_MCP_TOKEN, напечатает блок для Claude Code
  # затем switch_source сам уйдёт в central (--mode auto). Секрет — только в env, не в файл/git.
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
[ ] Проставь версии под свою конфигурацию в core/skills/1c-dev/references/conventions-template.md (общие правила — в core/AGENTS.md).
[ ] Заполни core/skills/1c-dev/references/conventions-template.md под свой проект (слои, префикс, точки расширения).
[ ] BSL-инструменты: detect_tools уже нашёл/скачал платформу/jar'ы и прописал env. Что помечено [нет] — доложи и повтори uv run scripts\detect_tools.py (мост BSL_LS_MCP берётся из репо mcp\bsl_ls_mcp.py).
[ ] Git-идентичность площадки и токен push — см. docs/git.md.
[ ] Перезапусти Claude Code в $WorkDir -> подтверди MCP-серверы.
[ ] Self-test: спроси onec-code (find_object) — ответ по коду из $SrcDir (или из центра, если подключён).
"@ | Write-Host
