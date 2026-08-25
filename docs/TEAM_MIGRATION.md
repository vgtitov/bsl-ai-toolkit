# Миграция командного форка на новую структуру (для Work PC)

Публичный toolkit переименован в `bsl-ai-toolkit` и переведён на AI-агностик структуру (`core/` + `adapters/` +
`build.sh`). Командный форк (`claude-1c-team` на корпоративном GitLab) тянет ядро **вендорингом по списку путей** —
значит, эти пути надо синхронно поправить, иначе `sync-core` перестанет копировать. Push в корпоративный GitLab
делается **только с Work PC** (Mac до корпоративной сети не достаёт).

> Всё ниже выполняется на Work PC, где есть доступ к `gitlab.<компания>`. На Mac подготовлено, но не запушено.

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
5. Пересобрать общий Docker-MCP (образы переименованы в `bsl-ai-toolkit/onec-*`). Он поднят на удалённом хосте за
   docker context (на Work PC это context `corp` = `ssh://corp-docker`), доступен с Work PC при включённом VPN.
   Готовый скрипт делает всё одной командой (обновит клон из публичного GitHub, проверит доступ, пересоберёт, покажет итог):
   ```powershell
   .\server\rebuild-central.ps1 -Context corp -RepoDir C:\dev\<путь>\claude-1c-toolkit
   ```
   Клиентам менять ничего не нужно — URL прежний, сменились только теги образов. Ночью простой допустим.

## Чек-лист задач (кратко)
- [ ] Work PC: `git pull` клона toolkit (публичный GitHub, без корп-авторизации) → проверить, что есть `core/`, `build.ps1`.
- [ ] Work PC: проверить PowerShell (`build.ps1 claude`, `onboard.ps1` в тест-каталоге) — синтаксис и сборка. (build.ps1 уже проверен: OK.)
- [ ] Team-форк: правки sync-скриптов (`skills/`→`core/skills/`, вендоринг `onec_metadata/**`+`bin/1c-meta`, дефолт-URL `bsl-ai-toolkit`), прогнать вендоринг, `git push` в корп-GitLab (нужно интерактивное окно авторизации), тег-релиз.
- [ ] Docker-MCP: `server\rebuild-central.ps1` при включённом VPN.
- [ ] Оповестить команду (текст в конце этого файла).

## Что сказать команде
Коротко: toolkit теперь называется bsl-ai-toolkit и работает не только с Claude, но и с Cursor, Copilot и другими.
Перед работой сделайте `git pull` и повторный onboard. Правила и навыки лежат в `core/`, руками правится `core/`, а
конфиги пересобираются командой сборки. Ничего из личных настроек не потеряется, профиль подключения остаётся локальным.
