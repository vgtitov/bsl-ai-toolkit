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
for s in 1c-dev 1c-analyst; do
  if [ -d "$TEAM_DIR/skills/$s" ]; then cp -R "$TEAM_DIR/skills/$s" "$SKILLS_DST/"; ok "скилл $s"; else warn "нет $TEAM_DIR/skills/$s"; fi
done

say "3/6 Каталог исходников 1С: $SRC_DIR"
mkdir -p "$SRC_DIR"
warn "Склонируй СВОИ репозитории конфигураций/расширений в $SRC_DIR (MCP подхватит их авто)."

say "4/6 Деплой MCP чтения кода"
cp "$TEAM_DIR/mcp/erp_mcp.py" "$SRC_DIR/erp_mcp.py"; ok "erp_mcp.py -> $SRC_DIR"

say "5/7 Профиль .mcp.json и CLAUDE.md -> $WORKDIR"
cp "$TEAM_DIR/mcp/dev.mcp.json" "$WORKDIR/.mcp.json"
cp "$TEAM_DIR/CLAUDE.md" "$WORKDIR/CLAUDE.md"; ok "профиль + правила"
# Источник erp-1c: local по умолчанию; central — если задан ERP1C_URL и центр доступен (подключение — set_token, ниже).
if command -v uv >/dev/null 2>&1; then
  uv run "$TEAM_DIR/scripts/switch_erp.py" --mode "${ERP1C_MODE:-auto}" --workdir "$WORKDIR" && ok "switch_erp (${ERP1C_MODE:-auto})" || warn "switch_erp пропущен"
else warn "uv не найден — switch_erp пропущен (erp-1c останется локальным)"; fi

say "6/8 Переменные окружения"
warn "Добавь в ~/.zshrc (или ~/.bashrc): export ONEC_SRC_DIR=\"$SRC_DIR\""
warn "Центральный erp-1c (если развёрнут в команде) — подключение одной командой (токен у тимлида): bash scripts/set_token.sh"

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
[ ] Self-test: спроси erp-1c (find_object) — ответ по коду из $SRC_DIR (или из центра, если подключён).
EOF
