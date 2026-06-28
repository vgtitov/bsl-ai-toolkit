# /// script
# dependencies = ["mcp"]
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
# Диагностика по порогам 1С/СУБД — находит проблемы из метрик (Zabbix/счётчики).
# Пороги — общеизвестная методика эксплуатации 1С (см. references performance-apdex,
# investigation-methodology). Источник истины — измерение; здесь — правила интерпретации.
# ============================================================================

def diagnose_metrics(metrics, cores=None):
    """Применить пороги к снимку метрик → список проблем с severity и рекомендацией.
    Понимает: swap_pct, page_life_expectancy, cpu_queue (+cores), disk_queue,
    deadlocks_per_sec, lock_waits_per_sec, buffer_cache_hit_pct, latches_ms."""
    flags = []

    def add(metric, value, threshold, severity, problem, rec):
        flags.append({"metric": metric, "value": value, "threshold": threshold,
                      "severity": severity, "problem": problem, "recommendation": rec})

    m = metrics
    if m.get("swap_pct") is not None and m["swap_pct"] > 70:
        add("swap_pct", m["swap_pct"], ">70%", "high",
            "Активный своп — нехватка RAM на сервере 1С",
            "Добавить RAM / разнести процессы; своп под нагрузкой резко роняет производительность")
    if m.get("page_life_expectancy") is not None and m["page_life_expectancy"] < 300:
        add("page_life_expectancy", m["page_life_expectancy"], "<300s", "high",
            "SQL Page Life Expectancy <300с — буферный кэш вымывается, нехватка RAM у СУБД",
            "Добавить RAM серверу СУБД и/или найти запросы, читающие лишние страницы (ТОП-25, план запроса)")
    if m.get("cpu_queue") is not None:
        thr = 2 * cores if cores else None
        if thr and m["cpu_queue"] > thr:
            add("cpu_queue", m["cpu_queue"], f">2×ядер ({thr})",
                "critical" if m["cpu_queue"] > 16 else "high",
                "Очередь к ЦП превышает 2×число ядер — нехватка CPU",
                "Профилировать ТОП-25 вызовов, оптимизировать тяжёлый код/запросы; при стабильном дефиците — нарастить CPU")
        elif m["cpu_queue"] > 16:
            add("cpu_queue", m["cpu_queue"], ">16", "critical",
                "Очередь к ЦП >16 — серьёзная нехватка CPU", "См. рекомендации по ТОП-25 и наращиванию CPU")
    if m.get("disk_queue") is not None and m["disk_queue"] > 2:
        add("disk_queue", m["disk_queue"], ">2 на диск", "high",
            "Очередь к диску >2 — дисковая подсистема не справляется",
            "Быстрее диски (NVMe), разнести данные/tempdb/логи по дискам; убрать избыточный I/O от запросов")
    if m.get("deadlocks_per_sec") is not None and m["deadlocks_per_sec"] > 0:
        add("deadlocks_per_sec", m["deadlocks_per_sec"], ">0", "high",
            "Взаимоблокировки (дедлоки) — транзакции конфликтуют насмерть",
            "Разобрать по TDEADLOCK (см. locks-and-deadlocks): упорядочить захваты ресурсов, сократить транзакции, индексы под условия")
    if m.get("lock_waits_per_sec") is not None and m["lock_waits_per_sec"] > 0:
        add("lock_waits_per_sec", m["lock_waits_per_sec"], ">0", "medium",
            "Ожидания на блокировках — конкуренция за данные",
            "Сократить длительность транзакций, проверить избыточные блокировки и индексы (locks-and-deadlocks)")
    if m.get("buffer_cache_hit_pct") is not None and m["buffer_cache_hit_pct"] < 90:
        add("buffer_cache_hit_pct", m["buffer_cache_hit_pct"], "<90%", "medium",
            "Buffer cache hit низкий — данные читаются с диска, не из кэша",
            "Добавить RAM СУБД / оптимизировать запросы, читающие лишние страницы")
    if m.get("latches_ms") is not None and m["latches_ms"] > 0:
        add("latches_ms", m["latches_ms"], ">0", "medium",
            "Латчи >0 — внутренняя конкуренция СУБД за страницы в памяти",
            "Проверить число файлов tempdb, горячие страницы/таблицы, всплески нагрузки")
    return flags


# ============================================================================
# Zabbix — ОПЦИОНАЛЬНЫЙ адаптер (read-only). Использовать, когда у тебя доступен ТОЛЬКО
# Zabbix (админы дали его, а прямого доступа к ТЖ/rac/СУБД нет). Тянет проблемы/историю
# метрик по JSON-RPC API. Токен — read-only пользователь, из env ZABBIX_TOKEN.
# ============================================================================

def _http_post_json(url, payload):  # pragma: no cover — реальный HTTP, в тестах подменяется
    import urllib.request
    import json as _json
    req = urllib.request.Request(url, data=_json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json-rpc"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return _json.loads(r.read().decode("utf-8"))


def _zbx_call(url, method, params, auth=None, _post=None):
    payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}
    if auth:
        payload["auth"] = auth  # Zabbix <6.4: поле auth; в 6.4+ можно через заголовок Authorization
    resp = (_post or _http_post_json)(url, payload)
    if isinstance(resp, dict) and resp.get("error"):
        e = resp["error"]
        raise RuntimeError(f"Zabbix API error: {e.get('message', '')} {e.get('data', '')}".strip())
    return resp.get("result") if isinstance(resp, dict) else resp


def zabbix_problems(url, auth=None, severities=None, recent=True, _post=None):
    """Текущие/недавние проблемы (триггеры) из Zabbix — как панель «Проблемы на сервере»."""
    params = {"output": ["eventid", "name", "severity", "clock"], "selectHosts": ["name"],
              "recent": recent, "sortfield": ["eventid"], "sortorder": "DESC"}
    if severities:
        params["severities"] = severities
    return _zbx_call(url, "problem.get", params, auth, _post) or []


def zabbix_history(url, auth=None, itemids=None, history=0, time_from=None, limit=500, _post=None):
    """История значений метрики (item) из Zabbix. history: 0=float,1=str,3=uint. time_from — unixtime."""
    params = {"output": "extend", "itemids": itemids, "history": history,
              "sortfield": "clock", "sortorder": "DESC", "limit": limit}
    if time_from:
        params["time_from"] = time_from
    return _zbx_call(url, "history.get", params, auth, _post) or []


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


# --- MCP-обёртка (read-only) ---
try:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("onec-ops")

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
    def prometheus_query_tool(query: str, base_url: str = "") -> list:
        """ОПЦИОНАЛЬНО (реже Zabbix): мгновенный PromQL-запрос к Prometheus (read-only). base_url — или из
        env PROMETHEUS_URL. Полезно с экспортером метрик кластера 1С."""
        base_url = base_url or os.environ.get("PROMETHEUS_URL", "")
        if not base_url:
            return [{"error": "укажи base_url или env PROMETHEUS_URL"}]
        return prometheus_query(base_url, query)

    if __name__ == "__main__":
        mcp.run()
except ImportError:  # pragma: no cover — модуль остаётся импортируемым без пакета mcp (для тестов парсера)
    mcp = None
