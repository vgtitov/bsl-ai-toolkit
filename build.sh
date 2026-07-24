#!/bin/sh
# build.sh — сгенерировать конфиги под разные AI-агенты из ЕДИНОГО ИСТОЧНИКА core/.
# Три оси переносимости: AGENTS.md (правила) + SKILL.md (навыки) + MCP (инструменты).
#
# Стратегия: если установлен rulesync — используем его как движок (20+ агентов). Иначе — минимальный
# фолбэк симлинками/копиями, которого достаточно для Claude Code + AGENTS.md-совместимых (Codex/Cursor/Gemini).
#
# Запуск:  sh build.sh            # собрать всё
#          sh build.sh claude     # только Claude Code
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
TARGET="${1:-all}"

link_or_copy() { # src dst  — симлинк, на Windows/без симлинков — копия
  src="$1"; dst="$2"
  mkdir -p "$(dirname "$dst")"
  rm -f "$dst"
  ln -s "$src" "$dst" 2>/dev/null || cp -f "$ROOT/$src" "$dst"
}

build_claude() {
  echo "[claude] CLAUDE.md (@AGENTS.md) + .claude/skills + .claude/settings.json + .mcp.json"
  # AGENTS.md в корне — канон для AGENTS.md-нативных агентов (Codex/Cursor/Gemini/Jules...)
  cp -f core/AGENTS.md AGENTS.md
  # CLAUDE.md импортирует AGENTS.md (Claude Code не читает AGENTS.md нативно) + claude-специфика
  cp -f adapters/claude/CLAUDE.md CLAUDE.md
  # Скиллы
  rm -rf .claude/skills && mkdir -p .claude/skills
  cp -R core/skills/. .claude/skills/
  # Хуки (PostToolUse — BSL guard)
  mkdir -p .claude
  cp -f adapters/claude/settings.json .claude/settings.json
  # MCP-профиль (ключ mcpServers — как есть)
  cp -f core/mcp/servers.json .mcp.json
}

build_gemini()  { echo "[gemini] GEMINI.md → AGENTS.md";  cp -f core/AGENTS.md AGENTS.md; link_or_copy core/AGENTS.md GEMINI.md; }
build_codex()   { echo "[codex] AGENTS.md (канон, читается нативно)"; cp -f core/AGENTS.md AGENTS.md; }

build_rulesync() {
  if command -v rulesync >/dev/null 2>&1 || command -v npx >/dev/null 2>&1; then
    echo "[rulesync] генерация под Cursor/Copilot/Cline/Windsurf/… (правила+MCP)"
    RS="rulesync"; command -v rulesync >/dev/null 2>&1 || RS="npx -y rulesync"
    $RS generate --targets claudecode,cursor,copilot,cline,geminicli,codexcli 2>/dev/null \
      || echo "[rulesync] пропущено (нет конфига .rulesync/ или сети) — фолбэк уже собран"
  else
    echo "[rulesync] не установлен — фолбэк (Claude/Gemini/Codex) собран симлинками. Для остальных: npm i -g rulesync"
  fi
}

case "$TARGET" in
  claude) build_claude ;;
  gemini) build_gemini ;;
  codex)  build_codex ;;
  all)    build_claude; build_gemini; build_codex; build_rulesync ;;
  *) echo "usage: sh build.sh [all|claude|gemini|codex]"; exit 1 ;;
esac
echo "[ok] build: $TARGET"
