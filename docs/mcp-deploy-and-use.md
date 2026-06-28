# Внедрение и использование MCP-инструментов toolkit (для Claude — в первую очередь)

Этот документ — машиночитаемая инструкция: как Claude (агент) поднимает, настраивает и применяет
MCP-серверы toolkit. Сначала — для агента (точные шаги/env/когда что вызывать), затем для человека,
затем — локализация под организацию (Team) **через подтягивание ядра из GitHub, без дублирования**.

---

## ЧАСТЬ 1. ДЛЯ CLAUDE (агента) — как поднять и применять

### 1.1. Реестр MCP-серверов toolkit (`mcp/dev.mcp.json`)
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
Регистрация для Claude Code — профиль `mcp/dev.mcp.json` раскладывается onboard'ом; для `onec-ops` задать env
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
Источник истины — измерение. Путь (см. `skills/1c-expert/references/investigation-methodology.md`):
1) уточнить операцию/окно/охват → 2) `apdex_by_operation` (baseline) → 3) собрать данные по доступному
(`tech_journal_parse` ИЛИ `event_log_parse` ИЛИ `zabbix_*`/`prometheus_query`) → 4) `diagnose_metrics`
(дерево гипотез) → 5) фикс корня → 6) повторный Apdex (доказать ускорение).

### 1.5. Безопасность (агент обязан соблюдать)
- Все `onec-ops` инструменты **read-only**: не пишут в ИБ/СУБД/кластер. Записи/рестарты/VACUUM — только человек.
- Секреты (`ZABBIX_TOKEN` и пр.) — только из env, НИКОГДА в чат/репозиторий/аргументы-литералы в коммитах.
- ПДн в тексте запросов/ЖР усекаются; сырой вывод не отправлять во внешние LLM; корп-данные не в публичный репо.

---

## ЧАСТЬ 2. ДЛЯ ЧЕЛОВЕКА — установка
1. Установить `uv` (astral) и `ripgrep`; для `bsl-ls` — Java.
2. `git clone <toolkit> && cd claude-1c-toolkit && bash onboard/onboard.sh` (Windows — `onboard.ps1`).
3. Заполнить env под свой проект (см. `docs/connect.md`): пути к клонам кода, jar'ы, `ONEC_OPS_MCP`.
4. Перезапустить Claude Code, подтвердить MCP-серверы.
5. Проверка: `uv run --with mcp --with openpyxl --with pytest pytest -q tests/` (зелёные).

---

## ЧАСТЬ 3. ЛОКАЛИЗАЦИЯ (Team-слой) — ТЯНУТЬ ЯДРО ИЗ GITHUB, НЕ ДУБЛИРОВАТЬ

**Принцип:** сначала усиливается/дорабатывается **toolkit** (это ядро, канон на GitHub). Организационный
(Team) репозиторий — **тонкий слой поверх**, который ПОДТЯГИВАЕТ toolkit из GitHub и добавляет только своё:
конфиги контуров, пути, токены (в env/секретах), org-специфичные данные. Ядро методологии/код MCP — НЕ копировать.

Способы подтягивания ядра (выбрать один):
- **git submodule** — `git submodule add https://github.com/<owner>/claude-1c-toolkit toolkit` → обновление
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
