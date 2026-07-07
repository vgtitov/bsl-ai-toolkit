"""Тесты сравнения «до/после» perf-отчётов Zabbix: раздел «Как докажем ускорение» методики —
повторный прогон ТЕМ ЖЕ охватом и сравнение impact-строк. perf_report_diff сверяет охват
(разный охват/окно = сравнение бракуется) и находки по (host, metric): решённые, новые, динамика.
Запуск:  uv run --with mcp --with pytest pytest -q tests/test_onec_ops_zabbix_perf_diff.py
"""
import importlib
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _load():
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("onec_ops_mcp"))


def _rep(findings, window=24, dashboard="D1, D2", hosts=("h1",), contour="kz"):
    return {"scope": {"dashboard": dashboard, "hosts": list(hosts), "contour": contour},
            "window_hours": window, "findings": findings}


def _f(host, metric, value, impact, persistence=0.5, severity="high", problem="p"):
    return {"host": host, "metric": metric, "value": value, "impact_score": impact,
            "persistence": persistence, "severity": severity, "problem": problem,
            "threshold": 1, "recommendation": "r"}


def test_diff_resolved_new_and_changed():
    m = _load()
    base = _rep([_f("h1", "tlock_count", 185, 90.0),
                 _f("h1", "pg_bloating_tables", 20, 70.0)])
    cur = _rep([_f("h1", "pg_bloating_tables", 8, 30.0),
                _f("h1", "excp_count", 2300, 50.0)])
    d = m.perf_report_diff(base, cur)
    assert not d["scope_mismatch"]
    assert [x["metric"] for x in d["resolved"]] == ["tlock_count"]
    assert [x["metric"] for x in d["new"]] == ["excp_count"]
    ch = d["changed"][0]
    assert ch["metric"] == "pg_bloating_tables"
    assert ch["value_before"] == 20 and ch["value_after"] == 8
    assert ch["impact_before"] == 70.0 and ch["impact_after"] == 30.0


def test_diff_flags_scope_mismatch():
    m = _load()
    base = _rep([], window=24, dashboard="D1", hosts=("h1",))
    cur = _rep([], window=1, dashboard="D1, D2", hosts=("h1", "h2"))
    d = m.perf_report_diff(base, cur)
    joined = " ".join(d["scope_mismatch"])
    assert "окно" in joined and "24" in joined
    assert "дашборд" in joined.lower() or "D2" in joined
    assert "хост" in joined.lower() or "h2" in joined


def test_diff_render_mentions_dynamics():
    m = _load()
    base = _rep([_f("h1", "tlock_count", 185, 90.0)])
    cur = _rep([_f("h1", "tlock_count", 40, 25.0)])
    md = m.render_perf_diff(m.perf_report_diff(base, cur))
    assert "tlock_count" in md and "185" in md and "40" in md
    # решённых/новых нет — разделы не мусорят
    assert "Решён" not in md or "нет" in md.lower()


def test_diff_contour_migration_not_flagged_when_actual_scope_same():
    """Старый baseline снят вручную (без пресета), новый — через --contour с ТЕМ ЖЕ фактическим
    охватом: это миграция на пресеты, а не смена охвата — брака быть не должно."""
    m = _load()
    base = _rep([], contour="")
    cur = _rep([], contour="kz")
    assert m.perf_report_diff(base, cur)["scope_mismatch"] == []


def test_diff_two_named_contours_flagged():
    m = _load()
    d = m.perf_report_diff(_rep([], contour="kz"), _rep([], contour="by"))
    assert any("kz" in s and "by" in s for s in d["scope_mismatch"])
