"""Тесты MCP эксплуатации 1С (onec_ops) — парсер технологического журнала.
Запуск:  uv run --with mcp --with pytest pytest -q tests/test_onec_ops_mcp.py
Проверяют: мультикорневой обход (разные серверы/кластеры), атрибуцию источника, фильтры
по событию/длительности/времени, агрегаты для расследования на уровне компании.
"""
import importlib
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _load():
    sys.path.insert(0, str(MCP_DIR))
    mod = importlib.import_module("onec_ops_mcp")
    return importlib.reload(mod)


def _make_tj(tmp_path):
    """Два «сервера кластера», у каждого свой каталог ТЖ с процессом и файлом часа 26062810 = 2026-06-28 10ч."""
    srv1 = tmp_path / "srv-1c-1"
    f1 = srv1 / "rphost_111" / "26062810.log"
    f1.parent.mkdir(parents=True)
    # событие-старт: mm:ss.micro-<duration>,Event,level,props ; Context — многострочный
    f1.write_text(
        "10:00.123456-1500000,DBPOSTGRS,3,process=rphost,Sql='SELECT T1._IDRRef FROM _Document123 T1',Rows=42\n"
        "10:00.200000-50,CALL,2,process=rphost,Memory=1024\n"
        "10:01.000000-3000000,TDEADLOCK,4,process=rphost,DeadlockConnectionIntersections='17 16 1 2'\n"
        "10:01.500000-200,SDBL,1,Func=Context,\n"
        "  ПродолжениеКонтекста многострочное\n",
        encoding="utf-8")
    srv2 = tmp_path / "srv-1c-2"
    f2 = srv2 / "rmngr_222" / "26062810.log"
    f2.parent.mkdir(parents=True)
    f2.write_text(
        "10:05.000000-2000000,TLOCK,4,process=rmngr,WaitConnections=5,Regions='_Document123'\n"
        "10:06.000000-10,CALL,2,process=rmngr\n",
        encoding="utf-8")
    return srv1, srv2


def test_multiroot_company_wide(tmp_path):
    mod = _load()
    srv1, srv2 = _make_tj(tmp_path)
    # группа каталогов разных серверов/кластеров — расследование по всей компании
    res = mod.parse_techlog([{"path": str(srv1), "label": "cluster-A/srv1"},
                             {"path": str(srv2), "label": "cluster-A/srv2"}])
    assert res["summary"]["files"] == 2
    assert res["summary"]["events_total"] == 6
    # агрегаты по событию и по источнику — для локализации проблемы
    assert res["summary"]["by_event"]["TDEADLOCK"] == 1
    assert res["summary"]["by_event"]["CALL"] == 2
    assert set(res["summary"]["by_source"].keys()) == {"cluster-A/srv1", "cluster-A/srv2"}
    # каждое событие тегировано источником + процессом + файлом
    ev = res["events"][0]
    assert {"source", "process", "file", "event", "duration", "timestamp"} <= set(ev)


def test_filter_event_and_duration(tmp_path):
    mod = _load()
    srv1, srv2 = _make_tj(tmp_path)
    res = mod.parse_techlog([str(srv1), str(srv2)],
                            events=["TDEADLOCK", "TLOCK"], min_duration=1000000, top=10)
    names = sorted(e["event"] for e in res["events"])
    assert names == ["TDEADLOCK", "TLOCK"]
    # из разных серверов — подтверждаем company-wide агрегацию
    assert {e["source"] for e in res["events"]} == {srv1.name, srv2.name}
    # топ отсортирован по убыванию длительности
    durs = [e["duration"] for e in res["events"]]
    assert durs == sorted(durs, reverse=True)


def test_props_and_multiline_context(tmp_path):
    mod = _load()
    srv1, _ = _make_tj(tmp_path)
    res = mod.parse_techlog(str(srv1), events=["DBPOSTGRS"])
    e = res["events"][0]
    assert e["props"].get("Rows") == "42"
    assert "SELECT" in e["props"].get("Sql", "")
    # timestamp: час из имени файла (26062810 → 28.06.2026 10ч) + минута:секунда из строки (10:00)
    assert e["timestamp"].startswith("2026-06-28 10:10:00")


def test_empty_and_missing_paths(tmp_path):
    mod = _load()
    res = mod.parse_techlog([str(tmp_path / "нет-такого")])
    assert res["summary"]["files"] == 0
    assert res["events"] == []
    assert "warnings" in res
