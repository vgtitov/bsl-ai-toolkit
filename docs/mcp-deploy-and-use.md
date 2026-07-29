# Внедрение и использование MCP-инструментов toolkit (для Claude — в первую очередь)

Этот документ — машиночитаемая инструкция: как Claude (агент) поднимает, настраивает и применяет
MCP-серверы toolkit. Сначала — для агента (точные шаги/env/когда что вызывать), затем для человека,
затем — локализация под организацию (Team) **через подтягивание ядра из GitHub, без дублирования**.

---

## ЧАСТЬ 1. ДЛЯ CLAUDE (агента) — как поднять и применять

### 1.1. Реестр MCP-серверов toolkit (`core/mcp/servers.json`)
| Сервер | Файл | Назначение | Обязательный env |
| --- | --- | --- | --- |
| `onec-code` | `mcp/onec_mcp.py` | Чтение кода 1С (ripgrep по клонам, auto-discovery слоёв) | `ONEC_SRC_DIR` |
| `bsl-platform` | (внешний jar `mcp-bsl-context`) | Справка платформы 1С | `BSL_PLATFORM_JAR`, `ONEC_PLATFORM_PATH` |
| `bsl-ls` | `mcp/bsl_ls_mcp.py` | Диагностики BSL Language Server | `BSL_JAR` |
| **`onec-ops`** | `mcp/onec_ops_mcp.py` | **Эксплуатация (read-only): ТЖ, ЖР, Apdex, диагностика, Zabbix/Prometheus** | — (пути/доступы — аргументами/env инструментов) |

### 1.2. Поднять `onec-ops` (правило: всё через `uv run`, зависимости в `# /// script`)
```bash
# проверка, что сервер стартует (deps ставятся автоматически из inline-метаданных):
uv run --with mcp --with openpyxl <repo>/mcp/onec_ops_mcp.py    # запустится как stdio MCP (Ctrl-C)
# тесты перед использованием/внедрением:
uv run --with mcp --with openpyxl --with pytest pytest -q <repo>/tests/test_onec_ops_*.py
```
Регистрация для Claude Code — профиль `core/mcp/servers.json` раскладывается onboard'ом; для `onec-ops` задать env
`ONEC_OPS_MCP=<repo>/mcp/onec_ops_mcp.py`. Опциональные env инструментов мониторинга:
`ZABBIX_URL`, `ZABBIX_TOKEN` (read-only пользователь), `PROMETHEUS_URL`.

### 1.3. Инструменты `onec-ops` и КОГДА их вызывать (read-only)
- `tech_journal_parse(roots, events, min_duration, since, until, top)` — разбор ТЖ по каталогу/**группе
  каталогов разных серверов/кластеров** (расследование по всей компании). Вызывать при разборе медленных
  операций/блокировок/дедлоков, когда есть доступ к файлам ТЖ.
- `event_log_parse(source, level, user, events, table)` — журнал регистрации из Excel-выгрузки (.xlsx) или
  `.lgd` (SQLite). Вызывать, когда доступен **только ЖР** (через отчёт 1С/файлы).
- `apdex(samples, t)` / `apdex_by_operation(records, thresholds)` — каноничный Apdex. Вызывать для замера
  РЕАЛЬНОГО ускорения операций (до/после), по операциям, с порогом T под каждую.
- `diagnose_metrics(metrics, cores)` — пороги 1С/СУБД → проблемы с рекомендациями. Вызывать на снимке метрик.
- `zabbix_problems`/`zabbix_history`, `prometheus_query` — **опционально**, когда доступен только мониторинг
  (админы дали Zabbix/Prometheus, прямого доступа к ТЖ/`rac`/СУБД нет).

### 1.4. Алгоритм агента «расследование от сценария пользователя»
Источник истины — измерение. Путь (см. `core/skills/1c-expert/references/investigation-methodology.md`):
1) уточнить операцию/окно/охват → 2) `apdex_by_operation` (baseline) → 3) собрать данные по доступному
(`tech_journal_parse` ИЛИ `event_log_parse` ИЛИ `zabbix_*`/`prometheus_query`) → 4) `diagnose_metrics`
(дерево гипотез) → 5) фикс корня → 6) повторный Apdex (доказать ускорение).

### 1.5. Безопасность (агент обязан соблюдать)
- Все `onec-ops` инструменты **read-only**: не пишут в ИБ/СУБД/кластер. Записи/рестарты/VACUUM — только человек.
- Секреты (`ZABBIX_TOKEN` и пр.) — только из env, НИКОГДА в чат/репозиторий/аргументы-литералы в коммитах.
- ПДн в тексте запросов/ЖР усекаются; сырой вывод не отправлять во внешние LLM; корп-данные не в публичный репо.

---

## ЧАСТЬ 1.6. РЕЖИМЫ РАБОТЫ, ОБНОВЛЕНИЕ, МИГРАЦИЯ (важно — чтобы не гадать «почему не работает»)

### Два режима каждого MCP (онэк-ops НЕ «только серверный»)
- **Локальный (stdio)** — для ОДНОГО разработчика на своей машине. MCP запускается по требованию как дочерний
  процесс Claude Code. Подключение: `claude mcp add onec-ops -s user -- uv run --with mcp --with openpyxl <repo>/mcp/onec_ops_mcp.py`.
  Транспорт по умолчанию `stdio`. Данные (ТЖ/ЖР/код) — локальные на этой же машине. Это **режим по умолчанию**.
- **Центральный (sse в docker)** — ОДИН сервис на всю команду (см. `deploy-center.md`). Запускается с
  `MCP_TRANSPORT=sse`, отдаёт по HTTP за Caddy bearer-auth (onec-code `:8000`, onec-ops `:8001`). Разработчики
  подключаются по сети, код/логи не клонируют локально. Данные — на сервере-центре.
- Один и тот же `onec_ops_mcp.py` обслуживает оба режима (разница только в `MCP_TRANSPORT`). Выбор: соло-работа на
  своей машине → локально; общий доступ нескольких разработчиков к единым данным → центр.

### Что делать тем, кто ИМПОРТИРОВАЛ данные/настроился ДО доработок
Ничего не ломается: `onec-code` (чтение кода) не менялся — импортированный код/каталоги `ONEC_SRC_DIR` и
подключение остаются рабочими. Чтобы получить НОВОЕ (скиллы 1c-admin-devops/1c-dba/1c-expert + MCP onec-ops):
1. `git pull` репозитория toolkit (или `git submodule update --remote` в Team-слое, см. ниже).
2. Переустановить скиллы: повторить `onboard` (копирует свежие skills в `~/.claude/skills`) — прочие
   ваши скиллы не затрагиваются (разные имена).
3. Добавить MCP onec-ops: `claude mcp add onec-ops -s user -- uv run --with mcp --with openpyxl <repo>/mcp/onec_ops_mcp.py`.
4. Перезапустить Claude Code. Импортированные данные/код re-import НЕ требуют — только добавляются новые инструменты.

### Как команда «тянет ядро из GitHub» (конкретные действия)
- **Работают напрямую с клоном toolkit:** `git pull` (обновить ядро) → повторить `onboard` (если появились новые
  скиллы/MCP). Всё.
- **Через Team-слой (submodule, см. `team-localization-template.md`):** `git submodule update --remote toolkit` →
  при необходимости сменить pin-тег → закоммитить обновление submodule в Team-репо. Org-конфиги/секреты при этом
  не трогаются (они в Team-слое, не в ядре).

### Переменные окружения — назначение, режим, что будет если не задать
| Переменная | Для чего | Где нужна | Если не задать |
| --- | --- | --- | --- |
| `ONEC_SRC_DIR` | каталог клонов кода 1С (вход onec-code, read-only) | onec-code (оба режима) | onec-code не находит код → пустой поиск |
| `ONEC_OPS_MCP` | путь к `onec_ops_mcp.py` для профиля `dev.mcp.json` | onec-ops локально (через профиль) | сервис не стартует из профиля (при `claude mcp add` путь задан явно — не нужна) |
| `ONEC_TJ_DIR` | каталог логов ТЖ/выгрузок ЖР (вход onec-ops в docker, read-only) | onec-ops центральный | в docker нечего читать; локально путь к логам передаётся аргументом инструмента |
| `MCP_TRANSPORT` | `stdio` (локально) / `sse` (центр) | оба MCP | по умолчанию `stdio` (локальный режим) |
| `MCP_HOST`/`MCP_PORT` | адрес/порт сетевого транспорта | центр (sse) | дефолт 0.0.0.0 ; onec-code 8000, onec-ops 8001 |
| `ONEC_BEARER_TOKEN` | секрет авторизации Caddy | центр | proxy не поднимется (сервис требует токен) |
| `ONEC_PUBLIC_PORT`/`ONEC_OPS_PUBLIC_PORT` | внешние порты центра | центр | дефолт 8000 / 8001 |
| `ZABBIX_URL`/`ZABBIX_TOKEN`, `PROMETHEUS_URL` | опц. адаптеры мониторинга (read-only) | onec-ops, если используются | адаптеры выключены (инструменты вернут подсказку «задай url/env») |
| `ONEC_LAYERS_CONFIG`, `ONEC_PROFILE` | свой конфиг слоёв/контуров и активный профиль | локализация | чистое auto-discovery слоёв |
| `ONEC_1CV8_BIN` | путь к `1cv8.exe` на СЕРВЕРЕ 1С (apply-слой `onec_metadata`) | `bin/1c-meta` apply в тест-базу | типовой путь `…\8.3.x\bin\1cv8.exe` |
| `ONEC_REMOTE_WORKDIR` | рабочий каталог на сервере для tar/логов (`onec_metadata`) | `bin/1c-meta` apply | по умолчанию `D:\src` |

`onec_metadata` (правки метаданных): env нужны только для apply на сервер (разбор/правка XML — без них); SSH-хост — алиас в `~/.ssh/config` (аргумент `Runner`), пароль ИБ — аргументом в момент запуска, не в env. Требуются `ssh`/`scp`/`tar` на машине (Windows 10+ — из коробки).

Секреты (`ONEC_BEARER_TOKEN`, `ZABBIX_TOKEN`) — только в `.env`/секрет-сторе, НИКОГДА в git/чат.

## ЧАСТЬ 2. ДЛЯ ЧЕЛОВЕКА — установка
1. Установить `uv` (astral) и `ripgrep`; для `bsl-ls` — Java.
2. `git clone <toolkit> && cd bsl-ai-toolkit && bash onboard/onboard.sh` (Windows — `onboard.ps1`).
3. Заполнить env под свой проект (см. `docs/connect.md`): пути к клонам кода, jar'ы, `ONEC_OPS_MCP`.
4. Перезапустить Claude Code, подтвердить MCP-серверы.
5. Проверка: `uv run --with mcp --with openpyxl --with pytest pytest -q tests/` (зелёные).

---

## ЧАСТЬ 3. ЛОКАЛИЗАЦИЯ (Team-слой) — ТЯНУТЬ ЯДРО ИЗ GITHUB, НЕ ДУБЛИРОВАТЬ

**Принцип:** сначала усиливается/дорабатывается **toolkit** (это ядро, канон на GitHub). Организационный
(Team) репозиторий — **тонкий слой поверх**, который ПОДТЯГИВАЕТ toolkit из GitHub и добавляет только своё:
конфиги контуров, пути, токены (в env/секретах), org-специфичные данные. Ядро методологии/код MCP — НЕ копировать.

Способы подтягивания ядра (выбрать один):
- **git submodule** — `git submodule add https://github.com/<owner>/bsl-ai-toolkit toolkit` → обновление
  `git submodule update --remote`. Team-репо хранит только свой `config/` и `CLAUDE.md`-оверрайды.
- **git subtree** — если нужен моно-репо без submodule-механики.
- **pin по тегу/релизу** — фиксировать версию toolkit, обновлять осознанно.

Что кладёт Team-слой (и только это):
- `config/layers.<org>.toml` (`ONEC_LAYERS_CONFIG`) — слои/контуры своей раскладки.
- `config/contours.<org>.md` — карта баз/контуров (org-данные; в публичный toolkit НЕ дублировать).
- env-файл со своими путями/токенами (вне git; см. `docs/connect.md`).
Org-данные (карта контуров, токены, имена баз/хостов) живут ТОЛЬКО в Team-слое/секретах, не в публичном ядре.

**Если для Team нужен сторонний инструмент** (из `docs/ops-tools-catalog.md`) — тянуть его из его GitHub
(submodule/образ/пакет), не копировать код; лицензии соблюдать (MIT/Apache/BSD — можно; GPL/AGPL — как сервис).

> Порядок работ: (1) усилить toolkit → (2) при необходимости локализовать в Team через pull из GitHub →
> (3) тестировать на реальном применении в Team-слое, фиксы — обратно в toolkit (ядро), а не в форк-копию.
