#!/usr/bin/env bash
# Обновление рабочего места ПОСЛЕ первичного onboard: git pull + доставка скиллов в ~/.claude/skills
# + подсказки, чего не хватает в .env. Повторный onboard НЕ нужен — он для первой установки.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILLS_DST="$HOME/.claude/skills"

echo "[1/3] git pull"
git -C "$REPO_ROOT" pull --ff-only

echo "[2/3] скиллы пакета -> ~/.claude/skills"
mkdir -p "$SKILLS_DST"
for d in "$REPO_ROOT"/core/skills/*/; do
  [ -d "$d" ] || continue
  s="$(basename "$d")"
  rm -rf "${SKILLS_DST:?}/$s"     # чистим, чтобы удалённые файлы не оставались
  cp -R "$d" "$SKILLS_DST/$s"
  echo "  [ok] $s"
done

echo "[3/3] проверка .env (новые переменные из .env.example)"
if [ -f "$REPO_ROOT/.env" ]; then
  missing=$(comm -23 \
    <(grep -oE '^[A-Z0-9_]+=' "$REPO_ROOT/.env.example" | sort -u) \
    <(grep -oE '^[A-Z0-9_]+=' "$REPO_ROOT/.env" | sort -u) | tr -d '=' || true)
  if [ -n "$missing" ]; then
    echo "  [!] в .env.example появились переменные, которых нет в твоём .env:"
    echo "$missing" | sed 's/^/      /'
    echo "      (описание — в .env.example; пустые значения тоже допустимы)"
  else
    echo "  [ok] .env покрывает все переменные шаблона"
  fi
else
  echo "  [!] .env не найден — скопируй .env.example в .env и заполни"
fi

echo "Готово. Перезапусти Claude Code, чтобы подхватились обновлённые скиллы."
