# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — включение объекта в состав подсистемы
"""subsystem.add_content: добавить объект в состав подсистемы (Content).
Структура Content снята с реального сервера УТ, фикстура обезличена.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.subsystem import add_content

FIX = Path(__file__).parent / "fixtures"
NS = cfg.NS


def _copy(tmp_path, name="subsystem.xml"):
    work = tmp_path / name
    shutil.copy(FIX / name, work)
    return work


def test_add_content_item(tmp_path):
    work = _copy(tmp_path)
    add_content(work, "Document.Заказ")
    doc = cfg.load(work)
    refs = doc.xpath("//md:Content/xr:Item/text()", namespaces=NS)
    assert refs == ["Catalog.Справочник1", "Report.Отчет1", "Document.Заказ"]


def test_new_item_has_xsi_type(tmp_path):
    work = _copy(tmp_path)
    add_content(work, "Document.Заказ")
    doc = cfg.load(work)
    items = doc.xpath(
        "//md:Content/xr:Item[text()='Document.Заказ']", namespaces=NS)
    assert items
    assert items[0].get(cfg.q("xsi", "type")) == "xr:MDObjectRef"


def test_duplicate_content_rejected(tmp_path):
    work = _copy(tmp_path)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError, match="Report.Отчет1"):
        add_content(work, "Report.Отчет1")
    assert work.read_bytes() == before


def test_bad_ref_rejected(tmp_path):
    work = _copy(tmp_path)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        add_content(work, "ПростоИмяБезТипа")
    assert work.read_bytes() == before


def test_bad_ref_name_part_rejected(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_content(work, "Document.Плохое-Имя")


def test_add_content_byte_perfect_tail(tmp_path):
    work = _copy(tmp_path)
    add_content(work, "Document.Заказ")
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.xml"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw


# DISCIPLINE_ALLOW_TEST_EDIT: EDT-паритетные тесты для subsystem.mdo
# ---------- EDT (.mdo) паритет ----------

def test_edt_add_content_item(tmp_path):
    work = _copy(tmp_path, "subsystem.mdo")
    add_content(work, "Document.Заказ")
    doc = cfg.load(work)
    refs = doc.xpath("//content/text()")
    assert refs == ["Catalog.Справочник1", "Report.Отчет1", "Document.Заказ"]


def test_edt_duplicate_content_rejected(tmp_path):
    work = _copy(tmp_path, "subsystem.mdo")
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        add_content(work, "Report.Отчет1")
    assert work.read_bytes() == before


def test_edt_add_content_byte_perfect_tail(tmp_path):
    work = _copy(tmp_path, "subsystem.mdo")
    add_content(work, "Document.Заказ")
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.mdo"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw
