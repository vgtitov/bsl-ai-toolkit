<#
.SYNOPSIS
  Подключение к ЦЕНТРАЛЬНОМУ erp-1c одним шагом (org-agnostic): кладёт URL и bearer-токен центра в переменные
  окружения пользователя и печатает готовый блок, который разработчик вставляет в Claude Code — дальше ассистент
  читает доку (docs/docker.md / CONNECT.md локализации) и настраивает всё сам.

  Клиентская сторона центрального сервиса (docker-compose + Caddy) — пара к scripts/switch_erp.py. Токен НИКУДА
  не пишется кроме переменной окружения пользователя: не в файл, не в git, не в .mcp.json. switch_erp подставит
  его плейсхолдером ${ERP1C_TOKEN} только при старте MCP. Локализации вендорят скрипт и задают свои URL/профиль.

.EXAMPLE
  .\scripts\set_token.ps1 -Url http://host:8000/sse     # спросит токен, профиль dev
  .\scripts\set_token.ps1 -Url http://host:8000/sse -Profile analyst
#>
param(
  [string]$Token,
  [string]$Url = $env:ERP1C_URL,
  [string]$Profile = 'dev',
  [switch]$NoTest
)
$ErrorActionPreference = 'Stop'
function Say($m){ Write-Host "`n== $m ==" -ForegroundColor Cyan }
function Ok($m){ Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [!] $m" -ForegroundColor Yellow }

# --- URL центра (параметр или env; иначе спросить) ---
if (-not $Url) { $Url = (Read-Host "URL центра (например http://host:8000/sse)").Trim() }
if (-not $Url) { Warn "URL не задан — отмена."; exit 1 }

# --- Токен: из параметра либо спросить (ввод скрыт) ---
if (-not $Token) {
  $sec  = Read-Host "Вставь bearer-токен центра (выдаёт владелец центра; ввод скрыт)" -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  $Token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
$Token = $Token.Trim()
if (-not $Token) { Warn "Токен пустой — отмена."; exit 1 }
if ($Token -match '(?i)rotate-me|change-me|example|your-token|<.*>') {
  Warn "Это похоже на ПЛЕЙСХОЛДЕР, не настоящий токен. Возьми реальный bearer у владельца центра. Отмена."
  exit 1
}

# --- Записать в окружение пользователя + текущую сессию ---
Say "Записываю доступ к центру в переменные окружения пользователя"
[Environment]::SetEnvironmentVariable('ERP1C_URL',   $Url,   'User'); Ok "ERP1C_URL = $Url (свой центр — перезапусти с -Url <адрес>)"
[Environment]::SetEnvironmentVariable('ERP1C_TOKEN', $Token, 'User'); Ok "ERP1C_TOKEN = <скрыт, длина $($Token.Length)>"
[Environment]::SetEnvironmentVariable('ERP1C_MODE',  'auto', 'User'); Ok "ERP1C_MODE = auto"
$env:ERP1C_URL = $Url; $env:ERP1C_TOKEN = $Token; $env:ERP1C_MODE = 'auto'

# --- Необязательная проверка связи (200 с токеном, 401 без) ---
if (-not $NoTest) {
  Say "Проверка центра (необязательная)"
  try {
    $r = Invoke-WebRequest -Uri $Url -Headers @{ Authorization = "Bearer $Token" } -Method Head -TimeoutSec 5 -ErrorAction Stop
    if ($r.StatusCode -eq 200) { Ok "центр ответил 200 — токен верный" } else { Warn "ответ $($r.StatusCode) — проверь токен/URL" }
  } catch {
    $code = $null; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    if ($code -eq 200) { Ok "центр ответил 200 — токен верный" }
    elseif ($code -eq 401) { Warn "401 — токен не принят" }
    elseif ($code) { Warn "ответ $code — проверь токен/URL" }
    else { Warn "связь не проверена (сеть до центра) — не блокер" }
  }
}

# --- Готовый блок для вставки в Claude Code ---
$repoRoot = Split-Path -Parent $PSScriptRoot
$paste = @"
Прочитай документацию по подключению к центру (docs/docker.md, в локализации — docs/CONNECT.md) и настрой инструменты 1С для Claude Code.
Сначала покажи дефолты (каталог клонов, URL центра, профиль) и спроси, хочу ли я что-то переопределить и как; объясни, что и где менять. Потом настраивай.
Профиль: $Profile.
Центр настроен: ERP1C_URL и ERP1C_TOKEN заданы в моём окружении (режим central, токен подставится сам).
Пройди: onboard -> switch_erp (--mode central) -> self-test (find_object на наборе репозиториев центра).
"@
Write-Host "`n=== ГОТОВО. Скопируй блок ниже целиком и вставь в Claude Code ===" -ForegroundColor Green
Write-Host ("-"*72); Write-Host $paste; Write-Host ("-"*72)
Write-Host "`nПеред вставкой ПЕРЕЗАПУСТИ терминал и Claude Code — env подхватывается только новыми процессами." -ForegroundColor Yellow
Write-Host "Дока (если хочешь руками): $repoRoot\docs\docker.md"
