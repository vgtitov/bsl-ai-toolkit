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
            return {"result": [{"eventid": "77", "name": "High CPU", "severity": "4", "clock": "1750000000",
                                "hosts": [{"name": "KZ APP01"}]}], "id": 1}
        raise AssertionError(f"неожиданный метод {meth}")

    rep = m.zabbix_perf_report("http://zbx/api_jsonrpc.php", auth="T",
                               dashboardid="408", window_hours=1, _post=fake_post)
    assert rep["scope"]["dashboard"] == "KZ прод"
    assert rep["findings"], "PLE=110с должен дать находку"
    assert rep["findings"][0]["metric"] == "page_life_expectancy"
    assert rep["active_problems"][0]["name"] == "High CPU"
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
