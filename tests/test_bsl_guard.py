# DISCIPLINE_ALLOW_TEST_EDIT: новый тест по TDD (детектор ещё не написан)
"""Тесты детектора анти-паттернов производительности BSL: запрос/подъём объекта/
обращение к БД через точку — ВНУТРИ цикла (баг печатной формы: Запрос…Выполнить()
в Пока/Для). Запуск:  python -m pytest -q tests/test_bsl_guard.py
"""
import importlib
import os
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


# --- осознанное исключение: отметка на строке с обязательной причиной ----------
# Боевой кейс: тест выполняет шаги сценария по очереди, каждый в своей
# откатываемой транзакции — пакетом это не переделать.

_STEP_MODULE = (
    "Функция ВыполнитьШаг(Шаг)\n"
    "    Запрос = Новый Запрос;\n"
    "    Возврат Запрос.Выполнить();\n"
    "КонецФункции\n"
    "Процедура Сценарий()\n"
    "    Для Каждого Шаг Из Шаги Цикл\n"
    "        {call}\n"
    "    КонецЦикла;\n"
    "КонецПроцедуры\n"
)


def test_ignore_with_reason_on_same_line():
    text = _STEP_MODULE.format(
        call="Р = ВыполнитьШаг(Шаг); // bsl-guard: ignore db-call-in-loop - "
             "шаги последовательны, пакетом нельзя")
    assert _codes(text) == []


def test_ignore_on_previous_line():
    text = _STEP_MODULE.format(
        call="// bsl-guard: ignore db-call-in-loop - шаги последовательны\n"
             "        Р = ВыполнитьШаг(Шаг);")
    assert _codes(text) == []


def test_ignore_without_reason_does_not_suppress():
    text = _STEP_MODULE.format(
        call="Р = ВыполнитьШаг(Шаг); // bsl-guard: ignore db-call-in-loop")
    findings = _mod().scan_text(text, "X.bsl")
    codes = sorted(f.code for f in findings)
    assert codes == ["bad-ignore", "db-call-in-loop"]
    bad = next(f for f in findings if f.code == "bad-ignore")
    assert bad.line == 7 and "нет причины" in bad.message


def test_ignore_unknown_code_does_not_suppress():
    text = _STEP_MODULE.format(
        call="Р = ВыполнитьШаг(Шаг); // bsl-guard: ignore db-in-loop - опечатка")
    findings = _mod().scan_text(text, "X.bsl")
    assert sorted(f.code for f in findings) == ["bad-ignore", "db-call-in-loop"]
    assert "db-in-loop" in next(f for f in findings if f.code == "bad-ignore").message


def test_ignore_covers_only_named_code():
    # отметка про db-call-in-loop не глушит запрос в цикле на той же строке
    text = (
        "Для Каждого С Из Т Цикл\n"
        "    Р = Запрос.Выполнить(); // bsl-guard: ignore db-call-in-loop - не то правило\n"
        "КонецЦикла;\n"
    )
    assert _codes(text) == ["query-exec-in-loop"]


def test_ignore_several_codes():
    text = (
        "Для Каждого С Из Т Цикл\n"
        "    // bsl-guard: ignore new-query-in-loop, query-exec-in-loop - разовый замер\n"
        "    Р = Новый Запрос(Т).Выполнить();\n"
        "КонецЦикла;\n"
    )
    assert _codes(text) == []


def test_ignore_inside_string_is_not_a_mark():
    text = _STEP_MODULE.format(
        call='Р = ВыполнитьШаг("// bsl-guard: ignore db-call-in-loop - нет");')
    assert _codes(text) == ["db-call-in-loop"]


# DISCIPLINE_ALLOW_TEST_EDIT: новые правила — грабли, которые не ловят BSL LS и CheckConfig

def _sev(text, name="X.bsl"):
    return sorted((f.code, f.severity) for f in _mod().scan_text(text, name))


def test_dash_in_comment_is_warning_not_blocking(tmp_path):
    text = "// Расчёт — по строкам\n// интервал 1–2\nА = \"текст — в строке\";\n// обычный - дефис\n"
    assert _sev(text) == [("dash-in-comment", "warning")] * 2
    f = tmp_path / "m.bsl"
    f.write_text(text, encoding="utf-8")
    assert _mod().main([str(f), "--quiet"]) == 0          # предупреждение не валит хук/коммит


def test_query_alias_keyword_flagged():
    text = (
        'Запрос.Текст = "ВЫБРАТЬ\n'
        '|	Т.Ссылка КАК Ссылка,\n'
        '|	Т.Код КАК Где\n'
        '|ИЗ\n'
        '|	Справочник.Товары КАК И";\n'
    )
    findings = _mod().scan_text(text, "X.bsl")
    assert [(f.code, f.line) for f in findings] == [
        ("query-alias-keyword", 3), ("query-alias-keyword", 5)]
    assert "«Где»" in findings[0].message


def test_query_alias_allowed_words_not_flagged():
    # ВЫБРАТЬ/КОНЕЦ/ИТОГИ/ССЫЛКА платформа псевдонимом принимает; ВЫРАЗИТЬ(… КАК Число) — тип
    text = (
        'Т = "ВЫБРАТЬ Т.Ссылка КАК Ссылка, Т.Сумма КАК Итоги, ВЫРАЗИТЬ(Т.К КАК ЧИСЛО(10, 2)) КАК Конец\n'
        '|ИЗ Справочник.Товары КАК Товары";\n'
        'Сообщение = "Так же, как и раньше";\n'       # не текст запроса
    )
    assert _codes(text) == []


def test_form_items_by_number_or_loop_counter_flagged():
    text = (
        "Процедура П()\n"
        "    Первый = Элементы[0];\n"
        "    Для Индекс = 0 По Элементы.Количество() - 1 Цикл\n"
        "        Э = Форма.Элементы[Индекс];\n"
        "    КонецЦикла;\n"
        "КонецПроцедуры\n"
    )
    findings = _mod().scan_text(text, "X.bsl")
    assert [(f.code, f.line, f.severity) for f in findings] == [
        ("form-items-by-index", 2, "error"), ("form-items-by-index", 4, "error")]


def test_form_items_by_name_and_tabular_section_not_flagged():
    text = (
        "Процедура П(ИмяЭлемента)\n"
        "    Э = Элементы[ИмяЭлемента];\n"
        "    Для Каждого Имя Из Имена Цикл\n"
        "        Элементы[Имя].Видимость = Ложь;\n"
        "    КонецЦикла;\n"
        "    Строка = Объект.Элементы[0];\n"              # табличная часть «Элементы»
        "    Строка = Объект . Элементы[0];\n"            # ревью №3: пробелы вокруг точки
        "    Строка = Объект. Элементы[0];\n"
        "    Строка = Объект .Элементы[0];\n"
        "    Кнопка = Группа.ПодчиненныеЭлементы[0];\n"
        "КонецПроцедуры\n"
        "Процедура Массивом(Элементы)\n"                     # параметр-массив с тем же именем
        "    Для Индекс = 0 По Элементы.ВГраница() Цикл\n"
        "        Элементы[Индекс].Видимость = Ложь;\n"
        "    КонецЦикла;\n"
        "КонецПроцедуры\n"
    )
    assert _codes(text) == []


def test_corpus_false_positives_not_flagged():
    # с корпуса типовой: комментарии внутри текста запроса, имя элемента склейкой со счётчиком
    text = (
        'Т = "ВЫБРАТЬ\n'
        '|	ВЫБОР КОГДА Т.А ТОГДА 3 // Оставить, как есть\n'
        '|	КОНЕЦ КАК Статус\n'
        '	// где выпуск проходит как по фиксированной оценке\n'
        '|ИЗ Т КАК Т";\n'
        "Процедура П()\n"
        "    Для Сч = 1 По 3 Цикл\n"
        '        Элементы["Префикс" + Строка(Сч)].Доступность = Истина;\n'
        "        Э = Элементы[Параметр.Имя + Сч];\n"
        "    КонецЦикла;\n"
        "КонецПроцедуры\n"
    )
    assert _codes(text) == []


def test_avro_date_arithmetic_warning():
    text = "Дата = '19700101' + Число(Сообщение.Date) * 86400;\nСекунд = Дней * 86400;\n"
    assert _sev(text) == [("avro-date-arithmetic", "warning")]


# --- несуществующий метод общего модуля (боевой: прошёл BSL LS и CheckConfig) -----

def _edt_module(root, name, body, adopted=False):
    d = root / "CommonModules" / name
    d.mkdir(parents=True)
    belonging = "\n  <objectBelonging>Adopted</objectBelonging>" if adopted else ""
    (d / f"{name}.mdo").write_text(
        f'<mdclass:CommonModule uuid="1">{belonging}\n  <name>{name}</name>\n</mdclass:CommonModule>\n',
        encoding="utf-8")
    (d / "Module.bsl").write_text(body, encoding="utf-8-sig")
    return d / "Module.bsl"


def _edt_root(tmp_path, sub="src", extension=False):
    root = tmp_path / sub
    (root / "Configuration").mkdir(parents=True)
    body = ("<x><configurationExtensionPurpose>Customization</configurationExtensionPurpose></x>"
            if extension else "<x/>")
    (root / "Configuration" / "Configuration.mdo").write_text(body, encoding="utf-8")
    return root


_LIB = (
    "Функция Есть(Знач А,\n    Б = Неопределено) Экспорт\nКонецФункции\n\n"
    "Procedure EnglishOne() Export\nEndProcedure\n\n"
    "Процедура Внутренняя()\nКонецПроцедуры\n"
)
_CALLS = (
    "Процедура Вызовы() Экспорт\n"
    "    ОбщийМодуль1.Есть(1);\n"
    "    ОбщийМодуль1.englishone();\n"                 # регистр имени не важен
    "    ОбщийМодуль1.НетТакогоМетода();\n"
    "    ОбщийМодуль1.Внутренняя();\n"
    "    Справочники.Товары.НайтиПоКоду(1);\n"         # менеджер — не общий модуль
    "    Запрос.Выполнить();\n"                        # переменная
    "КонецПроцедуры\n"
)


def _unknown(findings):
    return [(f.line, f.message.split(":")[0]) for f in findings if f.code == "unknown-module-method"]


def test_unknown_common_module_method_edt(tmp_path):
    root = _edt_root(tmp_path)
    _edt_module(root, "ОбщийМодуль1", _LIB)
    caller = _edt_module(root, "ОбщийМодуль2", _CALLS)
    findings = _mod().scan_file(caller)
    assert _unknown(findings) == [(4, "ОбщийМодуль1.НетТакогоМетода"), (5, "ОбщийМодуль1.Внутренняя")]
    assert "без Экспорт" in next(f.message for f in findings if f.line == 5)
    assert all(f.severity == "error" for f in findings)


def test_unknown_common_module_method_designer_layout(tmp_path):
    root = tmp_path / "cf"
    root.mkdir()
    (root / "Configuration.xml").write_text("<x/>", encoding="utf-8")
    for name, body in (("ОбщийМодуль1", _LIB), ("ОбщийМодуль2", _CALLS)):
        (root / "CommonModules" / name / "Ext").mkdir(parents=True)
        (root / "CommonModules" / f"{name}.xml").write_text("<CommonModule/>", encoding="utf-8")
        (root / "CommonModules" / name / "Ext" / "Module.bsl").write_text(body, encoding="utf-8")
    findings = _mod().scan_file(root / "CommonModules" / "ОбщийМодуль2" / "Ext" / "Module.bsl")
    assert [line for line, _ in _unknown(findings)] == [4, 5]


def test_variable_or_tabular_section_named_like_module_not_flagged(tmp_path):
    # с корпуса типовой: локальная переменная и табличная часть объекта с именем общего модуля
    root = _edt_root(tmp_path)
    _edt_module(root, "Пользователи", "Функция Текущий() Экспорт\nКонецФункции\n")
    caller = _edt_module(root, "ОбщийМодуль2", (
        "Процедура П() Экспорт\n"
        "    Пользователи = Новый Массив;\n"
        "    Пользователи.Добавить(1);\n"
        "КонецПроцедуры\n"))
    assert _unknown(_mod().scan_file(caller)) == []
    obj = root / "Catalogs" / "Группы"
    obj.mkdir(parents=True)
    (obj / "Группы.mdo").write_text(
        f"<mdclass:Catalog {_MD_NS}>\n  <tabularSections>\n    <name>Пользователи</name>\n"
        "  </tabularSections>\n</mdclass:Catalog>\n", encoding="utf-8")
    (obj / "ObjectModule.bsl").write_text(
        "Процедура ПередЗаписью(Отказ)\n    Н = Пользователи.Количество();\n"
        "    Пользователи.НетТакого();\nКонецПроцедуры\n", encoding="utf-8")
    assert _unknown(_mod().scan_file(obj / "ObjectModule.bsl")) == []
    (obj / "ManagerModule.bsl").write_text(               # без затенения правило работает
        "Процедура П()\n    Н = 1;\n    Пользователи.НетТакого();\nКонецПроцедуры\n", encoding="utf-8")
    (obj / "Группы.mdo").write_text(f"<mdclass:Catalog {_MD_NS}/>", encoding="utf-8")
    assert [line for line, _ in _unknown(_mod().scan_file(obj / "ManagerModule.bsl"))] == [3]


_MD_NS = 'xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass"'
_CALL_BAD = "Процедура П()\n    Пользователи.НетМетода();\nКонецПроцедуры\n"


# DISCIPLINE_ALLOW_TEST_EDIT: ревью №2 - имя берётся только из реквизитов, не из команд/элементов
def test_command_or_item_named_like_module_does_not_shadow_edt(tmp_path):
    root = _edt_root(tmp_path)
    _edt_module(root, "Пользователи", "Функция Текущий() Экспорт\nКонецФункции\n")
    obj = root / "Catalogs" / "Группы"
    form = obj / "Forms" / "ФормаЭлемента"
    form.mkdir(parents=True)
    (obj / "Группы.mdo").write_text(
        f"<mdclass:Catalog {_MD_NS}>\n  <commands>\n    <name>Пользователи</name>\n  </commands>\n"
        "  <tabularSections>\n    <attributes>\n      <name>Пользователи</name>\n"   # реквизит ТЧ - не в модуле
        "    </attributes>\n    <name>Состав</name>\n  </tabularSections>\n</mdclass:Catalog>\n",
        encoding="utf-8")
    (form / "Form.form").write_text(
        '<form:Form xmlns:form="http://g5.1c.ru/v8/dt/form">\n'
        "  <items>\n    <name>Пользователи</name>\n  </items>\n"
        "  <formCommands>\n    <name>Пользователи</name>\n  </formCommands>\n"
        "  <attributes>\n    <name>Объект</name>\n  </attributes>\n</form:Form>\n", encoding="utf-8")
    (form / "Module.bsl").write_text(_CALL_BAD, encoding="utf-8")
    (obj / "ObjectModule.bsl").write_text(_CALL_BAD, encoding="utf-8")
    assert [ln for ln, _ in _unknown(_mod().scan_file(form / "Module.bsl"))] == [2]
    assert [ln for ln, _ in _unknown(_mod().scan_file(obj / "ObjectModule.bsl"))] == [2]
    (form / "Form.form").write_text(                       # реквизит формы - затеняет
        '<form:Form xmlns:form="http://g5.1c.ru/v8/dt/form">\n'
        "  <attributes>\n    <name>Пользователи</name>\n  </attributes>\n</form:Form>\n", encoding="utf-8")
    assert _unknown(_mod().scan_file(form / "Module.bsl")) == []


def test_command_or_item_named_like_module_does_not_shadow_designer(tmp_path):
    root = tmp_path / "cf"
    (root / "CommonModules" / "Пользователи" / "Ext").mkdir(parents=True)
    (root / "Configuration.xml").write_text("<x/>", encoding="utf-8")
    (root / "CommonModules" / "Пользователи.xml").write_text("<CommonModule/>", encoding="utf-8")
    (root / "CommonModules" / "Пользователи" / "Ext" / "Module.bsl").write_text(
        "Функция Текущий() Экспорт\nКонецФункции\n", encoding="utf-8")
    ns = 'xmlns="http://v8.1c.ru/8.3/MDClasses"'
    cat = root / "Catalogs"
    (cat / "Группы" / "Ext").mkdir(parents=True)
    (cat / "Группы" / "Forms" / "Ф" / "Ext" / "Form").mkdir(parents=True)

    def obj_xml(child):
        return (f"<MetaDataObject {ns}><Catalog><ChildObjects>{child}</ChildObjects></Catalog>"
                "</MetaDataObject>")
    (cat / "Группы.xml").write_text(obj_xml(
        "<Command><Properties><Name>Пользователи</Name></Properties></Command><Form>Пользователи</Form>"),
        encoding="utf-8")
    (cat / "Группы" / "Forms" / "Ф" / "Ext" / "Form.xml").write_text(
        '<Form xmlns="http://v8.1c.ru/8.3/xcf/logform"><Commands><Command name="Пользователи"/></Commands>'
        '<Attributes><Attribute name="Объект"/></Attributes></Form>', encoding="utf-8")
    om = cat / "Группы" / "Ext" / "ObjectModule.bsl"
    fm = cat / "Группы" / "Forms" / "Ф" / "Ext" / "Form" / "Module.bsl"
    om.write_text(_CALL_BAD, encoding="utf-8")
    fm.write_text(_CALL_BAD, encoding="utf-8")
    assert [ln for ln, _ in _unknown(_mod().scan_file(om))] == [2]
    assert [ln for ln, _ in _unknown(_mod().scan_file(fm))] == [2]
    (cat / "Группы.xml").write_text(obj_xml(
        "<TabularSection><Properties><Name>Пользователи</Name></Properties></TabularSection>"),
        encoding="utf-8")
    (cat / "Группы" / "Forms" / "Ф" / "Ext" / "Form.xml").write_text(
        '<Form xmlns="http://v8.1c.ru/8.3/xcf/logform"><Attributes><Attribute name="Пользователи"/>'
        "</Attributes></Form>", encoding="utf-8")
    assert _unknown(_mod().scan_file(om)) == []
    assert _unknown(_mod().scan_file(fm)) == []


# DISCIPLINE_ALLOW_TEST_EDIT: ревью - затенение по области видимости процедуры
def test_shadowing_is_scoped_to_procedure(tmp_path):
    root = _edt_root(tmp_path)
    _edt_module(root, "ОбщийМодуль1", _LIB)
    _edt_module(root, "ОбщийМодуль3", _LIB)
    caller = _edt_module(root, "ОбщийМодуль2", (
        "Перем ОбщийМодуль3;\n"                        # переменная модуля - глушит везде
        "Процедура А(ОбщийМодуль1)\n"
        "    ОбщийМодуль1.Что();\n"                    # параметр - не общий модуль
        "КонецПроцедуры\n"
        "Процедура Б()\n"
        "    ОбщийМодуль1.НетМетода();\n"              # здесь параметра А нет
        "    ОбщийМодуль3.Что();\n"
        "КонецПроцедуры\n"))
    assert [line for line, _ in _unknown(_mod().scan_file(caller))] == [6]


def test_module_outside_project_is_silent(tmp_path):
    f = tmp_path / "Module.bsl"
    f.write_text(_CALLS, encoding="utf-8")
    assert _unknown(_mod().scan_file(f)) == []


def test_extension_adopted_module_needs_base_root(tmp_path, monkeypatch):
    """В расширении заимствованный модуль содержит только добавленное: без исходников
    основной конфигурации правило молчит, с BSL_GUARD_EXTRA_ROOTS — проверяет по обоим."""
    base = _edt_root(tmp_path, "base/src")
    _edt_module(base, "ОбщийМодуль1", _LIB)
    ext = _edt_root(tmp_path, "ext/src", extension=True)
    _edt_module(ext, "ОбщийМодуль1",
                '&После("Есть")\nПроцедура мр_ПослеЕсть() Экспорт\nКонецПроцедуры\n\n'
                "Процедура мр_Новый() Экспорт\nКонецПроцедуры\n", adopted=True)
    caller = _edt_module(ext, "мр_Модуль", (
        "Процедура П() Экспорт\n"
        "    ОбщийМодуль1.Есть(1);\n"
        "    ОбщийМодуль1.мр_Новый();\n"
        "    ОбщийМодуль1.мр_ПослеЕсть();\n"     # перехватчик методом модуля не становится
        "    ОбщийМодуль1.Нет();\n"
        "КонецПроцедуры\n"))
    m = _mod()
    monkeypatch.delenv("BSL_GUARD_EXTRA_ROOTS", raising=False)
    assert _unknown(m.scan_file(caller)) == []
    monkeypatch.setenv("BSL_GUARD_EXTRA_ROOTS", str(tmp_path / "base"))   # каталог проекта, без src
    found = [f for f in m.scan_file(caller) if f.code == "unknown-module-method"]
    assert [f.line for f in found] == [4, 5]
    # модуль «свой» только благодаря extra-корню: корень мог быть от другой конфигурации
    assert {f.severity for f in found} == {m.WARNING}


def test_extra_only_module_stays_warning_with_two_extra_roots(tmp_path, monkeypatch):
    for sub in ("b1/src", "b2/src"):
        _edt_module(_edt_root(tmp_path, sub), "ОбщийМодуль1", _LIB)
    ext = _edt_root(tmp_path, "ext/src", extension=True)
    caller = _edt_module(ext, "мр_Модуль", "Процедура П() Экспорт\n    ОбщийМодуль1.Нет();\nКонецПроцедуры\n")
    monkeypatch.setenv("BSL_GUARD_EXTRA_ROOTS", os.pathsep.join([str(tmp_path / "b1"), str(tmp_path / "b2")]))
    found = [f for f in _mod().scan_file(caller) if f.code == "unknown-module-method"]
    assert [(f.line, f.severity) for f in found] == [(2, "warning")]


def test_extra_roots_ignored_outside_extension(tmp_path, monkeypatch):
    """Переменная задана под расширение A, а файл из основной конфигурации Y: модули A
    к Y не подмешиваются, своя ошибка Y остаётся блокирующей."""
    a = _edt_root(tmp_path, "a/src")
    _edt_module(a, "ОбщийМодуль1", "Процедура ТолькоВА() Экспорт\nКонецПроцедуры\n")
    y = _edt_root(tmp_path, "y/src")
    _edt_module(y, "ОбщийМодуль1", _LIB)
    caller = _edt_module(y, "ОбщийМодуль2", (
        "Процедура П() Экспорт\n"
        "    ОбщийМодуль1.ТолькоВА();\n"
        "КонецПроцедуры\n"))
    monkeypatch.setenv("BSL_GUARD_EXTRA_ROOTS", str(tmp_path / "a"))
    found = [f for f in _mod().scan_file(caller) if f.code == "unknown-module-method"]
    assert [(f.line, f.severity) for f in found] == [(2, "error")]


def test_export_on_next_line_and_broken_cache(tmp_path, monkeypatch):
    root = _edt_root(tmp_path)
    _edt_module(root, "ОбщийМодуль1", "Процедура Длинная(А,\n    Б)\n    Экспорт\nКонецПроцедуры\n")
    caller = _edt_module(root, "ОбщийМодуль2", (
        "Процедура П() Экспорт\n"
        "    ОбщийМодуль1.Длинная(1, 2);\n"
        "КонецПроцедуры\n"))
    m = _mod()
    monkeypatch.setattr(m.tempfile, "gettempdir", lambda: str(tmp_path))
    for junk in ("[1, 2]", '{"v": 1, "mods": {"ОбщийМодуль1": "мусор"}}', "не json"):
        for f in tmp_path.glob("bsl_guard_idx_*.json"):
            f.write_text(junk, encoding="utf-8")
        m._INDEX_CACHE.clear()
        assert _unknown(m.scan_file(caller)) == []
        assert list(tmp_path.glob("bsl_guard_idx_*.json"))


def test_form_items_shadowed_by_module_var_and_tabular_section(tmp_path):
    m = _mod()
    text = ("Перем Элементы;\n\nПроцедура П()\n    Для Сч = 0 По 3 Цикл\n"
            "        А = Элементы[Сч];\n    КонецЦикла;\nКонецПроцедуры\n")
    assert [f for f in m.scan_text(text, "X.bsl") if f.code == "form-items-by-index"] == []
    root = _edt_root(tmp_path)
    d = root / "Catalogs" / "Товары"
    d.mkdir(parents=True)
    (d / "Товары.mdo").write_text(
        "<mdclass:Catalog xmlns:mdclass=\"http://g5.1c.ru/v8/dt/metadata/mdclass\"><tabularSections><name>Элементы</name></tabularSections></mdclass:Catalog>",
        encoding="utf-8")
    f = d / "ObjectModule.bsl"
    f.write_text("Процедура П()\n    А = Элементы[0];\nКонецПроцедуры\n", encoding="utf-8-sig")
    assert [x for x in m.scan_file(f) if x.code == "form-items-by-index"] == []


def test_query_alias_keyword_inside_query_literal_ignored():
    text = ('Процедура П()\n    Запрос = Новый Запрос("ВЫБРАТЬ ""КАК и"" КАК Поле ИЗ Справочник.Т КАК Т");\n'
            "КонецПроцедуры\n")
    assert "query-alias-keyword" not in _codes(text)


def test_ignore_mark_for_new_rule(tmp_path):
    root = _edt_root(tmp_path)
    _edt_module(root, "ОбщийМодуль1", _LIB)
    caller = _edt_module(root, "ОбщийМодуль2", (
        "Процедура П() Экспорт\n"
        "    ОбщийМодуль1.Динамический(); // bsl-guard: ignore unknown-module-method - метод добавляет другое расширение\n"
        "КонецПроцедуры\n"))
    assert _mod().scan_file(caller) == []


def test_ignore_reaches_only_next_line():
    text = (
        "Для Каждого С Из Т Цикл\n"
        "    // bsl-guard: ignore query-exec-in-loop - только следующая строка\n"
        "    А = 1;\n"
        "    Р = Запрос.Выполнить();\n"
        "КонецЦикла;\n"
    )
    assert _codes(text) == ["query-exec-in-loop"]
