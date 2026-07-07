# /// script
# dependencies = ["mcp", "openpyxl"]
# ///
"""MCP-сервер onec-ops — инструменты ЭКСПЛУАТАЦИИ 1С (read-only). Первый инструмент:
`tech_journal_parse` — разбор технологического журнала по каталогу ИЛИ ГРУППЕ каталогов
(разные рабочие серверы кластера, несколько кластеров) для расследования проблем
производительности НА УРОВНЕ ВСЕЙ КОМПАНИИ. Каждое событие тегируется источником
(метка сервера/кластера) + процессом + файлом, чтобы локализовать проблему.

Строго read-only: только ЧИТАЕТ файлы ТЖ, ничего не меняет в ИБ/СУБД/кластере.
Безопасность: пути — из доверенных аргументов; значения свойств (Sql/Context) усекаются;
персональные данные в тексте запросов могут присутствовать — не отправлять сырой вывод
во внешние сервисы (см. SKILL.md ролей, контур данных).

Образцы-ориентиры (см. docs/ops-tools-catalog.md): medigor/tech-log-parser, OneSTools.TechLog.

ENV: ничего обязательного (пути передаются аргументами инструмента).
"""
import os
import re
from collections import Counter


def load_dotenv_defaults(*dirs):
    """Подхватить KEY=VALUE из .env в указанных каталогах (по умолчанию: CWD, корень репо).
    Уже установленные переменные окружения НЕ перетираются — .env лишь дополняет.
    Возвращает {ключ: значение} реально добавленных. Секреты наружу не печатает."""
    search = list(dirs) or [os.getcwd(), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
    added = {}
    for d in search:
        path = os.path.join(d, ".env")
        if not os.path.isfile(path):
            continue
        try:
            lines = open(path, "r", encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v
                added[k] = v
    return added

# Стартовая строка события ТЖ: mm:ss.микросек-<длительность>,<Событие>,<остальное>
_EVENT_RE = re.compile(r"^(\d\d):(\d\d)\.(\d+)-(\d+),(.*)$")
_MAXVAL = 1000  # усечение длинных значений (Sql/Context) в выводе


def _split_props(s):
    """Quote-aware split строки свойств по запятым (значения могут быть в '...'/\"...\")."""
    out, cur, q = [], "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            cur += ch
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _parse_props(propstr):
    props = {}
    for tok in _split_props(propstr):
        tok = tok.strip()
        if not tok or "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] in "'\"" and v[-1] == v[0]:
            v = v[1:-1]
        if len(v) > _MAXVAL:
            v = v[:_MAXVAL] + "…"
        props[k.strip()] = v
    return props


def _norm_ts(s):
    """Нормализовать границу времени since/until к сортируемым 14 цифрам YYYYMMDDHHMMSS."""
    if not s:
        return None
    d = re.sub(r"\D", "", str(s))
    if not d:
        return None
    if len(d) == 12 and d[:2] not in ("19", "20"):  # YYMMDDHHMMSS → добавить век
        d = "20" + d
    return (d + "0" * 14)[:14]


def _file_base(stem):
    """Из имени файла ТЖ YYMMDDHH → (год, месяц, день, час) или None."""
    d = re.sub(r"\D", "", stem)
    if len(d) >= 8:
        return 2000 + int(d[0:2]), int(d[2:4]), int(d[4:6]), int(d[6:8])
    return None


def _normalize_roots(roots):
    if isinstance(roots, (str, os.PathLike)):
        roots = [roots]
    norm = []
    for r in roots:
        if isinstance(r, dict):
            path = r.get("path")
            label = r.get("label") or os.path.basename(os.path.normpath(path)) if path else None
        else:
            path = r
            label = os.path.basename(os.path.normpath(str(r)))
        if path:
            norm.append((label, str(path)))
    return norm


def parse_techlog(roots, events=None, min_duration=0, since=None, until=None, top=50):
    """Разобрать ТЖ по одному/нескольким каталогам с атрибуцией источника.

    roots: путь, список путей, или список {"path","label"} (label = метка сервера/кластера).
    events: список имён событий для отбора (напр. ["TLOCK","TDEADLOCK","DBMSSQL"]); None = все.
    min_duration: минимальная длительность (поле ТЖ, микросекунды [проверить под платформу]).
    since/until: границы времени (YYYY-MM-DD HH:MM или YYMMDDHHMM…).
    top: сколько самых длительных событий вернуть в events[].
    """
    events_filter = set(events) if events else None
    since_n, until_n = _norm_ts(since), _norm_ts(until)
    norm_roots = _normalize_roots(roots)

    collected = []
    warnings = []
    files_seen = 0
    by_event = Counter()
    by_source = Counter()

    for label, path in norm_roots:
        if not os.path.isdir(path):
            warnings.append(f"каталог не найден или не директория: {path}")
            continue
        for dirpath, _, files in os.walk(path):
            for fn in files:
                if not fn.lower().endswith(".log"):
                    continue
                full = os.path.join(dirpath, fn)
                files_seen += 1
                process = os.path.basename(dirpath)
                base = _file_base(os.path.splitext(fn)[0])
                try:
                    text = open(full, "r", encoding="utf-8", errors="replace").read()
                except OSError as e:
                    warnings.append(f"не прочитан {full}: {e}")
                    continue
                _parse_file(text, label, process, full, base,
                            events_filter, min_duration, since_n, until_n,
                            collected, by_event, by_source)

    collected.sort(key=lambda e: e["duration"], reverse=True)
    return {
        "summary": {
            "roots": [{"label": l, "path": p} for l, p in norm_roots],
            "files": files_seen,
            "events_total": len(collected),
            "by_event": dict(by_event.most_common()),
            "by_source": dict(by_source.most_common()),
        },
        "events": collected[: max(0, int(top))],
        "warnings": warnings,
    }


def _parse_file(text, label, process, full, base, events_filter, min_duration,
                since_n, until_n, collected, by_event, by_source):
    cur = None

    def finalize(ev):
        rest = ev.pop("_rest")
        parts = rest.split(",", 2)
        name = parts[0].strip()
        level = parts[1].strip() if len(parts) > 1 else ""
        propstr = parts[2] if len(parts) > 2 else ""
        if ev["_extra"]:
            propstr += ev["_extra"]
        ev.pop("_extra")
        if events_filter and name not in events_filter:
            return
        if ev["duration"] < min_duration:
            return
        ts14 = ev.pop("_ts14")
        if since_n and ts14 and ts14 < since_n:
            return
        if until_n and ts14 and ts14 > until_n:
            return
        ev["event"] = name
        ev["level"] = level
        ev["props"] = _parse_props(propstr)
        collected.append(ev)
        by_event[name] += 1
        by_source[label] += 1

    for line in text.splitlines():
        m = _EVENT_RE.match(line)
        if m:
            if cur is not None:
                finalize(cur)
            mm, ss, micro, dur, rest = m.groups()
            if base:
                y, mo, d, hh = base
                ts = f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mm}:{ss}.{micro}"
                ts14 = f"{y:04d}{mo:02d}{d:02d}{hh:02d}{mm}{ss}"
            else:
                ts, ts14 = f"??:?? {mm}:{ss}.{micro}", None
            cur = {
                "source": label, "process": process, "file": full,
                "timestamp": ts, "duration": int(dur),
                "_rest": rest, "_extra": "", "_ts14": ts14,
            }
        elif cur is not None:
            cur["_extra"] += "\n" + line
    if cur is not None:
        finalize(cur)


# ============================================================================
# Apdex — КАНОНИЧНАЯ формула. Бизнесу важно реальное УСКОРЕНИЕ операций, не только
# стабильность: Apdex_T = (Satisfied + Tolerating/2) / Total, где
#   Satisfied   — время отклика ≤ T            (пользователь доволен),
#   Tolerating  — T < время ≤ 4T               (терпит),
#   Frustrated  — время > 4T                   (раздражён).
# T (порог удовлетворённости) задаётся ОТДЕЛЬНО под каждую операцию (открытие формы,
# проведение документа, отчёт): у быстрых операций T меньше. Сравнивай Apdex до/после
# оптимизации — рост Apdex = реальное ускорение, ощущаемое пользователями.
# ============================================================================

def _apdex_rating(a):
    if a >= 0.94: return "excellent"
    if a >= 0.85: return "good"
    if a >= 0.70: return "fair"
    if a >= 0.50: return "poor"
    return "unacceptable"


def apdex(samples, t):
    """Apdex по выборке времён отклика (сек) и порогу T (сек). Границы включительны: ≤T satisfied, ≤4T tolerating."""
    t = float(t)
    sat = tol = fr = 0
    for x in samples:
        x = float(x)
        if x <= t:
            sat += 1
        elif x <= 4 * t:
            tol += 1
        else:
            fr += 1
    total = sat + tol + fr
    a = (sat + tol / 2) / total if total else 0.0
    return {"apdex": round(a, 4), "threshold_t": t, "satisfied": sat, "tolerating": tol,
            "frustrated": fr, "total": total, "rating": _apdex_rating(a) if total else "n/a"}


def apdex_by_operation(records, thresholds=None, default_t=1.0):
    """Apdex отдельно по каждой операции (у каждой свой порог T). records: [{"operation","duration"}].
    thresholds: {операция: T}. Возвращает operations[] (худшие сверху) — где именно «болит» у пользователей."""
    thresholds = thresholds or {}
    groups = {}
    for r in records:
        groups.setdefault(r.get("operation", "?"), []).append(float(r["duration"]))
    ops = []
    for op, durs in groups.items():
        res = apdex(durs, thresholds.get(op, default_t))
        res["operation"] = op
        ops.append(res)
    ops.sort(key=lambda o: o["apdex"])
    return {"operations": ops, "worst": ops[0] if ops else None}


# ============================================================================
# БСП «Оценка производительности»: парсер ШТАТНОГО экспорта замеров (Apdex).
# Регламентное задание ЭкспортОценкиПроизводительности пишет XML
# (ns …/apdexExport/1.0.0.4): KeyOperation(name, nameFull, priority, targetValue, uid)
# → measurement(value=длительность сек, weight, tUTC, userName, sessionNumber, runningError).
# targetValue операции = её порог T для Apdex. Локальный каталог или сетевая папка/FTP.
# ============================================================================

def _localname(tag):
    return tag.rsplit("}", 1)[-1]


def bsp_apdex_parse(source, default_t=1.0):
    """Разобрать файл или каталог XML-экспорта БСП «Оценка производительности» → Apdex по
    каждой ключевой операции (T = targetValue операции), худшие сверху. Read-only."""
    import xml.etree.ElementTree as ET
    paths = []
    if os.path.isdir(source):
        for fn in sorted(os.listdir(source)):
            if fn.lower().endswith(".xml"):
                paths.append(os.path.join(source, fn))
    elif os.path.isfile(source):
        paths.append(source)
    warnings = []
    ops = {}  # name -> {"t":..., "name_full":..., "priority":..., "values": [...], "errors": n}
    parsed = 0
    for p in paths:
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError as e:
            warnings.append(f"{os.path.basename(p)}: не XML ({e})")
            continue
        parsed += 1
        for ko in root:
            if _localname(ko.tag) != "KeyOperation":
                continue
            fields = {}
            values, errors = [], 0
            for child in ko:
                ln = _localname(child.tag)
                if ln == "measurement":
                    mv = {_localname(c.tag): (c.text or "") for c in child}
                    try:
                        values.append(float(mv.get("value")))
                    except (TypeError, ValueError):
                        continue
                    if str(mv.get("runningError", "")).lower() == "true":
                        errors += 1
                else:
                    fields[ln] = (child.text or "").strip()
            name = fields.get("name") or "?"
            try:
                t = float(fields.get("targetValue") or default_t) or default_t
            except ValueError:
                t = default_t
            agg = ops.setdefault(name, {"t": t, "name_full": fields.get("nameFull", ""),
                                        "priority": fields.get("priority", ""), "values": [], "errors": 0})
            agg["values"].extend(values)
            agg["errors"] += errors
    operations = []
    for name, agg in ops.items():
        res = apdex(agg["values"], agg["t"])
        res.update({"operation": name, "name_full": agg["name_full"],
                    "priority": agg["priority"], "errors": agg["errors"]})
        operations.append(res)
    operations.sort(key=lambda o: o["apdex"])
    return {"files": parsed, "operations": operations,
            "worst": operations[0] if operations else None, "warnings": warnings}


def apdex_sender_lines(parse_result, zbx_host):
    """Строки для zabbix_sender -i (trapper items): '<host> 1c.apdex[<операция>] <значение>'
    + 1c.apdex.worst (худший Apdex) и 1c.apdex.worst.name (какая операция)."""
    lines = []
    for op in parse_result.get("operations") or []:
        key = re.sub(r"[\[\]\s\"']+", "_", str(op["operation"]))
        lines.append(f"{zbx_host} 1c.apdex[{key}] {op['apdex']:.4f}")
    worst = parse_result.get("worst")
    if worst:
        lines.append(f"{zbx_host} 1c.apdex.worst {worst['apdex']:.4f}")
        lines.append(f'{zbx_host} 1c.apdex.worst.name "{worst["operation"]}"')
    return lines


# ============================================================================
# Диагностика по порогам 1С/СУБД — находит проблемы из метрик (Zabbix/счётчики).
# Пороги — общеизвестная методика эксплуатации 1С (см. references performance-apdex,
# investigation-methodology). Источник истины — измерение; здесь — правила интерпретации.
# ============================================================================

def diagnose_metrics(metrics, cores=None):
    """Применить пороги к снимку метрик → список проблем с severity и рекомендацией.
    Понимает: swap_pct, page_life_expectancy, cpu_queue (+cores), cpu_util, disk_queue,
    disk_util, disk_await_ms, mem_available_pct, net_errors_per_sec,
    deadlocks_per_sec, lock_waits_per_sec, buffer_cache_hit_pct, latches_ms.
    threshold_value — числовой порог (для ранжирования по кратности превышения)."""
    flags = []

    def add(metric, value, threshold, severity, problem, rec, thr_value=None):
        flags.append({"metric": metric, "value": value, "threshold": threshold,
                      "threshold_value": thr_value, "severity": severity,
                      "problem": problem, "recommendation": rec})

    m = metrics
    if m.get("swap_pct") is not None and m["swap_pct"] > 70:
        add("swap_pct", m["swap_pct"], ">70%", "high",
            "Активный своп — нехватка RAM на сервере 1С",
            "Добавить RAM / разнести процессы; своп под нагрузкой резко роняет производительность", 70)
    if m.get("page_life_expectancy") is not None and m["page_life_expectancy"] < 300:
        add("page_life_expectancy", m["page_life_expectancy"], "<300s", "high",
            "SQL Page Life Expectancy <300с — буферный кэш вымывается, нехватка RAM у СУБД",
            "Добавить RAM серверу СУБД и/или найти запросы, читающие лишние страницы (ТОП-25, план запроса)", 300)
    if m.get("cpu_queue") is not None:
        thr = 2 * cores if cores else None
        if thr and m["cpu_queue"] > thr:
            add("cpu_queue", m["cpu_queue"], f">2×ядер ({thr})",
                "critical" if m["cpu_queue"] > 16 else "high",
                "Очередь к ЦП превышает 2×число ядер — нехватка CPU",
                "Профилировать ТОП-25 вызовов, оптимизировать тяжёлый код/запросы; при стабильном дефиците — нарастить CPU", thr)
        elif m["cpu_queue"] > 16:
            add("cpu_queue", m["cpu_queue"], ">16", "critical",
                "Очередь к ЦП >16 — серьёзная нехватка CPU", "См. рекомендации по ТОП-25 и наращиванию CPU", 16)
    if m.get("cpu_util") is not None and m["cpu_util"] > 70:
        add("cpu_util", m["cpu_util"], ">70% (методика 1С)", "high" if m["cpu_util"] > 90 else "medium",
            "ЦП загружен выше методического порога 70% — запас исчерпывается, отклик деградирует",
            "Найти ТОП-25 потребителей (ТЖ CALL/DBMSSQL, планы запросов); балансировка rphost; при устойчивом >90% — нарастить CPU", 70)
    if m.get("mem_available_pct") is not None and m["mem_available_pct"] < 10:
        add("mem_available_pct", m["mem_available_pct"], "<10%", "high",
            "Свободной RAM <10% — сервер на грани свопа/выселения кэшей",
            "Добавить RAM / разнести роли (1С и СУБД на разных серверах); проверить утечки rphost (рестарты по памяти)", 10)
    if m.get("mem_available_bytes") is not None and m["mem_available_bytes"] < 2 * 1024**3:
        _gib = m["mem_available_bytes"] / 1024**3
        add("mem_available_bytes", m["mem_available_bytes"],
            "<2 GiB", "high" if _gib < 0.5 else "medium",
            f"Свободной RAM всего {_gib:.1f} GiB — сервер близок к нехватке памяти",
            "Добавить RAM / проверить рост rphost (Working Set) и утечки; настроить рестарт rphost по порогу памяти", 2 * 1024**3)
    if m.get("commit_pct") is not None and m["commit_pct"] > 80:
        add("commit_pct", m["commit_pct"], ">80%", "high" if m["commit_pct"] > 90 else "medium",
            "Выделенная виртуальная память (Commit) близка к пределу — риск отказов выделения памяти",
            "Добавить RAM/увеличить файл подкачки; найти процессы с растущим Commit (rphost, SQL)", 80)
    if m.get("pages_per_sec") is not None and m["pages_per_sec"] > 20:
        add("pages_per_sec", m["pages_per_sec"], ">20 (методика 1С)",
            "medium" if m["pages_per_sec"] > 1000 else "low",
            "Memory Pages/sec выше методического порога — возможен пейджинг ИЛИ интенсивный файловый I/O",
            "ЛОВУШКА Windows: Pages/sec включает memory-mapped I/O (чтение DLL/файлов 1С), а не только pagefile. "
            "Реальный своп подтверждается ТОЛЬКО связкой: Paging file %Usage растёт + Available RAM мало. "
            "Если Available в норме и %Usage≈0 — это файловый I/O: смотреть, что читает диск (Disk Read Bytes/sec)", 20)
    if m.get("tlock_count") is not None and m["tlock_count"] > 0:
        add("tlock_count", m["tlock_count"], ">0", "medium",
            "Длинные ожидания на блокировках (TLOCK) — транзакции стоят в очереди за данными",
            "Разобрать ТЖ по TLOCK: какие таблицы/регистры, кто держит; сократить транзакции, проверить индексы", 0)
    if m.get("lock_timeouts_count") is not None and m["lock_timeouts_count"] > 0:
        add("lock_timeouts_count", m["lock_timeouts_count"], ">0", "high",
            "Таймауты блокировок (TTIMEOUT) — пользователи ловят «превышено время ожидания блокировки»",
            "Разобрать ТЖ по TTIMEOUT/TLOCK (tech_journal_parse): кто держит, длина транзакций, индексы под условия", 0)
    if m.get("excp_count") is not None and m["excp_count"] > 0:
        add("excp_count", m["excp_count"], ">0", "low",
            "Исключения в ТЖ (EXCP) — есть ошибки на сервере 1С",
            "Выбрать EXCP из ТЖ (tech_journal_parse events=[\"EXCP\"]), сгруппировать по контексту — часто виден источник тормозов/сбоев", 0)
    if m.get("cpu_iowait_pct") is not None and m["cpu_iowait_pct"] > 10:
        add("cpu_iowait_pct", m["cpu_iowait_pct"], ">10%", "high" if m["cpu_iowait_pct"] > 25 else "medium",
            "CPU iowait высокий — процессор простаивает в ожидании диска",
            "Смотреть дисковую подсистему (await/util), быстрее диски, убрать лишний I/O (запросы, temp files)", 10)
    if m.get("pg_temp_files_per_sec") is not None and m["pg_temp_files_per_sec"] > 0.1:
        add("pg_temp_files_per_sec", m["pg_temp_files_per_sec"], ">0.1/с", "medium",
            "PostgreSQL пишет временные файлы — сортировки/хэши не помещаются в work_mem",
            "Поднять work_mem (осторожно, на сессию); найти запросы с Sort/Hash on disk (log_temp_files, EXPLAIN)", 0.1)
    if m.get("pg_bloating_tables") is not None and m["pg_bloating_tables"] > 10:
        add("pg_bloating_tables", m["pg_bloating_tables"], ">10", "medium",
            "Распухшие таблицы PostgreSQL — bloat замедляет чтение и раздувает диск",
            "Настроить autovacuum агрессивнее для горячих таблиц; разово — pg_repack без блокировки. "
            "Если рядом видны длинные транзакции (pg_long_transactions) — сперва убрать их: "
            "открытая транзакция держит xmin и не даёт vacuum'у чистить строки", 10)
    if m.get("pg_connections_pct") is not None and m["pg_connections_pct"] > 80:
        add("pg_connections_pct", m["pg_connections_pct"], ">80%", "high" if m["pg_connections_pct"] > 90 else "medium",
            "Подключения PostgreSQL близки к max_connections — новые соединения получат отказ",
            "Проверить, кто держит соединения (idle in transaction?); пул соединений 1С/pgbouncer; "
            "поднимать max_connections — последним (каждый backend ест RAM)", 80)
    if m.get("rphost_cpu_cap_pct") is not None and m["rphost_cpu_cap_pct"] > 70:
        add("rphost_cpu_cap_pct", m["rphost_cpu_cap_pct"], ">70% лицензионного лимита",
            "high" if m["rphost_cpu_cap_pct"] > 85 else "medium",
            "rphost выбирает лицензионный потолок ядер (лицензия ПРОФ ограничивает рабочие процессы "
            "сервера 1С): часть CPU сервера простаивает, а пользователи ждут",
            "Смотреть НЕ системный CPU, а CPU rphost против лимита ядер лицензии. Лечение: "
            "оптимизировать топ-потребителей (ТЖ CALL/DBMSSQL), выключить лишние регламентные, "
            "перенести фон на окна; апгрейд на КОРП — последним, когда код выжат", 70)
    if m.get("disk_queue") is not None and m["disk_queue"] > 2:
        add("disk_queue", m["disk_queue"], ">2 на диск", "high",
            "Очередь к диску >2 — дисковая подсистема не справляется",
            "Быстрее диски (NVMe), разнести данные/tempdb/логи по дискам; убрать избыточный I/O от запросов", 2)
    if m.get("disk_util") is not None and m["disk_util"] > 90:
        add("disk_util", m["disk_util"], ">90%", "high",
            "Диск занят >90% времени — узкое место I/O",
            "Проверить кто грузит (СУБД/бэкапы/антивирус), разнести нагрузку, быстрее диски", 90)
    if m.get("disk_await_ms") is not None and m["disk_await_ms"] > 20:
        add("disk_await_ms", m["disk_await_ms"], ">20ms", "high" if m["disk_await_ms"] > 50 else "medium",
            "Латентность диска высокая — операции ждут I/O",
            "Для СУБД целевая латентность ≤5–10мс: быстрее диски/массив, проверить очереди и кэш контроллера", 20)
    if m.get("net_errors_per_sec") is not None and m["net_errors_per_sec"] > 0:
        add("net_errors_per_sec", m["net_errors_per_sec"], ">0", "medium",
            "Ошибки сетевого интерфейса — потери/ретрансмиты бьют по клиент-серверным вызовам",
            "Проверить дуплекс/драйвер/кабель/коммутатор; для кластера 1С сеть до СУБД критична", 0)
    if m.get("deadlocks_per_sec") is not None and m["deadlocks_per_sec"] > 0:
        add("deadlocks_per_sec", m["deadlocks_per_sec"], ">0", "high",
            "Взаимоблокировки (дедлоки) — транзакции конфликтуют насмерть",
            "Разобрать по TDEADLOCK (см. locks-and-deadlocks): упорядочить захваты ресурсов, сократить транзакции, индексы под условия", 0)
    if m.get("lock_waits_per_sec") is not None and m["lock_waits_per_sec"] > 0:
        add("lock_waits_per_sec", m["lock_waits_per_sec"], ">0", "medium",
            "Ожидания на блокировках — конкуренция за данные",
            "Сократить длительность транзакций, проверить избыточные блокировки и индексы (locks-and-deadlocks)", 0)
    if m.get("buffer_cache_hit_pct") is not None and m["buffer_cache_hit_pct"] < 90:
        add("buffer_cache_hit_pct", m["buffer_cache_hit_pct"], "<90%", "medium",
            "Buffer cache hit низкий — данные читаются с диска, не из кэша",
            "Добавить RAM СУБД / оптимизировать запросы, читающие лишние страницы", 90)
    if m.get("latches_ms") is not None and m["latches_ms"] > 0:
        add("latches_ms", m["latches_ms"], ">0", "medium",
            "Латчи >0 — внутренняя конкуренция СУБД за страницы в памяти",
            "Проверить число файлов tempdb, горячие страницы/таблицы, всплески нагрузки", 0)
    return flags


# ============================================================================
# Zabbix — ОПЦИОНАЛЬНЫЙ адаптер (read-only). Использовать, когда у тебя доступен ТОЛЬКО
# Zabbix (админы дали его, а прямого доступа к ТЖ/rac/СУБД нет). Тянет проблемы/историю
# метрик по JSON-RPC API. Токен — read-only пользователь, из env ZABBIX_TOKEN.
# ============================================================================

def _http_post_json(url, payload, headers=None):  # pragma: no cover — реальный HTTP, в тестах подменяется
    import urllib.request
    import json as _json
    h = {"Content-Type": "application/json-rpc"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=_json.dumps(payload).encode("utf-8"), headers=h)
    with urllib.request.urlopen(req, timeout=60) as r:
        return _json.loads(r.read().decode("utf-8"))


def _post_supports_headers(post):
    """Принимает ли пользовательский _post третий аргумент headers (новая сигнатура)."""
    import inspect
    try:
        params = inspect.signature(post).parameters.values()
    except (TypeError, ValueError):  # builtin/C-callable — считаем современным
        return True
    return len(params) >= 3 or any(p.kind == p.VAR_KEYWORD for p in params)


def _zbx_call(url, method, params, auth=None, _post=None):
    """Вызов Zabbix JSON-RPC. Токен — заголовком Authorization: Bearer (6.4+/7.x;
    поле auth в payload deprecated в 7.0 и удалено в 7.2). Если переданный _post
    не принимает headers (старая сигнатура, по inspect), fallback — auth в payload (<7.2)."""
    payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}
    post = _post or _http_post_json
    if _post_supports_headers(post):
        resp = post(url, payload, {"Authorization": f"Bearer {auth}"} if auth else None)
    else:
        if auth:
            payload["auth"] = auth
        resp = post(url, payload)
    if isinstance(resp, dict) and resp.get("error"):
        e = resp["error"]
        raise RuntimeError(f"Zabbix API error: {e.get('message', '')} {e.get('data', '')}".strip())
    return resp.get("result") if isinstance(resp, dict) else resp


def zabbix_problems(url, auth=None, severities=None, recent=True, _post=None):
    """Текущие/недавние проблемы (триггеры) из Zabbix — как панель «Проблемы на сервере».
    ВАЖНО: problem.get НЕ поддерживает selectHosts (проверено на живом 7.0.27) — хосты
    добираются отдельно, см. zabbix_problems_with_hosts."""
    params = {"output": ["eventid", "objectid", "name", "severity", "clock"],
              "recent": recent, "sortfield": ["eventid"], "sortorder": "DESC"}
    if severities:
        params["severities"] = severities
    return _zbx_call(url, "problem.get", params, auth, _post) or []


def zabbix_problems_with_hosts(url, auth=None, severities=None, recent=True, _post=None):
    """Проблемы + имена хостов: problem.get (objectid = triggerid) → trigger.get selectHosts."""
    problems = zabbix_problems(url, auth=auth, severities=severities, recent=recent, _post=_post)
    trig_ids = sorted({str(p["objectid"]) for p in problems if p.get("objectid")})
    hosts_by_trigger = {}
    if trig_ids:
        for t in _zbx_call(url, "trigger.get",
                           {"triggerids": trig_ids, "output": ["triggerid"],
                            "selectHosts": ["host", "name"]}, auth, _post) or []:
            hosts_by_trigger[str(t.get("triggerid"))] = [
                h.get("name") or h.get("host") for h in t.get("hosts") or []]
    return [{**p, "hosts": hosts_by_trigger.get(str(p.get("objectid")), [])} for p in problems]


def zabbix_history(url, auth=None, itemids=None, history=0, time_from=None, limit=500, _post=None):
    """История значений метрики (item) из Zabbix. history: 0=float,1=str,3=uint. time_from — unixtime."""
    params = {"output": "extend", "itemids": itemids, "history": history,
              "sortfield": "clock", "sortorder": "DESC", "limit": limit}
    if time_from:
        params["time_from"] = time_from
    return _zbx_call(url, "history.get", params, auth, _post) or []


# ============================================================================
# Zabbix: слой АНАЛИЗА ПРОИЗВОДИТЕЛЬНОСТИ (read-only). Идеи взяты из обзора
# опенсорса (см. docs/ops-tools-catalog.md): универсальный вызов с whitelist
# read-only методов; составные инструменты «дашборд → items → история → находки»;
# автовыбор history.get/trend.get по глубине окна (как в grafana-zabbix).
# Дашборд может меняться — извлечение item'ов не завязано на конкретные виджеты.
# ============================================================================

_ZBX_READONLY_RE = re.compile(r"^(?:[a-z]+\.get|apiinfo\.version|[a-z]+\.export)$")


def zabbix_api(url, method, params=None, auth=None, _post=None):
    """Универсальный read-only вызов Zabbix API: разрешены только *.get / apiinfo.version /
    *.export (whitelist). Любой другой метод — отказ без обращения к серверу."""
    if not _ZBX_READONLY_RE.match(str(method or "")):
        raise RuntimeError(f"метод '{method}' запрещён: адаптер read-only (только *.get/apiinfo.version/*.export)")
    return _zbx_call(url, method, params or {}, auth, _post)


# --- классификация item → каноничная метрика -------------------------------
# Правила матчатся по "key_ name" (lower). transform: (value, units) → каноничное значение.
def _t_ident(v, units=""):
    return v


def _t_invert_pct(v, units=""):
    return 100.0 - v


def _t_to_ms(v, units=""):
    return v * 1000.0 if units == "s" else v


_ZBX_CLASS_RULES = [
    (r"processor queue length|system\.cpu\.load", "cpu_queue", _t_ident),
    # CPU конкретного процесса (\Process(...)\% Processor Time) — НЕ системный: 100% = одно ядро,
    # значение может быть кратно больше. Правило стоит ДО системного cpu_util (первый матч выигрывает).
    (r"\\process\(.*% processor time", "process_cpu_pct", _t_ident),
    # Режимы CPU (util[,idle]/user/system/…) — не «общая загрузка»; iowait — своя метрика,
    # остальные режимы отсекаются заглушкой ДО правила cpu_util (кейс: idle 82% ≠ «ЦП загружен»).
    (r"system\.cpu\.util\[,?iowait\]|cpu iowait", "cpu_iowait_pct", _t_ident),
    (r"system\.cpu\.util\[,?\w+\]|cpu (?:idle|user|system|steal|nice|guest|interrupt|softirq|privileged|dpc) time", None, None),
    (r"system\.cpu\.util|cpu utilization|% processor time", "cpu_util", _t_ident),
    (r"vm\.memory\.size\[pavailable\]|available memory in %|memory available %", "mem_available_pct", _t_ident),
    (r"vm\.memory\.size\[available\]|available memory in bytes", "mem_available_bytes", _t_ident),
    (r"memory.*pages/sec|\\memory\\pages", "pages_per_sec", _t_ident),
    (r"committed bytes in use|commit.*%|процент выделенной", "commit_pct", _t_ident),
    # PostgreSQL (официальный шаблон Zabbix)
    (r"pgsql\.cache\.hit|cache hit ratio", "buffer_cache_hit_pct", _t_ident),
    (r"pgsql\..*temp_files|temp files per second", "pg_temp_files_per_sec", _t_ident),
    (r"pgsql\.db\.bloating_tables|bloating tables", "pg_bloating_tables", _t_ident),
    (r"pgsql\.connections\.sum\.total_pct|connections sum: total, %", "pg_connections_pct", _t_ident),
    # Кастомные 1С-метрики (шаблоны Zabbix для кластера 1С; ключи 1c.*)
    (r"1c\.tj\.ttimeout|lock timeouts", "lock_timeouts_count", _t_ident),
    (r"1c\.tj\.tlock|long lock waits", "tlock_count", _t_ident),
    (r"1c\.tj\.excp|exceptions count", "excp_count", _t_ident),
    (r"1c\.sessions\.total|total sessions count", "sessions_total", _t_ident),
    (r"1c\.sessions\.thin|thin clients count", "sessions_thin", _t_ident),
    (r"1c\.sessions\.hibernate|hibernating sessions", "sessions_hibernate", _t_ident),
    (r"1c\.sessions\.licenses|licenses count", "licenses_count", _t_ident),
    (r"1c\.sessions\.connections|total connections count", "connections_total", _t_ident),
    (r"1c\.sessions\.max_held|max session held time", "session_max_held_s", _t_ident),
    (r"1c\.rphost\.mem|ram процессов rphost", "rphost_mem_bytes", _t_ident),
    (r"vm\.memory\.util|memory utilization", "mem_available_pct", _t_invert_pct),
    (r"system\.swap\.(?:size\[,?pfree\]|pfree)|free swap space in %|free swap", "swap_pct", _t_invert_pct),
    (r"swap.*pused|paging file.*% usage|used swap space", "swap_pct", _t_ident),
    (r"page life expectancy", "page_life_expectancy", _t_ident),
    (r"buffer cache hit", "buffer_cache_hit_pct", _t_ident),
    (r"deadlock", "deadlocks_per_sec", _t_ident),
    (r"lock wait", "lock_waits_per_sec", _t_ident),
    (r"latch wait|average latch wait", "latches_ms", _t_ident),
    (r"avg\. disk queue|disk queue length|vfs\.dev\.queue", "disk_queue", _t_ident),
    (r"vfs\.dev\.util|disk utili[sz]ation|% disk time", "disk_util", _t_ident),
    (r"avg\. disk sec/|await|disk latency|read time.*ms|write time.*ms", "disk_await_ms", _t_to_ms),
    (r"net\.if\.(?:in|out).*errors|network.*errors|errors per second", "net_errors_per_sec", _t_ident),
]
_ZBX_CLASS_COMPILED = [(re.compile(pat), metric, tr) for pat, metric, tr in _ZBX_CLASS_RULES]

# Направление «хуже»: True = плохо, когда БОЛЬШЕ; False = плохо, когда МЕНЬШЕ.
_METRIC_BAD_HIGH = {
    "page_life_expectancy": False,
    "buffer_cache_hit_pct": False,
    "mem_available_pct": False,
    "mem_available_bytes": False,
}

# КОНТЕКСТ нагрузки, а не проблема: порогов нет, в отчёте — отдельной секцией.
# process_cpu_pct здесь же: 100% = одно ядро, без числа ядер порогом не мерить.
_METRIC_CONTEXT = {"sessions_total", "sessions_thin", "sessions_hibernate", "licenses_count",
                   "connections_total", "session_max_held_s", "rphost_mem_bytes", "process_cpu_pct"}

# ТЕКСТОВЫЕ evidence-item'ы: прямые указатели на места кода/держателей сессий
# (выгружаются в Zabbix скриптом разбора ТЖ на сервере 1С) И на тяжёлый SQL/транзакции
# СУБД (админ-скрипт поверх pg_stat_statements / pg_stat_activity). В отчёте — evidence_texts.
_ZBX_TEXT_EVIDENCE = [
    (re.compile(r"1c\.tj\.call_context|call context", re.I), "tj_call_context"),
    (re.compile(r"1c\.sessions\.held_info|held context", re.I), "session_held_info"),
    (re.compile(r"pg\.top\.sql\.time|sql.*(?:запросов по времени|by (?:total )?time)", re.I), "pg_top_sql_time"),
    (re.compile(r"pg\.top\.sql\.disk|sql.*(?:по дисковому чтению|by disk|disk io)", re.I), "pg_top_sql_disk"),
    (re.compile(r"pg\.long\.transactions|(?:долг|длинн|завис)\w*\s+.*транзакц|long.*transactions", re.I), "pg_long_transactions"),
]

# Человекочитаемые подписи видов evidence для рендера отчёта.
_ZBX_EVIDENCE_LABELS = {
    "tj_call_context": "топ CALL-контекстов ТЖ — места кода 1С",
    "session_held_info": "держатели сессий",
    "pg_top_sql_time": "СУБД: топ SQL по суммарному времени",
    "pg_top_sql_disk": "СУБД: топ SQL по чтению с диска",
    "pg_long_transactions": "СУБД: длинные/зависшие транзакции",
}


def zbx_classify_text_evidence(item):
    hay = f"{item.get('key_', '')} {item.get('name', '')}".lower()
    for rx, kind in _ZBX_TEXT_EVIDENCE:
        if rx.search(hay):
            return kind
    return None


_ZBX_STUB = "__stub__"  # осознанная заглушка (режимы CPU и т.п.) — игнор, не «нераспознано»


def _zbx_classify_raw(item):
    """Внутренняя классификация: (metric, transform) | _ZBX_STUB (осознанный игнор) | None."""
    hay = f"{item.get('key_', '')} {item.get('name', '')}".lower()
    units = item.get("units", "")
    for rx, metric, tr in _ZBX_CLASS_COMPILED:
        if rx.search(hay):
            if metric is None:
                return _ZBX_STUB
            return metric, (lambda v, _tr=tr, _u=units: _tr(float(v), _u))
    return None


def zbx_classify_item(item):
    """Определить каноничную метрику по item Zabbix (key_ + name, любые шаблоны:
    Windows perf_counter, Linux-агент, MSSQL/PG). → (metric, transform) или None.
    Правило с metric=None — осознанная заглушка (перехватить и отбросить)."""
    r = _zbx_classify_raw(item)
    return None if r == _ZBX_STUB else r


# Инвентарные/служебные item'ы — не метрики производительности: не считать «нераспознанными».
# Паттерны с ^/$ рассчитаны на КЛЮЧ — проверяется и key_ отдельно, и key_+name вместе.
_ZBX_INVENTORY_RE = re.compile(
    r"^agent\.|^system\.(?:uname|uptime|hostname|localtime|boottime|sw\.|users\.)|^wmi\.get"
    r"|^vm\.memory\.size\[(?:total|used)\]|^system\.swap\.(?:size\[,?(?:total|free)\]|free$)"
    r"|^kernel\.|^proc\.num|^zabbix\[|^vfs\.file\.(?:cksum|contents)|^net\.if\.(?:speed|status|type)"
    r"|\.text$|^pgsql\.(?:ping|uptime|version|replication\.(?:recovery_role|status|count)|db\.age)"
    r"|^system\.cpu\.num|number of cores|^vfs\.fs\.(?:get$|dependent\[[^,]+,data\])")
# Мастер-item'ы официальных шаблонов СУБД (сырой JSON «Get dbstat/bgwriter/…») и
# текстовые заготовки для виджетов — служебные по ИМЕНИ.
_ZBX_INVENTORY_NAME_RE = re.compile(r"(?:^|:\s)get\s|\(текст для виджета\)|text for widget", re.I)


_ZBX_INV_FS_RE = re.compile(r"^vfs\.fs\.(?:dependent|size)\[([^,\]]+),(total|used|pused)\]")


def zbx_inventory_facts(items):
    """Инвентарные факты по хостам из item'ов — знаменатели для интерпретации находок
    (методика: «31 GiB rphost — много или норм? только относительно общей RAM»): ядра,
    RAM total, ОС, версия СУБД, uptime, диски (объём/заполненность). По lastvalue —
    истории для инвентаря не нужно."""
    inv = {}
    for it in items:
        key = str(it.get("key_", ""))
        val = str(it.get("lastvalue") or "").strip()
        if not val:
            continue
        h = inv.setdefault(it.get("host", "?"), {})
        m = _ZBX_INV_FS_RE.match(key)
        if m:
            fs, kind = m.group(1), m.group(2)
            d = h.setdefault("disks", {}).setdefault(fs, {})
            try:
                if kind == "total":
                    d["total_bytes"] = int(float(val))
                elif kind == "used":
                    d["used_bytes"] = int(float(val))
                else:
                    d["used_pct"] = float(val)
            except ValueError:
                pass
            continue
        try:
            if key.startswith("system.cpu.num"):
                h["cpu_num"] = int(float(val))
                continue
            if key.startswith("vm.memory.size[total]"):
                h["mem_total_bytes"] = int(float(val))
                continue
            if key.startswith("system.uptime"):
                h["uptime_s"] = int(float(val))
                continue
        except ValueError:
            continue
        if key.startswith(("system.sw.os", "system.uname")):
            h.setdefault("os", val)
        elif key.startswith("pgsql.version"):
            h["dbms"] = val if val[:1].isalpha() else f"PostgreSQL {val}"
    return {h: f for h, f in inv.items() if f}


def _render_inventory_host(host, f):
    """Одна строка инвентаря хоста для markdown-отчёта. RAM — GiB (двоичные),
    диски — GB (десятичные, как их показывает Zabbix/df)."""
    parts = []
    if f.get("os"):
        parts.append(f["os"])
    if f.get("dbms"):
        parts.append(f["dbms"])
    if f.get("cpu_num"):
        parts.append(f"{f['cpu_num']} vCPU")
    if f.get("mem_total_bytes"):
        parts.append(f"{f['mem_total_bytes'] / 1024 ** 3:.0f} GiB RAM")
    for fs, d in sorted((f.get("disks") or {}).items()):
        s = fs
        if d.get("total_bytes"):
            s += f" {d['total_bytes'] / 10 ** 9:.0f} GB"
        if d.get("used_pct") is not None:
            s += f" (занято {d['used_pct']:g}%)"
        elif d.get("used_bytes") and d.get("total_bytes"):
            s += f" (занято {d['used_bytes'] / d['total_bytes'] * 100:.0f}%)"
        parts.append(s)
    if f.get("uptime_s"):
        parts.append(f"uptime {f['uptime_s'] // 86400} дн")
    return f"- **{host}**: " + " · ".join(parts)


def zbx_is_inventory_item(item):
    key = str(item.get("key_", "")).lower()
    name = str(item.get("name", "")).lower()
    if _ZBX_INVENTORY_RE.search(key) or _ZBX_INVENTORY_RE.search(f"{key} {name}"):
        return True
    if _ZBX_INVENTORY_NAME_RE.search(name):
        return True
    # сырые JSON-агрегаты шаблонов PG (dependent-item'ы разбирают их в метрики)
    return str(item.get("value_type", "")) == "4" and "{$pg.connstring" in key


# --- дашборд → items --------------------------------------------------------
def zabbix_dashboard_items(url, auth=None, dashboardid=None, _post=None):
    """Собрать элементы данных (items) с дашборда: widget fields type=4 (item) +
    type=6 (graph → items графика). Не зависит от типов виджетов и имён полей —
    дашборд можно менять. Возвращает {dashboard, items[{itemid,host,...}], warnings}."""
    dashboards = _zbx_call(url, "dashboard.get",
                           {"dashboardids": [str(dashboardid)], "selectPages": "extend",
                            "output": ["dashboardid", "name"]}, auth, _post) or []
    if not dashboards:
        return {"dashboard": None, "items": [], "warnings": [f"дашборд {dashboardid} не найден или нет прав"]}
    dash = dashboards[0]
    itemids, graphids = set(), set()
    for page in dash.get("pages") or []:
        for w in page.get("widgets") or []:
            for f in w.get("fields") or []:
                t = str(f.get("type"))
                if t == "4":
                    itemids.add(str(f.get("value")))
                elif t == "6":
                    graphids.add(str(f.get("value")))
    warnings = []
    if graphids:
        graphs = _zbx_call(url, "graph.get",
                           {"graphids": sorted(graphids), "selectGraphItems": "extend",
                            "output": ["graphid"]}, auth, _post) or []
        for g in graphs:
            for gi in g.get("gitems") or []:
                itemids.add(str(gi.get("itemid")))
    if not itemids:
        warnings.append("на дашборде не найдено item/graph-виджетов с данными")
        return {"dashboard": dash.get("name"), "items": [], "warnings": warnings}
    raw = _zbx_call(url, "item.get",
                    {"itemids": sorted(itemids),
                     "output": ["itemid", "hostid", "name", "key_", "units", "value_type",
                                "lastvalue", "lastclock"],
                     "selectHosts": ["host", "name"]}, auth, _post) or []
    items = []
    for it in raw:
        hosts = it.get("hosts") or [{}]
        items.append({**{k: it.get(k) for k in ("itemid", "name", "key_", "units",
                                                "value_type", "lastvalue", "lastclock")},
                      "host": hosts[0].get("name") or hosts[0].get("host") or "?"})
    return {"dashboard": dash.get("name"), "items": items, "warnings": warnings}


def zabbix_host_items(url, auth=None, hosts=None, key_search=None, limit=2000, _post=None):
    """Items по хостам (имя/паттерн): host.get → item.get. Для анализа без дашборда."""
    found = _zbx_call(url, "host.get",
                      {"output": ["hostid", "host", "name"],
                       "search": {"name": hosts}, "searchByAny": True,
                       "searchWildcardsEnabled": True}, auth, _post) or []
    if not found:
        return {"hosts": [], "items": [], "warnings": [f"хосты не найдены: {hosts}"]}
    params = {"hostids": [h["hostid"] for h in found], "monitored": True,
              "output": ["itemid", "hostid", "name", "key_", "units", "value_type",
                         "lastvalue", "lastclock"],
              "selectHosts": ["host", "name"], "limit": int(limit)}
    if key_search:
        params["search"] = {"key_": key_search}
    raw = _zbx_call(url, "item.get", params, auth, _post) or []
    items = []
    for it in raw:
        hh = it.get("hosts") or [{}]
        items.append({**{k: it.get(k) for k in ("itemid", "name", "key_", "units",
                                                "value_type", "lastvalue", "lastclock")},
                      "host": hh[0].get("name") or hh[0].get("host") or "?"})
    return {"hosts": [h.get("name") or h.get("host") for h in found], "items": items, "warnings": []}


# --- история/тренды → статистика --------------------------------------------
_TREND_WINDOW_S = 48 * 3600  # глубже 48ч — trend.get (как grafana-zabbix)
_HIST_LIMIT = 10000          # строк на ОДИН item (запрос на item, чтобы лимит не делился на батч)


def _stats_from_values(values):
    if not values:
        return None
    vs = sorted(values)
    n = len(vs)
    return {"n": n, "min": vs[0], "max": vs[-1], "avg": sum(vs) / n,
            "p95": vs[min(n - 1, int(0.95 * (n - 1) + 0.5))], "last": values[-1],
            "values": values[-2000:]}


def zabbix_item_stats(url, auth=None, items=None, time_from=None, time_till=None, _post=None):
    """Статистика значений по items за окно: history.get (окно ≤48ч) или trend.get (глубже).
    → {itemid: {n,min,max,avg,p95,last,values[,truncated]}}. value_type учитывается (0 float, 3 uint).
    Запрос — ПО ОДНОМУ item (общий limit на батч делился бы между items → тихая потеря данных);
    history запрашивается свежими вперёд (DESC), порядок восстанавливается по clock на клиенте,
    при упоре в лимит ставится truncated=True (учтены последние _HIST_LIMIT точек окна)."""
    import time as _time
    time_till = int(time_till or _time.time())
    time_from = int(time_from or (time_till - 3600))
    use_trend = (time_till - time_from) > _TREND_WINDOW_S
    out = {}
    for it in items or []:
        vt = int(it.get("value_type", 0))
        if vt not in (0, 3):  # только числовые
            continue
        iid = str(it["itemid"])
        if use_trend:
            rows = _zbx_call(url, "trend.get",
                             {"itemids": [iid], "output": "extend",
                              "time_from": time_from, "time_till": time_till,
                              "limit": _HIST_LIMIT}, auth, _post) or []
            pairs, vmax, cnt = [], float("-inf"), 0
            for r in rows:
                try:
                    pairs.append((int(r.get("clock", 0)), float(r["value_avg"])))
                    vmax = max(vmax, float(r["value_max"]))
                    cnt += int(r.get("num", 1))
                except (TypeError, ValueError):
                    continue
            pairs.sort()  # trend.get не гарантирует порядок — сортируем по clock
            s = _stats_from_values([v for _c, v in pairs])
            if s:
                s["max"] = max(s["max"], vmax) if vmax != float("-inf") else s["max"]
                s["n"] = cnt or s["n"]
                out[iid] = s
        else:
            rows = _zbx_call(url, "history.get",
                             {"itemids": [iid], "history": vt, "output": "extend",
                              "time_from": time_from, "time_till": time_till,
                              "sortfield": "clock", "sortorder": "DESC",
                              "limit": _HIST_LIMIT}, auth, _post) or []
            pairs = []
            for r in rows:
                try:
                    pairs.append((int(r.get("clock", 0)), float(r["value"])))
                except (TypeError, ValueError):
                    continue
            pairs.sort()
            s = _stats_from_values([v for _c, v in pairs])
            if s:
                if len(rows) >= _HIST_LIMIT:
                    s["truncated"] = True  # окно плотнее лимита — статистика по последним точкам
                out[iid] = s
    return out


# --- ранжирование находок по вкладу в производительность ---------------------
_SEV_WEIGHT = {"critical": 3.0, "high": 2.0, "medium": 1.0, "low": 0.5}


def _violates(metric, value, thr):
    bad_high = _METRIC_BAD_HIGH.get(metric, True)
    return value > thr if bad_high else value < thr


def _exceedance(metric, value, thr):
    """Во сколько раз значение хуже порога (≥1, cap 10)."""
    try:
        bad_high = _METRIC_BAD_HIGH.get(metric, True)
        r = (value / thr) if bad_high else (thr / value if value > 0 else 10.0)
    except ZeroDivisionError:
        r = 10.0
    return max(1.0, min(10.0, r))


def rank_findings(host_metrics):
    """Ранжировать проблемы по вкладу в производительность (самое значимое — первым).
    host_metrics: {host: {snapshot:{metric:value}, samples:{metric:[...]}, cores:int}}.
    score = вес severity × кратность превышения порога × доля времени за порогом."""
    findings = []
    for host, hm in (host_metrics or {}).items():
        snapshot = hm.get("snapshot") or {}
        samples = hm.get("samples") or {}
        for flag in diagnose_metrics(snapshot, cores=hm.get("cores")):
            metric, thr = flag["metric"], flag.get("threshold_value")
            value = flag["value"]
            if thr is not None:
                sv = samples.get(metric) or []
                persistence = (sum(1 for x in sv if _violates(metric, x, thr)) / len(sv)) if sv else 0.5
                exceed = _exceedance(metric, float(value), float(thr))
            else:
                persistence, exceed = 0.5, 1.0
            score = round(_SEV_WEIGHT.get(flag["severity"], 1.0) * exceed * max(persistence, 0.05), 2)
            findings.append({**flag, "host": host, "persistence": round(persistence, 3),
                             "impact_score": score})
    findings.sort(key=lambda f: f["impact_score"], reverse=True)
    return findings


_ZBX_SEVERITY_NAMES = {0: "not classified", 1: "information", 2: "warning",
                       3: "average", 4: "high", 5: "disaster"}

# Длительности в текстовых evidence (снимки админ-скриптов): «6147.3 сек» / «42 sec».
_ZBX_DURATION_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:сек|sec)\b", re.I)
_ZBX_TEXT_HIST_LIMIT = 500  # снимков текстового item'а на окно (обычно 1/мин — сутки с запасом)


def zbx_text_evidence_window(url, auth, item, time_from, time_till, _post=None):
    """История ТЕКСТОВОГО evidence-item'а за окно (history=4): свежий непустой снимок +
    пиковый снимок по максимальной длительности «N сек» в тексте. Снимок «сейчас» может
    быть пустым/спокойным, а пик окна (транзакция на 6000 сек час назад) — главная улика.
    → {"last": str, "peak": {"text","clock","max_seconds"}|None}."""
    rows = _zbx_call(url, "history.get",
                     {"itemids": [str(item["itemid"])], "history": 4, "output": "extend",
                      "time_from": int(time_from), "time_till": int(time_till),
                      "sortfield": "clock", "sortorder": "DESC",
                      "limit": _ZBX_TEXT_HIST_LIMIT}, auth, _post) or []
    snaps = []
    for r in rows:  # DESC: свежие первыми
        try:
            snaps.append((int(r.get("clock", 0)), str(r.get("value") or "")))
        except (TypeError, ValueError):
            continue
    last = next((v for _c, v in snaps if v.strip()), "")
    peak = None
    for clock, val in snaps:
        for mt in _ZBX_DURATION_RE.finditer(val):
            secs = float(mt.group(1).replace(",", "."))
            if peak is None or secs > peak["max_seconds"]:
                peak = {"max_seconds": secs, "clock": clock, "text": val}
    return {"last": last, "peak": peak}


# --- Пресеты контуров Zabbix: «имя контура → полный охват съёма» -------------
# Без пресета простой запрос «проанализируй контур X» вырождается в бедный охват
# (только дашборд, короткое окно, без лимита ядер) — главный анти-паттерн методики.
# Файл — TOML локализации (в toolkit — config/zabbix-contours.example.toml).

def default_contours_config():
    """Путь к файлу пресетов: env ZABBIX_CONTOURS_CONFIG, иначе <репо>/config/zabbix-contours.toml."""
    p = os.environ.get("ZABBIX_CONTOURS_CONFIG", "")
    if p:
        return p
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "config", "zabbix-contours.toml")


def load_zabbix_contours(path=None):
    """Все пресеты из TOML. Нет файла/tomllib — пустой dict (пресеты опциональны)."""
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover
        return {}
    path = path or default_contours_config()
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def resolve_zabbix_contour(name, path=None):
    """Пресет контура по имени → нормализованный dict: url, dashboards (строка «408,410»),
    hosts (список), window_hours (дефолт 24 — сутки, не час), licensed_cores {хост: ядра},
    storage_map_base, onec_profile. Нет файла/имени — RuntimeError с подсказкой."""
    cfg = load_zabbix_contours(path)
    if not cfg:
        raise RuntimeError(
            f"пресеты контуров не найдены ({path or default_contours_config()}): "
            "заполни config/zabbix-contours.toml (образец zabbix-contours.example.toml) "
            "или укажи env ZABBIX_CONTOURS_CONFIG")
    c = cfg.get(name)
    if not isinstance(c, dict):
        low = {k.lower(): v for k, v in cfg.items() if isinstance(v, dict)}
        c = low.get(str(name).lower())
    if not isinstance(c, dict):
        known = ", ".join(sorted(k for k, v in cfg.items() if isinstance(v, dict)))
        raise RuntimeError(f"контур '{name}' не описан в пресетах; доступны: {known}")
    hosts = c.get("hosts") or []
    if isinstance(hosts, str):
        hosts = [h.strip() for h in hosts.split(",") if h.strip()]
    return {"name": str(name),
            "url": str(c.get("url") or ""),
            "dashboards": str(c.get("dashboards") or ""),
            "hosts": [str(h) for h in hosts],
            "window_hours": float(c.get("window_hours") or 24),
            "licensed_cores": {str(h): int(n) for h, n in (c.get("licensed_cores") or {}).items()},
            "storage_map_base": str(c.get("storage_map_base") or ""),
            "onec_profile": str(c.get("onec_profile") or ""),
            "notes": str(c.get("notes") or "")}


def resolve_zabbix_scope(contour="", url="", token="", dashboardid="", hosts=None,
                         window_hours=None, licensed_cores=None, maps_dir="", contours_path=None):
    """Единый вход CLI и MCP: слить пресет контура с явными аргументами и env.
    Приоритет: явный аргумент → env (url/token) → пресет. Без contour — прежнее
    поведение (window_hours по умолчанию 1). Возвращает готовые параметры съёма
    + служебное из пресета (onec_profile, storage_map_base) для такта синтеза."""
    preset = resolve_zabbix_contour(contour, contours_path) if contour else {}
    return {"contour": contour or "",
            "url": url or os.environ.get("ZABBIX_URL", "") or preset.get("url", ""),
            "token": token or os.environ.get("ZABBIX_TOKEN", ""),
            "dashboardid": dashboardid or preset.get("dashboards", ""),
            "hosts": list(hosts) if hosts else list(preset.get("hosts") or []),
            "window_hours": float(window_hours) if window_hours else float(preset.get("window_hours") or 1.0),
            "licensed_cores": dict(licensed_cores) if licensed_cores else dict(preset.get("licensed_cores") or {}),
            "maps_dir": maps_dir or "",
            "onec_profile": preset.get("onec_profile", ""),
            "storage_map_base": preset.get("storage_map_base", "")}


def _load_storage_maps_for_report(maps_dir):
    """Карты структуры хранения (scripts/storage_map.py) для аннотации SQL-уликов.
    Порядок: аргумент → env ONEC_STORAGE_MAPS_DIR → <репозиторий>/data/storage_maps
    (карты, зашитые в локализацию). Возвращает (модуль, maps) или (None, None)."""
    try:
        import sys as _sys
        scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
        if scripts not in _sys.path:
            _sys.path.insert(0, scripts)
        import storage_map as _sm
        maps_dir = maps_dir or _sm.default_maps_dir()
        if not maps_dir or not os.path.isdir(maps_dir):
            return None, None
        maps = _sm.load_maps(maps_dir)
        return (_sm, maps) if maps else (None, None)
    except Exception:  # noqa: BLE001 — карты не должны ронять отчёт
        return None, None


def zabbix_perf_report(url, auth=None, dashboardid=None, hosts=None, window_hours=1,
                       cores_by_host=None, licensed_cores_by_host=None, maps_dir=None,
                       contour="", _post=None):
    """Сквозной отчёт производительности: items (с дашборда/дашбордОВ и/или хостов) → история
    за окно → каноничные метрики по хостам → находки, ранжированные по вкладу (самое значимое
    первым) + активные проблемы Zabbix. dashboardid: один id или несколько через запятую
    («408,410» — app-сервер + СУБД). licensed_cores_by_host: лимит ядер лицензии 1С на хост
    (ПРОФ = 12 на рабочий сервер) — включает контроль «rphost упёрся в потолок лицензии».
    maps_dir (или env ONEC_STORAGE_MAPS_DIR): карты структуры хранения — SQL-улики
    аннотируются объектами 1С. Read-only."""
    import time as _time
    warnings, items, scope = [], [], {}
    if contour:
        scope["contour"] = contour  # «до/после» сравнивают ТЕМ ЖЕ охватом — имя пресета в отчёте
    if dashboardid:
        dash_names = []
        for did in str(dashboardid).split(","):
            did = did.strip()
            if not did:
                continue
            d = zabbix_dashboard_items(url, auth, did, _post=_post)
            if d["dashboard"]:
                dash_names.append(d["dashboard"])
            items += d["items"]
            warnings += d["warnings"]
        scope["dashboard"] = ", ".join(dash_names) or None
        # дискавери: админы добавляют дашборды молча — предупредить о дашбордах вне охвата,
        # чтобы новый (например, свежий дашборд СУБД) не остался незамеченным
        try:
            all_dash = _zbx_call(url, "dashboard.get",
                                 {"output": ["dashboardid", "name"]}, auth, _post) or []
            wanted = {d.strip() for d in str(dashboardid).split(",") if d.strip()}
            extra = [f"{d['dashboardid']} «{d.get('name', '')}»" for d in all_dash
                     if str(d.get("dashboardid")) not in wanted]
            if extra:
                warnings.append("дашборды вне охвата (проверь, не про этот ли контур, и дополни пресет): "
                                + ", ".join(extra))
        except RuntimeError as e:
            warnings.append(f"dashboard.get (дискавери всех дашбордов): {e}")
    if hosts:
        h = zabbix_host_items(url, auth, hosts, _post=_post)
        scope["hosts"] = h["hosts"]
        items += h["items"]
        warnings += h["warnings"]
    # dedup по itemid
    items = list({i["itemid"]: i for i in items}.values())
    inventory = zbx_inventory_facts(items)
    if licensed_cores_by_host:
        scope["licensed_cores"] = dict(licensed_cores_by_host)

    time_till = int(_time.time())
    time_from = time_till - int(float(window_hours) * 3600)

    classified, unclassified = [], []
    evidence_texts = {}
    evidence_peaks = {}
    ignored = 0
    for it in items:
        kind = zbx_classify_text_evidence(it)
        if kind:
            # смотреть ИСТОРИЮ за окно, не только lastvalue: текущий снимок бывает пустым,
            # а пик окна (транзакция на тысячи секунд час назад) — главная улика
            try:
                win = zbx_text_evidence_window(url, auth, it, time_from, time_till, _post=_post)
            except RuntimeError as e:
                warnings.append(f"история текстового item '{it.get('name')}': {e}")
                win = {"last": "", "peak": None}
            val = (win["last"] or str(it.get("lastvalue") or "")).strip()
            if val:
                evidence_texts.setdefault(it.get("host", "?"), {})[kind] = val[:4000]
            peak = win["peak"]
            if peak and peak["text"].strip() and peak["text"] != val:
                evidence_peaks.setdefault(it.get("host", "?"), {})[kind] = {
                    "max_seconds": peak["max_seconds"], "clock": peak["clock"],
                    "text": peak["text"][:4000]}
            continue
        c = _zbx_classify_raw(it)
        if c == _ZBX_STUB:
            ignored += 1
        elif c:
            classified.append((it, *c))
        elif zbx_is_inventory_item(it):
            ignored += 1
        else:
            unclassified.append(f"{it.get('host', '?')}: {it.get('name', '')} [{it.get('key_', '')}]")

    # карты структуры хранения: перевод _InfoRg…/_Reference… из SQL-уликов в объекты 1С
    sm, smaps = _load_storage_maps_for_report(maps_dir or os.environ.get("ONEC_STORAGE_MAPS_DIR", ""))
    evidence_tables = []
    if sm and (evidence_texts or evidence_peaks):
        blobs = [t for kinds in evidence_texts.values() for t in kinds.values()]
        blobs += [p["text"] for kinds in evidence_peaks.values() for p in kinds.values()]
        seen_tbl = set()
        for note in sm.tables_in_text("\n".join(blobs), smaps):
            key = (note["table"].lower(), note["base"])
            if key not in seen_tbl:
                seen_tbl.add(key)
                evidence_tables.append(note)

    stats = zabbix_item_stats(url, auth, [it for it, _m, _t in classified],
                              time_from=time_from, time_till=time_till, _post=_post)

    host_metrics = {}
    evidence = {}
    context = {}
    for it, metric, transform in classified:
        host = it.get("host", "?")
        st = stats.get(str(it["itemid"]))
        if st:
            vals = [transform(v) for v in st["values"]]
            rep_val = transform(st["avg"])
        else:
            try:
                rep_val = transform(float(it.get("lastvalue")))
                vals = [rep_val]
            except (TypeError, ValueError):
                continue
        if metric in _METRIC_CONTEXT:
            # контекст нагрузки (сессии/лицензии/CPU процесса) — текущее значение, не порог
            context.setdefault(host, {})[metric] = transform(st["last"]) if st else rep_val
            # …но если известен лимит ядер лицензии 1С (ПРОФ = 12 на рабочий сервер) — CPU rphost
            # сравнивается с ЛИМИТОМ, а не с числом ядер сервера: 12 из 14 ядер = системно 86%,
            # а для 1С это все 100% доступного
            lic = (licensed_cores_by_host or {}).get(host)
            if metric == "process_cpu_pct" and lic:
                cap_vals = [v / (float(lic) * 100.0) * 100.0 for v in vals]
                cap_val = rep_val / (float(lic) * 100.0) * 100.0
                hm = host_metrics.setdefault(host, {"snapshot": {}, "samples": {},
                                                    "cores": (cores_by_host or {}).get(host)})
                prev = hm["snapshot"].get("rphost_cpu_cap_pct")
                if prev is None or cap_val > prev:
                    hm["snapshot"]["rphost_cpu_cap_pct"] = cap_val
                    hm["samples"]["rphost_cpu_cap_pct"] = cap_vals
                    evidence[(host, "rphost_cpu_cap_pct")] = {"item": it.get("name"), "key": it.get("key_"),
                                                              "samples": len(cap_vals)}
            continue
        if st and st.get("truncated"):
            warnings.append(f"{host}: история '{it.get('name')}' плотнее лимита {_HIST_LIMIT} — "
                            f"статистика по последним {_HIST_LIMIT} точкам окна")
        hm = host_metrics.setdefault(host, {"snapshot": {}, "samples": {}, "cores": (cores_by_host or {}).get(host)})
        prev = hm["snapshot"].get(metric)
        bad_high = _METRIC_BAD_HIGH.get(metric, True)
        # несколько item'ов одной метрики на хосте (диски и т.п.) — берём ХУДШИЙ item,
        # и samples тоже его (иначе persistence размывается образцами здоровых собратьев)
        if prev is None or (rep_val > prev if bad_high else rep_val < prev):
            hm["snapshot"][metric] = rep_val
            hm["samples"][metric] = list(vals)
            evidence[(host, metric)] = {"item": it.get("name"), "key": it.get("key_"),
                                        "samples": len(vals)}

    findings = rank_findings(host_metrics)
    for f in findings:
        f["evidence"] = evidence.get((f["host"], f["metric"]))

    problems = []
    try:
        for p in zabbix_problems_with_hosts(url, auth=auth, _post=_post):
            sev = int(p.get("severity", 0))
            problems.append({"name": p.get("name"), "severity": sev,
                             "severity_name": _ZBX_SEVERITY_NAMES.get(sev, str(sev)),
                             "hosts": p.get("hosts") or [], "clock": p.get("clock")})
        problems.sort(key=lambda p: p["severity"], reverse=True)
    except RuntimeError as e:
        warnings.append(f"problem.get: {e}")

    return {"scope": scope, "window_hours": window_hours,
            "items_total": len(items), "items_classified": len(classified),
            "items_inventory_ignored": ignored, "inventory": inventory,
            "findings": findings, "active_problems": problems, "context": context,
            "evidence_texts": evidence_texts, "evidence_peaks": evidence_peaks,
            "evidence_tables": evidence_tables,
            "unclassified": unclassified[:50], "warnings": warnings}


def render_perf_report(report):
    """Отчёт → markdown для человека: находки от самого значимого вклада к незначимому."""
    lines = []
    sc = report.get("scope", {})
    title = sc.get("dashboard") or ", ".join(sc.get("hosts") or []) or "Zabbix"
    if sc.get("contour"):
        title = f"контур {sc['contour']} — {title}"
    lines.append(f"# Производительность: {title} (окно {report.get('window_hours')}ч)")
    lines.append(f"_Элементов данных: {report.get('items_total')}, распознано: {report.get('items_classified')}_")
    inv = report.get("inventory") or {}
    if inv:
        lines.append("\n## Инвентарь хостов (знаменатели для интерпретации)")
        lic = (report.get("scope") or {}).get("licensed_cores") or {}
        for host in sorted(inv):
            line = _render_inventory_host(host, inv[host])
            if host in lic:
                line += f" · **потолок {lic[host]} ядер rphost (лицензия 1С)**"
            lines.append(line)
    findings = report.get("findings") or []
    if findings:
        lines.append("\n## Предложения (по убыванию вклада в производительность)")
        for i, f in enumerate(findings, 1):
            pct = round(f.get("persistence", 0) * 100)
            lines.append(f"\n**{i}. [{f['severity']}] {f['host']} — {f['problem']}**")
            lines.append(f"   - метрика: `{f['metric']}` = {f['value']} (порог {f['threshold']}), "
                         f"за порогом {pct}% времени, impact {f['impact_score']}")
            if f.get("evidence"):
                lines.append(f"   - источник: {f['evidence']['item']} `{f['evidence']['key']}`")
            lines.append(f"   - что делать: {f['recommendation']}")
    else:
        lines.append("\nПо распознанным метрикам порогов не превышено.")
    ctx = report.get("context") or {}
    if ctx:
        lines.append("\n## Контекст нагрузки (не проблемы — фон для интерпретации)")
        for host, metrics in ctx.items():
            pretty = ", ".join(f"{k}={v:g}" for k, v in sorted(metrics.items()))
            lines.append(f"- {host}: {pretty}")
    evt = report.get("evidence_texts") or {}
    peaks = report.get("evidence_peaks") or {}
    if evt or peaks:
        lines.append("\n## Указатели на места кода и SQL (из ТЖ и СУБД через Zabbix)")
        for host in sorted(set(evt) | set(peaks)):
            kinds = dict(evt.get(host) or {})
            for kind in sorted(set(kinds) | set(peaks.get(host) or {})):
                label = _ZBX_EVIDENCE_LABELS.get(kind, kind)
                text = kinds.get(kind)
                if text:
                    lines.append(f"\n### {host} · {label}\n```\n{text}\n```")
                pk = (peaks.get(host) or {}).get(kind)
                if pk:
                    import time as _time
                    ts = _time.strftime("%d.%m %H:%M", _time.localtime(int(pk["clock"]))) if pk.get("clock") else "?"
                    lines.append(f"\n**Пик за окно** ({label}): {pk['max_seconds']:g} сек @ {ts}\n```\n{pk['text']}\n```")
    tabs = report.get("evidence_tables") or []
    if tabs:
        lines.append("\n## Расшифровка таблиц СУБД (карты структуры хранения)")
        for t in tabs:
            extra = f" [{t['purpose']}]" if t.get("purpose") and t["purpose"] != "Основная" else ""
            lines.append(f"- `{t['table']}` = **{t['metadata']}**{extra} (карта: {t['base']})")
    probs = report.get("active_problems") or []
    if probs:
        lines.append("\n## Активные проблемы Zabbix (триггеры)")
        for p in probs[:20]:
            hosts = ", ".join(p["hosts"]) if p["hosts"] else "?"
            lines.append(f"- [{p['severity_name']}] {p['name']} ({hosts})")
    unc = report.get("unclassified") or []
    if unc:
        lines.append(f"\n## Не распознано ({len(unc)} — расширяй правила классификации)")
        for u in unc[:15]:
            lines.append(f"- {u}")
    for w in report.get("warnings") or []:
        lines.append(f"\n⚠ {w}")
    return "\n".join(lines)


# --- Сравнение «до/после» perf-отчётов (доказательство ускорения) -----------

def perf_report_diff(baseline, current):
    """Сверка двух perf-отчётов (JSON zabbix_perf_report): решённые/новые находки и динамика
    по (host, metric). Методика: «до/после» сравнивают ТЕМ ЖЕ охватом и окном — расхождение
    охвата попадает в scope_mismatch (такое сравнение бракуется, не интерпретируй молча)."""
    mism = []
    bs, cs = baseline.get("scope") or {}, current.get("scope") or {}
    if baseline.get("window_hours") != current.get("window_hours"):
        mism.append(f"окно: {baseline.get('window_hours')}ч → {current.get('window_hours')}ч")
    if (bs.get("dashboard") or "") != (cs.get("dashboard") or ""):
        mism.append(f"дашборды: {bs.get('dashboard')} → {cs.get('dashboard')}")
    if sorted(bs.get("hosts") or []) != sorted(cs.get("hosts") or []):
        mism.append(f"хосты: {bs.get('hosts')} → {cs.get('hosts')}")
    if (bs.get("contour") or "") != (cs.get("contour") or ""):
        mism.append(f"контур: {bs.get('contour')} → {cs.get('contour')}")

    def _by_key(rep):
        return {(f.get("host"), f.get("metric")): f for f in rep.get("findings") or []}

    b, c = _by_key(baseline), _by_key(current)
    resolved = [b[k] for k in b if k not in c]
    new = [c[k] for k in c if k not in b]
    changed = []
    for k in b:
        if k in c:
            changed.append({"host": k[0], "metric": k[1], "problem": c[k].get("problem"),
                            "value_before": b[k].get("value"), "value_after": c[k].get("value"),
                            "impact_before": b[k].get("impact_score"),
                            "impact_after": c[k].get("impact_score"),
                            "persistence_before": b[k].get("persistence"),
                            "persistence_after": c[k].get("persistence")})
    changed.sort(key=lambda x: (x.get("impact_before") or 0), reverse=True)
    return {"scope_mismatch": mism, "resolved": resolved, "new": new, "changed": changed}


def render_perf_diff(diff):
    """Дифф «до/после» → markdown: сперва брак охвата, затем решённое, динамика, новое."""
    lines = ["# Сравнение до/после (impact-строки)"]
    if diff.get("scope_mismatch"):
        lines.append("\n⚠ **ОХВАТ РАЗЛИЧАЕТСЯ — сравнение некорректно** (методика: тот же охват и окно):")
        for s in diff["scope_mismatch"]:
            lines.append(f"- {s}")
    if diff.get("resolved"):
        lines.append("\n## Решённые находки (были в базовом, ушли)")
        for f in diff["resolved"]:
            lines.append(f"- {f.get('host')} · `{f.get('metric')}` — было {f.get('value')} (impact {f.get('impact_score')})")
    if diff.get("changed"):
        lines.append("\n## Динамика (по убыванию исходного impact)")
        for ch in diff["changed"]:
            pb = round((ch.get("persistence_before") or 0) * 100)
            pa = round((ch.get("persistence_after") or 0) * 100)
            lines.append(f"- {ch['host']} · `{ch['metric']}`: {ch['value_before']} → {ch['value_after']}; "
                         f"impact {ch['impact_before']} → {ch['impact_after']}; за порогом {pb}% → {pa}% времени")
    if diff.get("new"):
        lines.append("\n## Новые находки (не было в базовом)")
        for f in diff["new"]:
            lines.append(f"- {f.get('host')} · `{f.get('metric')}` — {f.get('value')} (impact {f.get('impact_score')})")
    if not (diff.get("resolved") or diff.get("changed") or diff.get("new")):
        lines.append("\nНаходок нет ни в одном из отчётов.")
    return "\n".join(lines)


# ============================================================================
# Prometheus — ОПЦИОНАЛЬНЫЙ адаптер (встречается реже Zabbix). Read-only PromQL.
# Полезен, когда метрики кластера 1С отдаёт экспортер (напр. LazarenkoA/prometheus_1C_exporter),
# а доступен только Prometheus/Grafana.
# ============================================================================

def _http_get_json(url, params):  # pragma: no cover — реальный HTTP, в тестах подменяется
    import urllib.request
    import urllib.parse
    import json as _json
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    with urllib.request.urlopen(url + "?" + q, timeout=30) as r:
        return _json.loads(r.read().decode("utf-8"))


def prometheus_query(base_url, query, time=None, _get=None):
    """Мгновенный PromQL-запрос (read-only) к Prometheus HTTP API /api/v1/query.
    Возвращает [{metric, timestamp, value}]. На status!=success бросает RuntimeError."""
    url = base_url.rstrip("/") + "/api/v1/query"
    resp = (_get or _http_get_json)(url, {"query": query, "time": time})
    if not isinstance(resp, dict) or resp.get("status") != "success":
        err = resp.get("error") if isinstance(resp, dict) else str(resp)
        raise RuntimeError(f"Prometheus error: {err}")
    out = []
    for s in resp.get("data", {}).get("result", []):
        val = s.get("value") or [None, None]
        out.append({"metric": s.get("metric", {}), "timestamp": val[0], "value": val[1]})
    return out


# ============================================================================
# Журнал регистрации (ЖР) — кейс «доступ ТОЛЬКО к ЖР»: из Excel-выгрузки отчёта 1С
# ИЛИ из файла журнала нового формата .lgd (SQLite). Анализ ошибок/по пользователям/
# по событиям, когда нет доступа к ТЖ/rac/СУБД. Только чтение.
# ============================================================================

def _eventlog_colmap(headers):
    """Сопоставить столбцы (рус. заголовки выгрузки ЖР / колонки .lgd) к ключам по подстроке."""
    m = {}
    for i, h in enumerate(headers):
        hl = str(h or "").strip().lower()
        if any(k in hl for k in ("важност", "уровен", "severity")):
            m.setdefault("level", i)
        elif "пользоват" in hl or "user" in hl:
            m.setdefault("user", i)
        elif "событие" in hl or "event" in hl:
            m.setdefault("event", i)
        elif "метадан" in hl or "metadata" in hl:
            m.setdefault("metadata", i)
        elif "дата" in hl or "date" in hl or "врем" in hl:
            m.setdefault("date", i)
        elif "коммент" in hl or "comment" in hl:
            m.setdefault("comment", i)
        elif "компьютер" in hl or "computer" in hl:
            m.setdefault("computer", i)
        elif "приложение" in hl or hl == "app":
            m.setdefault("app", i)
    return m


def _row_to_entry(row, colmap):
    return {k: (str(row[i]) if i < len(row) and row[i] is not None else "") for k, i in colmap.items()}


def _parse_eventlog_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    headers = next(it, None)
    if not headers:
        wb.close()
        return [], ["пустой xlsx"]
    cm = _eventlog_colmap(headers)
    entries = []
    for r in it:
        if r is None or all(c is None for c in r):
            continue
        entries.append(_row_to_entry(r, cm))
    wb.close()
    return entries, ([] if cm else ["не распознаны столбцы ЖР в заголовке"])


def _pick_eventlog_table(tables):
    for t in tables:
        if any(k in t.lower() for k in ("eventlog", "журнал", "event")):
            return t
    return tables[0]


def _parse_eventlog_sqlite(path, table=None):
    import sqlite3
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    cur = con.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    if not tables:
        con.close()
        return [], ["в базе .lgd нет таблиц"]
    t = table or _pick_eventlog_table(tables)
    if t not in tables:
        con.close()
        return [], [f"таблица '{t}' не найдена; есть: {tables}"]
    cur.execute(f'SELECT * FROM "{t}"')
    cols = [d[0] for d in cur.description]
    cm = _eventlog_colmap(cols)
    entries = [_row_to_entry(row, cm) for row in cur.fetchall()]
    con.close()
    warn = [] if cm else [f"в таблице '{t}' не распознаны столбцы ЖР (колонки: {cols}) — реальный .lgd версионно-зависим [проверить], надёжнее Excel-выгрузка отчёта ЖР"]
    return entries, warn


def event_log_parse(source, level=None, user=None, events=None, top=100, table=None):
    """Анализ журнала регистрации 1С из Excel-выгрузки (.xlsx) ИЛИ файла .lgd (SQLite).
    Фильтры: level (Ошибка/Предупреждение/Информация/Примечание), user, events (подстроки).
    Возвращает summary (rows, by_level, by_user, by_event) + entries[] (top) + warnings."""
    empty = {"summary": {"rows": 0, "by_level": {}, "by_user": {}, "by_event": {}}, "entries": [], "warnings": []}
    if not os.path.exists(source):
        empty["warnings"] = [f"источник не найден: {source}"]
        return empty
    ext = os.path.splitext(source)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            entries, warn = _parse_eventlog_xlsx(source)
        elif ext in (".lgd", ".db", ".sqlite", ".sqlite3"):
            entries, warn = _parse_eventlog_sqlite(source, table)
        else:
            empty["warnings"] = [f"неизвестный формат '{ext}'; поддержано .xlsx (выгрузка отчёта ЖР) и .lgd (SQLite журнал)"]
            return empty
    except Exception as e:  # noqa: BLE001 — вернуть как warning, не падать
        empty["warnings"] = [f"ошибка чтения {os.path.basename(source)}: {e}"]
        return empty

    evset = [e.lower() for e in events] if events else None

    def keep(e):
        if level and str(e.get("level", "")).strip().lower() != level.strip().lower():
            return False
        if user and str(e.get("user", "")) != user:
            return False
        if evset and not any(x in str(e.get("event", "")).lower() for x in evset):
            return False
        return True

    filt = [e for e in entries if keep(e)]
    return {
        "summary": {
            "rows": len(filt),
            "by_level": dict(Counter(e["level"] for e in filt if e.get("level")).most_common()),
            "by_user": dict(Counter(e["user"] for e in filt if e.get("user")).most_common()),
            "by_event": dict(Counter(e["event"] for e in filt if e.get("event")).most_common()),
        },
        "entries": filt[: max(0, int(top))],
        "warnings": warn,
    }


# --- MCP-обёртка (read-only) ---
try:
    from mcp.server.fastmcp import FastMCP
    # host/port для сетевого транспорта (sse/streamable-http) при центральном развёртывании в docker
    mcp = FastMCP("onec-ops",
                  host=os.environ.get("MCP_HOST", "0.0.0.0"),
                  port=int(os.environ.get("MCP_PORT", "8001")))

    @mcp.tool()
    def tech_journal_parse(roots, events=None, min_duration: int = 0,
                           since: str = "", until: str = "", top: int = 50) -> dict:
        """Разбор технологического журнала 1С по каталогу/группе каталогов (разные серверы
        кластера, несколько кластеров) для расследования проблем производительности по всей
        компании. Каждое событие тегируется источником/процессом/файлом. Только чтение.

        roots: путь, список путей или список {"path","label"} (label = метка сервера/кластера).
        events: имена событий для отбора, напр. ["TLOCK","TDEADLOCK","DBMSSQL","DBPOSTGRS","EXCP"].
        min_duration: минимальная длительность события (поле ТЖ). since/until: границы времени.
        top: сколько самых длительных событий вернуть.
        Возвращает summary (files, events_total, by_event, by_source) + events[] (топ) + warnings.
        """
        return parse_techlog(roots, events=events or None, min_duration=min_duration,
                             since=since or None, until=until or None, top=top)

    @mcp.tool()
    def apdex_tool(samples: list, threshold_t: float) -> dict:
        """Apdex по выборке времён отклика операции (сек) и порогу T (сек). Каноничная формула
        (Satisfied≤T, Tolerating≤4T, Frustrated>4T). Бизнес-смысл: рост Apdex до/после = реальное
        ускорение, ощущаемое пользователями. Возвращает apdex, satisfied/tolerating/frustrated, rating."""
        return apdex(samples, threshold_t)

    @mcp.tool()
    def apdex_by_operation_tool(records: list, thresholds: "dict | None" = None, default_t: float = 1.0) -> dict:
        """Apdex ОТДЕЛЬНО по каждой операции (у каждой свой порог T). records: [{"operation","duration"}].
        thresholds: {операция: T}. Возвращает operations[] (худшие сверху) — где именно «болит» у пользователей."""
        return apdex_by_operation(records, thresholds=thresholds or None, default_t=default_t)

    @mcp.tool()
    def diagnose_metrics_tool(metrics: dict, cores: int = 0) -> list:
        """Найти проблемы в снимке метрик 1С/СУБД по общеизвестным порогам (swap>70%, page life<300с,
        очередь ЦП>2×ядер, очередь диска>2, дедлоки>0, latches>0, cache hit<90%). Возвращает список
        проблем с severity и рекомендацией. cores — число ядер (для порога очереди ЦП)."""
        return diagnose_metrics(metrics, cores=cores or None)

    @mcp.tool()
    def zabbix_problems_tool(url: str = "", token: str = "", recent: bool = True) -> list:
        """ОПЦИОНАЛЬНО: текущие проблемы из Zabbix (когда доступен только он). url/token — или из env
        ZABBIX_URL/ZABBIX_TOKEN (read-only пользователь). Read-only (problem.get)."""
        url = url or os.environ.get("ZABBIX_URL", "")
        token = token or os.environ.get("ZABBIX_TOKEN", "")
        if not url:
            return [{"error": "укажи url или env ZABBIX_URL (read-only)"}]
        return zabbix_problems(url, auth=token or None, recent=recent)

    @mcp.tool()
    def bsp_apdex_parse_tool(source: str, default_t: float = 1.0) -> dict:
        """Apdex из ШТАТНОГО экспорта БСП «Оценка производительности»: файл или каталог XML
        (регламентное задание ЭкспортОценкиПроизводительности пишет их в локальный/сетевой каталог).
        T каждой операции = её targetValue. Возвращает operations[] (худшие сверху) + worst.
        Бизнес-смысл: сравнение apdex до/после = доказанное ускорение. Read-only."""
        return bsp_apdex_parse(source, default_t=default_t)

    @mcp.tool()
    def zabbix_api_tool(method: str, params: dict = None, url: str = "", token: str = "") -> "list | dict":
        """Универсальный READ-ONLY вызов Zabbix API (whitelist: только *.get / apiinfo.version /
        *.export). Покрывает всё, чего нет в составных инструментах: host.get, trigger.get,
        item.get, dashboard.get... url/token — или env ZABBIX_URL/ZABBIX_TOKEN."""
        url = url or os.environ.get("ZABBIX_URL", "")
        token = token or os.environ.get("ZABBIX_TOKEN", "")
        if not url:
            return [{"error": "укажи url или env ZABBIX_URL"}]
        return zabbix_api(url, method, params or {}, auth=token or None)

    @mcp.tool()
    def zabbix_dashboard_items_tool(dashboardid: str, url: str = "", token: str = "") -> dict:
        """Элементы данных (items) с дашборда Zabbix: виджеты-item + виджеты-график → items.
        Работает с любым дашбордом (dashboardid из URL: ...dashboardid=408). Read-only."""
        url = url or os.environ.get("ZABBIX_URL", "")
        token = token or os.environ.get("ZABBIX_TOKEN", "")
        if not url:
            return {"error": "укажи url или env ZABBIX_URL"}
        return zabbix_dashboard_items(url, auth=token or None, dashboardid=dashboardid)

    @mcp.tool()
    def zabbix_perf_report_tool(contour: str = "", dashboardid: str = "", hosts: list = None,
                                window_hours: float = 0, url: str = "", token: str = "",
                                licensed_cores: "dict | None" = None, maps_dir: str = "") -> dict:
        """ГЛАВНЫЙ инструмент анализа производительности по Zabbix: дашборд(ы) и/или хосты →
        items → история за окно (history/trend автоматически) → каноничные метрики → находки,
        РАНЖИРОВАННЫЕ по вкладу в производительность (самое значимое первым) + активные
        проблемы + текстовые улики (CALL-контексты ТЖ, топ SQL СУБД, длинные транзакции —
        с пиком за окно). contour: имя пресета из config/zabbix-contours.toml — «полный охват
        контура одним словом» (дашборды+хосты+окно 24ч+лимит ядер); явные аргументы главнее.
        dashboardid: один id или несколько через запятую («408,410»). licensed_cores:
        {хост: лимит ядер лицензии 1С} (ПРОФ = 12 на рабочий сервер) — контроль «rphost упёрся
        в потолок лицензии, хотя системный CPU зелёный». maps_dir (или env ONEC_STORAGE_MAPS_DIR):
        карты структуры хранения — таблицы в SQL-уликах переводятся в объекты 1С
        (scripts/storage_map.py). Возвращает report + markdown. Read-only."""
        try:
            scope = resolve_zabbix_scope(contour=contour, url=url, token=token,
                                         dashboardid=dashboardid, hosts=hosts,
                                         window_hours=window_hours or None,
                                         licensed_cores=licensed_cores, maps_dir=maps_dir)
        except RuntimeError as e:
            return {"error": str(e)}
        if not scope["url"]:
            return {"error": "укажи url, env ZABBIX_URL или url в пресете контура"}
        rep = zabbix_perf_report(scope["url"], auth=scope["token"] or None,
                                 dashboardid=scope["dashboardid"] or None,
                                 hosts=scope["hosts"] or None,
                                 window_hours=scope["window_hours"],
                                 licensed_cores_by_host=scope["licensed_cores"] or None,
                                 maps_dir=scope["maps_dir"] or None,
                                 contour=scope["contour"])
        rep["markdown"] = render_perf_report(rep)
        return rep

    @mcp.tool()
    def zabbix_perf_diff_tool(baseline: dict, current: dict) -> dict:
        """Сравнение «до/после» двух perf-отчётов (JSON zabbix_perf_report_tool) — доказательство
        ускорения по методике: решённые/новые находки и динамика value/impact/persistence по
        (host, metric). Расхождение охвата или окна попадает в scope_mismatch — такое сравнение
        бракуется. Возвращает diff + markdown. Read-only."""
        d = perf_report_diff(baseline, current)
        d["markdown"] = render_perf_diff(d)
        return d

    @mcp.tool()
    def event_log_parse_tool(source: str, level: str = "", user: str = "",
                             events: list = None, top: int = 100, table: str = "") -> dict:
        """Анализ журнала регистрации 1С (кейс «доступ только к ЖР»): из Excel-выгрузки отчёта (.xlsx)
        ИЛИ файла .lgd (SQLite). Фильтры level (Ошибка/Предупреждение/Информация/Примечание)/user/events.
        Возвращает summary (by_level/by_user/by_event) + entries + warnings. Read-only."""
        return event_log_parse(source, level=level or None, user=user or None,
                               events=events or None, top=top, table=table or None)

    @mcp.tool()
    def prometheus_query_tool(query: str, base_url: str = "") -> list:
        """ОПЦИОНАЛЬНО (реже Zabbix): мгновенный PromQL-запрос к Prometheus (read-only). base_url — или из
        env PROMETHEUS_URL. Полезно с экспортером метрик кластера 1С."""
        base_url = base_url or os.environ.get("PROMETHEUS_URL", "")
        if not base_url:
            return [{"error": "укажи base_url или env PROMETHEUS_URL"}]
        return prometheus_query(base_url, query)

    if __name__ == "__main__":
        # транспорт: stdio (по умолчанию, для локального Claude Code) | sse | streamable-http (для docker-центра)
        mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))
except ImportError:  # pragma: no cover — модуль остаётся импортируемым без пакета mcp (для тестов парсера)
    mcp = None
