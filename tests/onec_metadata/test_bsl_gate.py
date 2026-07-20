# DISCIPLINE_ALLOW_TEST_EDIT: новый тест по TDD (гейт ещё не написан)
"""Гейт производительности BSL в конвейере применения (apply.bsl_gate).

Боевой урок 2026-07-20: инструмент ЗАГРУЗИЛ в базу модуль с запросом в цикле —
детектор существовал, но в конвейере применения не стоял. Гейт: перед
upload_tree сканировать дерево bsl_guard'ом; старые (легаси) находки фиксируются
в baseline и пропускаются, любая НОВАЯ находка — ApplyError.
"""
import json

import pytest

from onec_metadata.apply.bsl_gate import (
    BASELINE_NAME, enforce, scan_tree, write_baseline,
)
from onec_metadata.apply.dumpload import ApplyError

BAD_BSL = (
    "Процедура Печать()\n"
    "    Пока Выборка.Следующий() Цикл\n"
    "        Запрос = Новый Запрос;\n"
    "        Результат = Запрос.Выполнить();\n"
    "    КонецЦикла;\n"
    "КонецПроцедуры\n"
)
CLEAN_BSL = (
    "Процедура Печать()\n"
    "    Запрос = Новый Запрос;\n"
    "    Результат = Запрос.Выполнить();\n"
    "КонецПроцедуры\n"
)


def _tree(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def test_scan_tree_finds_findings(tmp_path):
    tree = _tree(tmp_path, {"A/Ext/Module.bsl": BAD_BSL})
    found = scan_tree(tree)
    assert found and all(f["file"].startswith("A/") for f in found)


def test_enforce_raises_on_findings(tmp_path):
    tree = _tree(tmp_path, {"A/Ext/Module.bsl": BAD_BSL})
    with pytest.raises(ApplyError) as e:
        enforce(tree)
    assert "запрос" in str(e.value).lower() or "цикле" in str(e.value)


def test_enforce_clean_tree_passes(tmp_path):
    tree = _tree(tmp_path, {"A/Ext/Module.bsl": CLEAN_BSL})
    enforce(tree)  # не должно бросить


def test_baseline_allows_legacy_but_blocks_new(tmp_path):
    tree = _tree(tmp_path, {"Legacy/Ext/Module.bsl": BAD_BSL})
    write_baseline(tree)
    assert (tree / BASELINE_NAME).exists()
    enforce(tree)  # легаси зафиксировано — проходит

    # новая находка в ДРУГОМ файле — блокирует
    _tree(tmp_path, {"New/Ext/Module.bsl": BAD_BSL})
    with pytest.raises(ApplyError) as e:
        enforce(tree)
    assert "New/Ext/Module.bsl" in str(e.value)
    assert "Legacy" not in str(e.value)


def test_baseline_blocks_growth_in_same_file(tmp_path):
    tree = _tree(tmp_path, {"A/Ext/Module.bsl": BAD_BSL})
    write_baseline(tree)
    enforce(tree)
    # добавили ЕЩЁ один запрос-в-цикле в тот же файл
    (tree / "A/Ext/Module.bsl").write_text(BAD_BSL + BAD_BSL, encoding="utf-8")
    with pytest.raises(ApplyError):
        enforce(tree)


def test_env_off_disables(tmp_path, monkeypatch):
    tree = _tree(tmp_path, {"A/Ext/Module.bsl": BAD_BSL})
    monkeypatch.setenv("ONEC_BSL_GUARD", "off")
    enforce(tree)  # выключено явно — проходит


def test_baseline_file_is_valid_json(tmp_path):
    tree = _tree(tmp_path, {"A/Ext/Module.bsl": BAD_BSL})
    write_baseline(tree)
    data = json.loads((tree / BASELINE_NAME).read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data
