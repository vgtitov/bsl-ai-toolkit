# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — set-property (правка скалярного свойства)
"""set_property: изменить СУЩЕСТВУЮЩЕЕ скалярное свойство объекта или реквизита.
Структурные свойства (Synonym/Type/Content) и отсутствующие — отказ предусловием.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.set_property import set_property

FIX = Path(__file__).parent / "fixtures"
NS = cfg.NS


def _copy(tmp_path, name):
    work = tmp_path / name
    shutil.copy(FIX / name, work)
    return work


# ---------- Конфигуратор ----------

def test_set_object_flag_cfg(tmp_path):
    work = _copy(tmp_path, "subsystem.xml")
    set_property(work, "IncludeInCommandInterface", "false")
    doc = cfg.load(work)
    vals = doc.xpath(
        "//md:Subsystem/md:Properties/md:IncludeInCommandInterface/text()",
        namespaces=NS)
    assert vals == ["false"]


def test_set_object_comment_empty_to_text_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    set_property(work, "Comment", "Пояснение")
    doc = cfg.load(work)
    vals = doc.xpath("/*/md:Catalog/md:Properties/md:Comment/text()", namespaces=NS)
    assert vals == ["Пояснение"]


def test_set_attribute_property_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    set_property(work, "Comment", "Реквизитный", attribute="Реквизит1")
    doc = cfg.load(work)
    vals = doc.xpath(
        "//md:Attribute[md:Properties/md:Name='Реквизит1']/md:Properties/md:Comment/text()",
        namespaces=NS)
    assert vals == ["Реквизитный"]


def test_structural_property_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "subsystem.xml")
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        set_property(work, "Content", "чепуха")  # у Content есть дети
    assert work.read_bytes() == before


def test_absent_property_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        set_property(work, "ПолнотекстовыйПоиск", "Использовать")
    assert work.read_bytes() == before


def test_absent_attribute_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    with pytest.raises(OpPreconditionError):
        set_property(work, "Comment", "x", attribute="НетТакого")


def test_bad_property_name_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    with pytest.raises(OpPreconditionError):
        set_property(work, "Плохое-Имя", "x")


def test_byte_perfect_cfg(tmp_path):
    work = _copy(tmp_path, "subsystem.xml")
    set_property(work, "IncludeInCommandInterface", "false")
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.xml"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw


# ---------- EDT ----------

def test_set_object_flag_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    set_property(work, "useStandardCommands", "false")
    doc = cfg.load(work)
    vals = doc.xpath("/mdclass:Catalog/useStandardCommands/text()",
                     namespaces={"mdclass": "http://g5.1c.ru/v8/dt/metadata/mdclass"})
    assert vals == ["false"]


def test_set_attribute_property_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    # у реквизита EDT задать comment — свойство отсутствует (EDT опускает дефолты) → отказ
    with pytest.raises(OpPreconditionError):
        set_property(work, "comment", "x", attribute="Реквизит1")


def test_structural_property_rejected_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    with pytest.raises(OpPreconditionError):
        set_property(work, "synonym", "x")  # synonym структурный


def test_byte_perfect_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    set_property(work, "useStandardCommands", "false")
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.mdo"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw
