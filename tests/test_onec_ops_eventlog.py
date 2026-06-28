"""Тесты onec_ops: анализ журнала регистрации 1С (ЖР) — кейс «доступ только к ЖР».
Источники: Excel-выгрузка отчёта ЖР (.xlsx) ИЛИ файл журнала нового формата (.lgd, SQLite).
Запуск:  uv run --with mcp --with openpyxl --with pytest pytest -q tests/test_onec_ops_eventlog.py
"""
import importlib
import sqlite3
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _load():
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("onec_ops_mcp"))


def _make_xlsx(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Дата, время", "Пользователь", "Компьютер", "Приложение", "Событие",
               "Метаданные", "Данные", "Комментарий", "Важность"])
    ws.append(["2026-06-28 10:00:01", "Иванов", "PC1", "Толстый клиент",
               "Сеанс.Аутентификация", "", "", "Вход в систему", "Информация"])
    ws.append(["2026-06-28 10:05:00", "Петров", "PC2", "Тонкий клиент",
               "Документ.РеализацияТоваровУслуг.Проведение", "Документ.Реализация", "...", "Ошибка проведения", "Ошибка"])
    ws.append(["2026-06-28 10:06:00", "Петров", "PC2", "Тонкий клиент",
               "Доступ.Отказ", "", "", "Недостаточно прав", "Предупреждение"])
    p = tmp_path / "ЖР_выгрузка.xlsx"
    wb.save(p)
    return p


def _make_lgd(tmp_path):
    p = tmp_path / "1Cv8.lgd"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE EventLog (rowId INTEGER, date TEXT, severity TEXT, userName TEXT, eventName TEXT, comment TEXT)")
    con.executemany("INSERT INTO EventLog VALUES (?,?,?,?,?,?)", [
        (1, "20260628100001", "Информация", "Иванов", "Сеанс.Аутентификация", "Вход"),
        (2, "20260628100500", "Ошибка", "Петров", "Документ.Проведение", "Ошибка проведения"),
        (3, "20260628100600", "Предупреждение", "Петров", "Доступ.Отказ", "Нет прав"),
    ])
    con.commit()
    con.close()
    return p


def test_eventlog_from_excel(tmp_path):
    m = _load()
    xlsx = _make_xlsx(tmp_path)
    res = m.event_log_parse(str(xlsx))
    assert res["summary"]["rows"] == 3
    assert res["summary"]["by_level"]["Ошибка"] == 1
    assert res["summary"]["by_level"]["Предупреждение"] == 1
    assert res["summary"]["by_user"]["Петров"] == 2
    e = res["entries"][0]
    assert {"level", "user", "event"} <= set(e)


def test_eventlog_excel_filter_errors(tmp_path):
    m = _load()
    xlsx = _make_xlsx(tmp_path)
    res = m.event_log_parse(str(xlsx), level="Ошибка")
    assert len(res["entries"]) == 1
    assert "Проведение" in res["entries"][0]["event"]
    assert res["entries"][0]["user"] == "Петров"


def test_eventlog_from_lgd_sqlite(tmp_path):
    m = _load()
    lgd = _make_lgd(tmp_path)
    res = m.event_log_parse(str(lgd), table="EventLog")
    assert res["summary"]["rows"] == 3
    assert res["summary"]["by_level"]["Ошибка"] == 1
    assert res["summary"]["by_user"]["Петров"] == 2


def test_eventlog_unknown_source(tmp_path):
    m = _load()
    res = m.event_log_parse(str(tmp_path / "нет.xlsx"))
    assert res["summary"]["rows"] == 0
    assert "warnings" in res and res["warnings"]
