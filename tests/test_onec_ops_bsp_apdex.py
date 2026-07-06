"""Тесты парсера штатного экспорта БСП «Оценка производительности» (Apdex).
Формат: XML ns www.v8.1c.ru/ssl/performace-assessment/apdexExport/1.0.0.4,
KeyOperation(name, nameFull, priority, targetValue, uid) → measurement(value, weight, tUTC, ...).
targetValue ключевой операции = порог T для Apdex.
Запуск:  uv run --with mcp --with pytest pytest -q tests/test_onec_ops_bsp_apdex.py
"""
import importlib
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<Performance xmlns="www.v8.1c.ru/ssl/performace-assessment/apdexExport/1.0.0.4"
    xmlns:prf="www.v8.1c.ru/ssl/performace-assessment/apdexExport/1.0.0.4"
    xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    version="1.0.0.4" period="2026-07-06T10:00:00">
  <KeyOperation>
    <name>ПроведениеЗаказаКлиента</name>
    <nameFull>Проведение документа Заказ клиента</nameFull>
    <long>false</long>
    <comment></comment>
    <priority>1</priority>
    <targetValue>2</targetValue>
    <uid>aaaa-bbbb</uid>
    <measurement><value>1.5</value><weight>1</weight><tUTC>63879000000</tUTC>
      <userName>Иванов</userName><tSaveUTC>63879000001</tSaveUTC><sessionNumber>10</sessionNumber>
      <runningError>false</runningError></measurement>
    <measurement><value>3.0</value><weight>1</weight><tUTC>63879000060</tUTC>
      <userName>Петров</userName><tSaveUTC>63879000061</tSaveUTC><sessionNumber>11</sessionNumber>
      <runningError>false</runningError></measurement>
    <measurement><value>10.0</value><weight>1</weight><tUTC>63879000120</tUTC>
      <userName>Сидоров</userName><tSaveUTC>63879000121</tSaveUTC><sessionNumber>12</sessionNumber>
      <runningError>false</runningError></measurement>
  </KeyOperation>
  <KeyOperation>
    <name>ОткрытиеФормыЗаказа</name>
    <nameFull>Открытие формы Заказ клиента</nameFull>
    <long>false</long>
    <comment></comment>
    <priority>2</priority>
    <targetValue>1</targetValue>
    <uid>cccc-dddd</uid>
    <measurement><value>0.5</value><weight>1</weight><tUTC>63879000000</tUTC>
      <userName>Иванов</userName><tSaveUTC>63879000001</tSaveUTC><sessionNumber>10</sessionNumber>
      <runningError>false</runningError></measurement>
  </KeyOperation>
</Performance>
"""


def _load():
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("onec_ops_mcp"))


def _write_sample(tmp_path, name="apdex_00001.xml", body=SAMPLE):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_single_file_records_and_thresholds(tmp_path):
    m = _load()
    res = m.bsp_apdex_parse(str(_write_sample(tmp_path)))
    assert res["files"] == 1
    ops = {o["operation"]: o for o in res["operations"]}
    # T берётся из targetValue операции: 2с → 1.5 satisfied, 3.0 tolerating (≤4T=8), 10 frustrated
    prov = ops["ПроведениеЗаказаКлиента"]
    assert prov["satisfied"] == 1 and prov["tolerating"] == 1 and prov["frustrated"] == 1
    assert abs(prov["apdex"] - 0.5) < 1e-9
    assert prov["threshold_t"] == 2.0
    assert prov["name_full"] == "Проведение документа Заказ клиента"
    # операции отсортированы: худший apdex первым
    assert res["operations"][0]["operation"] == "ПроведениеЗаказаКлиента"
    assert ops["ОткрытиеФормыЗаказа"]["apdex"] == 1.0


def test_parse_directory_aggregates_files(tmp_path):
    m = _load()
    _write_sample(tmp_path, "apdex_00001.xml")
    _write_sample(tmp_path, "apdex_00002.xml")
    res = m.bsp_apdex_parse(str(tmp_path))
    assert res["files"] == 2
    prov = next(o for o in res["operations"] if o["operation"] == "ПроведениеЗаказаКлиента")
    assert prov["total"] == 6  # замеры обоих файлов сложились


def test_parse_bad_file_warning_not_crash(tmp_path):
    m = _load()
    _write_sample(tmp_path, "good.xml")
    (tmp_path / "broken.xml").write_text("<не xml", encoding="utf-8")
    res = m.bsp_apdex_parse(str(tmp_path))
    assert res["files"] == 1 and res["warnings"]


def test_zabbix_sender_lines(tmp_path):
    """Строки для zabbix_sender: apdex по операции + total, host из аргумента, ключ безопасный."""
    m = _load()
    res = m.bsp_apdex_parse(str(_write_sample(tmp_path)))
    lines = m.apdex_sender_lines(res, zbx_host="vc-1c-app-01")
    joined = "\n".join(lines)
    assert 'vc-1c-app-01 1c.apdex[ПроведениеЗаказаКлиента] 0.5000' in joined
    assert 'vc-1c-app-01 1c.apdex[ОткрытиеФормыЗаказа] 1.0000' in joined
    assert any("1c.apdex.worst" in ln for ln in lines)
