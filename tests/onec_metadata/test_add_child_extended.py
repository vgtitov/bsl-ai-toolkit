# DISCIPLINE_ALLOW_TEST_EDIT: новый тест (расширение add_child: ТЧ/Command/вложенные)
"""add_child: табличные части, команды и реквизиты ВНУТРИ табличной части
(параметр parent) — оба формата."""
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _work(tmp_path, name):
    p = tmp_path / name
    shutil.copy(FIXTURES / name, p)
    return p


# ---------- Конфигуратор ----------

def test_add_tabular_section_configurator(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "document.xml")
    add_child(p, kind="TabularSection", name="Услуги", synonym="Услуги")
    doc = cfg.load(p)
    ts = doc.xpath("//md:TabularSection/md:Properties/md:Name/text()",
                   namespaces=cfg.NS)
    assert ts == ["Товары", "Услуги"]


def test_add_command_configurator(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "document.xml")
    add_child(p, kind="Command", name="Команда2", synonym="Команда 2")
    doc = cfg.load(p)
    cmds = doc.xpath("//md:Command/md:Properties/md:Name/text()",
                     namespaces=cfg.NS)
    assert cmds == ["Команда1", "Команда2"]


def test_add_attribute_into_tabular_section_configurator(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "document.xml")
    add_child(p, kind="Attribute", name="Цена", synonym="Цена",
              type_ref="xs:decimal", parent="Товары")
    doc = cfg.load(p)
    # добавилось ВНУТРИ ТЧ «Товары», не в корень документа
    ts_attrs = doc.xpath(
        "//md:TabularSection[md:Properties/md:Name='Товары']"
        "/md:ChildObjects/md:Attribute/md:Properties/md:Name/text()",
        namespaces=cfg.NS)
    assert ts_attrs == ["Номенклатура", "Количество", "Цена"]
    # корневые реквизиты документа не тронуты (там только Реквизит1)
    root_attrs = doc.xpath(
        "/md:MetaDataObject/md:Document/md:ChildObjects/md:Attribute"
        "/md:Properties/md:Name/text()", namespaces=cfg.NS)
    assert root_attrs == ["Реквизит1"]


def test_add_into_unknown_parent_raises(tmp_path):
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "document.xml")
    with pytest.raises(OpPreconditionError):
        add_child(p, kind="Attribute", name="X", synonym="X",
                  type_ref="xs:string", parent="НетТакойТЧ")


def test_roundtrip_after_nested_add_configurator(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "document.xml")
    add_child(p, kind="Attribute", name="Цена", synonym="Цена",
              type_ref="xs:decimal", parent="Товары")
    edited = p.read_bytes()
    doc = cfg.load(p)
    out = tmp_path / "rt.xml"
    cfg.save(doc, out)
    assert out.read_bytes() == edited


# ---------- EDT ----------

def test_add_tabular_section_edt(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "document.mdo")
    add_child(p, kind="TabularSection", name="Услуги", synonym="Услуги")
    doc = cfg.load(p)
    assert doc.xpath("//tabularSections/name/text()") == ["Товары", "Услуги"]


def test_add_attribute_into_tabular_section_edt(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "document.mdo")
    add_child(p, kind="Attribute", name="Цена", synonym="Цена",
              type_ref="Number", parent="Товары")
    doc = cfg.load(p)
    ts_attrs = doc.xpath(
        "//tabularSections[name='Товары']/attributes/name/text()")
    assert ts_attrs == ["Номенклатура", "Количество", "Цена"]
    # корневой реквизит не задет
    root_attrs = doc.xpath("/mdclass:Document/attributes/name/text()",
                           namespaces={"mdclass": "http://g5.1c.ru/v8/dt/metadata/mdclass"})
    assert root_attrs == ["Реквизит1"]


def test_roundtrip_after_nested_add_edt(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "document.mdo")
    add_child(p, kind="Command", name="Команда2", synonym="Команда 2")
    edited = p.read_bytes()
    doc = cfg.load(p)
    out = tmp_path / "rt.mdo"
    cfg.save(doc, out)
    assert out.read_bytes() == edited
