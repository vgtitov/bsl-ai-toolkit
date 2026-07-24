# Миграция командного форка на новую структуру (для Work PC)

Публичный toolkit переименован в `bsl-ai-toolkit` и переведён на AI-агностик структуру (`core/` + `adapters/` +
`build.sh`). Командный форк (`claude-1c-team` на корпоративном GitLab) тянет ядро **вендорингом по списку путей** —
значит, эти пути надо синхронно поправить, иначе `sync-core` перестанет копировать. Push в корпоративный GitLab
делается **только с Work PC** (Mac до корпоративной сети не достаёт).

> Всё ниже выполняется на Work PC, где есть доступ к `gitlab.example.ru`. На Mac подготовлено, но не запушено.

## Что изменилось в toolkit (влияет на вендоринг)
1. `skills/` → **`core/skills/`** (навыки переехали в единый источник).
2. Появились `core/AGENTS.md`, `core/mcp/servers.json`, `adapters/`, `build.sh` / `build.ps1`.
3. `mcp/dev.mcp.json` остался как совместимый алиас, канон профиля — `core/mcp/servers.json`.
4. Ссылка на toolkit сменилась: `…/claude-1c-toolkit` → `…/bsl-ai-toolkit` (старый адрес редиректит, но лучше обновить явно).
5. Корневые `CLAUDE.md` / `AGENTS.md` теперь генерируются из `core/` через `build.sh`.

## Правки в командном форке (`claude-1c-team`)
1. **`scripts/sync-core`** (и `.ps1`) — в списке копируемых путей заменить источник навыков и добавить движок метаданных:
   - `skills/…` → `core/skills/…` там, где копируются навыки из toolkit (если копируются оттуда, а не из `~/.claude`).
   - добавить в список: `core/AGENTS.md`, `core/mcp/servers.json`, `build.sh`, `build.ps1`, `adapters/claude/**`.
   - **P6 (давно ждёт):** добавить вендоринг `onec_metadata/**` и `bin/1c-meta`.
   - дефолтный адрес toolkit: `bsl-ai-toolkit`.
2. **`scripts/sync-skills`** (и `.ps1`) — если тянет навыки из toolkit, путь `skills/` → `core/skills/`. Навык
   метаданных: добавить `1c-metadata` (в команде можно под своим именем).
3. **Онбординг команды** — если у форка свой `onboard`, свериться с новым: навыки берутся из `core/skills`, профиль
   `.mcp.json` собирается `build.sh`/`build.ps1` из `core/mcp/servers.json` и не коммитится (локальный).

## Порядок на Work PC
1. `git -C <клон bsl-ai-toolkit> pull` — подтянуть новую структуру (проверить: есть `core/`, `build.ps1`).
2. Проверить PowerShell-скрипты, которые на Mac не запускались: `powershell -File build.ps1 claude`,
   `powershell -File onboard/onboard.ps1` (в тестовом каталоге) — синтаксис и сборка.
3. В форке `claude-1c-team` внести правки из раздела выше, прогнать `sync-core` из публичного toolkit, убедиться, что
   `onec_metadata/**` и `core/skills/**` доехали.
4. Прогнать тесты форка (если есть), затем `git push` в корпоративный GitLab, поставить тег-релиз.
5. Пересобрать общий Docker-MCP (если он поднят на Work PC или Server PC) с новыми тегами образов
   (`bsl-ai-toolkit/onec-*`): `docker compose -f server/docker-compose.yml up -d --build`. Ночью простой допустим.

## Что сказать команде
Коротко: toolkit теперь называется bsl-ai-toolkit и работает не только с Claude, но и с Cursor, Copilot и другими.
Перед работой сделайте `git pull` и повторный onboard. Правила и навыки лежат в `core/`, руками правится `core/`, а
конфиги пересобираются командой сборки. Ничего из личных настроек не потеряется, профиль подключения остаётся локальным.
