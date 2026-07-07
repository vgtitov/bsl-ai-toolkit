"""Тесты реестра карт структуры хранения (метаданные ↔ таблицы СУБД) по базам:
импорт выгрузки → карта базы + index.json (какие базы покрыты, свежесть), точечный
lookup с нормализацией суффиксов (_VT…/X1/_Chng…), аннотация SQL-текста.
Запуск:  python -m pytest -q tests/test_storage_map.py
"""
import importlib
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.reload(importlib.import_module("storage_map"))


ROWS = [
    # (полное имя, таблица СУБД, метаданные, назначение, размер КБ)
    ["РегистрСведений.ОчередьСообщений", "_InfoRg32784", "РегистрСведений.ОчередьСообщений", "Основная", "1024"],
    ["Справочник.Сделки", "_Reference379", "Справочник.Сделки", "Основная", ""],
    ["Справочник.Виды.ТЧ", "_Reference283_VT21706", "Справочник.Виды", "ТабличнаяЧасть", ""],
    ["", "_UsersWorkHistory", "", "ИсторияРаботыПользователей", ""],
]


def test_import_rows_and_status(tmp_path):
    m = _load()
    maps_dir = tmp_path / "maps"
    rec = m.import_rows(ROWS, base="ERP_TEST", maps_dir=str(maps_dir), source="unit")
    assert rec["tables"] == 4
    # карта на диске + реестр
    idx = json.loads((maps_dir / "index.json").read_text(encoding="utf-8"))
    assert "ERP_TEST" in idx["maps"]
    assert idx["maps"]["ERP_TEST"]["tables"] == 4
    st = m.status(maps_dir=str(maps_dir), expected=["ERP_TEST", "ERP_BY"])
    have = {b["base"]: b for b in st["bases"]}
    assert have["ERP_TEST"]["present"] is True
    assert have["ERP_BY"]["present"] is False  # учёт «для каких баз карты НЕТ»


def test_reimport_updates_not_duplicates(tmp_path):
    m = _load()
    maps_dir = tmp_path / "maps"
    m.import_rows(ROWS, base="ERP_TEST", maps_dir=str(maps_dir), source="v1")
    m.import_rows(ROWS[:2], base="ERP_TEST", maps_dir=str(maps_dir), source="v2")  # обновление карты
    idx = json.loads((maps_dir / "index.json").read_text(encoding="utf-8"))
    assert idx["maps"]["ERP_TEST"]["tables"] == 2
    assert idx["maps"]["ERP_TEST"]["source"] == "v2"


def test_lookup_exact_and_suffix_normalization(tmp_path):
    m = _load()
    maps_dir = tmp_path / "maps"
    m.import_rows(ROWS, base="ERP_TEST", maps_dir=str(maps_dir))
    maps = m.load_maps(str(maps_dir))
    # точное имя
    r = m.lookup("_InfoRg32784", maps)
    assert r and r[0]["metadata"] == "РегистрСведений.ОчередьСообщений"
    # суффикс X1 (индексная копия/алиас в запросе) отбрасывается
    r = m.lookup("_Reference283_VT21706X1", maps)
    assert r and r[0]["purpose"] == "ТабличнаяЧасть"
    # ТЧ сводится к родителю, если самой ТЧ в карте нет
    r = m.lookup("_Reference379_VT99999X1", maps)
    assert r and r[0]["metadata"] == "Справочник.Сделки"
    # регистр букв не важен
    assert m.lookup("_INFORG32784", maps)
    # неизвестное — пусто, не падает
    assert m.lookup("_InfoRg99999", maps) == []


def test_annotate_sql_text(tmp_path):
    m = _load()
    maps_dir = tmp_path / "maps"
    m.import_rows(ROWS, base="ERP_TEST", maps_dir=str(maps_dir))
    maps = m.load_maps(str(maps_dir))
    sql = ("SELECT T1._Fld32792 FROM _InfoRg32784 T1; "
           "SELECT x FROM _Reference283_VT21706X1 JOIN _Reference379 ...")
    notes = m.tables_in_text(sql, maps)
    named = {n["table"]: n["metadata"] for n in notes}
    assert named["_InfoRg32784"] == "РегистрСведений.ОчередьСообщений"
    assert named["_Reference379"] == "Справочник.Сделки"
    assert any(n["table"].startswith("_Reference283") for n in notes)
    # временные таблицы и поля не считаются таблицами
    assert not any(n["table"].startswith(("_Fld", "pg_temp", "tt")) for n in notes)


def test_parse_export_csv(tmp_path):
    """Импорт из CSV/«;»-текста (как из сниппета ПолучитьСтруктуруХраненияБазыДанных)."""
    m = _load()
    f = tmp_path / "exp.csv"
    f.write_text("Имя таблицы;Имя таблицы хранения;Метаданные;Назначение;Данные (КБ)\n"
                 "Справочник.Сделки;_Reference379;Справочник.Сделки;Основная;12\n",
                 encoding="utf-8")
    rows = m.read_export(str(f))
    assert rows == [["Справочник.Сделки", "_Reference379", "Справочник.Сделки", "Основная", "12"]]


def test_default_maps_dir_env_and_bundled(tmp_path, monkeypatch):
    """Порядок: env ONEC_STORAGE_MAPS_DIR → <репо>/data/storage_maps (карты в локализации) → ''."""
    m = _load()
    monkeypatch.delenv("ONEC_STORAGE_MAPS_DIR", raising=False)
    assert m.default_maps_dir(repo_root=str(tmp_path)) == ""  # каталога нет
    bundled = tmp_path / "data" / "storage_maps"
    bundled.mkdir(parents=True)
    assert m.default_maps_dir(repo_root=str(tmp_path)) == str(bundled)
    monkeypatch.setenv("ONEC_STORAGE_MAPS_DIR", str(tmp_path))
    assert m.default_maps_dir(repo_root=str(tmp_path)) == str(tmp_path)  # env главнее


def test_staleness_warning(tmp_path):
    m = _load()
    maps_dir = tmp_path / "maps"
    m.import_rows(ROWS, base="ERP_TEST", maps_dir=str(maps_dir))
    # состарить дату импорта
    idx_p = maps_dir / "index.json"
    idx = json.loads(idx_p.read_text(encoding="utf-8"))
    idx["maps"]["ERP_TEST"]["imported_at"] = "2020-01-01 00:00"
    idx_p.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    st = m.status(maps_dir=str(maps_dir))
    b = st["bases"][0]
    assert b["age_days"] > 365 and b["stale"] is True
