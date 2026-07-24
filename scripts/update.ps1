# Обновление рабочего места ПОСЛЕ первичного onboard: git pull + доставка скиллов в ~/.claude/skills
# + подсказки, чего не хватает в .env. Повторный onboard НЕ нужен — он для первой установки.
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SkillsDst = Join-Path $env:USERPROFILE '.claude\skills'

Write-Host "[1/3] git pull"
git -C $RepoRoot pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "git pull не прошёл" }

Write-Host "[2/3] скиллы пакета -> ~/.claude/skills"
New-Item -ItemType Directory -Force -Path $SkillsDst | Out-Null
Get-ChildItem -Directory (Join-Path $RepoRoot 'core\skills') | ForEach-Object {
  $to = Join-Path $SkillsDst $_.Name
  if (Test-Path $to) { Remove-Item $to -Recurse -Force }   # чистим, чтобы удалённые файлы не оставались
  Copy-Item $_.FullName $SkillsDst -Recurse -Force
  Write-Host "  [ok] $($_.Name)" -ForegroundColor Green
}

Write-Host "[3/3] проверка .env (новые переменные из .env.example)"
$envFile = Join-Path $RepoRoot '.env'
$example = Join-Path $RepoRoot '.env.example'
if (Test-Path $envFile) {
  $keys = { param($p) (Get-Content $p) | Where-Object { $_ -match '^[A-Z0-9_]+=' } |
            ForEach-Object { ($_ -split '=', 2)[0] } | Sort-Object -Unique }
  $missing = @(Compare-Object (& $keys $example) (& $keys $envFile) |
               Where-Object SideIndicator -eq '<=' | ForEach-Object InputObject)
  if ($missing.Count -gt 0) {
    Write-Host "  [!] в .env.example появились переменные, которых нет в твоём .env:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "      $_" -ForegroundColor Yellow }
    Write-Host "      (описание — в .env.example; пустые значения тоже допустимы)"
  } else {
    Write-Host "  [ok] .env покрывает все переменные шаблона" -ForegroundColor Green
  }
} else {
  Write-Host "  [!] .env не найден — скопируй .env.example в .env и заполни" -ForegroundColor Yellow
}

Write-Host "Готово. Перезапусти Claude Code, чтобы подхватились обновлённые скиллы."
