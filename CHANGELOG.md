# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/). Версии — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

## [2.0.1] - 2026-07-24

### Fixed
- **Центральный `docker compose` не поднимался без тест-ИБ.** Сервис `onec-data` в v2.0.0 стал обязательным
  (`ONEC_DATA_BASE_URL:?` + `proxy depends_on onec-data`), из-за чего `compose up` падал везде, где слой данных не
  провизинен (центр на хосте, Work PC). Теперь `onec-data` — **opt-in через compose-профиль** `data`: дефолтный
  `compose up` поднимает `onec-code`/`onec-ops`/`proxy` (как раньше), а слой данных включается явно
  `docker compose --profile data up -d` при заданных `ONEC_DATA_*`. `ONEC_DATA_BASE_URL` смягчён с `:?` на `:-`,
  зависимость `proxy → onec-data` убрана (иначе compose поднимал бы сервис вопреки профилю).

## [2.0.0] - 2026-07-24

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
- Документация: `docs/CONCEPT.md` (методология, артефакты ЧТЗ/тех-проект, связь с курсами), `docs/TOOLS.md` (каталог
  инструментов), `docs/AGENT_SETUP.md` (настройка руками агента), `docs/ACCESS_SETUP.md` (доступ к LLM под санкциями),
  `docs/RELEASING.md` (процесс релизов), `docs/TEAM_MIGRATION.md` (миграция командного форка).
- Интеграция с 1C:EDT: `docs/EDT_SETUP.md` — опциональный MCP-плагин EDT (живой доступ к рабочему пространству), установка/подключение. Агенты Kiro и Roo Code добавлены в матрицу адаптеров.
- Кросс-платформенность: `build.ps1` (Windows-паритет `build.sh`), матрица ОС в README; `onboard.ps1` под новую структуру.
- Релизы: workflow `release.yml` (тег `vX.Y.Z` → GitHub Release + исходник расширения `ai_debug`); политика версий в `RELEASING.md`.

### Fixed
- CI: тесты падали в чистом окружении (`ModuleNotFoundError: lxml`) — в прогон добавлены зависимости движка
  `lxml`/`openpyxl`/`xlrd`, запуск изолирован (`--no-project`).

### Removed
- Устаревший личный `HANDOFF.md` из корня; служебная папка `docs/superpowers/` (спека перенесена в `docs/design/`).

### Added (слой применения и проверки, влито из onec-data)
- **Автономный сервер** (`onec_metadata/apply/standalone.py`) — HTTP-доступ к файловой базе (веб-клиент, OData,
  HTTP-сервисы) без веб-сервера и публикации администратором. Проверять слой данных можно сразу на файловой копии.
- **CLI лестницы тестирования** `scripts/onec_verify.py` — `check` (синтаксис платформой) и `smoke` (серверный BSL на
  реальных данных) одной командой, транспорт local или по `--host`.
- **Предполёт «защиты от опасных действий»** (`onec_metadata/apply/protection.py`) — ловит зависание батч-сеанса до
  подключения, локально по файлу настроек.
- **Готовность удалённого хоста** (`onec_metadata/apply/remote.py`) — проверки только на чтение перед прогоном на сервере.
- **Обработки и расширения** (`onec_metadata/apply/external.py`) — сборка/разбор `.epf`/`.erf` и `.cfe` через Конфигуратор.
- `docs/setup-actions-required.md` — что должен сделать человек (публикация, флаг защиты, платформа), со ссылками из инструментов.

### Существовало ранее (сводно)
- Движок правки метаданных `onec_metadata` (Конфигуратор-XML + EDT `.mdo`/`.dcs`, byte-perfect, round-trip verify,
  ~215 тестов) + CLI `bin/1c-meta`.
- MCP: `onec-code` (реальный код 1С), `bsl-ls` (диагностики BSL, в комплекте), `onec-ops` (эксплуатация: ТЖ/APDEX/
  Zabbix/Prometheus).
- Зашитые проверки: `bsl_guard.py` (межпроцедурный детектор «запрос в цикле»), гейты против поломки СКД и дрейфа свойств;
  git-хуки (commit-msg + pre-commit); лестница тестирования (docs/testing-ladder.md), `doctor.py` (health-check окружения).

[Unreleased]: https://github.com/vgtitov/bsl-ai-toolkit
