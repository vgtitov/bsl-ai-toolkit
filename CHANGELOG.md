# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/). Версии — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Changed
- **Переименование `claude-1c-toolkit` → `bsl-ai-toolkit`** и переход на **AI-агностик** архитектуру: правила больше
  не привязаны к Claude Code. Единый источник `core/` (`AGENTS.md` + `skills/` + `mcp/servers.json`), из него
  `sh build.sh` генерирует конфиги под конкретных агентов. Старый корневой `CLAUDE.md` теперь генерируется как
  `@AGENTS.md` + Claude-специфика.

### Added
- Ядро `core/` (общее для всех AI-агентов): `AGENTS.md` (правила: спроси-инструмент, слои контура, гейт метаданных,
  производительность, безопасность), `core/skills/` (6 скиллов: `1c-dev` / `1c-analyst` / `1c-metadata` /
  `1c-admin-devops` / `1c-dba` / `1c-expert`), `core/mcp/servers.json` (профиль MCP).
- Мульти-агентность: `adapters/` (claude — эталон; codex/gemini — фолбэк; cursor/copilot/cline/windsurf/aider —
  rulesync/ручные) + `build.sh`. Оси переносимости: AGENTS.md + SKILL.md + MCP. Раздел про провайдеров LLM под РФ/санкции.
- Слой доступа к данным живой ИБ `onec-data` (MCP, 10 инструментов) + расширение 1С `ai_debug` (read-only под
  пользователем, честный RLS, маскирование ПДн, privileged за fail-closed-гейтом); локальный runner-EPF без SSH.
- OSS-обвязка: `CONTRIBUTING`, `CODE_OF_CONDUCT`, `SECURITY`, шаблоны Issue/PR, бейджи в README, CI (GitHub Actions),
  MIT `LICENSE`.

### Существовало ранее (сводно)
- Движок правки метаданных `onec_metadata` (Конфигуратор-XML + EDT `.mdo`/`.dcs`, byte-perfect, round-trip verify,
  ~215 тестов) + CLI `bin/1c-meta`.
- MCP: `onec-code` (реальный код 1С), `bsl-ls` (диагностики BSL, в комплекте), `onec-ops` (эксплуатация: ТЖ/APDEX/
  Zabbix/Prometheus).
- Зашитые проверки: `bsl_guard.py` (межпроцедурный детектор «запрос в цикле»), гейты против поломки СКД и дрейфа свойств;
  git-хуки (commit-msg + pre-commit); лестница тестирования (docs/testing-ladder.md), `doctor.py` (health-check окружения).

[Unreleased]: https://github.com/vgtitov/bsl-ai-toolkit
