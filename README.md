# bsl-ai-toolkit — AI-first разработка 1С:Предприятие (мульти-агентный контур)

[![CI](https://github.com/vgtitov/bsl-ai-toolkit/actions/workflows/tests.yml/badge.svg)](https://github.com/vgtitov/bsl-ai-toolkit/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-00B9BF.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![AGENTS.md](https://img.shields.io/badge/AGENTS.md-ready-blue.svg)](AGENTS.md)
[![1С:Предприятие 8.3](https://img.shields.io/badge/1С:Предприятие-8.3-red.svg)](https://v8.1c.ru/)
[![MCP](https://img.shields.io/badge/MCP-servers-5b21b6.svg)](https://modelcontextprotocol.io/)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)

Переиспользуемый набор **правил + скиллов + MCP-серверов + byte-perfect движка правки метаданных + методологии** для
AI-ассистированной разработки на **1С:Предприятие 8.3 (BSL)**. **Не привязан к конкретному AI** — работает под разные
AI-агенты (Claude Code, Cursor, Copilot, Gemini CLI, Codex, Cline, Windsurf, Aider) через единое ядро и тонкие адаптеры.
Не привязан к конкретной организации — адаптируется под любую конфигурацию/расширение. Принцип: **AI не угадывает 1С,
а работает по РЕАЛЬНОМУ коду и справке, по стандартам, доводит код до production-ready.**

> **Claude Code — первая (эталонная) реализация.** Остальные агенты подключаются минимальной адаптацией из общего
> ядра (`core/`) — см. `adapters/`. Три оси переносимости: **AGENTS.md** (правила) + **SKILL.md** (навыки) + **MCP**
> (инструменты). Не 1С-Битрикс (это CMS на PHP — [отдельный toolkit](https://github.com/vgtitov/bitrix-ai-toolkit)).

## Идея
Вся RU-сцена AI-разработки 1С («вайб-кодинг», агенты в EDT/Cursor) упирается в одно: **модель галлюцинирует детали 1С** —
выдумывает методы, путает фасады (менеджер/объект/выборка), не знает состав типовой и режим совместимости; а формы/СКД/
метаданные считаются «последним рубежом» генерации и обходятся вручную. Рычаг toolkit — **проверяемость через инструменты**:
- **`onec-code`** — поиск по реальному коду конфигурации и расширения (оба слоя контура), а не по памяти;
- **`bsl-ls`** — диагностики BSL Language Server после каждой правки;
- **`onec_metadata`** — детерминированная **byte-perfect** правка метаданных (Конфигуратор-XML + EDT `.mdo`/`.dcs`) с
  **round-trip verify** (загрузка в ТЕСТ-базу платформой + повторная выгрузка = ровно целевой дифф). Это закрывает тот самый
  «последний рубеж», который остальные обходят;
- **`onec-ops`** — эксплуатация и производительность (ТЖ, план запроса, APDEX, Zabbix/Prometheus): agentic SWE, а не только кодинг;
- **`onec-data`** — доступ к данным живой ИБ read-only под исследуемым пользователем (честный RLS, маскирование ПДн).

Артефакты (ЧТЗ → тех-проект) превращаются в упорядоченные атомарные задачи и проверенный код. Ниша одной фразой:
**детерминированный, проверяемый и безопасный контур AI-разработки 1С поверх EDT/git — с byte-perfect метаданными и
эксплуатацией, agent-agnostic (в т.ч. под ограниченный доступ к LLM).**

## Чем отличается от соседей
| | Что даёт | Наш акцент |
|---|---|---|
| `comol/ai_rules_1c`, `cursor_rules_1c` | правила/rules для LLM | у нас правила + **исполнение через MCP на реальном коде**, а не только текст-инструкции |
| `Nikolay-Shirokov/cc-1c-skills` | скиллы Claude Code для 1С | у нас скиллы **+ движок метаданных + эксплуатация + мульти-агент** |
| VibeFlow1C, Shotgun | фреймворк/пайплайн вайб-кодинга | мы **инфраструктурный слой** (MCP+движок), совместимый, а не конкурирующий |
| `1c-syntax/bsl-language-server` | синтаксис/диагностики | используем как инструмент (`bsl-ls`), добавляем контекст кода/данных |

Уникальное: **byte-perfect правка метаданных с round-trip** (форм/СКД/реквизитов) — то, что почти вся сцена признаёт
«последним рубежом» и обходит стороной.

## Состав
```
bsl-ai-toolkit/
├── core/                       # ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ (общий для всех AI-агентов)
│   ├── AGENTS.md               # правила (спроси-инструмент, слои контура, гейт метаданных, производительность, безопасность)
│   ├── skills/                 # SKILL.md × 6: 1c-dev · 1c-analyst · 1c-metadata · 1c-admin-devops · 1c-dba · 1c-expert
│   └── mcp/servers.json        # профиль MCP: onec-code · bsl-ls · onec-ops · onec-data (пути/креды через env)
├── adapters/                   # тонкие адаптеры под агентов (claude — эталон; codex/gemini — фолбэк; cursor/copilot/… — rulesync)
│   ├── claude/                 #   CLAUDE.md (@AGENTS.md) + settings.json (хук bsl_guard)
│   └── README.md               #   матрица «агент → файл правил → MCP-конфиг» + провайдеры LLM (РФ/санкции)
├── build.sh                    # генерация из core/: CLAUDE.md · AGENTS.md · GEMINI.md · .mcp.json · .claude/  (закоммичены)
├── onec_metadata/              # ОС-независимый движок правок метаданных (Конфигуратор-XML + EDT .mdo/.dcs), byte-perfect
│   └── formats/ · ops/ · apply/ · catalog.py    #  apply-слой (ssh/scp/tar или локально) + round-trip verify в ТЕСТ-базе
├── bin/1c-meta                 # headless CLI движка (detect/template/scd/attr/child)
├── mcp/                        # реализации MCP: onec_mcp.py · bsl_ls_mcp.py · onec_ops_mcp.py · onec_data_mcp.py
├── extensions/ai_debug/        # расширение 1С для onec-data: HTTP-сервис (read-only под пользователем, privileged за гейтом)
├── config/                     # layers.example.toml (слои/scope) · contours.example.md (карта баз для 1c-analyst)
├── server/                     # серверная часть (центральный onec-code по HTTP за auth — для команды)
├── scripts/                    # bsl_guard.py (детектор «запрос в цикле») · git-hooks · switch_source · detect_tools · doctor
├── onboard/                    # идемпотентный bootstrap (onboard.sh/.ps1/.cmd)
├── tests/                      # pytest (движок метаданных, guard, onec-data, smoke) — 500+ тестов
└── docs/                       # AI_INSTALL_GUIDE · AI_UPDATE · data-access-architecture · testing-ladder · connect · …
```
Сгенерированные из `core/` файлы (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.mcp.json`, `.claude/`) закоммичены, чтобы
репозиторий работал сразу после клона. Правь `core/` → пересобирай `sh build.sh` (не редактируй сгенерированное вручную).

## Кросс-платформенность (реально проверено)
Переносимость — по обеим осям: **любой AI-агент** и **любая ОС разработчика**.

| Компонент | macOS | Linux | Windows |
|---|---|---|---|
| Движок метаданных `onec_metadata`, MCP, `scripts/*` | ✅ Python | ✅ Python | ✅ Python |
| Сборка конфигов | `sh build.sh` | `sh build.sh` | `build.ps1` (нативно) или `sh build.sh` (Git Bash) |
| Онбординг | `onboard/onboard.sh` | `onboard/onboard.sh` | `onboard/onboard.ps1` (+ `.cmd`) |
| Apply-слой (правки в тест-базу) | `ssh`/`scp`/`tar` или локальный runner | то же | то же (ssh/tar есть в Win10+) или локальный runner |
| Git-хуки (`commit-msg`, `pre-commit`) | ✅ | ✅ | ✅ (Git for Windows) |

Движок — **чистый Python** (без ОС-зависимостей); apply-слой использует `ssh`/`scp`/`tar` (есть во всех трёх ОС) или
**локальный runner** без SSH (если платформа на своей машине). Генерируемые агент-конфиги (`CLAUDE.md`, `AGENTS.md`,
`GEMINI.md`, `.claude/`) закоммичены → на любой ОС репозиторий работает сразу после клона. Единственная Windows-специфика —
сам СЕРВЕР 1С, к которому идёт удалённое применение; на машине разработчика ОС значения не имеет.

## Ключевые принципы (методология)
- **Спроси инструмент, не угадывай** — сигнатуры/поведение только по коду (`onec-code`) и справке платформы/БСП, не по
  памяти. После правки — `bsl-ls`. Механизм без сверки помечать `[проверить]`, не «верно».
- **Слои контура** — конфигурация (типовая, read-only) + расширение; искать в ОБОИХ, править в расширение.
- **Гейт метаданных** — структурные правки только через `onec_metadata` под round-trip + scope-границей; каскадный
  деструктив (переименование/удаление) — человек в 1C:EDT.
- **Рабочий цикл (6 шагов):** понять и сверить → атомарные задачи [Разработчик/IDE]|[AI/код] → инструкции по не-коду →
  код (reuse-first БСП, производительно, по стандартам `v8-code-style`) → проверки/тесты → передача пакетом.
- **Безопасность/комплаенс** — секреты и ПДн в модель не передаются; данные ИБ — read-only под пользователем с маскированием.

## Поддерживаемые агенты и провайдеры
Toolkit **agent-agnostic и provider-agnostic**. Основные агенты — **Claude Code** (Sonnet 4.5 — лучший по BSL) и **Cursor**;
плюс Codex/Gemini/Copilot/Cline/Windsurf/Aider через `adapters/` + `build.sh`. Доступ к LLM из РФ ограничен
(`unsupported_region`) — подключение через прокси-обёртку API / RU-VDS + свой прокси / VPN на роутере; отечественные
(GigaChat, YandexGPT) и self-host китайских — как fallback. Toolkit не диктует провайдера — настройку доступа задаёшь на
уровне окружения агента. Подробнее — `adapters/README.md`, `docs/ACCESS_SETUP.md`.

## Интеграции и коннекторы (уже в комплекте)
Toolkit — не только про код: смежные системы SDLC/эксплуатации уже подключаемы, агент работает с ними напрямую.

| Система | Что даёт | Как |
|---|---|---|
| **Jira** | чтение задач, поиск по JQL, комментарии | `scripts/atlassian.py jira issue/search` (токен read-only) |
| **Confluence** | чтение страниц/дерева, поиск, **публикация** `md → страница` | `scripts/atlassian.py conf page/tree/search/publish` |
| **Zabbix** | APDEX → trapper-items (`1c.apdex[...]`), метрики/проблемы/дашборды, perf-отчёт и diff | `scripts/apdex_to_zabbix.py`, `scripts/zabbix_perf.py`, MCP `onec-ops` (Zabbix API) |
| **Prometheus** | запрос метрик 1С/кластера (PromQL) | MCP `onec-ops` (`prometheus_query`) |
| **Технологический журнал / ЖР** | разбор ТЖ (TTIMEOUT/TLOCK/EXCP…), журнал регистрации, APDEX по операциям | MCP `onec-ops` (`tech_journal_parse`, `event_log_parse`, `apdex_by_operation`) |
| **BSL Language Server** | диагностики BSL после каждой правки | MCP `bsl-ls` (в комплекте) + авто-скачивание `scripts/detect_tools.py` |
| **Данные живой ИБ** | OData + отладочный сервис `ai_debug` (read-only, RLS, маскирование ПДн) | MCP `onec-data` (10 инструментов) |
| **Центральный onec-code** | общий на команду поиск по коду 1С по HTTP за auth (Caddy) | `server/` (docker-compose) + `scripts/switch_source.py` / `set_token.*` |

Принцип единый: **источник истины — реальный инструмент и измерение** (`rac`, ТЖ, `pg_stat_*`, счётчики), а не память
модели. Добавить свой коннектор — тонкий скрипт в `scripts/` или инструмент в MCP `onec-ops`; секреты — только через env.

## Установка
```bash
git clone https://github.com/vgtitov/bsl-ai-toolkit && cd bsl-ai-toolkit
bash onboard/onboard.sh          # macOS/Linux
```
```powershell
git clone https://github.com/vgtitov/bsl-ai-toolkit ; cd bsl-ai-toolkit
.\onboard\onboard.ps1            # Windows
```
onboard раскладывает скиллы, профиль `.mcp.json` и правила, задаёт env, ставит git-хуки и делает самотест. Дальше
перезапусти агента и подтверди MCP-серверы. Под другого агента: `sh build.sh <agent>` (Windows — `build.ps1`; см. `adapters/README.md`).

> **Пусть настроит сам агент.** Можно не настраивать руками — дай ИИ-агенту готовый промпт из
> [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md), и он развернёт окружение сам (человеку — только склонировать свои
> 1С-исходники, ввести секреты и опубликовать тест-базу).

- **Установка инструментов ИИ (гайд + грабли)** — [docs/AI_INSTALL_GUIDE.md](docs/AI_INSTALL_GUIDE.md).
- **Версионирование и обновление** — [docs/AI_UPDATE.md](docs/AI_UPDATE.md) (ядро пинится semver-тегом; ИИ обновляется сам).
- **Здоровье окружения** — `python scripts/doctor.py` (пререквизиты + MCP из `.mcp.json` + доступы).

## Адаптация под свою организацию
Локализация — это **конфиги и отдельный слой поверх**, не правка ядра:
1. Версии проекта (платформа, режим совместимости, конфигурация, библиотеки) — в `core/skills/1c-dev/references/conventions-template.md`.
2. `config/layers.example.toml` → свой файл (`ONEC_LAYERS_CONFIG`): слои/контуры (что базовая конфигурация, что расширения),
   ярлыки, scope-группы, профили. Без файла — чистое auto-discovery. **Код MCP менять не нужно.**
3. `config/contours.example.md` → свой файл: карта контуров/баз для `1c-analyst`.
4. `core/mcp/servers.json` → пути к своим инструментам (через env) → `sh build.sh`.

Ядро методологии, стандарты и движок MCP остаются как есть.

## Документация
- [docs/CONCEPT.md](docs/CONCEPT.md) — идея, методология, работа с артефактами (ЧТЗ, тех-проект), связь с курсами и реальными процессами.
- [docs/TOOLS.md](docs/TOOLS.md) — каталог инструментов: что внутри, что делает, как пользоваться.
- [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md) — как поручить настройку самому ИИ-агенту (готовый промпт).
- [docs/AI_INSTALL_GUIDE.md](docs/AI_INSTALL_GUIDE.md) · [docs/AI_UPDATE.md](docs/AI_UPDATE.md) — установка и обновление.
- [docs/example.md](docs/example.md) — пример: как ИИ ведёт задачу от тех-проекта до передачи кода.
- [docs/data-access-architecture.md](docs/data-access-architecture.md) — доступ к данным живой базы (лестница уровней).
- [docs/testing-ladder.md](docs/testing-ladder.md) — как доказываем поведение (лестница тестирования).
- [docs/ops-tools-catalog.md](docs/ops-tools-catalog.md) · [docs/zabbix-1c-monitoring-guide.md](docs/zabbix-1c-monitoring-guide.md) — эксплуатация и мониторинг.
- [docs/mcp-deploy-and-use.md](docs/mcp-deploy-and-use.md) · [docs/deploy-center.md](docs/deploy-center.md) — общий сервер на команду.

## Лицензия и оговорки
MIT (см. `LICENSE`). Самостоятельный обезличенный продукт — внутренних данных организаций в репозитории нет и быть не должно.
Правовые оговорки (товарные знаки 1С, БСП как справочный материал, независимость проекта) — `NOTICE.md`; сторонние
компоненты и лицензии — `THIRD_PARTY.md`.

## Вклад
PR и issue приветствуются — и от людей, и от AI-агентов. См. [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md),
[SECURITY.md](SECURITY.md). Читай `AGENTS.md` перед правками: правь `core/`, добавляй тест на каждую проверку, факты — по коду/справке 1С.
