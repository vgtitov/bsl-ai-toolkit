# DISCIPLINE_ALLOW_TEST_EDIT: новый тест по TDD (детектор ещё не написан)
"""Тесты детектора анти-паттернов производительности BSL: запрос/подъём объекта/
обращение к БД через точку — ВНУТРИ цикла (баг печатной формы: Запрос…Выполнить()
в Пока/Для). Запуск:  python -m pytest -q tests/test_bsl_guard.py
"""
import importlib
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _mod():
    sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.reload(importlib.import_module("bsl_guard"))


def _codes(text):
    return sorted(f.code for f in _mod().scan_text(text, "X.bsl"))


def test_query_exec_in_loop_flagged():
    text = (
        "Процедура Печать()\n"
        "    Пока Выборка.Следующий() Цикл\n"
        "        Запрос = Новый Запрос;\n"
        "        Результат = Запрос.Выполнить();\n"
        "    КонецЦикла;\n"
        "КонецПроцедуры\n"
    )
    codes = _codes(text)
    assert "new-query-in-loop" in codes
    assert "query-exec-in-loop" in codes
    # строки должны указывать на тело цикла (3 и 4), не на заголовок
    lines = {f.line for f in _mod().scan_text(text, "X.bsl")}
    assert 3 in lines and 4 in lines


def test_for_each_loop_flagged():
    text = (
        "Для Каждого Стр Из Таблица Цикл\n"
        "    Об = Стр.Ссылка.ПолучитьОбъект();\n"
        "КонецЦикла;\n"
    )
    codes = _codes(text)
    assert "get-object-in-loop" in codes


def test_ref_dot_in_loop_flagged():
    text = (
        "Для Каждого Стр Из Таблица Цикл\n"
        "    Артикул = Стр.Ссылка.Номенклатура.Артикул;\n"
        "КонецЦикла;\n"
    )
    assert "ref-dot-in-loop" in _codes(text)


def test_no_flag_outside_loop():
    text = (
        "Запрос = Новый Запрос;\n"
        "Результат = Запрос.Выполнить();\n"
        "Об = Ссылка.ПолучитьОбъект();\n"
    )
    assert _codes(text) == []


def test_query_in_string_not_flagged():
    # ключевые слова внутри строкового литерала не считаются кодом
    text = (
        'Текст = "Пока по циклу мы выполнить Новый Запрос не будем";\n'
        'Сообщить(Текст);\n'
    )
    assert _codes(text) == []


def test_query_in_comment_not_flagged():
    text = (
        "// Пока Цикл: тут был Новый Запрос и Запрос.Выполнить()\n"
        "Значение = 1;\n"
    )
    assert _codes(text) == []


def test_konec_cikla_does_not_open_loop():
    # 'КонецЦикла' не должен быть распознан как открытие цикла (\bЦикл\b)
    text = (
        "Для Каждого Стр Из Т Цикл\n"
        "    А = 1;\n"
        "КонецЦикла;\n"
        "Результат = Запрос.Выполнить();\n"  # уже вне цикла
    )
    assert _codes(text) == []


def test_nested_loops_depth():
    text = (
        "Пока А Цикл\n"
        "    Пока Б Цикл\n"
        "        Р = Запрос.Выполнить();\n"
        "    КонецЦикла;\n"
        "КонецЦикла;\n"
    )
    assert "query-exec-in-loop" in _codes(text)


def test_scan_paths_and_exit(tmp_path):
    mod = _mod()
    good = tmp_path / "good.bsl"
    good.write_text("А = 1;\n", encoding="utf-8")
    bad = tmp_path / "bad.bsl"
    bad.write_text(
        "Для Каждого С Из Т Цикл\n    Р = Запрос.Выполнить();\nКонецЦикла;\n",
        encoding="utf-8")
    findings = mod.scan_paths([tmp_path])
    files = {f.file for f in findings}
    assert str(bad) in files
    assert str(good) not in files


# --- межпроцедурный анализ (боевой пропуск 2026-07-20: Ав_НомерСертификата) ----
# Реальный кейс: запрос сидит ВНУТРИ функции модуля, а из цикла зовут функцию.
# Старый детектор смотрел только тело цикла и пропускал это.

def test_call_of_query_function_in_loop_flagged():
    text = (
        "Функция НомерСертификата(Строка)\n"
        "    Запрос = Новый Запрос;\n"
        "    Выборка = Запрос.Выполнить().Выбрать();\n"
        "    Возврат \"\";\n"
        "КонецФункции\n"
        "\n"
        "Процедура Печать()\n"
        "    Пока Выборка.Следующий() Цикл\n"
        "        Область.Параметры.Сертификат = НомерСертификата(Выборка);\n"
        "    КонецЦикла;\n"
        "КонецПроцедуры\n"
    )
    m = _mod()
    findings = m.scan_text(text, "X.bsl")
    codes = [f.code for f in findings]
    assert "db-call-in-loop" in codes
    f = next(f for f in findings if f.code == "db-call-in-loop")
    assert f.line == 9  # строка вызова в цикле
    assert "НомерСертификата" in f.message


def test_transitive_call_chain_flagged():
    text = (
        "Функция Внутренняя()\n"
        "    Запрос = Новый Запрос;\n"
        "    Возврат Запрос.Выполнить();\n"
        "КонецФункции\n"
        "Функция Обёртка()\n"
        "    Возврат Внутренняя();\n"
        "КонецФункции\n"
        "Процедура Печать()\n"
        "    Для Каждого Стр Из Таблица Цикл\n"
        "        Х = Обёртка();\n"
        "    КонецЦикла;\n"
        "КонецПроцедуры\n"
    )
    assert "db-call-in-loop" in _codes(text)


def test_query_function_called_outside_loop_not_flagged():
    text = (
        "Функция НомерСертификата()\n"
        "    Запрос = Новый Запрос;\n"
        "    Возврат Запрос.Выполнить();\n"
        "КонецФункции\n"
        "Процедура Печать()\n"
        "    Номер = НомерСертификата();\n"
        "    Пока Выборка.Следующий() Цикл\n"
        "        Область.Параметры.Сертификат = Номер;\n"
        "    КонецЦикла;\n"
        "КонецПроцедуры\n"
    )
    assert "db-call-in-loop" not in _codes(text)


def test_clean_function_call_in_loop_not_flagged():
    text = (
        "Функция Формат(З)\n"
        "    Возврат СокрЛП(З);\n"
        "КонецФункции\n"
        "Процедура Печать()\n"
        "    Пока Выборка.Следующий() Цикл\n"
        "        Х = Формат(Выборка.Число);\n"
        "    КонецЦикла;\n"
        "КонецПроцедуры\n"
    )
    assert _codes(text) == []
