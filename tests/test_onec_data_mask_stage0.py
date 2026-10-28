"""Маска onec-data: ссылки на людей, алиасы, номера документов, зарплата, лишние маски. Данные вымышленные."""
import importlib
import json
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"

FIO = "Петров Пётр Петрович"
GUID = "3f2b8c1e-7a4d-4e5b-9c6a-1d2e3f4a5b6c"


@pytest.fixture()
def mod(monkeypatch):
    monkeypatch.delenv("ONEC_DATA_MASK_OFF", raising=False)
    monkeypatch.delenv("ONEC_DATA_MASK_FIELDS", raising=False)
    sys.path.insert(0, str(MCP_DIR))
    return importlib.reload(importlib.import_module("onec_data_mcp"))


def dump(x):
    return json.dumps(x, ensure_ascii=False)


def test_person_reference_presentation_and_guid_masked(mod):
    row = {"Сотрудник": {"_ref": "Справочник.Сотрудники", "uuid": GUID, "presentation": FIO}, "Должность": "Кладовщик"}
    out = dump(mod.mask_rows([row]))
    assert FIO not in out and GUID not in out
    assert "Кладовщик" in out


def test_reference_to_person_by_name_hint_even_without_fio_shape(mod):
    row = {"Физлицо": {"uuid": GUID, "presentation": "Иванов"}}
    assert GUID not in dump(mod.mask_rows([row])) and "Иванов" not in dump(mod.mask_rows([row]))


def test_non_person_reference_stays_open(mod):
    row = {"Номенклатура": {"_ref": "Справочник.Номенклатура", "uuid": GUID, "presentation": "Стол офисный"}}
    out = dump(mod.mask_rows([row]))
    assert "Стол офисный" in out and GUID in out


def test_alias_with_fio_value_masked(mod):
    out = dump(mod.mask_rows([{"Работник": "Сидоров Сергей Семёнович", "Кто": "Сидоров С.С.", "Ещё": "С.С. Сидоров"}]))
    assert "Сидоров" not in out


def test_fio_inside_text_masked(mod):
    out = dump(mod.mask_rows([{"Комментарий": "принял " + FIO + " вчера"}]))
    assert FIO not in out and "принял" in out


def test_salary_fields_masked(mod):
    row = {"Оклад": 1500, "ЗарплатаБрутто": 2000, "Заработная": 3, "Код": 7}
    out = mod.mask_rows([row])[0]
    assert out["Оклад"] == "***" and out["ЗарплатаБрутто"] == "***" and out["Заработная"] == "***"
    assert out["Код"] == 7


def test_passport_number_next_to_series_masked(mod):
    out = mod.mask_rows([{"ПаспортСерия": "MP", "Номер": "1234567", "ИмяФайла": "a.txt"}])[0]
    assert out["Номер"] == "***"
    assert out["ИмяФайла"] == "a.txt"


def test_document_number_without_passport_sibling_stays(mod):
    assert mod.mask_rows([{"Номер": "0000123", "Дата": "2026-10-01"}])[0]["Номер"] == "0000123"


def test_passport_values_masked_by_shape(mod):
    out = dump(mod.mask_rows([{"A": "4512 345678", "B": "45 12 345678", "C": "MP1234567"}]))
    assert "345678" not in out and "1234567" not in out


@pytest.mark.parametrize("key", ["ИмяФайла", "ИмяПоля", "ИмяПараметра", "ИмяОбъекта"])
def test_technical_name_fields_open(mod, key):
    assert mod.mask_rows([{key: "x.txt"}])[0][key] == "x.txt"


def test_plain_name_field_still_masked(mod):
    assert mod.mask_rows([{"Имя": "Пётр", "Фамилия": "Петров"}])[0] == {"Имя": "***", "Фамилия": "***"}


def test_error_message_is_filtered(mod):
    res = mod._err(400, {"error": "нет записи для " + FIO + " инн 770708389312"}, "OData X")
    assert FIO not in res["error"] and "770708389312" not in res["error"]


def test_mask_off_keeps_everything(mod, monkeypatch):
    monkeypatch.setenv("ONEC_DATA_MASK_OFF", "1")
    row = {"Сотрудник": {"uuid": GUID, "presentation": FIO}}
    assert mod.mask_rows([row]) == [row]


def test_patronymic_ich_forms_masked(mod):
    out = dump(mod.mask_rows([{"Кто": "Иванов Иван Ильич", "Ещё": "Кузнецов Пётр Кузьмич", "Тот": "Орлов Олег Никитич"}]))
    assert "Иванов" not in out and "Кузнецов" not in out and "Орлов" not in out


def test_row_with_presentation_and_other_fields_masks_by_field(mod):
    out = mod.mask_rows([{"presentation": FIO, "Количество": 10}])[0]
    assert out["Количество"] == 10 and FIO not in dump(out)


def test_error_object_is_filtered(mod):
    res = mod._err(500, {"error": {"message": FIO}}, "ai_debug/x")
    assert FIO not in res["error"]
