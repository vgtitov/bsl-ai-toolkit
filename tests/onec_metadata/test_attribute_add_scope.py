# DISCIPLINE_ALLOW_TEST_EDIT: синтетические обезличенные фикстуры
"""Реквизит объекта не должен попадать внутрь табличной части.

Регрессия: образец искали по всему файлу (`//md:Attribute`, `//attributes`) и
брали последний. У объекта с табличными частями последний реквизит лежит ВНУТРИ
ТЧ, поэтому новый реквизит оказывался там же. Загрузка в ИБ при этом проходит
успешно и пишет «Объект изменён», а поле не находится запросом к объекту.
"""
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def test_configurator_attribute_lands_on_object_not_in_tabular_section(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.attribute_add import add_attribute

    p = tmp_path / "document_ts_and_attr.xml"
    shutil.copy(FIXTURES / "document_ts_and_attr.xml", p)

    add_attribute(p, name="Ав_Новый", type_ref="xs:string", synonym="Новый")

    doc = cfg.load(p)
    own = doc.xpath(
        "//md:ChildObjects[not(ancestor::md:TabularSection)]"
        "/md:Attribute/md:Properties/md:Name/text()", namespaces=cfg.NS)
    in_ts = doc.xpath(
        "//md:TabularSection//md:Attribute/md:Properties/md:Name/text()",
        namespaces=cfg.NS)

    assert "Ав_Новый" in own, "реквизит должен быть на уровне объекта"
    assert "Ав_Новый" not in in_ts, "реквизит не должен попасть в табличную часть"


def test_configurator_no_object_level_sample_is_explicit_error(tmp_path):
    """Образец есть только в ТЧ — операция обязана отказать, а не класть в ТЧ."""
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.attribute_add import add_attribute

    p = tmp_path / "document_ts.xml"
    shutil.copy(FIXTURES / "document_ts.xml", p)

    with pytest.raises(OpPreconditionError):
        add_attribute(p, name="Ав_Новый", type_ref="xs:string", synonym="Новый")


def test_edt_attribute_lands_on_object_not_in_tabular_section(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.attribute_add import add_attribute

    p = tmp_path / "document.mdo"
    shutil.copy(FIXTURES / "document.mdo", p)

    add_attribute(p, name="Ав_Новый", type_ref="xs:string", synonym="Новый")

    doc = cfg.load(p)
    root = doc.getroot()
    own = [el.findtext("name") for el in root.xpath("attributes")]
    in_ts = [el.findtext("name")
             for el in doc.xpath("//tabularSections/attributes")]

    assert "Ав_Новый" in own, "реквизит должен быть прямым потомком корня .mdo"
    assert "Ав_Новый" not in in_ts, "реквизит не должен попасть в табличную часть"
