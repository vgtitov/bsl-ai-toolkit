# DISCIPLINE_ALLOW_TEST_EDIT: переведён на синтетические обезличенные фикстуры
"""Операция attribute_add на синтетическом справочнике."""
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _work(tmp_path):
    p = tmp_path / "catalog.xml"
    shutil.copy(FIXTURES / "catalog.xml", p)
    return p


def test_add_attribute(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.attribute_add import add_attribute

    p = _work(tmp_path)
    add_attribute(p, name="Ав_ТестовыйРеквизит", type_ref="xs:string",
                  synonym="Тестовый реквизит")

    doc = cfg.load(p)
    names = doc.xpath("//md:Attribute/md:Properties/md:Name/text()",
                      namespaces=cfg.NS)
    assert "Ав_ТестовыйРеквизит" in names

    uuids = doc.xpath("//md:Attribute/@uuid", namespaces=cfg.NS)
    assert len(uuids) == len(set(uuids))

    new_attr = doc.xpath(
        "//md:Attribute[md:Properties/md:Name='Ав_ТестовыйРеквизит']",
        namespaces=cfg.NS)[0]
    syns = new_attr.xpath(".//v8:content/text()", namespaces=cfg.NS)
    assert "Тестовый реквизит" in syns
    types = new_attr.xpath(".//v8:Type/text()", namespaces=cfg.NS)
    assert "xs:string" in types


def test_duplicate_raises(tmp_path):
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.attribute_add import add_attribute

    p = _work(tmp_path)
    add_attribute(p, "Ав_Дубль", "xs:string", "Дубль")
    with pytest.raises(OpPreconditionError):
        add_attribute(p, "Ав_Дубль", "xs:string", "Дубль")


def test_roundtrip_after_add(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.attribute_add import add_attribute

    p = _work(tmp_path)
    add_attribute(p, "Ав_ТестовыйРеквизит", "xs:string", "Тестовый реквизит")
    edited = p.read_bytes()
    doc = cfg.load(p)
    out = tmp_path / "rt.xml"
    cfg.save(doc, out)
    assert out.read_bytes() == edited
