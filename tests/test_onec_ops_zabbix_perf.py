"""Тесты слоя АНАЛИЗА ПРОИЗВОДИТЕЛЬНОСТИ поверх Zabbix API (read-only, без живого сервера):
дашборд → items, классификация метрик, агрегация истории/трендов, ранжирование находок
по вкладу в производительность (самое значимое сверху).
Запуск:  uv run --with mcp --with pytest pytest -q tests/test_onec_ops_zabbix_perf.py
"""
import importlib
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _load():
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("onec_ops_mcp"))


# ---------------------------------------------------------------------------
# Аутентификация: Zabbix 6.4+/7.0 — токен в заголовке Authorization: Bearer,
# НЕ в payload (auth в payload deprecated в 7.0, удалён в 7.2).
# Старые fake_post(url, payload) без headers должны продолжать работать (fallback).
# ---------------------------------------------------------------------------
def test_zbx_call_uses_bearer_header():
    m = _load()
    seen = {}

    def fake_post(url, payload, headers=None):
        seen["headers"] = headers or {}
        seen["payload"] = payload
        return {"jsonrpc": "2.0", "result": [], "id": 1}

    m.zabbix_problems("http://zbx/api_jsonrpc.php", auth="TOK123", _post=fake_post)
    assert seen["headers"].get("Authorization") == "Bearer TOK123"
    assert "auth" not in seen["payload"]


def test_zbx_call_fallback_payload_auth_for_legacy_post():
    m = _load()
    seen = {}

    def fake_post(url, payload):  # старая сигнатура без headers
        seen["payload"] = payload
        return {"jsonrpc": "2.0", "result": [], "id": 1}

    m.zabbix_problems("http://zbx/api_jsonrpc.php", auth="TOK123", _post=fake_post)
    assert seen["payload"].get("auth") == "TOK123"


def test_zbx_call_does_not_swallow_typeerror_from_post():
    m = _load()

    def broken_post(url, payload, headers=None):  # современная сигнатура, но падает внутри
        raise TypeError("object is not JSON serializable")

    try:
        m.zabbix_problems("http://zbx/api_jsonrpc.php", auth="T", _post=broken_post)
        assert False, "TypeError изнутри post не должен маскироваться fallback'ом"
    except TypeError as e:
        assert "serializable" in str(e)


def test_zbx_api_readonly_whitelist():
    m = _load()

    def fake_post(url, payload, headers=None):
        return {"result": [], "id": 1}

    assert m.zabbix_api("http://z/api_jsonrpc.php", "host.get", {}, _post=fake_post) == []
    for bad in ("user.login", "host.delete", "item.create", "configuration.import"):
        try:
            m.zabbix_api("http://z/api_jsonrpc.php", bad, {}, _post=fake_post)
            assert False, f"{bad} должен быть запрещён"
        except RuntimeError as e:
            assert "read-only" in str(e)


# ---------------------------------------------------------------------------
# Дашборд → элементы данных: widget fields type=4 (item) и type=6 (graph→items).
# Дашборд может меняться — извлечение НЕ завязано на имена полей/тип виджета.
# ---------------------------------------------------------------------------
def _dashboard_fake_post(calls):
    def fake_post(url, payload, headers=None):
        method = payload["method"]
        calls.append(method)
        if method == "dashboard.get":
            return {"result": [{
                "dashboardid": "408", "name": "KZ прод",
                "pages": [{"widgets": [
                    {"type": "svggraph", "name": "CPU", "fields": [
                        {"type": "4", "name": "ds.0.itemids.0", "value": "111"},
                        {"type": "1", "name": "ds.0.color", "value": "FF0000"},
                    ]},
                    {"type": "graph", "name": "Диск", "fields": [
                        {"type": "6", "name": "graphid.0", "value": "9"},
                    ]},
                ]}],
            }], "id": 1}
        if method == "graph.get":
            assert payload["params"]["graphids"] == ["9"]
            return {"result": [{"graphid": "9", "gitems": [{"itemid": "222"}]}], "id": 1}
        if method == "item.get":
            assert set(payload["params"]["itemids"]) == {"111", "222"}
            return {"result": [
                {"itemid": "111", "name": "CPU utilization", "key_": "system.cpu.util",
                 "units": "%", "value_type": "0", "lastvalue": "35.5", "lastclock": "1750000000",
                 "hosts": [{"host": "kz-app01", "name": "KZ APP01"}]},
                {"itemid": "222", "name": "Avg. Disk Queue Length", "key_": "perf_counter_en[\"\\\\PhysicalDisk(_Total)\\\\Avg. Disk Queue Length\"]",
                 "units": "", "value_type": "0", "lastvalue": "4.2", "lastclock": "1750000000",
                 "hosts": [{"host": "kz-db01", "name": "KZ DB01"}]},
            ], "id": 1}
        raise AssertionError(f"неожиданный метод {method}")
    return fake_post


def test_dashboard_items_from_widgets_and_graphs():
    m = _load()
    calls = []
    res = m.zabbix_dashboard_items("http://zbx/api_jsonrpc.php", auth="T",
                                   dashboardid="408", _post=_dashboard_fake_post(calls))
    assert res["dashboard"] == "KZ прод"
    ids = {i["itemid"] for i in res["items"]}
    assert ids == {"111", "222"}
    assert "graph.get" in calls and "item.get" in calls
    host_by_item = {i["itemid"]: i["host"] for i in res["items"]}
    assert host_by_item["111"] == "KZ APP01"


# ---------------------------------------------------------------------------
# Классификация item → каноничная метрика (работает с разными данными:
# Windows perf_counter, Linux-агенты, шаблоны MSSQL) + трансформация значения.
# ---------------------------------------------------------------------------
def test_classify_items_canonical_metrics():
    m = _load()

    def c(key_, name="", units=""):
        return m.zbx_classify_item({"key_": key_, "name": name, "units": units})

    assert c("system.cpu.util", "CPU utilization")[0] == "cpu_util"
    assert c('perf_counter_en["\\System\\Processor Queue Length"]')[0] == "cpu_queue"
    assert c("vm.memory.size[pavailable]", "Available memory in %")[0] == "mem_available_pct"
    assert c('perf_counter_en["\\SQLServer:Buffer Manager\\Page life expectancy"]')[0] == "page_life_expectancy"
    assert c("", "MSSQL: Buffer cache hit ratio")[0] == "buffer_cache_hit_pct"
    assert c("", "MSSQL: Deadlocks per second")[0] == "deadlocks_per_sec"
    assert c('perf_counter_en["\\PhysicalDisk(_Total)\\Avg. Disk Queue Length"]')[0] == "disk_queue"
    assert c("vfs.dev.util[sda]", "sda: Disk utilization")[0] == "disk_util"
    # неизвестное — None, не падает
    assert c("agent.ping", "Zabbix agent ping") is None


def test_classify_1c_and_windows_memory_items():
    """Реальный дашборд КЗ (vc-1c-app-01): кастомные 1С-метрики и память Windows
    должны распознаваться, а CPU процесса (\\Process(rphost*)) НЕ должен попадать
    в системный cpu_util (счётчик процесса >100% = больше одного ядра)."""
    m = _load()

    def c(key_, name="", units=""):
        return m.zbx_classify_item({"key_": key_, "name": name, "units": units})

    assert c('perf_counter_en["\\Process(rphost*)\\% Processor Time"]', "CPU процессов rphost")[0] == "process_cpu_pct"
    assert c("system.cpu.util", "CPU utilization")[0] == "cpu_util"  # системный — как раньше
    assert c('perf_counter_en["\\Memory\\Pages/sec"]', "Memory pages per second")[0] == "pages_per_sec"
    assert c('perf_counter_en["\\Memory\\% Committed Bytes In Use"]', "Процент выделенной виртуальной памяти")[0] == "commit_pct"
    assert c("vm.memory.size[available]", "Available memory in Bytes", "B")[0] == "mem_available_bytes"
    assert c("1c.tj.ttimeout", "1C: Techlog Lock Timeouts Count (TTIMEOUT)")[0] == "lock_timeouts_count"
    assert c("1c.tj.excp", "1C: Techlog Exceptions Count (EXCP)")[0] == "excp_count"
    assert c("1c.sessions.total", "1C: Total Sessions Count")[0] == "sessions_total"
    assert c("1c.sessions.licenses", "1C: Licenses Count")[0] == "licenses_count"


def test_diagnose_windows_memory_and_1c_thresholds():
    m = _load()
    flags = m.diagnose_metrics({"commit_pct": 95, "pages_per_sec": 2500,
                                "mem_available_bytes": 400 * 1024 * 1024,
                                "lock_timeouts_count": 3, "excp_count": 50, "tlock_count": 10})
    by = {f["metric"]: f for f in flags}
    assert by["commit_pct"]["severity"] == "high"
    assert "pages_per_sec" in by and "mem_available_bytes" in by
    assert by["lock_timeouts_count"]["severity"] == "high"  # таймауты блокировок = боль пользователей
    assert "excp_count" in by and "tlock_count" in by
    # контекстные метрики (сессии/лицензии/CPU процесса) НЕ дают находок сами по себе
    assert m.diagnose_metrics({"sessions_total": 500, "licenses_count": 400, "process_cpu_pct": 250}) == []


def test_diagnose_its_thresholds():
    """Пороги по методике ИТС (metod8dev/5838): CPU >70%, Pages/sec >20 (сигнал к проверке)."""
    m = _load()
    by = {f["metric"]: f for f in m.diagnose_metrics({"cpu_util": 75, "pages_per_sec": 30})}
    assert "cpu_util" in by, "по ИТС загрузка ЦП штатно не должна превышать 70%"
    assert by["cpu_util"]["severity"] == "medium"  # 75 — выше методики, но не пожар
    assert by["pages_per_sec"]["severity"] == "low"  # >20 по ИТС — проверить вместе с RAM/свопом
    assert {f["metric"] for f in m.diagnose_metrics({"cpu_util": 95})} == {"cpu_util"}
    assert next(iter(m.diagnose_metrics({"cpu_util": 95})))["severity"] == "high"


def test_text_evidence_classified_and_reported():
    """Текстовые item'ы (контекст CALL из ТЖ, инфо о держателе сессии) → секция evidence_texts,
    а не unclassified: это прямые указатели на места кода."""
    m = _load()

    def fake_post(url, payload, headers=None):
        meth = payload["method"]
        if meth == "dashboard.get":
            return {"result": [{"dashboardid": "4", "name": "D", "pages": [{"widgets": [
                {"type": "item", "name": "w", "fields": [{"type": "4", "name": "itemid.0", "value": "7"}]},
                {"type": "item", "name": "w2", "fields": [{"type": "4", "name": "itemid.0", "value": "8"}]},
            ]}]}], "id": 1}
        if meth == "item.get":
            return {"result": [
                {"itemid": "7", "name": "1C: Techlog CALL Context Logs", "key_": "1c.tj.call_context",
                 "units": "", "value_type": "4",
                 "lastvalue": "22046 ms  База->ОбщийМодуль.ОбменДаннымиСервер.Модуль : 3689", "lastclock": "1",
                 "hosts": [{"host": "h", "name": "H"}]},
                {"itemid": "8", "name": "1C: Max Session Held Context Info", "key_": "1c.sessions.held_info",
                 "units": "", "value_type": "4",
                 "lastvalue": "База: X | Пользователь: Y | Время: 114 мин.", "lastclock": "1",
                 "hosts": [{"host": "h", "name": "H"}]},
            ], "id": 1}
        if meth == "history.get":
            return {"result": [], "id": 1}
        if meth == "problem.get":
            return {"result": [], "id": 1}
        raise AssertionError(meth)

    rep = m.zabbix_perf_report("http://zbx/api_jsonrpc.php", auth="T", dashboardid="4", _post=fake_post)
    assert not rep["unclassified"]
    ev = rep["evidence_texts"]["H"]
    assert "ОбменДаннымиСервер" in ev["tj_call_context"]
    assert "114 мин" in ev["session_held_info"]
    assert "ОбменДаннымиСервер" in m.render_perf_report(rep)


def test_context_metrics_in_report():
    """Сессии/лицензии/CPU rphost — не проблемы, а контекст нагрузки: в отчёте отдельно."""
    m = _load()

    def fake_post(url, payload, headers=None):
        meth = payload["method"]
        if meth == "dashboard.get":
            return {"result": [{"dashboardid": "3", "name": "D", "pages": [{"widgets": [
                {"type": "item", "name": "w", "fields": [{"type": "4", "name": "itemid.0", "value": "1"}]},
                {"type": "item", "name": "w2", "fields": [{"type": "4", "name": "itemid.0", "value": "2"}]},
            ]}]}], "id": 1}
        if meth == "item.get":
            return {"result": [
                {"itemid": "1", "name": "1C: Total Sessions Count", "key_": "1c.sessions.total",
                 "units": "", "value_type": "3", "lastvalue": "412", "lastclock": "1", "hosts": [{"host": "h", "name": "H"}]},
                {"itemid": "2", "name": "CPU процессов rphost", "key_": 'perf_counter_en["\\Process(rphost*)\\% Processor Time"]',
                 "units": "%", "value_type": "0", "lastvalue": "114", "lastclock": "1", "hosts": [{"host": "h", "name": "H"}]},
            ], "id": 1}
        if meth == "history.get":
            iid = payload["params"]["itemids"][0]
            return {"result": [{"itemid": iid, "clock": "1750000000", "value": "412" if iid == "1" else "114"}], "id": 1}
        if meth == "problem.get":
            return {"result": [], "id": 1}
        raise AssertionError(meth)

    rep = m.zabbix_perf_report("http://zbx/api_jsonrpc.php", auth="T", dashboardid="3", _post=fake_post)
    assert rep["findings"] == []  # контекст не превращается в находки
    assert rep["context"]["H"]["sessions_total"] == 412.0
    assert rep["context"]["H"]["process_cpu_pct"] == 114.0
    assert not rep["unclassified"]  # распознаны, не потеряны
    text = m.render_perf_report(rep)
    assert "Контекст нагрузки" in text and "412" in text


def test_problems_hosts_resolved_via_trigger_get():
    """Живой Zabbix 7.0 отверг selectHosts у problem.get — хосты добираются через trigger.get."""
    m = _load()
    methods = []

    def fake_post(url, payload, headers=None):
        meth = payload["method"]
        methods.append(meth)
        if meth == "problem.get":
            assert "selectHosts" not in payload["params"], "problem.get не поддерживает selectHosts (Zabbix 7.0)"
            return {"result": [{"eventid": "9", "objectid": "555", "name": "High CPU",
                                "severity": "4", "clock": "1750000000"}], "id": 1}
        if meth == "trigger.get":
            assert payload["params"]["triggerids"] == ["555"]
            return {"result": [{"triggerid": "555", "hosts": [{"host": "h1", "name": "KZ APP01"}]}], "id": 1}
        raise AssertionError(meth)

    res = m.zabbix_problems_with_hosts("http://zbx/api_jsonrpc.php", auth="T", _post=fake_post)
    assert methods == ["problem.get", "trigger.get"]
    assert res[0]["hosts"] == ["KZ APP01"]


def test_classify_cpu_idle_not_cpu_util():
    """system.cpu.util[,idle|user|system|iowait] — режимы, НЕ общая загрузка (кейс: idle 82%
    ложно давал «ЦП загружен 82%»). iowait — своя метрика, idle/user/system — контекст/None."""
    m = _load()

    def c(key_, name=""):
        return m.zbx_classify_item({"key_": key_, "name": name, "units": "%"})

    assert c("system.cpu.util", "CPU utilization")[0] == "cpu_util"
    assert c("system.cpu.util[,idle]", "CPU idle time") is None
    assert c("system.cpu.util[,user]", "CPU user time") is None
    assert c("system.cpu.util[,iowait]", "CPU iowait time")[0] == "cpu_iowait_pct"


def test_classify_postgres_metrics():
    m = _load()

    def c(key_, name="", units=""):
        return m.zbx_classify_item({"key_": key_, "name": name, "units": units})

    assert c("pgsql.cache.hit", "Cache hit ratio, %")[0] == "buffer_cache_hit_pct"
    assert c("pgsql.dbstat.sum.temp_files.rate", "Dbstat: Number temp files per second")[0] == "pg_temp_files_per_sec"
    assert c('pgsql.db.bloating_tables[...,"ERP"]', "DB [ERP]: Bloating tables")[0] == "pg_bloating_tables"
    assert c("pgsql.dbstat.sum.deadlocks.rate", "Deadlocks per second")[0] == "deadlocks_per_sec"
    # Linux swap free % в варианте system.swap.size[,pfree] и Windows Paging file % Usage
    ms, tr = c("system.swap.size[,pfree]", "Free swap space in %")
    assert ms == "swap_pct" and tr(70.0) == 30.0
    assert c('perf_counter_en["\\Paging file(_Total)\\% Usage"]', "Used swap space in %")[0] == "swap_pct"


def test_inventory_items_not_in_unclassified():
    """Служебные/инвентарные item'ы (agent.ping, hostname, total memory, uptime) — не шум
    в unclassified: игнорируются осознанно."""
    m = _load()
    for key_, name in [("agent.ping", "Zabbix agent ping"), ("system.uname", "System description"),
                       ("system.uptime", "Uptime"), ("vm.memory.size[total]", "Total memory"),
                       ("system.hostname", "System name"), ("agent.version", "Version of Zabbix agent"),
                       ('wmi.get[root/cimv2,"Select X"]', "Number of cores")]:
        assert m.zbx_is_inventory_item({"key_": key_, "name": name}), f"{key_} должен игнорироваться"
    assert not m.zbx_is_inventory_item({"key_": "system.cpu.util", "name": "CPU utilization"})


def test_diagnose_pg_thresholds():
    m = _load()
    by = {f["metric"]: f for f in m.diagnose_metrics(
        {"pg_temp_files_per_sec": 0.5, "pg_bloating_tables": 18, "cpu_iowait_pct": 30})}
    assert "pg_temp_files_per_sec" in by  # сортировки на диске → work_mem
    assert "pg_bloating_tables" in by     # регламент обслуживания
    assert by["cpu_iowait_pct"]["severity"] in {"medium", "high"}


def test_classify_swap_pfree_transforms_to_used_pct():
    m = _load()
    metric, transform = m.zbx_classify_item({"key_": "system.swap.pfree", "name": "Free swap in %", "units": "%"})
    assert metric == "swap_pct"
    assert transform(25.0) == 75.0  # 25% свободно → 75% занято


# ---------------------------------------------------------------------------
# Агрегация истории: avg/max/p95/last + доля образцов за порогом (persistence).
# ---------------------------------------------------------------------------
def test_history_stats_aggregation():
    m = _load()

    def fake_post(url, payload, headers=None):
        assert payload["method"] == "history.get"
        assert payload["params"]["history"] == 0  # value_type item'а
        # сервер отдаёт DESC (свежие вперёд) — клиент обязан восстановить порядок по clock
        rows = [{"itemid": "111", "clock": str(1750000000 + i), "value": str(v)}
                for i, v in enumerate([1.0, 2.0, 3.0, 10.0])]
        return {"result": list(reversed(rows)), "id": 1}

    items = [{"itemid": "111", "value_type": "0", "name": "x", "key_": "k", "units": "", "host": "h1"}]
    stats = m.zabbix_item_stats("http://zbx/api_jsonrpc.php", auth="T", items=items,
                                time_from=1750000000, time_till=1750003600, _post=fake_post)
    s = stats["111"]
    assert s["n"] == 4 and s["last"] == 10.0 and s["max"] == 10.0
    assert abs(s["avg"] - 4.0) < 1e-9
    assert s["p95"] >= 3.0
    assert "truncated" not in s


def test_history_stats_per_item_requests_and_truncation():
    m = _load()
    m._HIST_LIMIT = 3  # уменьшить лимит для теста; _load() перезагружает модуль — не протекает
    requested = []

    def fake_post(url, payload, headers=None):
        ids = payload["params"]["itemids"]
        assert len(ids) == 1, "запрос должен идти ПО ОДНОМУ item (лимит не делится на батч)"
        requested.append(ids[0])
        return {"result": [{"itemid": ids[0], "clock": str(1750000000 + i), "value": "1"}
                           for i in range(3)], "id": 1}  # ровно limit → truncated

    items = [{"itemid": "1", "value_type": "0"}, {"itemid": "2", "value_type": "0"},
             {"itemid": "3", "value_type": "1"}]  # строковый — пропускается
    stats = m.zabbix_item_stats("http://zbx/api_jsonrpc.php", auth="T", items=items,
                                time_from=1750000000, time_till=1750003600, _post=fake_post)
    assert requested == ["1", "2"]
    assert stats["1"]["truncated"] is True and stats["2"]["truncated"] is True


def test_trend_stats_for_long_window():
    m = _load()
    methods = []

    def fake_post(url, payload, headers=None):
        methods.append(payload["method"])
        assert payload["method"] == "trend.get"
        return {"result": [{"itemid": "111", "clock": "1749000000", "num": "60",
                            "value_min": "1", "value_avg": "2", "value_max": "9"}], "id": 1}

    items = [{"itemid": "111", "value_type": "0", "name": "x", "key_": "k", "units": "", "host": "h1"}]
    # окно > 48 часов → trend.get вместо history.get
    stats = m.zabbix_item_stats("http://zbx/api_jsonrpc.php", auth="T", items=items,
                                time_from=1749000000, time_till=1749000000 + 72 * 3600, _post=fake_post)
    assert methods == ["trend.get"]
    assert stats["111"]["max"] == 9.0


# ---------------------------------------------------------------------------
# Новые пороги диагностики (расширение diagnose_metrics) — старые тесты не трогаем.
# ---------------------------------------------------------------------------
def test_diagnose_new_thresholds():
    m = _load()
    flags = m.diagnose_metrics({"cpu_util": 95, "mem_available_pct": 5,
                                "disk_util": 95, "disk_await_ms": 40, "net_errors_per_sec": 2})
    keys = {f["metric"] for f in flags}
    assert {"cpu_util", "mem_available_pct", "disk_util", "disk_await_ms", "net_errors_per_sec"} <= keys


# ---------------------------------------------------------------------------
# Ранжирование: находки отсортированы по impact_score (вклад в производительность),
# самое значимое — первым. Persistence усиливает score.
# ---------------------------------------------------------------------------
def test_ranking_most_impactful_first():
    m = _load()
    host_metrics = {
        "kz-db01": {
            "snapshot": {"page_life_expectancy": 60, "buffer_cache_hit_pct": 89.5},
            "samples": {"page_life_expectancy": [50, 60, 70, 55], "buffer_cache_hit_pct": [89, 90, 89.5]},
            "cores": 16,
        },
        "kz-app01": {
            "snapshot": {"cpu_util": 82},
            "samples": {"cpu_util": [10, 20, 82]},
            "cores": 8,
        },
    }
    findings = m.rank_findings(host_metrics)
    assert findings, "должны быть находки"
    scores = [f["impact_score"] for f in findings]
    assert scores == sorted(scores, reverse=True)
    # PLE=60 при пороге 300 (в 5 раз хуже, high, 100% образцов) должен быть выше cache hit (medium)
    top = findings[0]
    assert top["metric"] == "page_life_expectancy" and top["host"] == "kz-db01"
    assert 0 < top["persistence"] <= 1
    assert top["recommendation"]


# ---------------------------------------------------------------------------
# Сквозной отчёт: дашборд → items → история → находки (ранжированы) + активные проблемы.
# ---------------------------------------------------------------------------
def test_perf_report_end_to_end():
    m = _load()

    def fake_post(url, payload, headers=None):
        meth = payload["method"]
        if meth == "dashboard.get":
            return {"result": [{"dashboardid": "408", "name": "KZ прод", "pages": [{"widgets": [
                {"type": "item", "name": "PLE", "fields": [{"type": "4", "name": "itemid.0", "value": "5"}]},
            ]}]}], "id": 1}
        if meth == "item.get":
            return {"result": [{"itemid": "5", "name": "MSSQL: Page life expectancy",
                                "key_": 'perf_counter_en["\\SQLServer:Buffer Manager\\Page life expectancy"]',
                                "units": "s", "value_type": "0", "lastvalue": "100", "lastclock": "1750000000",
                                "hosts": [{"host": "kz-db01", "name": "KZ DB01"}]}], "id": 1}
        if meth == "history.get":
            return {"result": [{"itemid": "5", "clock": "1750000000", "value": "100"},
                               {"itemid": "5", "clock": "1750000060", "value": "120"}], "id": 1}
        if meth == "problem.get":
            return {"result": [{"eventid": "77", "objectid": "555", "name": "High CPU",
                                "severity": "4", "clock": "1750000000"}], "id": 1}
        if meth == "trigger.get":
            return {"result": [{"triggerid": "555", "hosts": [{"host": "app01", "name": "KZ APP01"}]}], "id": 1}
        raise AssertionError(f"неожиданный метод {meth}")

    rep = m.zabbix_perf_report("http://zbx/api_jsonrpc.php", auth="T",
                               dashboardid="408", window_hours=1, _post=fake_post)
    assert rep["scope"]["dashboard"] == "KZ прод"
    assert rep["findings"], "PLE=110с должен дать находку"
    assert rep["findings"][0]["metric"] == "page_life_expectancy"
    assert rep["active_problems"][0]["name"] == "High CPU"
    assert rep["active_problems"][0]["hosts"] == ["KZ APP01"]
    # рендер: человекочитаемый markdown, самое значимое первым
    text = m.render_perf_report(rep)
    assert "page_life_expectancy" in text or "Page life expectancy" in text or "буферный кэш" in text.lower()


def test_perf_report_persistence_follows_worst_item():
    """Два диска на хосте: больной и здоровый. Persistence находки — по БОЛЬНОМУ item,
    а не по объединённому пулу образцов (иначе impact занижается вдвое)."""
    m = _load()

    def fake_post(url, payload, headers=None):
        meth = payload["method"]
        if meth == "dashboard.get":
            return {"result": [{"dashboardid": "2", "name": "D", "pages": [{"widgets": [
                {"type": "item", "name": "w1", "fields": [{"type": "4", "name": "itemid.0", "value": "10"}]},
                {"type": "item", "name": "w2", "fields": [{"type": "4", "name": "itemid.0", "value": "20"}]},
            ]}]}], "id": 1}
        if meth == "item.get":
            return {"result": [
                {"itemid": "10", "name": "sda: Disk utilization", "key_": "vfs.dev.util[sda]",
                 "units": "%", "value_type": "0", "lastvalue": "99", "lastclock": "1", "hosts": [{"host": "h", "name": "H"}]},
                {"itemid": "20", "name": "sdb: Disk utilization", "key_": "vfs.dev.util[sdb]",
                 "units": "%", "value_type": "0", "lastvalue": "5", "lastclock": "1", "hosts": [{"host": "h", "name": "H"}]},
            ], "id": 1}
        if meth == "history.get":
            iid = payload["params"]["itemids"][0]
            val = "99" if iid == "10" else "5"
            return {"result": [{"itemid": iid, "clock": str(1750000000 + i), "value": val}
                               for i in range(4)], "id": 1}
        if meth == "problem.get":
            return {"result": [], "id": 1}
        raise AssertionError(meth)

    rep = m.zabbix_perf_report("http://zbx/api_jsonrpc.php", auth="T", dashboardid="2", _post=fake_post)
    f = next(f for f in rep["findings"] if f["metric"] == "disk_util")
    assert f["persistence"] == 1.0, "образцы здорового sdb не должны размывать persistence больного sda"
    assert "sda" in (f.get("evidence") or {}).get("key", "")


def test_perf_report_unclassified_not_lost():
    m = _load()

    def fake_post(url, payload, headers=None):
        meth = payload["method"]
        if meth == "dashboard.get":
            return {"result": [{"dashboardid": "1", "name": "D", "pages": [{"widgets": [
                {"type": "item", "name": "w", "fields": [{"type": "4", "name": "itemid.0", "value": "9"}]},
            ]}]}], "id": 1}
        if meth == "item.get":
            return {"result": [{"itemid": "9", "name": "Какая-то экзотика", "key_": "custom.metric",
                                "units": "", "value_type": "0", "lastvalue": "1", "lastclock": "0",
                                "hosts": [{"host": "h", "name": "H"}]}], "id": 1}
        if meth == "history.get":
            return {"result": [], "id": 1}
        if meth == "problem.get":
            return {"result": [], "id": 1}
        raise AssertionError(meth)

    rep = m.zabbix_perf_report("http://zbx/api_jsonrpc.php", auth="T", dashboardid="1", _post=fake_post)
    # неклассифицированное не теряется молча — фиксируется в отчёте
    assert any("custom.metric" in u or "экзотика" in u for u in rep["unclassified"])
