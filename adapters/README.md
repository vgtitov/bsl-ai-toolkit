# Адаптеры под разные AI-агенты

Тонкие адаптеры поверх `core/` (единый источник истины). Claude Code — эталон, остальные — маппинг путей/ключей.
Генерация: `sh build.sh` (использует rulesync, если установлен; иначе минимальный фолбэк симлинками).

| Агент | Файл правил | MCP-конфиг (ключ) | Статус |
|---|---|---|---|
| **claude** | `CLAUDE.md` (`@AGENTS.md` + спец) | `.mcp.json` (`mcpServers`) | ✅ эталон (полный) |
| **codex** | `AGENTS.md` (нативно) | `~/.codex/config.toml` (TOML) | ✅ фолбэк (AGENTS.md) |
| **gemini** | `GEMINI.md` → AGENTS.md | `.gemini/settings.json` (`mcpServers`) | ✅ фолбэк (симлинк) |
| **cursor** | `.cursor/rules/*.mdc` | `.cursor/mcp.json` (`mcpServers`) | ⚙️ через rulesync |
| **copilot** | `.github/copilot-instructions.md` | `.vscode/mcp.json` (**`servers`**) | ⚙️ через rulesync |
| **cline** | `.clinerules/` | `cline_mcp_settings.json` | ⚙️ через rulesync |
| **windsurf** | `.windsurf/rules/*.md` | `~/.codeium/windsurf/mcp_config.json` | ⚙️ ручной адаптер |
| **aider** | `CONVENTIONS.md` + `.aider.conf.yml` (`read: AGENTS.md`) | — | ⚙️ ручной адаптер |

Три оси переносимости: **AGENTS.md** (правила) + **SKILL.md** (навыки) + **MCP** (инструменты).
Claude Code НЕ читает AGENTS.md нативно → связка через `@AGENTS.md` в `CLAUDE.md`.

## Провайдеры LLM (важно для РФ / санкций)
Toolkit **agent-agnostic и provider-agnostic** — он не привязан к конкретной модели/провайдеру. Практика 2026:
- **Claude Code** (Anthropic, Sonnet 4.5 — лучший по BSL) и **Cursor** — основные агенты.
- Доступ из РФ ограничен (`unsupported_region`): подключение через **прокси-обёртку Anthropic API** / **RU-VDS с оплатой
  в рублях + свой прокси** / VPN на роутере (XRay/VLESS). Настройку прокси задаёшь на уровне окружения агента (env),
  toolkit её не диктует.
- Отечественные (**GigaChat**, **YandexGPT**) и китайские (DeepSeek/Qwen self-host) — как fallback; по кодингу BSL пока
  заметно слабее. Toolkit с ними работает, если агент/IDE их поддерживает.

Детали окружения и доступа — `docs/AI_INSTALL_GUIDE.md` и `docs/ACCESS_SETUP.md`.

## Как добавить нового агента
1. Убедись, что правила есть в `core/AGENTS.md`, навыки в `core/skills/`, инструменты в `core/mcp/servers.json`.
2. Добавь ветку в `build.sh` (маппинг файла правил + формата MCP-конфига) или запись в `.rulesync/`.
3. Положи агент-специфику (если есть) в `adapters/<agent>/`. Не форкай ядро — только маппинг.
4. Прогони `sh build.sh <agent>` и проверь, что агент видит правила/скиллы/MCP.
