# Сторонние компоненты и атрибуции

Toolkit сам по себе — оригинальная работа под MIT (`LICENSE`). Ниже — что он использует. Лицензии указаны по
сведениям первоисточников; авторитетный источник — лицензия в репозитории каждого проекта (сверяйте при сомнении).

## Поставляются в Docker-образе центрального MCP (`server/Dockerfile`/`server/docker-compose.yml`)
Эти компоненты не лежат в репозитории, но попадают в собранный образ:

| Компонент | Назначение | Лицензия |
|---|---|---|
| [ripgrep](https://github.com/BurntSushi/ripgrep) | быстрый поиск по коду | MIT / Unlicense |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | сервер MCP (FastMCP) | MIT |
| [Caddy](https://github.com/caddyserver/caddy) | reverse-proxy с авторизацией | Apache-2.0 |
| Python | среда выполнения | PSF License |

## Внешние инструменты (referenced, НЕ включены в репозиторий)
Toolkit ссылается на них в скиллах/доках; код не копируется, используются по своим лицензиям:

| Проект | Зачем | Где упомянут |
|---|---|---|
| [1c-syntax/bsl-language-server](https://github.com/1c-syntax/bsl-language-server) | проверка синтаксиса/стандартов BSL | `skills/1c-dev/references/code-standards.md` |
| [1C-Company/v8-code-style](https://github.com/1C-Company/v8-code-style) | официальные стандарты разработки (ссылки на checks) | `code-standards.md` |
| [alkoleft/mcp-bsl-context](https://github.com/alkoleft/mcp-bsl-context) | справка по API платформы | `README.md` |
| [Nikolay-Shirokov/cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills) | механические операции с платформой 1С | `platform-ops.md` (MIT) |

Лицензию каждого внешнего проекта смотрите в его репозитории. Если вы включаете какой-то из них в свою сборку —
соблюдайте его условия отдельно.

## 1С-экосистема (справочные факты, не код)
Имена методов БСП и ключи диагностик стандартов — справочный материал (см. `NOTICE.md`). Исходники БСП/типовых
конфигураций и текст справки платформы в репозитории не распространяются.
