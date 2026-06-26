# Центральный MCP чтения кода 1С в Docker

> **Разворачиваешь центр?** Пошаговый исполняемый runbook «под ключ» (с развилкой «разворачивать или нет») —
> `docs/deploy-center.md`. Этот файл — **справочник по стеку** (что внутри, безопасность, тюнинг, обкатка).

Зачем: один контейнер отдаёт MCP по HTTP — несколько разработчиков читают код через него, не держа MCP локально.
Для команды/нескольких баз это удобнее, чем stdio у каждого. Локально (один разработчик) проще оставить stdio.

Схема: `onec-code` отдаёт MCP по SSE **только во внутреннюю сеть** compose; наружу публикуется сервис `proxy`
(Caddy) с проверкой bearer-токена. Так центр не выставлен без авторизации.

> **Серверный стек живёт в `server/`** (`server/Dockerfile`, `server/docker-compose.yml`, `server/caddy/`,
> `server/.env.example`). Движок `mcp/onec_mcp.py` — общий с клиентом, поэтому контекст сборки оставлен корнем репо
> (`build.context: ..`). Все команды ниже — из каталога `server/`.

## Запуск
1. Положи клоны конфигураций/расширений 1С в каталог исходников (на хосте).
2. Скопируй `server/.env.example` → `server/.env`, заполни:
   - `ONEC_SRC_DIR` — путь к каталогу исходников на хосте (лучше абсолютный);
   - `ONEC_BEARER_TOKEN` — общий секрет (сгенерируй: `openssl rand -hex 32`);
   - `ONEC_PUBLIC_PORT` — порт наружу (по умолчанию 8000).
3. Подними сервис:
```bash
cd server && docker compose up -d --build
```
4. Проверь: `docker compose ps` — `proxy` слушает `:8000` наружу, `onec-code` только внутри сети, `/src` смонтирован read-only.

## Подключение Claude Code к центральному MCP
В `.mcp.json` — HTTP с заголовком авторизации:
```json
{
  "mcpServers": {
    "onec-code": {
      "type": "sse",
      "url": "http://<хост>:8000/sse",
      "headers": { "Authorization": "Bearer <ONEC_BEARER_TOKEN>" }
    }
  }
}
```
(хост — где поднят контейнер; в локальной отладке `localhost`.) Без верного токена Caddy отдаёт 401.

## Переключение клиента local ↔ central (`scripts/switch_source.py`)
Чтобы не править `.mcp.json` руками, переключение источника onec-code автоматизировано — скрипт переписывает ТОЛЬКО
блок `onec-code`, остальные серверы не трогает:
```bash
uv run scripts/switch_source.py --mode auto      # auto|local|central; по умолчанию auto
```
- **local** — stdio-движок читает клоны из `ONEC_SRC_DIR` (блок берётся из `mcp/<profile>.mcp.json`).
- **central** — SSE к центру; URL/токен — env-плейсхолдеры `${ONEC_MCP_URL}`/`${ONEC_MCP_TOKEN}` (секрет в файл НЕ пишется).
- **auto** — если `ONEC_MCP_URL` задан и центр доступен по TCP → central, иначе local (безопасный откат).

Сценарий безболезненного перехода: команда стартует на `local` (центр ещё не поднят). Когда центр готов — раздать
`ONEC_MCP_URL` и `ONEC_MCP_TOKEN` (env) и выполнить `switch_source.py --mode auto` (его может запустить и сам ассистент),
затем перезапустить Claude Code. `.mcp.json` остаётся валидным в обоих режимах; токен живёт в окружении, не в файле.

## Подключение разработчика к центру одной вставкой (`scripts/set_token.py`-helper)

Чтобы разработчику не возиться с `setx`/`export` руками, в паре к `switch_source.py` идёт **`scripts/set_token.ps1` /
`scripts/set_token.sh`**: спрашивает токен скрыто, кладёт `ONEC_MCP_URL`/`ONEC_MCP_TOKEN`/`ONEC_MCP_MODE=auto` в окружение
пользователя и печатает готовый блок-инструкцию, который разработчик просто вставляет в Claude Code — дальше
ассистент сам проходит onboard → `switch_source --mode central` → self-test.

Пример (org-agnostic, URL обязателен — задаётся владельцем центра):
```powershell
# Windows (если PowerShell блокирует .ps1 политикой Restricted — запусти .cmd-обёртку: scripts\set_token.cmd ...)
.\scripts\set_token.ps1 -Url http://host:8000/sse              # спросит токен, профиль dev
.\scripts\set_token.ps1 -Url http://host:8000/sse -Profile analyst
```
```bash
# mac/Linux
bash scripts/set_token.sh --url http://host:8000/sse           # спросит токен
```

Принципы (те же, что у `switch_source`): секрет — **только в env пользователя**, никогда в файл/git/чат; в `.mcp.json`
попадает лишь плейсхолдер `${ONEC_MCP_TOKEN}`. Скрипт идемпотентен и не оставляет токен на экране (ввод скрыт).

**Локализация** вендорит этот helper как есть и при необходимости делает поверх тонкую обёртку со своими данными
(дефолтный URL центра, имена профилей, формулировку блока-инструкции под свои контуры) — ровно как `onboard`
и `switch_source`: ядро = механизм, локализация = данные.

## Конфиг слоёв/scope/режимов
В контейнер пробрасывается `../config/layers.example.toml` (env `ONEC_LAYERS_CONFIG`). Это generic-дефолт —
каждый слой тегируется именем репозитория, scope `all`. Своя локализация = СВОЙ файл конфига
(`ONEC_LAYERS_CONFIG=../config/layers.local.toml` в `server/.env`), код менять не нужно. Схема — в самом example-файле.

## Обновление кода
Исходники монтируются read-only с хоста — обновляй их `git pull` на хосте; контейнер перечитывает корни при
рестарте (`docker compose restart onec-code`).

## Транспорт
`onec_mcp.py` выбирает транспорт по env `MCP_TRANSPORT` (`stdio` по умолчанию; `sse`/`streamable-http` в контейнере),
`MCP_HOST`/`MCP_PORT`. Так один и тот же код работает и локально (stdio), и как сервис (HTTP).

## Безопасность
- Контейнер читает код read-only, ничего не пишет.
- Наружу торчит только Caddy с bearer-авторизацией; сам `onec-code` в host-сеть не публикуется (`expose`, не `ports`).
- Токен — в `server/.env` (в репозиторий не коммитится); ротация = поменять `ONEC_BEARER_TOKEN` и `cd server && docker compose up -d`.
- Перед выставлением за пределы доверенной сети — добавь TLS (Caddy умеет автоматически по доменному имени).
- Никаких секретов в образ не класть; токены интеграций — не сюда.

## Тесты
```bash
uv run --with mcp --with pytest pytest tests/ -q
```
Проверяют discovery/search/find/read и конфиг слоёв на временном «репозитории». Гонять перед изменениями onec_mcp.py.

## Ручная обкатка HTTP-транспорта
`tests/smoke_http_client.py` — не часть pytest: подключается к живому сервису по SSE, перечисляет инструменты,
зовёт `list_modules`/`find_object`/`search_1c` на реальном наборе репозиториев. Если задан `ONEC_BEARER_TOKEN` —
шлёт заголовок авторизации (проверка центра за Caddy). Подними сервер и натрави клиент:
```bash
# вариант без Docker (тот же код, без auth)
ONEC_SRC_DIR=/path/to/1c-src MCP_TRANSPORT=sse MCP_HOST=127.0.0.1 MCP_PORT=8000 uv run --with mcp mcp/onec_mcp.py
uv run --with mcp tests/smoke_http_client.py http://127.0.0.1:8000/sse

# вариант через docker-compose (с auth)
ONEC_BEARER_TOKEN=<токен из .env> uv run --with mcp tests/smoke_http_client.py http://127.0.0.1:8000/sse
```
Обкатано вживую на боевом наборе (11 слоёв): auth (401 без токена / проход с токеном), discovery, search,
find_object и list_modules отдаются по HTTP сквозь Caddy корректно.

> Заметка о производительности на Windows: bind-mount исходников в Docker Desktop под Windows медленный —
> тяжёлые `search_1c` (ripgrep) и `find_object` (обход дерева) по большому набору репозиториев идут долго.
> На боевом Linux-сервере с нативной ФС этого нет. Для локальной отладки под Windows удобнее stdio.
