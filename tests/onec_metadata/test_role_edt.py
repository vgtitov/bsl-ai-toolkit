# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — права роли в формате EDT (.rights)
"""role.grant_right на EDT-файле прав `Rights.rights`.

Структура снята с реальной EDT-выгрузки ERP 2.5 (Roles/*/Rights.rights):
тот же namespace http://v8.1c.ru/8.2/roles, что у Конфигуратора, но корень
несёт xsi:type="Rights", отступы — ТАБЫ, стиль файла LF без BOM.
"""
import shutil
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops.role import grant_right

FIX = Path(__file__).parent / "fixtures"
R = {"r": "http://v8.1c.ru/8.2/roles"}


def _copy(tmp_path):
    work = tmp_path / "Rights.rights"
    shutil.copy(FIX / "rights.rights", work)
    return work


def _rights_of(doc, object_ref):
    return doc.xpath(
        f"//r:object[r:name='{object_ref}']/r:right/r:name/text()", namespaces=R)


def test_grant_new_right_on_existing_object_edt(tmp_path):
    work = _copy(tmp_path)
    grant_right(work, "Catalog.Товары", "Update")
    doc = cfg.load(work)
    assert _rights_of(doc, "Catalog.Товары") == ["Read", "View", "Update"]


def test_create_object_entry_edt(tmp_path):
    work = _copy(tmp_path)
    grant_right(work, "Document.Заказ", "Read")
    doc = cfg.load(work)
    assert _rights_of(doc, "Document.Заказ") == ["Read"]
    # существующие объекты не задеты
    assert _rights_of(doc, "CommonCommand.ОткрытьПанель") == ["View"]


def test_set_existing_right_value_idempotent_edt(tmp_path):
    work = _copy(tmp_path)
    grant_right(work, "Catalog.Товары", "Read", value=False)
    doc = cfg.load(work)
    vals = doc.xpath(
        "//r:object[r:name='Catalog.Товары']/r:right[r:name='Read']/r:value/text()",
        namespaces=R)
    assert vals == ["false"]


def test_edt_style_preserved(tmp_path):
    """byte-perfect: правка не меняет стиль файла (LF, без BOM, табы, xsi:type)."""
    work = _copy(tmp_path)
    grant_right(work, "Catalog.Товары", "Update")
    raw = work.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")          # BOM не появился
    assert b"\r\n" not in raw                            # LF-стиль сохранён
    assert b'xsi:type="Rights"' in raw                   # корень EDT не тронут
    assert b"\t\t<right>" in raw                         # таб-отступы живы


def test_edt_minimal_diff(tmp_path):
    """Дифф против исходника — только добавленный блок права (4 строки).

    DISCIPLINE_ALLOW_TEST_EDIT: наивное «строка не из before» съедало
    <value>true</value> (такая строка уже есть в файле) — нужен позиционный дифф.
    """
    import difflib
    work = _copy(tmp_path)
    before = (FIX / "rights.rights").read_text(encoding="utf-8").splitlines()
    grant_right(work, "Catalog.Товары", "Update")
    after = work.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(before, after, lineterm="", n=0))
    removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
    added = [l[1:].strip() for l in diff if l.startswith("+") and not l.startswith("+++")]
    assert removed == []
    assert added == ["<right>", "<name>Update</name>", "<value>true</value>", "</right>"]
