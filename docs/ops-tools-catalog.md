# Каталог опенсорс-инструментов для эксплуатации 1С (admin/DevOps/DBA/expert)

Курированный список **проверенных** (существование/лицензия/активность сверены по GitHub API на 2026-06)
опенсорс-инструментов, применимых к ролям `1c-admin-devops`, `1c-dba`, `1c-expert`. Для каждого: что делает,
лицензия, зрелость и **как применить** (подключить готовый · взять за образец · упомянуть в reference).

Принципы отбора: реальные репозитории (не выдуманные); живые/завершённые; лицензия учтена (см. ⚠️ ниже);
для управляющих инструментов — приоритет **read-only** (минимальные права).

> **Лицензионная гигиена.** MIT/Apache-2.0/BSD — код можно переиспользовать. **MPL-2.0** — copyleft на уровне файлов.
> **GPL-3.0/AGPL-3.0** — «вирусные»: запускать как отдельный сервис можно, копировать код в проприетарное нельзя
> (AGPL бьёт даже по сетевому использованию). **Без файла LICENSE** = «all rights reserved» — изучать можно,
> копировать код нельзя. Помечено у каждого инструмента.

---

## 1. Контейнеризация и оркестрация → `1c-admin-devops` / containers-docker-k8s.md

| Инструмент | Что | Лиц. / актив. | Применить |
| --- | --- | --- | --- |
| **firstBitMarksistskaya/onec-docker** | Эталонная «фабрика образов» всей экосистемы 1С: сервер/клиент/RAS/веб/CRS-хранилище/SonarScanner/gitsync/EDT, агенты под Swarm и k8s. Поддержка nixel2007 (автор BSL LS). | активен 2026-06 | **Ядро рекомендаций** по сборке образов 1С для CI/CD. Production-grade. |
| **jugatsu/onec-docker** | Первоисточник сборки образов 1С 8.3 (форк-предок firstBit). ~119★ | активен 2026-04 | Упомянуть как первоисточник; развитие — у форка firstBit. |
| **alexanderfefelov/docker-1c-server** (+ `-ws`, `-postgrespro-1c`) | Минималистичный набор «сервер + веб-публикация + PostgreSQL Pro под 1С». ~164★ | активен 2026 | Пример простого стека, когда не нужна вся фабрика. |
| **1C-Company/docker_fresh** | **Официальный** (вендор) стенд облачной подсистемы 1С:Фреш в Docker (core/db/site/gate). | активен 2026-06 | Единственный легитимный пример Fresh-в-Docker; для dev/тест-стендов (платформа в примере устаревшая). |
| **thedemoncat/kubeonec** + **arkuznetsov/hirac** | Helm-чарт кластера 1С в k8s + REST-API над `rac` с **Prometheus-метриками** (HiRAC, MIT). | kubeonec 2025-10 / hirac активен | k8s — **только как пример с предупреждением**: запуск сервера 1С в k8s вендором НЕ поддерживается. HiRAC — рекомендовать как слой наблюдаемости. |
| **agibalovsa/-1C_DevOps** | Cookbook скриптов/compose/инструкций для контура 1С. ~37★ | активен 2026-06 | Справочник рецептов DevOps-обвязки. |

Не существует: `1C-Company/onec-images` (вендорских образов платформы в открытом виде нет — лицензия 1С; образы собирают локально под учёткой releases.1c.ru).

---

## 2. CI/CD и автоматизация разработки → `1c-admin-devops` / update-deploy-cicd.md

| Инструмент | Что | Лиц. / актив. | Применить |
| --- | --- | --- | --- |
| **1c-syntax/bsl-language-server** | Линтер/диагностики/метрики BSL (LSP), CLI-режим для CI. Java, ~447★ | активнейший | **Обязателен** (и IDE, и CI). Уже опора правил проекта. |
| **vanessa-opensource/vanessa-runner** | Оркестратор операций разработчика (создать/обновить ИБ, cf/cfe, тесты, синтаксконтроль). ~257★. **Орг — `vanessa-opensource`**, не oscript-library. | активен 2026 | Ядро стадий prepare/syntax-check/smoke. |
| **oscript-library/gitsync** | Мост «хранилище конфигурации → git» (каждая версия → коммит). ~325★ | активен 2026 | Незаменим для легаси на Хранилище. Уже описан в reference. |
| **Pr-Mex/vanessa-automation** | BDD в 1С (Gherkin на русском), приёмочные UI-сценарии. ~664★ (топ) | активен 2026 | Приёмочные тесты. |
| **vanessa-opensource/add** (Vanessa-ADD) | TDD/BDD/smoke/unit/acceptance-фреймворк. MPL-2.0, ~373★. (`oscript-library/add` — устаревший форк, не брать.) | активен 2026 | Smoke + BDD. |
| **bia-technologies/yaxunit** (YAXUnit) | Современный фреймворк **модульных** тестов на встроенном языке 1С (тесты как процедуры расширения, запуск из CI через `vrunner`). Сменил `xDrivenDevelopment/xUnitFor1C` (legacy). | активен 2026 | Unit-тесты бизнес-логики. → testing-and-quality.md. [проверить] актуальность репо/орг. |
| **1c-syntax/sonar-bsl-plugin-community** | Поддержка 1С/OneScript в SonarQube. ~259★ | активен 2026 | Quality gate (Sonar). |
| **1C-Company/v8-code-style** | Официальное расширение EDT: проверки кода по стандартам 1С (опенсорс-аналог 1С:АПК). Java, ~232★ | активен 2026 | Рекомендовать для EDT-контура. |
| **oscript-library/deployka** | Continuous Delivery для 1С (доставка cf/cfe в среды). ~102★ | активен 2026-05 | Опция стадии deploy. |
| **oscript-library/precommit4onec** | Хранение epf/erf как исходников при коммите (живее классического `xDrivenDevelopment/precommit1c`). | активен | Контур «Конфигуратор + git». |
| **EvilBeaver/OneScript** + **opm** + **ovm** | Runtime OneScript + пакетный менеджер + менеджер версий. ~569★/82★ | активен 2026 | Фундамент всего oscript-стека CI. |

⚠️ **EDT CLI (`ring`/`1cedtcli`)** в CI ненадёжен: может вернуть exit 0 при ошибке (issue #1344) — для headless-сборки cf reference верно предпочитает `ibcmd`.

---

## 3. PostgreSQL / DBA под высоконагруженную 1С → `1c-dba`

| Инструмент | Что | Лиц. / актив. | Применить |
| --- | --- | --- | --- |
| **lesovsky/pgcenter** | Realtime «htop для Postgres»: активность, локи, kill бэкендов. BSD-3, ~1.6k★ | активен 2026 | **«Скорая помощь»** в момент тормозов 1С: кто кого блокирует. Ноль установки в базу. → monitoring-and-locks. |
| **pgbackrest/pgbackrest** | Инкрементальные бэкапы, PITR, S3, контрольные суммы. MIT, ~4.2k★ | очень активен | Золотой стандарт бэкапа больших боевых баз 1С. → backup-recovery. |
| **reorg/pg_repack** | Онлайн-устранение bloat таблиц/индексов без длинной блокировки. BSD-3, ~2.3k★ | активен 2026 | Чистка раздувшихся регистров 1С без окна простоя. → maintenance. |
| **postgrespro/mamonsu** | Активный агент мониторинга PG для **Zabbix** (~90 метрик), RU-вендор Postgres Pro. BSD-3 | активен 2026 | Если стек Zabbix — прямое попадание под 1С. → monitoring. |
| **prometheus-community/postgres_exporter** | Экспортёр метрик PG для Prometheus/Grafana. Apache-2.0, ~3.5k★ | очень активен | Если стек Prometheus/Grafana. → monitoring. |
| **HypoPG/hypopg** + **powa-team/pg_qualstats** | Гипотетические индексы (проверить EXPLAIN без построения) + статистика предикатов. PostgreSQL-lic | активен 2026 | Безопасный подбор недостающих индексов для тяжёлых запросов 1С на боевой. → postgres-tuning. |
| **darold/pgbadger** | Анализатор логов PG → HTML-отчёт (топ медленных/частых запросов, локи). PostgreSQL-lic, ~4k★ | активен 2026 | Пост-фактум разбор нагрузки 1С (с `log_min_duration_statement`). |
| **patroni/patroni** | Автофейловер HA-кластера PG (org переименован из `zalando`). MIT, ~8.5k★ | очень активен | Де-факто стандарт HA под 1С. → ha-and-scaling. |
| **vitabaks/autobase** | Ansible/GitOps развёртывание production-кластера PG (Patroni+pgBackRest+балансировка). MIT, ~4.3k★, RU-автор | активен 2026 | Эталонная отказоустойчивая архитектура «под ключ». → ha-and-scaling. |
| **zeerayne/1cv8-postgresql-config-helper** · **larionit/onec-pg-tune** | Подбор/правка postgresql.conf по рекомендациям 1С / под железо. RU | активны | Референс параметров. → postgres-tuning. |

⚠️ Поправки к расхожим названиям: `pgwatch2`→**`cybertec-postgresql/pgwatch`** (v2 архивирован, брать v3); `pg_profile`→**`zubkov-andrei/pg_profile`**; **pgstattuple** — contrib в составе PG (репо нет, `CREATE EXTENSION`). RU-практики: **gilev.ru** (тест Гилёва TPC-1C), **Антон Дорошкевич/ИнфоСофт** (регламент обслуживания), **PostgresPro-1C** (офиц. сборка с патчами под 1С).

---

## 4. Производительность / технологический журнал / мониторинг кластера → `1c-expert` / monitoring

| Инструмент | Что | Лиц. / актив. | Применить |
| --- | --- | --- | --- |
| **LazarenkoA/prometheus_1C_exporter** | Экспортёр метрик кластера 1С (через `rac`) в Prometheus: сессии, лицензии, память rphost, длительность вызовов + Grafana-дашборды. MPL-2.0, ~214★ | очень активен 2026-06 | **Флагман OSS-мониторинга кластера 1С.** → monitoring-and-backup. |
| **Polyplastic/1c-parsing-tech-log** | Самое полное RU-решение разбора ТЖ + счётчики + автоклассификация ошибок. ~326★ (⚠️ нет LICENSE) | активен 2026-06 | Эталон, какие события ТЖ/счётчики важны. → tech-journal / investigation-methodology. **Код не копировать** (нет лицензии). |
| **medigor/tech-log-parser** | Парсер ТЖ на **Rust** → JSON; есть как библиотека и внешняя компонента. MIT, ~14★ | свежий 2025-11 | **Лучшее ядро для будущего MCP-инструмента** `tech_journal_parse` (быстрый, MIT). |
| **akpaevj/OneSTools.TechLog** | Библиотека C# парсинга ТЖ (обычный и live-режим), нормализация событий. MIT | завершён 2021 | Референс-реализация формата ТЖ; live-режим → streaming-MCP. |
| **ValentinKozlov/1C_PrometheusExporter** | Расширение 1С: histogram-метрики прикладного слоя → **APDEX в Grafana**. Apache-2.0, ~72★ | 2022 | Закрывает APDEX из прикладного слоя. → performance-apdex. |
| **kuzyara/Lock1C-cheet-sheet** | Шпаргалка по блокировкам 1С: разбор TLOCK/TDEADLOCK, SDBL↔1С, пересечение областей. ~130★ | свежий 2025-10 | Готовая методика. → locks-and-deadlocks. |
| **grumagargler/tester** («Тестер 1С») | Сценарное тестирование 1С (BDD/TDD по UI), воспроизводимая нагрузка/замеры. BSD-2, ~198★ | активен 2026-06 | Свободная альтернатива «Тест-центр/КИП». → highload-cluster-loadtest. |

Закрытого OSS-аналога ЦКК / открытого исходника perfexpert/теста Гилёва нет — это закрытые продукты, годятся как методологический ориентир. Открытая замена ЦКК собирается из: разбор ТЖ (Polyplastic) + метрики/APDEX (экспортёры) + нагрузка (tester).

---

## 5. MCP-серверы и AI-обвязка → toolkit `mcp/` + docs/mcp-ops-tools-design.md

**Подключить готовый:**
- **crystaldba/postgres-mcp** (Python, MIT, ~3k★, активен) — **готовая реализация ровно design-only `pg_health`**: health-анализ (index/connection/buffer-cache/vacuum/replication), index-tuning, EXPLAIN, hypothetical indexes, безопасный настраиваемый read-only. Подключить для роли `1c-dba` ИЛИ взять эталоном для `mcp/ops_pg_mcp.py`.
- **docker/mcp-gateway** (Go, официальный, MIT, ~1.5k★) — контейнерная изоляция/доставка MCP-серверов (каталог 270+ подписанных). Ложится на принцип аудируемости (память про Docker-MCP-Gateway).
- **zereight/gitlab-mcp** (TS, MIT, ~1.7k★, self-hosted) — под gitlab-инстанс; дать PAT с **read-scope** (имеет полный CRUD).
- **grafana/mcp-grafana** (Go, Apache-2.0, ~3.2k★) — если мониторинг кластера в Grafana.
- **prometheus-mcp-server** (`pab1it0`, Python, MIT) — PromQL-запросы; образец для своего `cluster_metrics`.

**Взять за образец (для своих MCP, развитие `onec_mcp.py`/`bsl_ls_mcp.py`):**
- **feenlace/mcp-1c** (Go, MIT, RU, read-only) — метаданные + `execute_query` (SELECT) + `get_event_log` через HTTP-сервис 1С. `get_event_log`/`execute_query` ≈ ваш будущий `tech_journal_parse`/снятие живых данных. Требует развёрнутого HTTP-сервиса в ИБ — код чужого `.cfe` проверять.
- **ROCTUP/1c-mcp-toolkit** (1С, GPL-3.0, ~183★) — как 1С-сторона отдаёт метаданные/данные наружу (запускать как сервис, код не линковать).
- **DitriXNew/MCP-DB-Client** (C++, MIT) — MCP изнутри 1С через native-компоненту (транспорт в DLL, tools пишет 1С-разработчик).
- **1c-syntax/claude-code-bsl-lsp** (MIT) — официальный плагин Claude Code для BSL LSP; проверить, не дублирует ли `bsl_ls_mcp.py`. У самого **bsl-language-server** появляется встроенный MCP-режим — может заменить самописный мост.

⚠️ **НЕ использовать** официальный `@modelcontextprotocol/server-postgres` — **заархивирован**, найдена SQL-инъекция в обход read-only. Урок для нашего дизайна: «read-only» через строковую сборку SQL ненадёжен — безопасный парсинг (как у crystaldba) обязателен.

---

## Что сделать с toolkit (рекомендации)
1. **Подключить готовое** там, где не надо изобретать: `crystaldba/postgres-mcp` (закрывает `pg_health`), `LazarenkoA/prometheus_1C_exporter` (мониторинг кластера), `docker/mcp-gateway` (изоляция MCP).
2. **Развить свои MCP** по образцу `feenlace/mcp-1c` + ядро парсинга ТЖ `medigor/tech-log-parser` (Rust/MIT) → `tech_journal_parse`.
3. **Сослаться в references** на проверенные инструменты (сделано: containers, cicd, dba, monitoring) — пользователю не надо искать заново.
4. **Лицензии**: переиспользовать код только из MIT/Apache/BSD; GPL/AGPL/без-лицензии — запускать как сервис, не копировать.
