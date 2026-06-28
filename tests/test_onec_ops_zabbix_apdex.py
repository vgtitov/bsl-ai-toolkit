"""Тесты onec_ops: корректный Apdex, диагностика по порогам 1С/SQL, Zabbix-адаптер (read-only).
Запуск:  uv run --with mcp --with pytest pytest -q tests/test_onec_ops_zabbix_apdex.py
"""
import importlib
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _load():
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("onec_ops_mcp"))


# --- Apdex: формула должна быть КАНОНИЧНОЙ (T, 4T) ---
def test_apdex_canonical_formula():
    m = _load()
    # T=1.0: satisfied ≤1 (0.5,1.0)=2; tolerating 1<x≤4 (2.0)=1; frustrated >4 (5.0)=1
    r = m.apdex([0.5, 1.0, 2.0, 5.0], 1.0)
    assert r["satisfied"] == 2 and r["tolerating"] == 1 and r["frustrated"] == 1
    assert r["total"] == 4
    assert abs(r["apdex"] - 0.625) < 1e-9          # (2 + 1/2) / 4
    assert r["rating"] in {"poor", "unacceptable", "fair", "good", "excellent"}


def test_apdex_boundaries_inclusive():
    m = _load()
    # ровно T → satisfied; ровно 4T → tolerating (граница включительно)
    r = m.apdex([1.0, 4.0], 1.0)
    assert r["satisfied"] == 1 and r["tolerating"] == 1 and r["frustrated"] == 0
    assert abs(r["apdex"] - 0.75) < 1e-9


def test_apdex_by_operation_threshold_per_op():
    m = _load()
    # у разных операций свой порог T (открытие формы vs проведение документа)
    records = [
        {"operation": "ОткрытиеФормы", "duration": 0.3},
        {"operation": "ОткрытиеФормы", "duration": 2.0},
        {"operation": "Проведение", "duration": 3.0},
        {"operation": "Проведение", "duration": 30.0},
    ]
    res = m.apdex_by_operation(records, thresholds={"ОткрытиеФормы": 1.0, "Проведение": 5.0}, default_t=1.0)
    by = {o["operation"]: o for o in res["operations"]}
    assert by["ОткрытиеФормы"]["satisfied"] == 1 and by["ОткрытиеФормы"]["tolerating"] == 1
    # Проведение T=5: 3.0 satisfied, 30.0 frustrated (>20)
    assert by["Проведение"]["satisfied"] == 1 and by["Проведение"]["frustrated"] == 1


# --- Диагностика по порогам (находит проблемы из метрик Zabbix/счётчиков) ---
def test_zabbix_diagnose_thresholds():
    m = _load()
    flags = m.diagnose_metrics(
        {"swap_pct": 80, "page_life_expectancy": 200, "cpu_queue": 5,
         "disk_queue": 4, "deadlocks_per_sec": 3, "buffer_cache_hit_pct": 85},
        cores=2)
    keys = {f["metric"] for f in flags}
    assert {"swap_pct", "page_life_expectancy", "cpu_queue", "disk_queue", "deadlocks_per_sec"} <= keys
    # у каждого флага — порог, severity и рекомендация
    f = next(f for f in flags if f["metric"] == "page_life_expectancy")
    assert f["severity"] in {"high", "critical", "medium"} and f["recommendation"]


def test_zabbix_diagnose_all_green():
    m = _load()
    flags = m.diagnose_metrics(
        {"swap_pct": 5, "page_life_expectancy": 5000, "cpu_queue": 1,
         "disk_queue": 0, "deadlocks_per_sec": 0, "buffer_cache_hit_pct": 99},
        cores=8)
    assert flags == []


# --- Zabbix API (read-only) с инъекцией HTTP-слоя (без живого сервера) ---
def test_zabbix_problems_parsing():
    m = _load()
    calls = {}

    def fake_post(url, payload):
        calls["method"] = payload["method"]
        assert url.endswith("/api_jsonrpc.php")
        return {"jsonrpc": "2.0", "result": [
            {"eventid": "1", "name": "MSSQL DB log backup is old", "severity": "4", "hosts": [{"name": "SERVER1C"}]}],
            "id": 1}

    res = m.zabbix_problems("http://zbx.example/api_jsonrpc.php", auth="TOKEN", _post=fake_post)
    assert calls["method"] == "problem.get"
    assert res[0]["name"].startswith("MSSQL DB log backup")


def test_zabbix_api_error_raises():
    m = _load()

    def fake_post(url, payload):
        return {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params", "data": "no perm"}, "id": 1}

    try:
        m.zabbix_problems("http://zbx/api_jsonrpc.php", auth="bad", _post=fake_post)
        assert False, "должно бросить на error"
    except RuntimeError as e:
        assert "Invalid params" in str(e) or "no perm" in str(e)


# --- Prometheus (опциональный, реже Zabbix) — read-only PromQL с инъекцией HTTP ---
def test_prometheus_query_parsing():
    m = _load()

    def fake_get(url, params):
        assert url.endswith("/api/v1/query")
        assert params["query"] == "up"
        return {"status": "success", "data": {"resultType": "vector",
                "result": [{"metric": {"job": "1c", "instance": "server1c"}, "value": [1717000000, "1"]}]}}

    res = m.prometheus_query("http://prom.example:9090", "up", _get=fake_get)
    assert res[0]["metric"]["job"] == "1c"
    assert res[0]["value"] == "1"
    assert res[0]["timestamp"] == 1717000000


def test_prometheus_error_raises():
    m = _load()

    def fake_get(url, params):
        return {"status": "error", "errorType": "bad_data", "error": "parse error at char 1"}

    try:
        m.prometheus_query("http://prom:9090", "???", _get=fake_get)
        assert False, "должно бросить на error"
    except RuntimeError as e:
        assert "parse error" in str(e)
