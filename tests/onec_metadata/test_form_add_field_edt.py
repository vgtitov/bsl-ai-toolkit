# DISCIPLINE_ALLOW_TEST_EDIT: новый red-набор TDD — form add-field для EDT (.form)
"""add_field для EDT-формы (.form): визуальное поле = items xsi:type=form:FormField
с extInfo InputFieldExtInfo. Клон образца, свежие id в пространстве ЭЛЕМЕНТОВ
(включая вложенные extendedTooltip/contextMenu), привязка dataPath/segments,
переименование вложенных частей по конвенции. Byte-perfect (фикстура CRLF)."""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form import add_field

FIX = Path(__file__).parent / "fixtures"
FORM_NS = {"form": "http://g5.1c.ru/v8/dt/form",
           "xsi": "http://www.w3.org/2001/XMLSchema-instance"}


def _copy(tmp_path):
    work = tmp_path / "form_field_edt.form"
    shutil.copy(FIX / "form_field_edt.form", work)
    return work


def _no_lone_lf(work):
    raw = work.read_bytes()
    return raw.count(b"\n") == raw.count(b"\r\n")


def test_add_field_edt_creates_formfield(tmp_path):
    work = _copy(tmp_path)
    add_field(work, "Артикул", "Объект.Артикул")
    doc = cfg.load(work)
    root = doc.getroot()
    # новый FormField с нужным именем и привязкой
    fields = root.xpath("items[@xsi:type='form:FormField']", namespaces=FORM_NS)
    names = [f.findtext("name") for f in fields]
    assert "Артикул" in names
    new = [f for f in fields if f.findtext("name") == "Артикул"][0]
    assert new.xpath("dataPath/segments/text()") == ["Объект.Артикул"]
    assert new.findtext("type") == "InputField"


def test_add_field_edt_fresh_ids_in_element_space(tmp_path):
    work = _copy(tmp_path)
    add_field(work, "Артикул", "Объект.Артикул")
    doc = cfg.load(work)
    root = doc.getroot()
    new = [f for f in root.xpath("items[@xsi:type='form:FormField']", namespaces=FORM_NS)
           if f.findtext("name") == "Артикул"][0]
    # исходные id элементов: FormField=4, contextMenu=5, tooltip=6 → новые > 6
    field_id = int(new.findtext("id"))
    tip_id = int(new.xpath("extendedTooltip/id/text()")[0])
    menu_id = int(new.xpath("contextMenu/id/text()")[0])
    assert field_id > 6 and tip_id > 6 and menu_id > 6
    assert len({field_id, tip_id, menu_id}) == 3          # все разные
    # реквизит (пространство реквизитов) НЕ затронут
    assert root.xpath("attributes/id/text()") == ["1"]


def test_add_field_edt_renames_nested_parts(tmp_path):
    work = _copy(tmp_path)
    add_field(work, "Артикул", "Объект.Артикул")
    doc = cfg.load(work)
    new = [f for f in doc.getroot().xpath("items[@xsi:type='form:FormField']", namespaces=FORM_NS)
           if f.findtext("name") == "Артикул"][0]
    assert new.xpath("extendedTooltip/name/text()") == ["АртикулРасширеннаяПодсказка"]
    assert new.xpath("contextMenu/name/text()") == ["АртикулКонтекстноеМеню"]


def test_add_field_edt_original_untouched(tmp_path):
    work = _copy(tmp_path)
    add_field(work, "Артикул", "Объект.Артикул")
    doc = cfg.load(work)
    orig = [f for f in doc.getroot().xpath("items[@xsi:type='form:FormField']", namespaces=FORM_NS)
            if f.findtext("name") == "Наименование"][0]
    assert orig.findtext("id") == "4"
    assert orig.xpath("contextMenu/id/text()") == ["5"]
    assert orig.xpath("extendedTooltip/id/text()") == ["6"]


def test_add_field_edt_duplicate_rejected(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_field(work, "Наименование", "Объект.Description")


def test_add_field_edt_byte_perfect(tmp_path):
    work = _copy(tmp_path)
    add_field(work, "Артикул", "Объект.Артикул")
    assert _no_lone_lf(work)               # CRLF-фикстура: ни одного одинокого LF
    raw = work.read_bytes()
    tmp2 = tmp_path / "rt.form"
    cfg.save(cfg.load(work), tmp2)
    assert tmp2.read_bytes() == raw
