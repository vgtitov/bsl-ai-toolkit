# Центральный MCP чтения кода 1С в Docker

Зачем: один контейнер отдаёт MCP по HTTP — несколько разработчиков читают код через него, не держа MCP локально.
Для команды/нескольких баз это удобнее, чем stdio у каждого. Локально (один разработчик) проще оставить stdio.

Схема: `erp-1c` отдаёт MCP по SSE **только во внутреннюю сеть** compose; наружу публикуется сервис `proxy`
(Caddy) с проверкой bearer-токена. Так центр не выставлен без авторизации.

## Запуск
1. Положи клоны конфигураций/расширений 1С в каталог исходников (на хосте).
2. Скопируй `.env.example` → `.env`, заполни:
   - `ONEC_SRC_DIR` — путь к каталогу исходников на хосте;
   - `ERP_BEARER_TOKEN` — общий секрет (сгенерируй: `openssl rand -hex 32`);
   - `ERP_PUBLIC_PORT` — порт наружу (по умолчанию 8000).
3. Подними сервис:
```bash
docker compose up -d --build
```
4. Проверь: `docker compose ps` — `proxy` слушает `:8000` наружу, `erp-1c` только внутри сети, `/src` смонтирован read-only.

## Подключение Claude Code к центральному MCP
В `.mcp.json` — HTTP с заголовком авторизации:
```json
{
  "mcpServers": {
    "erp-1c": {
      "type": "sse",
      "url": "http://<хост>:8000/sse",
      "headers": { "Authorization": "Bearer <ERP_BEARER_TOKEN>" }
    }
  }
}
```
(хост — где поднят контейнер; в локальной отладке `localhost`.) Без верного токена Caddy отдаёт 401.

## Конфиг слоёв/scope/режимов
В контейнер пробрасывается `config/layers.example.toml` (env `ONEC_LAYERS_CONFIG`). Это generic-дефолт —
каждый слой тегируется именем репозитория, scope `all`. Своя локализация = СВОЙ файл конфига
(`ONEC_LAYERS_CONFIG=./config/layers.local.toml` в `.env`), код менять не нужно. Схема — в самом example-файле.

## Обновление кода
Исходники монтируются read-only с хоста — обновляй их `git pull` на хосте; контейнер перечитывает корни при
рестарте (`docker compose restart erp-1c`).

## Транспорт
`erp_mcp.py` выбирает транспорт по env `MCP_TRANSPORT` (`stdio` по умолчанию; `sse`/`streamable-http` в контейнере),
`MCP_HOST`/`MCP_PORT`. Так один и тот же код работает и локально (stdio), и как сервис (HTTP).

## Безопасность
- Контейнер читает код read-only, ничего не пишет.
- Наружу торчит только Caddy с bearer-авторизацией; сам `erp-1c` в host-сеть не публикуется (`expose`, не `ports`).
- Токен — в `.env` (в репозиторий не коммитится); ротация = поменять `ERP_BEARER_TOKEN` и `docker compose up -d`.
- Перед выставлением за пределы доверенной сети — добавь TLS (Caddy умеет автоматически по доменному имени).
- Никаких секретов в образ не класть; токены интеграций — не сюда.

## Тесты
```bash
uv run --with mcp --with pytest pytest tests/ -q
```
Проверяют discovery/search/find/read и конфиг слоёв на временном «репозитории». Гонять перед изменениями erp_mcp.py.

## Ручная обкатка HTTP-транспорта
`tests/smoke_http_client.py` — не часть pytest: подключается к живому сервису по SSE, перечисляет инструменты,
зовёт `list_modules`/`find_object`/`search_1c` на реальном наборе репозиториев. Если задан `ERP_BEARER_TOKEN` —
шлёт заголовок авторизации (проверка центра за Caddy). Подними сервер и натрави клиент:
```bash
# вариант без Docker (тот же код, без auth)
ONEC_SRC_DIR=/path/to/1c-src MCP_TRANSPORT=sse MCP_HOST=127.0.0.1 MCP_PORT=8000 uv run --with mcp mcp/erp_mcp.py
uv run --with mcp tests/smoke_http_client.py http://127.0.0.1:8000/sse

# вариант через docker-compose (с auth)
ERP_BEARER_TOKEN=<токен из .env> uv run --with mcp tests/smoke_http_client.py http://127.0.0.1:8000/sse
```
Обкатано вживую на боевом наборе (11 слоёв): auth (401 без токена / проход с токеном), discovery, search,
find_object и list_modules отдаются по HTTP сквозь Caddy корректно.

> Заметка о производительности на Windows: bind-mount исходников в Docker Desktop под Windows медленный —
> тяжёлые `search_1c` (ripgrep) и `find_object` (обход дерева) по большому набору репозиториев идут долго.
> На боевом Linux-сервере с нативной ФС этого нет. Для локальной отладки под Windows удобнее stdio.
