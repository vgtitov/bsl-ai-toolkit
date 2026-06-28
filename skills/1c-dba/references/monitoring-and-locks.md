# Мониторинг и блокировки PostgreSQL под 1С

Правило инцидента: **сначала диагностика, потом действие**. Не «убивать» сеансы вслепую и не крутить
параметры — сними состояние (`pg_stat_activity`, `pg_locks`, `pg_stat_statements`/`pgpro_stats`, IO/CPU),
найди корневую причину, затем чини. Многие «тормоза СУБД» на деле — неоптимальный запрос 1С (корень — у
разработчика, `1c-dev`), а не настройка сервера.

---

## «7 команд» базовой диагностики

```sql
-- 1. Размер текущей базы
SELECT pg_size_pretty(pg_database_size(current_database()));

-- 2. Количество таблиц в схеме public
SELECT count(*) FROM information_schema.tables WHERE table_schema='public';

-- 3. Топ-10 самых больших таблиц
SELECT n.nspname||'.'||c.relname AS relation, pg_size_pretty(pg_relation_size(c.oid)) AS size
FROM pg_class c LEFT JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog','information_schema')
ORDER BY pg_relation_size(c.oid) DESC LIMIT 10;

-- 4. Подключённые сеансы
SELECT datname, usename, client_addr, client_port FROM pg_stat_activity;

-- 5. Активность конкретного пользователя
SELECT datname FROM pg_stat_activity WHERE usename='postgres';

-- 6. Перечитать настройки без перезапуска (после правки postgresql.conf)
SELECT pg_reload_conf();

-- 7. Выполняющиеся запросы вместе с состоянием блокировок
SELECT s.pid, age(clock_timestamp(), s.query_start) AS duration, s.usename,
       s.query, l.mode, l.locktype, l.granted, s.datname
FROM pg_stat_activity s
JOIN pg_locks l ON s.pid = l.pid
ORDER BY l.granted, l.pid DESC;
```

Снять зависший запрос (см. различие в разделе блокировок):
```sql
SELECT pg_cancel_backend(<pid>);     -- мягко: отменить текущий запрос
SELECT pg_terminate_backend(<pid>);  -- жёстко: завершить весь сеанс
```

---

## Блокировки и зависшие транзакции

### Кто кого блокирует
В `pg_locks` строки с `granted = false` — это ожидающие. Связать «жертву» и «виновника»:
```sql
SELECT blocked.pid     AS blocked_pid,
       blocked_a.usename AS blocked_user,
       blocked_a.query AS blocked_query,
       blocking.pid    AS blocking_pid,
       blocking_a.usename AS blocking_user,
       blocking_a.query AS blocking_query,
       blocking_a.state AS blocking_state,
       age(clock_timestamp(), blocking_a.xact_start) AS blocking_xact_age
FROM pg_locks blocked
JOIN pg_stat_activity blocked_a ON blocked_a.pid = blocked.pid
JOIN pg_locks blocking
     ON blocking.locktype = blocked.locktype
    AND blocking.database IS NOT DISTINCT FROM blocked.database
    AND blocking.relation IS NOT DISTINCT FROM blocked.relation
    AND blocking.page     IS NOT DISTINCT FROM blocked.page
    AND blocking.tuple    IS NOT DISTINCT FROM blocked.tuple
    AND blocking.transactionid IS NOT DISTINCT FROM blocked.transactionid
    AND blocking.pid <> blocked.pid
JOIN pg_stat_activity blocking_a ON blocking_a.pid = blocking.pid
WHERE NOT blocked.granted;
```
Проще (PostgreSQL 9.6+): `SELECT pg_blocking_pids(<pid>);` — массив PID, блокирующих данный сеанс.

### Интерпретация и действие
- **Виновник в `state = 'idle in transaction'`** — открыл транзакцию и держит (частая беда: клиент 1С/
  внешнее подключение не закоммитил/не откатил). Сначала понять, чей это сеанс (`usename`,
  `application_name`, `client_addr`); по возможности завершить операцию на стороне приложения.
- **Долгая `xact_start`** при активном запросе — длинная транзакция держит блокировки и мешает VACUUM
  (мёртвые строки не убираются, пока их «видит» старая транзакция).
- **Снятие — по нарастающей:** сначала `pg_cancel_backend` (отменит текущий запрос, сеанс жив — мягко).
  Если не помогло или сеанс «idle in transaction» — `pg_terminate_backend` (рвёт сеанс целиком). Перед
  жёстким завершением убедись, что это не критичная транзакция (риск отката большой работы).
- Уровень блокировок 1С (управляемые блокировки, дедлоки приложения) — это слой приложения; на уровне
  СУБД ты видишь следствие. Дедлоки самой СУБД пишутся в лог (`deadlock`).

### Длинные транзакции и долгие/idle-in-transaction сеансы
```sql
SELECT pid, usename, application_name, client_addr, state,
       age(clock_timestamp(), xact_start)  AS xact_age,
       age(clock_timestamp(), query_start) AS query_age,
       left(query, 200) AS query
FROM pg_stat_activity
WHERE state <> 'idle'
ORDER BY xact_start NULLS LAST;
```
Сигнал к разбору: `xact_age` в десятки минут/часы, особенно `idle in transaction`. Профилактика —
`idle_in_transaction_session_timeout`, `statement_timeout` (выставлять осознанно, чтобы не рвать
легитимные длинные регламенты 1С).

---

## Cache hit ratio

Доля чтений из кэша СУБД (а не с диска). Цель под 1С — высоко (> 95%, в идеале 99%+):
```sql
SELECT datname,
       round(100.0 * blks_hit / NULLIF(blks_hit + blks_read, 0), 2) AS cache_hit_pct
FROM pg_stat_database
WHERE datname = current_database();
```
Низкий ratio → мало `shared_buffers` либо рабочий набор не помещается в RAM (см. `postgres-tuning.md` §1).
Индексный кэш-хит по таблицам — через `pg_statio_user_tables`/`pg_statio_user_indexes`.

> `pg_stat_database` накапливает с момента сброса статистики. НЕ сбрасывай `pg_stat_reset()` в регламенте
> — потеряешь базу для этих метрик (см. `maintenance.md`).

---

## age(datfrozenxid) — контроль приближения к wraparound

```sql
SELECT datname, age(datfrozenxid) AS xid_age
FROM pg_database ORDER BY xid_age DESC;
```
`age` растёт по мере новых транзакций; критический предел — около 2 млрд. Чем выше — тем срочнее
заморозка (`VACUUM FREEZE`, см. `maintenance.md` §3). Завести алерт на порог (напр. > 1 млрд). Самые
«старые» таблицы:
```sql
SELECT c.relname, age(c.relfrozenxid) AS xid_age
FROM pg_class c
WHERE c.relkind IN ('r','m','t')
ORDER BY age(c.relfrozenxid) DESC LIMIT 20;
```

---

## pg_stat_statements — топ тяжёлых запросов

Требует `shared_preload_libraries = '...,pg_stat_statements'`, `compute_query_id = auto` и
`CREATE EXTENSION pg_stat_statements;` (рестарт). Топ по суммарному времени выполнения:
```sql
SELECT queryid, calls, round(total_exec_time) AS total_ms,
       round(mean_exec_time, 2) AS mean_ms, rows,
       left(query, 200) AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC LIMIT 20;
```
Интерпретация: высокий `total_ms` при многих `calls` = частый умеренный запрос (кандидат на кэш/
оптимизацию в коде 1С); высокий `mean_ms` = тяжёлый запрос (план через `EXPLAIN (ANALYZE, BUFFERS)`).
Дальше — план запроса (сканы вместо индексов, плохие соединения) → корень обычно в коде/индексах 1С,
передавать разработчику (`1c-dev`).

---

## pgpro_stats (Postgres Pro Enterprise)

Расширение Postgres Pro — надстройка над `pg_stat_statements`: помимо времени собирает **планы** и
**статистику ожиданий** (`wait_stats`). Включается через `shared_preload_libraries` (рестарт),
представления см. в справке Postgres Pro своей версии. Полезно для 1С, чтобы видеть, на чём запрос **ждёт**
(IO, блокировки, IPC), а не только сколько он шёл:
```sql
SELECT queryid, query, planid, plan, wait_stats
FROM pgpro_stats_statements
WHERE query LIKE 'ВЫБРАТЬ%' LIMIT 20;
-- wait_stats: {"IO": {"DataFileRead": 10}, "IPC": {...}, "Total": {...}}
```
> В части версий статистика **планирования** не собирается (соответствующие столбцы = 0) —
> ориентируйся на статистику выполнения и `wait_stats`. Точный набор представлений/столбцов — по справке
> своей версии Postgres Pro, непроверенное помечай **[проверить]**.

---

## Базовый набор метрик для алертов (что мониторить постоянно)
- среднее число активных воркеров автовакуума < `autovacuum_max_workers` (см. `postgres-tuning.md` §4);
- cache hit ratio > 95%;
- `age(datfrozenxid)` ниже порога (алерт при росте);
- наличие блокировок с `granted=false`/длинные `idle in transaction`;
- топ `pg_stat_statements`/`pgpro_stats` по времени и ожиданиям;
- IO/CPU/swap на уровне ОС (`iostat`/`vmstat`/`top`/`free`);
- мёртвые строки и распухание (тренд, см. `maintenance.md`).

Любой алерт — повод снять диагностику (запросы выше), а не сразу менять конфиг или «убивать» сеансы.

---

## Инструменты мониторинга и сопутствующее

- **Постоянный мониторинг:** `pg_stat_statements` + **mamonsu** (агент Postgres Pro) → **Zabbix** или
  **Prometheus** (+Grafana). Целевой **cache hit ratio ≥ 90%** (под высоконагруж. с тяжёлым IO — > 95%).
- **Анализ логов разово, не постоянно:** `pgBadger` / `pgFouine` / `pghero` дают разбор медленных
  запросов, но создают **IO-overhead** на сборе → включать на время расследования, не держать в проде.
- **Дедлоки** (счётчик с момента сброса статистики):
  ```sql
  SELECT deadlocks FROM pg_stat_database WHERE datname = current_database();
  ```
  Рост → разбирать в логе (`deadlock`); корень дедлоков 1С обычно в коде/порядке блокировок (`1c-dev`).
- **`wal_sync_method`** — метод синхронизации WAL: на Linux обычно `fdatasync`, на Windows
  `open_datasync`. Менять только по результатам `pg_test_fsync`, помечай **[проверить]**.
- **Троттлинг автовакуума под нагрузкой:** держать CPU в пике ≤ 80% (см. `postgres-tuning.md` §4) —
  иначе автовакуум конкурирует с пользовательскими сеансами.
