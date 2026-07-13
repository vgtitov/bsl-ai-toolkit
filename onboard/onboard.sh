#!/usr/bin/env bash
# Bootstrap окружения Claude Code для разработки 1С. Идемпотентный. Org-agnostic. macOS/Linux.
# Запуск:  bash onboard/onboard.sh   [--srcdir <путь>]
set -euo pipefail
WORKDIR="$(pwd)"; SRC_DIR="${ONEC_SRC_DIR:-$HOME/erp-src}"
while [ $# -gt 0 ]; do case "$1" in
  --srcdir) SRC_DIR="$2"; shift 2;;
  --workdir) WORKDIR="$2"; shift 2;;
  *) echo "неизвестный аргумент: $1"; exit 1;;
esac; done

say(){ printf '\n== %s ==\n' "$1"; }
ok(){ printf '  [ok] %s\n' "$1"; }
warn(){ printf '  [!] %s\n' "$1"; }

TEAM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKILLS_DST="$HOME/.claude/skills"

say "1/6 Пререквизиты"
for c in git uv java claude rg; do
  if command -v "$c" >/dev/null 2>&1; then ok "$c"; else warn "$c не найден (доустанови)"; fi
done

say "2/6 Скиллы -> ~/.claude/skills"
mkdir -p "$SKILLS_DST"
# generic: доедет всё, что лежит в skills/ (1c-dev, 1c-analyst, 1c-metadata, будущие)
found=0
for d in "$TEAM_DIR"/skills/*/; do
  [ -d "$d" ] || continue
  cp -R "$d" "$SKILLS_DST/"; ok "скилл $(basename "$d")"; found=1
done
[ "$found" = 1 ] || warn "в $TEAM_DIR/skills/ нет скиллов"

say "3/6 Каталог исходников 1С: $SRC_DIR"
mkdir -p "$SRC_DIR"
warn "Склонируй СВОИ репозитории конфигураций/расширений в $SRC_DIR (MCP подхватит их авто)."

say "4/6 Деплой MCP чтения кода"
cp "$TEAM_DIR/mcp/onec_mcp.py" "$SRC_DIR/onec_mcp.py"; ok "onec_mcp.py -> $SRC_DIR"
if [ -f "$SRC_DIR/erp_mcp.py" ]; then rm -f "$SRC_DIR/erp_mcp.py"; ok "удалён легаси erp_mcp.py"; fi

say "5/7 Профиль .mcp.json и CLAUDE.md -> $WORKDIR"
cp "$TEAM_DIR/mcp/dev.mcp.json" "$WORKDIR/.mcp.json"
cp "$TEAM_DIR/CLAUDE.md" "$WORKDIR/CLAUDE.md"; ok "профиль + правила"
# Источник onec-code: local по умолчанию; central — если задан ONEC_MCP_URL и центр доступен (подключение — set_token, ниже).
if command -v uv >/dev/null 2>&1; then
  uv run "$TEAM_DIR/scripts/switch_source.py" --mode "${ONEC_MCP_MODE:-auto}" --workdir "$WORKDIR" && ok "switch_source (${ONEC_MCP_MODE:-auto})" || warn "switch_source пропущен"
else warn "uv не найден — switch_source пропущен (onec-code останется локальным)"; fi

say "6/8 Переменные окружения"
warn "Добавь в ~/.zshrc (или ~/.bashrc): export ONEC_SRC_DIR=\"$SRC_DIR\""
warn "Все переменные и их назначение — в .env.example (скопируй в .env и заполни под контур). Полная таблица: docs/mcp-deploy-and-use.md."
warn "Для onec-metadata (bin/1c-meta, правки метаданных по SSH): опц. ONEC_1CV8_BIN и ONEC_REMOTE_WORKDIR; нужны ssh/scp/tar на машине."
warn "Центральный onec-code (если развёрнут в команде) — подключение одной командой (токен у тимлида): bash scripts/set_token.sh"

say "7/8 Платформа 1С и BSL-инструменты (поиск + скачивание свободных jar'ов)"
if command -v uv >/dev/null 2>&1; then
  uv run "$TEAM_DIR/scripts/detect_tools.py" --install && ok "detect_tools: платформа/BSL найдены/скачаны, env прописаны (мост BSL_LS_MCP — из mcp/bsl_ls_mcp.py)" || warn "detect_tools пропущен — запусти: uv run scripts/detect_tools.py --install"
else warn "uv не найден — позже: python scripts/detect_tools.py --install"; fi

say "8/8 Git: коммиты без соавторства Claude (commit-msg хук)"
if command -v uv >/dev/null 2>&1; then
  uv run "$TEAM_DIR/scripts/install_git_hooks.py" && ok "commit-msg хук поставлен" || warn "install_git_hooks пропущен — вручную: uv run scripts/install_git_hooks.py"
else warn "uv не найден — поставь хук вручную: python scripts/install_git_hooks.py"; fi

say "Готово. Ручные шаги"
cat <<EOF
[ ] Проставь версии под свою конфигурацию в CLAUDE.md.
[ ] Заполни skills/1c-dev/references/conventions-template.md под свой проект.
[ ] BSL-инструменты: detect_tools уже нашёл/скачал платформу/jar'ы и прописал env. Что [нет] — доложи и повтори uv run scripts/detect_tools.py (мост BSL_LS_MCP — из репо mcp/bsl_ls_mcp.py).
[ ] Git-идентичность площадки и токен push — см. docs/git.md.
[ ] Перезапусти Claude Code в $WORKDIR -> подтверди MCP.
[ ] Self-test: спроси onec-code (find_object) — ответ по коду из $SRC_DIR (или из центра, если подключён).
EOF
