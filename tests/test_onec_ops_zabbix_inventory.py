"""Тесты ИНВЕНТАРЯ ХОСТОВ в perf-отчёте Zabbix: инвентарные item'ы (ядра, RAM, ОС, версия СУБД,
диски, uptime) не выбрасываются, а собираются в report["inventory"] и рендерятся таблицей —
методика требует интерпретировать находки через знаменатели («31 GiB rphost — много или норм?»),
раньше это добиралось вторым запросом руками.
Запуск:  uv run --with mcp --with pytest pytest -q tests/test_onec_ops_zabbix_inventory.py
"""
import importlib
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _load():
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("onec_ops_mcp"))


ITEMS = [
    {"itemid": "1", "host": "app-01", "name": "Number of CPUs", "key_": "system.cpu.num",
     "lastvalue": "14", "units": "", "value_type": "3"},
    {"itemid": "2", "host": "app-01", "name": "Total memory", "key_": "vm.memory.size[total]",
     "lastvalue": str(85 * 1024**3), "units": "B", "value_type": "3"},
    {"itemid": "3", "host": "app-01", "name": "Operating system", "key_": "system.sw.os",
     "lastvalue": "Windows Server 2022 Standard", "units": "", "value_type": "1"},
    {"itemid": "4", "host": "app-01", "name": "Uptime", "key_": "system.uptime",
     "lastvalue": str(42 * 86400), "units": "s", "value_type": "3"},
    {"itemid": "5", "host": "db-01", "name": "PostgreSQL version", "key_": "pgsql.version[...]",
     "lastvalue": "15.2", "units": "", "value_type": "1"},
    {"itemid": "6", "host": "db-01", "name": "/data: Total space", "key_": "vfs.fs.dependent[/data,total]",
     "lastvalue": str(676 * 10**9), "units": "B", "value_type": "3"},
    {"itemid": "7", "host": "db-01", "name": "/data: Space utilization", "key_": "vfs.fs.dependent[/data,pused]",
     "lastvalue": "63.5", "units": "%", "value_type": "0"},
    # НЕ инвентарь — не должен попасть в факты
    {"itemid": "8", "host": "db-01", "name": "CPU utilization", "key_": "system.cpu.util",
     "lastvalue": "40", "units": "%", "value_type": "0"},
]


def test_inventory_facts_collects_per_host():
    m = _load()
    inv = m.zbx_inventory_facts(ITEMS)
    a = inv["app-01"]
    assert a["cpu_num"] == 14
    assert a["mem_total_bytes"] == 85 * 1024**3
    assert "Windows Server 2022" in a["os"]
    assert a["uptime_s"] == 42 * 86400
    d = inv["db-01"]
    assert d["dbms"].startswith("PostgreSQL 15.2")
    assert d["disks"]["/data"]["total_bytes"] == 676 * 10**9
    assert d["disks"]["/data"]["used_pct"] == 63.5
    assert "cpu_num" not in d  # метрика производительности не утекла в инвентарь


def test_perf_report_carries_inventory_and_renders_it():
    m = _load()

    calls = {}

    def fake_post(url, payload, headers=None):
        method = payload["method"]
        calls.setdefault(method, 0)
        calls[method] += 1
        if method == "host.get":
            return {"jsonrpc": "2.0", "result": [{"hostid": "10", "host": "app-01",
                                                  "name": "app-01"}], "id": 1}
        if method == "item.get":
            return {"jsonrpc": "2.0",
                    "result": [dict(i, hostid="10", hosts=[{"host": "app-01", "name": "app-01"}])
                               for i in ITEMS if i["host"] == "app-01"], "id": 1}
        return {"jsonrpc": "2.0", "result": [], "id": 1}

    rep = m.zabbix_perf_report("http://z/api_jsonrpc.php", hosts=["app-01"],
                               window_hours=1, _post=fake_post)
    assert rep["inventory"]["app-01"]["cpu_num"] == 14
    md = m.render_perf_report(rep)
    assert "Инвентарь" in md
    assert "14" in md and "85" in md  # ядра и RAM в GiB


def test_render_inventory_human_units():
    m = _load()
    line = m._render_inventory_host("db-01", {
        "os": "Linux EL 8", "dbms": "PostgreSQL 15.2", "cpu_num": 14,
        "mem_total_bytes": 41 * 1024**3,
        "disks": {"/data": {"total_bytes": 676 * 10**9, "used_pct": 63.5}}})
    assert "14 vCPU" in line and "41 GiB" in line
    assert "/data" in line and "676" in line and "63" in line
