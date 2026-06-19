# Центральный MCP чтения кода 1С в Docker

Зачем: один контейнер отдаёт MCP по HTTP — несколько разработчиков читают код через него, не держа MCP локально.
Для команды/нескольких баз это удобнее, чем stdio у каждого. Локально (один разработчик) проще оставить stdio.

## Запуск
1. Положи клоны конфигураций/расширений 1С в каталог исходников (на хосте), укажи его в `ONEC_SRC_DIR`.
2. Подними сервис:
```bash
ONEC_SRC_DIR=/path/to/1c-src docker compose up -d --build
```
Windows (PowerShell): `$env:ONEC_SRC_DIR="C:/1c-src"; docker compose up -d --build`
3. Проверь: контейнер `erp-1c` слушает `:8000`, исходники смонтированы в `/src` (read-only).

## Подключение Claude Code к центральному MCP
В `.mcp.json` вместо локального stdio-сервера — HTTP:
```json
{
  "mcpServers": {
    "erp-1c": { "type": "sse", "url": "http://<хост>:8000/sse" }
  }
}
```
(хост — где поднят контейнер; в локальной отладке `localhost`.)

## Обновление кода
Исходники монтируются read-only с хоста — обновляй их `git pull` на хосте; контейнер перечитывает корни при
рестарте (`docker compose restart erp-1c`).

## Транспорт
`erp_mcp.py` выбирает транспорт по env `MCP_TRANSPORT` (`stdio` по умолчанию; `sse`/`streamable-http` в контейнере),
`MCP_HOST`/`MCP_PORT`. Так один и тот же код работает и локально (stdio), и как сервис (HTTP).

## Безопасность
- Контейнер читает код read-only, ничего не пишет.
- Сервис не аутентифицирует клиентов — держи его во внутренней сети/за reverse-proxy с auth, не наружу.
- Никаких секретов в образ не класть; токены интеграций — не сюда.

## Тесты
```bash
uv run --with mcp --with pytest pytest tests/ -q
```
Проверяют discovery/search/find/read на временном «репозитории». Гонять перед изменениями erp_mcp.py.

## Ручная обкатка HTTP-транспорта
`tests/smoke_http_client.py` — не часть pytest: подключается к живому сервису по SSE, перечисляет инструменты,
зовёт `list_modules`/`find_object`/`search_1c` на реальном наборе репозиториев. Подними сервер и натрави клиент:
```bash
# терминал 1 — сервер (без Docker, тот же код)
ONEC_SRC_DIR=/path/to/1c-src MCP_TRANSPORT=sse MCP_HOST=127.0.0.1 MCP_PORT=8000 uv run --with mcp mcp/erp_mcp.py
# терминал 2 — клиент
uv run --with mcp tests/smoke_http_client.py http://127.0.0.1:8000/sse
```
Обкатано на боевом наборе (11 слоёв конфигураций/расширений): discovery, search и find_object отдаются по HTTP корректно.
