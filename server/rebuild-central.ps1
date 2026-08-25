<#
.SYNOPSIS
  Пересобрать центральный Docker-MCP (onec-code / onec-ops / onec-data) на удалённом хосте после обновления кода.
  Запускать НА Work PC (там настроен docker context на удалённый движок). Требует включённого доступа к хосту (VPN).
.DESCRIPTION
  Образы переименованы claude-1c-toolkit/* -> bsl-ai-toolkit/*. Контекст сборки = корень репозитория (Dockerfile
  делает COPY mcp/*.py), поэтому запускать из клона toolkit. Клон обновляется из ПУБЛИЧНОГО GitHub (без корп-авторизации).
.PARAMETER Context
  Имя docker context удалённого движка (по умолчанию 'corp' = ssh://corp-docker).
.PARAMETER RepoDir
  Путь к клону toolkit на Work PC.
.EXAMPLE
  .\server\rebuild-central.ps1 -Context corp -RepoDir C:\dev\<путь>\claude-1c-toolkit
#>
param(
  [string]$Context = 'corp',
  [string]$RepoDir = 'C:\dev\<путь>\claude-1c-toolkit'
)
$ErrorActionPreference = 'Stop'

Write-Host "[1/4] Обновляю клон toolkit из публичного GitHub (без корп-авторизации)"
Set-Location $RepoDir
git fetch origin --quiet
git checkout main --quiet
git reset --hard origin/main --quiet
git log --oneline -1

Write-Host "[2/4] Проверяю доступ к удалённому docker context '$Context' (нужен VPN/доступ к хосту)"
docker --context $Context info --format '{{.Name}} / containers: {{.Containers}}'

Write-Host "[3/4] Пересобираю и поднимаю стек (новые теги bsl-ai-toolkit/*)"
# По умолчанию поднимается профиль без onec-data (слой данных opt-in: --profile data + ONEC_DATA_* в .env).
docker --context $Context compose -f server/docker-compose.yml up -d --build
# $ErrorActionPreference не ловит ненулевой exit нативного docker в PS 5.1 — проверяем явно, иначе [ok] будет ложным.
if ($LASTEXITCODE -ne 0) { throw "docker compose up завершился с кодом $LASTEXITCODE — стек НЕ пересобран (см. вывод выше)" }

Write-Host "[4/4] Итог: что поднято"
docker --context $Context ps --format '{{.Names}}  {{.Image}}  {{.Status}}'
Write-Host "[ok] central Docker-MCP пересобран. Клиентам менять ничего не нужно (URL прежний, сменились только теги образов)."
