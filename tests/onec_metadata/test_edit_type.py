# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — edit-type (смена/расширение типа реквизита)
"""edit_type: сменить тип СУЩЕСТВУЮЩЕГО реквизита на одиночный тип (квалификаторы
снимаются) или изменить длину существующего строкового квалификатора.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.edit_type import edit_type

FIX = Path(__file__).parent / "fixtures"
NS = cfg.NS


def _copy(tmp_path, name):
    work = tmp_path / name
    shutil.copy(FIX / name, work)
    return work


# ---------- Конфигуратор ----------

def test_change_to_ref_type_cfg(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: усиление — отступ </Type> после удаления квалификаторов
    work = _copy(tmp_path, "catalog.xml")
    edit_type(work, "Реквизит1", "cfg:CatalogRef.Товары")
    doc = cfg.load(work)
    a = doc.xpath("//md:Attribute[md:Properties/md:Name='Реквизит1']", namespaces=NS)[0]
    assert a.xpath(".//v8:Type/text()", namespaces=NS) == ["cfg:CatalogRef.Товары"]
    # ссылочный тип — квалификаторы сняты
    assert a.xpath(".//v8:StringQualifiers", namespaces=NS) == []
    # отступ закрывающего </Type> не съехал (byte-perfect после удаления)
    txt = work.read_text(encoding="utf-8-sig")
    open_type = next(l for l in txt.splitlines() if l.strip() == "<Type>")
    close_type = next(l for l in txt.splitlines() if l.strip() == "</Type>")
    indent = lambda s: s[:len(s) - len(s.lstrip())]
    assert indent(open_type) == indent(close_type)


def test_extend_string_length_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    edit_type(work, "Реквизит1", "xs:string", length=50)
    doc = cfg.load(work)
    a = doc.xpath("//md:Attribute[md:Properties/md:Name='Реквизит1']", namespaces=NS)[0]
    assert a.xpath(".//v8:Type/text()", namespaces=NS) == ["xs:string"]
    assert a.xpath(".//v8:StringQualifiers/v8:Length/text()", namespaces=NS) == ["50"]


def test_length_on_non_string_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        edit_type(work, "Реквизит1", "cfg:CatalogRef.Товары", length=50)
    assert work.read_bytes() == before


def test_absent_attribute_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    with pytest.raises(OpPreconditionError):
        edit_type(work, "НетТакого", "xs:string")


def test_bad_attribute_name_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    with pytest.raises(OpPreconditionError):
        edit_type(work, "Плохое-Имя", "xs:string")


def test_byte_perfect_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    edit_type(work, "Реквизит1", "xs:string", length=50)
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.xml"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw


# ---------- EDT ----------

def test_change_to_ref_type_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    edit_type(work, "Реквизит1", "CatalogRef.Товары")
    doc = cfg.load(work)
    a = doc.xpath("//attributes[name='Реквизит1']")[0]
    assert a.xpath("type/types/text()") == ["CatalogRef.Товары"]
    assert a.xpath("type/stringQualifiers") == []


def test_extend_string_length_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    edit_type(work, "Реквизит1", "String", length=50)
    doc = cfg.load(work)
    a = doc.xpath("//attributes[name='Реквизит1']")[0]
    assert a.xpath("type/types/text()") == ["String"]
    assert a.xpath("type/stringQualifiers/length/text()") == ["50"]


def test_byte_perfect_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    edit_type(work, "Реквизит1", "String", length=50)
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.mdo"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw
