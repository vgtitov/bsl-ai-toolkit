# Team-локализация (org-слой поверх toolkit) — шаблон/runbook

Как развернуть toolkit В КОМАНДЕ под конкретную организацию, **не дублируя ядро**: Team-репозиторий —
тонкий слой, который ПОДТЯГИВАЕТ `bsl-ai-toolkit` из GitHub и добавляет только org-специфичное
(конфиги контуров, пути, секреты). Ядро (скиллы, MCP, server-стек) — НЕ копировать.

> Где живёт Team-репо: на **корпоративном** Git (напр. внутренний GitLab), НЕ на публичном GitHub —
> он содержит org-данные (карта баз/контуров) и не должен утекать. Публичный toolkit остаётся обезличенным.

## Структура Team-репо (минимум)
```
<org>-1c-toolkit-team/
├── toolkit/                      # git submodule → https://github.com/<owner>/bsl-ai-toolkit (ядро, pin по тегу)
├── config/
│   ├── layers.<org>.toml         # слои/контуры ОРГАНИЗАЦИИ (какие репо = конфигурация, какие = расширения)
│   └── contours.<org>.md         # карта баз/контуров (ORG-ДАННЫЕ — только здесь, не в ядро)
├── server/
│   └── .env                      # секреты/пути ОРГ (вне git: .gitignore) — ONEC_SRC_DIR, токен, ONEC_TJ_DIR, Zabbix/Prometheus
├── CLAUDE.md                     # org-оверрайды правил (версии платформы/совместимости/конфигурации)
└── README.md                     # как обновлять submodule, как поднимать центр
```

## Шаги (runbook для Claude/инженера — выполнять НА корп-стороне)
1. **Создать Team-репо** на корп-Git. Подключить ядро как submodule (pin по тегу/релизу, обновлять осознанно):
   ```bash
   git submodule add https://github.com/<owner>/bsl-ai-toolkit toolkit
   git -C toolkit checkout <tag>          # зафиксировать версию ядра
   ```
2. **Заполнить org-конфиги** (это и есть «адаптация под организацию»):
   - `config/layers.<org>.toml` ← по образцу `toolkit/config/layers.example.toml`: реальные репозитории
     конфигурации/расширений, префиксы, scope-группы, профили (страновые/контурные).
   - `config/contours.<org>.md` ← по образцу `toolkit/config/contours.example.md`: карта «какая база с чем
     вяжется», риски переноса, слепые зоны. **ORG-данные — не в публичный toolkit.**
   - `toolkit/skills/1c-dev/references/conventions-template.md` НЕ править в submodule — конвенции вынести в
     свой слой/`CLAUDE.md`, иначе потеряются при обновлении ядра.
3. **server/.env** (секреты, вне git) — по `toolkit/server/.env.example`:
   - `ONEC_SRC_DIR` — реальный каталог клонов кода 1С;
   - `ONEC_BEARER_TOKEN` — `openssl rand -hex 32`;
   - `ONEC_TJ_DIR` — каталог логов ТЖ/выгрузок ЖР для onec-ops;
   - `ZABBIX_URL/ZABBIX_TOKEN`, `PROMETHEUS_URL` — если используются (read-only).
   - `JIRA_URL/JIRA_PAT`, `CONFLUENCE_URL/CONFLUENCE_PAT` — трекер задач и база знаний организации (для
     `scripts/atlassian.py`); Cloud — добавить `JIRA_USER/CONFLUENCE_USER`. Домены и токены — только здесь, не в ядро.
4. **Поднять центр в корп-docker** (на корп-хосте с Docker; runbook — `toolkit/docs/deploy-center.md`):
   ```bash
   cd toolkit/server
   docker compose --env-file ../../server/.env up -d --build
   docker compose ps      # onec-code:8000 и onec-ops:8001 за Caddy bearer-auth
   ```
5. **Разработчики подключаются** к центру по HTTP (SSE) с bearer-токеном (см. `toolkit/docs/connect.md`):
   onec-code — `:8000`, onec-ops — `:8001`. Локально код/логи не клонируют — берут с центра.

## Что адаптируется под организацию (и где помечается)
| Что | Где (Team-слой) | НЕ в публичном ядре |
| --- | --- | --- |
| Карта баз/контуров, какие репо = конфигурация/расширения | `config/layers.<org>.toml`, `config/contours.<org>.md` | да — обезличено |
| Карта базы знаний (домен, разделы/id, «когда смотреть»), префиксы трекера | Team-слой по `references/knowledge-base-map-template.md` | да — обезличено |
| Рамки документов: состав блоков, свои варианты/правила, ID шаблонов заказчика | Team-слой: свой `document-frames.md`, переопределяющий generic из `references/document-frames.md` (наследование generic→team, поблочно) | да — шаблоны заказчика только в Team |
| Версии платформы/совместимости/конфигурации | `CLAUDE.md` (org) | да |
| Реальные пути (код, логи ТЖ/ЖР), хост docker | `server/.env` (вне git) | да |
| Секреты (bearer, Zabbix-токен) | `server/.env` / секрет-стор | НИКОГДА в git |
| URL мониторинга (Zabbix/Prometheus) | `server/.env` | да |

## Принцип обновления
Улучшения/фиксы — **в ядро** `bsl-ai-toolkit` (PR на GitHub), затем в Team `git submodule update --remote` +
смена тега. Никогда не форкать ядро под org-правки — только слой поверх. Так Team всегда получает свежее ядро,
а org-специфика остаётся изолированной.

## В CLAUDE.md Team-репо добавь «Правило №0» (подтягивать в начале работы)
Впиши в свой org-`CLAUDE.md` (наследуется вместе с ядром) правило, чтобы Claude в начале
сессии сам подтягивал изменения ДО работы:

> **Правило №0.** В начале сессии/задачи сделать `git fetch` + `git pull` в этом Team-репо и
> `git submodule update --remote` для ядра `toolkit/` (сверься с origin). Работа на устаревшем
> клоне даёт ложные результаты и дубли уже существующих механизмов. То же — на любой машине с
> клоном, где идёт прогон/применение: сначала подтяни, потом работай.

(Ядро `toolkit/CLAUDE.md` уже несёт обезличенную версию этого правила — здесь добавляется
Team-специфика про submodule.)
