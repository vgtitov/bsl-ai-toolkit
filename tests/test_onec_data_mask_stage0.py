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


def test_reference_to_person_by_type_under_alias(mod):
    row = {"Автор": {"_ref": "Справочник.Сотрудники", "uuid": GUID, "presentation": "Иванов Иван"}}
    out = dump(mod.mask_rows([row]))
    assert GUID not in out and "Иванов" not in out and "Справочник.Сотрудники" in out


def test_ref_type_and_underscore_type_kept(mod):
    row = {"Сотрудник": {"type": "Справочник.Сотрудники", "_type": "СправочникСсылка", "uuid": GUID, "presentation": "Иванов"}}
    out = mod.mask_rows([row])[0]["Сотрудник"]
    assert out["type"] == "Справочник.Сотрудники" and out["_type"] == "СправочникСсылка"
    assert out["uuid"] == "***" and out["presentation"] == "***"


def test_hyphenated_surname_with_initials_masked(mod):
    out = dump(mod.mask_rows([{"A": "Иванов-Петров И.И.", "B": "И.И. Иванов-Петров"}]))
    assert "Иванов" not in out and "Петров" not in out


def test_error_object_masks_by_field_name(mod):
    res = mod._err(500, {"error": {"Фамилия": "Петров", "Оклад": 1500}}, "x")
    assert "Петров" not in res["error"] and "1500" not in res["error"]


def test_uppercase_fio_masked(mod):
    out = dump(mod.mask_rows([{"A": "ИВАНОВ ИВАН ИВАНОВИЧ", "B": "ИВАНОВ И.И.", "C": "И.И. ИВАНОВ"}]))
    assert "ИВАНОВ" not in out


def test_explicit_mask_field_overrides_technical_exception(mod, monkeypatch):
    monkeypatch.setenv("ONEC_DATA_MASK_FIELDS", "имяфайла")
    assert mod.mask_rows([{"ИмяФайла": "secret.txt"}])[0]["ИмяФайла"] == "***"


def test_person_typed_reference_with_extra_keys(mod):
    row = {"Автор": {"_ref": "Справочник.Сотрудники", "uuid": GUID, "presentation": "Иванов Иван", "Код": "001"}}
    out = mod.mask_rows([row])[0]["Автор"]
    assert out["uuid"] == "***" and out["presentation"] == "***" and out["Код"] == "001"


def test_person_key_reference_with_extra_keys(mod):
    out = mod.mask_rows([{"Сотрудник": {"uuid": GUID, "presentation": "Иванов", "Код": "001"}}])[0]["Сотрудник"]
    assert out["uuid"] == "***" and out["presentation"] == "***" and out["Код"] == "001"


def test_guid_inside_ref_string_masked(mod):
    out = mod.mask_rows([{"Автор": {"_ref": "Справочник.Сотрудники/" + GUID, "uuid": GUID, "presentation": "И", "Код": "1"}}])
    assert GUID not in dump(out)


def test_double_first_name_masked(mod):
    assert "Петрова" not in dump(mod.mask_rows([{"Кто": "Петрова Анна-Мария Ивановна"}]))


def test_person_ref_with_passport_siblings(mod):
    row = {"Сотрудник": {"uuid": GUID, "presentation": "И", "ПаспортСерия": "MP", "Номер": "1234567"}}
    assert mod.mask_rows([row])[0]["Сотрудник"]["Номер"] == "***"


def test_explicit_mask_for_type_key_applies_to_person_ref(mod, monkeypatch):
    monkeypatch.setenv("ONEC_DATA_MASK_FIELDS", "type,presentation")
    out = mod.mask_rows([{"Сотрудник": {"type": "Справочник.Сотрудники", "uuid": GUID, "presentation": "Иванов"}}])[0]["Сотрудник"]
    assert out["type"] == "***"


def test_long_error_masks_before_truncation(mod):
    res = mod._err(500, {"error": "x" * 485 + " " + FIO}, "w")
    assert "Петров" not in res["error"] and "Пётр" not in res["error"]


def test_mixed_case_fio_masked(mod):
    out = dump(mod.mask_rows([{"A": "ПЕТРОВ Пётр Петрович", "B": "Петров ПЁТР ПЕТРОВИЧ", "C": "ПЕТРОВ П.П."}]))
    assert "ПЕТРОВ" not in out and "Петров" not in out


def test_huge_error_message_is_fast(mod):
    import time
    t = time.time()
    res = mod._err(500, {"error": "x" * 1_000_000}, "w")
    assert time.time() - t < 2 and len(res["error"]) < 600 and "xxxx" not in res["error"]


def test_long_error_with_name_at_boundary_hidden(mod):
    res = mod._err(500, {"error": ("user@example.org " * 117) + FIO}, "w")
    assert "Пётр" not in res["error"]


# --- маски по умолчанию из config/depersonalize.default.json (значения вымышленные) ---

@pytest.mark.parametrize("field", [
    "УНП", "НомерСчета", "IBAN", "ЛичныйНомер", "ИдентификационныйНомер", "Адрес", "АдресДоставки",
    "Логин", "ЛогинДляПолученияВыписки", "Пароль", "ПарольПользователя", "Токен", "ТокенАвторизации",
    "Секрет", "КлючДоступа", "ApiKey", "api_key", "x-api-key", "Pwd", "Passwd", "Pass", "PinCode", "PrivateKey", "AccessKey", "Authorization", "Cookie",
])
def test_default_mask_covers_field_class_by_name(mod, field):
    assert mod.mask_rows([{field: "значение-123"}])[0][field] == "***"


@pytest.mark.parametrize("text,secret", [
    ("УНП 123456789", "123456789"),
    ("УНП покупателя 190123456", "190123456"),
    ("УНП организации-получателя: 190123456", "190123456"),
    ("ПИН: 4821", "4821"),
    ("счёт BY20OLMP31350000001000000933", "BY20"),
    ("звонить +375 29 111-22-33", "111-22"),
    ("звонить +375291112233", "291112233"),
    ("тел +998 90 123-45-67", "123-45"),
    ("тел +996 555 123 456", "123 456"),
    ("тел +374 91 123456", "123456"),
    ("тел +37491123456", "91123456"),
    ("тел +7 701 123 45 67", "123 45"),
    ("тел +77011234567", "1234567"),
    ("личный 3010190A123PB4", "3010190A123PB4"),
    ("личный 3010190a123pb4", "3010190a123pb4"),
    ("mail ivan@example.test", "ivan@"),
    ("Authorization: Bearer abcdef123456", "abcdef123456"),
    ("password=Qwerty123", "Qwerty123"),
    ("access_token=abc12345", "abc12345"),
    ("client_secret=s3cr3tXYZ", "s3cr3tXYZ"),
    ("db_password: Qwerty123", "Qwerty123"),
    ("пароль: Zx9plm", "Zx9plm"),
    ("пароль - Zx9plm", "Zx9plm"),
    ("пароль: 123", "123"),
    ("login ivanov pass: Qwerty123", "Qwerty123"),
    ("pin: 4821", "4821"),
    ("пин-код: 4821", "4821"),
    ('{"password":"Qw\\"erty"}', "erty"),
    ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg" + "x" * 9000, "MIIEvQIBADANBg"),
    ("пароль: Весна Лето 2024", "2024"),
    ("Password:        Qwerty123", "Qwerty123"),
    ("API key: abc123", "abc123"),
    ("пароль от почты: Qwerty123", "Qwerty123"),
    ("password for admin is Qwerty123", "Qwerty123"),
    ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----", "MIIEvQIBADANBg"),
    ("password=ab,Cdefgh9", "ab,Cdefgh9"),
    ("password=Qw'erty12", "erty12"),
    ("password=Qw;erty12", "erty12"),
    ("звонить 8 029 111-22-33", "111-22"),
    ("звонить 80291112233", "1112233"),
    ("звонить 375291112233", "1112233"),
    ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ('{"password":"Qwerty123"}', "Qwerty123"),
    ("<token>abc12345</token>", "abc12345"),
    ("password Qwerty123", "Qwerty123"),
    ("eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY3.SflKxwRJSMeKKF2QT4", "SflKxwRJSMeKKF2QT4"),
])
def test_default_mask_covers_value_patterns_in_free_text(mod, text, secret):
    out = mod.mask_rows([{"Комментарий": text}])[0]["Комментарий"]
    assert secret not in out and "***" in out


def test_ordinary_values_stay_open(mod):
    row = {"Номенклатура": "Стол офисный", "Описание": "Passport check failed: timeout, bypass secretary basic kit", "Доставка": "Shipping", "Количество": 5, "Комментарий": "заказ 123456789012345 доставлен"}
    assert mod.mask_rows([row])[0] == row


def test_env_mask_fields_adds_not_replaces(mod, monkeypatch):
    monkeypatch.setenv("ONEC_DATA_MASK_FIELDS", "складскаяячейка")
    out = mod.mask_rows([{"Фамилия": "Сидоров", "Оклад": 100, "СкладскаяЯчейка": "A-12", "Артикул": "X1"}])[0]
    assert out["Фамилия"] == "***" and out["Оклад"] == "***" and out["СкладскаяЯчейка"] == "***"
    assert out["Артикул"] == "X1"
    assert "фио" in mod._mask_tokens() and "складскаяячейка" in mod._mask_tokens()


def test_missing_or_broken_config_fails_closed(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(mod.os.path, "dirname", lambda p: str(tmp_path))   # config/ не найден
    with pytest.raises(RuntimeError):
        mod._load_defaults()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "depersonalize.default.json").write_text('{"mask_fields": "фио", "value_patterns": []}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        mod._load_defaults()


def test_default_tokens_come_from_json_file(mod):
    cfg = json.loads((MCP_DIR.parent / "config" / "depersonalize.default.json").read_text(encoding="utf-8"))
    assert set(cfg["mask_fields"]) <= set(mod._mask_tokens())


def test_value_patterns_do_not_hang_on_long_hostile_input(mod):
    import time
    t = time.time()
    for bad in ("token" * 20000, "-eyJ" * 25000, "password" * 15000):
        mod._mask_scalar(bad)
    assert time.time() - t < 5


@pytest.mark.parametrize("text", [
    "+375 29 111-22-33", "+375291112233", "+998 90 123-45-67", "+996 555 123 456", "+374 91 123456",
    "+37491123456", "+7 701 123 45 67", "+77011234567", "8 029 111-22-33", "80291112233", "375291112233",
])
def test_phone_patterns_from_config_work_without_legacy_patterns(mod, text):
    # только шаблоны из JSON, без старых _VALUE_PII (они частично перекрываются)
    assert any(rx.search(text) for rx in mod._EXTRA_VALUE_PII)


def test_long_secret_masked_to_the_end(mod):
    assert "A" * 10 not in mod._mask_scalar("token=" + "A" * 300)


def test_long_jwt_masked(mod):
    jwt = "eyJhbGciOiJIUzI1." + "A" * 9000 + "." + "B" * 100
    assert "AAAAAAAAAA" not in mod._mask_scalar("x " + jwt)
