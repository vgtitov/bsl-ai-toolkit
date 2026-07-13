# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — вид AddressingAttribute на реальной структуре Task
"""add_child на объекте Task (Задача): обычный Attribute и новый вид
AddressingAttribute (реквизит адресации). Фикстура — обезличенная структура,
снятая с реального объекта сервера УТ.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.add_child import add_child

FIX = Path(__file__).parent / "fixtures"
NS = cfg.NS


def _copy(tmp_path):
    work = tmp_path / "task.xml"
    shutil.copy(FIX / "task.xml", work)
    return work


def test_add_addressing_attribute(tmp_path):
    work = _copy(tmp_path)
    add_child(work, "AddressingAttribute", "ДополнительныйИсполнитель",
              "Дополнительный исполнитель", type_ref="cfg:CatalogRef.Исполнители")
    doc = cfg.load(work)
    names = doc.xpath(
        "//md:AddressingAttribute/md:Properties/md:Name/text()", namespaces=NS)
    assert names == ["Исполнитель", "ДополнительныйИсполнитель"]
    # тип проставлен в новый элемент
    new_types = doc.xpath(
        "//md:AddressingAttribute[md:Properties/md:Name='ДополнительныйИсполнитель']"
        "//v8:Type/text()", namespaces=NS)
    assert new_types == ["cfg:CatalogRef.Исполнители"]


def test_add_plain_attribute_to_task(tmp_path):
    work = _copy(tmp_path)
    add_child(work, "Attribute", "Реквизит2", "Реквизит 2", type_ref="xs:string")
    doc = cfg.load(work)
    names = doc.xpath(
        "//md:Attribute/md:Properties/md:Name/text()", namespaces=NS)
    assert names == ["Реквизит1", "Реквизит2"]


def test_addressing_attribute_requires_type(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_child(work, "AddressingAttribute", "БезТипа", "Без типа")


def test_duplicate_addressing_attribute_rejected(tmp_path):
    work = _copy(tmp_path)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        add_child(work, "AddressingAttribute", "Исполнитель", "Исполнитель",
                  type_ref="cfg:CatalogRef.Исполнители")
    assert work.read_bytes() == before


def test_add_addressing_attribute_byte_perfect_tail(tmp_path):
    """После вставки файл остаётся валидным и round-trip стабилен."""
    work = _copy(tmp_path)
    add_child(work, "AddressingAttribute", "Куратор", "Куратор",
              type_ref="cfg:CatalogRef.Исполнители")
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.xml"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw
