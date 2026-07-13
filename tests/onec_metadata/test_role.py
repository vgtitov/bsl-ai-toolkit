# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — права роли (Rights.xml)
"""role.grant_right: выдать/установить право объекта в файле прав роли.
Структура Rights.xml (namespace roles) снята с реального сервера УТ, обезличена.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.role import grant_right

FIX = Path(__file__).parent / "fixtures"
R = {"r": "http://v8.1c.ru/8.2/roles"}


def _copy(tmp_path):
    work = tmp_path / "rights.xml"
    shutil.copy(FIX / "rights.xml", work)
    return work


def _rights_of(doc, object_ref):
    return doc.xpath(
        f"//r:object[r:name='{object_ref}']/r:right/r:name/text()", namespaces=R)


def test_grant_new_right_on_existing_object(tmp_path):
    work = _copy(tmp_path)
    grant_right(work, "Catalog.Справочник1", "Insert")
    doc = cfg.load(work)
    assert _rights_of(doc, "Catalog.Справочник1") == ["Read", "View", "Insert"]


def test_new_right_value(tmp_path):
    work = _copy(tmp_path)
    grant_right(work, "Catalog.Справочник1", "Insert", value=True)
    doc = cfg.load(work)
    vals = doc.xpath(
        "//r:object[r:name='Catalog.Справочник1']/r:right[r:name='Insert']/r:value/text()",
        namespaces=R)
    assert vals == ["true"]


def test_set_existing_right_value(tmp_path):
    work = _copy(tmp_path)
    grant_right(work, "Catalog.Справочник1", "Read", value=False)
    doc = cfg.load(work)
    vals = doc.xpath(
        "//r:object[r:name='Catalog.Справочник1']/r:right[r:name='Read']/r:value/text()",
        namespaces=R)
    assert vals == ["false"]
    # дубля права не появилось
    assert _rights_of(doc, "Catalog.Справочник1").count("Read") == 1


def test_grant_on_new_object(tmp_path):
    work = _copy(tmp_path)
    grant_right(work, "Report.Отчет1", "View")
    doc = cfg.load(work)
    names = doc.xpath("//r:object/r:name/text()", namespaces=R)
    assert names == ["Catalog.Справочник1", "Document.Документ1", "Report.Отчет1"]
    assert _rights_of(doc, "Report.Отчет1") == ["View"]


# DISCIPLINE_ALLOW_TEST_EDIT: покрытие многосегментной ссылки права (ТЧ/реквизит)
def test_grant_on_subobject_ref(tmp_path):
    # право роли может ссылаться на подобъект (ТЧ/реквизит) — многосегментный путь
    work = _copy(tmp_path)
    grant_right(work, "Catalog.Справочник1.TabularSection.Состав", "View")
    doc = cfg.load(work)
    names = doc.xpath("//r:object/r:name/text()", namespaces=R)
    assert "Catalog.Справочник1.TabularSection.Состав" in names


def test_bad_object_ref_rejected(tmp_path):
    work = _copy(tmp_path)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        grant_right(work, "ПлохаяСсылка", "Read")           # одна часть
    with pytest.raises(OpPreconditionError):
        grant_right(work, "Catalog.Плохое-Имя", "Read")     # битая часть
    assert work.read_bytes() == before


def test_bad_right_name_rejected(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        grant_right(work, "Catalog.Справочник1", "Read-Право")


def test_byte_perfect_round_trip(tmp_path):
    work = _copy(tmp_path)
    grant_right(work, "Catalog.Справочник1", "Insert")
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.xml"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw
    # BOM и CRLF сохранены
    assert raw[:3] == b"\xef\xbb\xbf"
    assert b"\r\n" in raw
