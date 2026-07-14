# DISCIPLINE_ALLOW_TEST_EDIT: red-тест — уникальность идентификаторов при deepcopy (баг ТЧ)
"""При добавлении элемента с InternalInfo/GeneratedType (табличная часть) все
внутренние идентификаторы нового поддерева должны быть УНИКАЛЬНЫ, а имена
порождаемых типов — указывать на новый объект. Иначе конфигурация ломается
коллизией UUID.
"""
import shutil
from pathlib import Path

from lxml import etree

from onec_metadata.ops.add_child import add_child

FIX = Path(__file__).parent / "fixtures"
MD = "http://v8.1c.ru/8.3/MDClasses"
XR = "http://v8.1c.ru/8.3/xcf/readable"


def _all_ids(root):
    uuids = root.xpath("//@uuid")
    tids = root.xpath("//xr:TypeId/text()", namespaces={"xr": XR})
    vids = root.xpath("//xr:ValueId/text()", namespaces={"xr": XR})
    return uuids + tids + vids


def test_add_tabular_section_regenerates_all_ids(tmp_path):
    work = tmp_path / "document_ts.xml"
    shutil.copy(FIX / "document_ts.xml", work)
    add_child(work, "TabularSection", "НоваяТЧ", "Новая ТЧ")
    root = etree.parse(str(work)).getroot()

    ids = _all_ids(root)
    dups = sorted({x for x in ids if ids.count(x) > 1})
    assert dups == [], f"дублированные идентификаторы: {dups}"


def test_new_tabular_section_generated_type_names_point_to_new(tmp_path):
    work = tmp_path / "document_ts.xml"
    shutil.copy(FIX / "document_ts.xml", work)
    add_child(work, "TabularSection", "НоваяТЧ", "Новая ТЧ")
    root = etree.parse(str(work)).getroot()

    new_ts = root.xpath(
        "//md:TabularSection[md:Properties/md:Name='НоваяТЧ']",
        namespaces={"md": MD})[0]
    names = new_ts.xpath(".//xr:GeneratedType/@name", namespaces={"xr": XR})
    # имена порождаемых типов должны оканчиваться на новое имя ТЧ, не на старое
    assert names, "у новой ТЧ нет GeneratedType"
    assert all(n.endswith(".НоваяТЧ") for n in names), names
    assert not any(n.endswith(".ТЧ1") for n in names), names


def test_add_attribute_still_regenerates_single_uuid(tmp_path):
    # реквизит — единственный uuid; поведение не должно сломаться
    work = tmp_path / "catalog.xml"
    shutil.copy(FIX / "catalog.xml", work)
    add_child(work, "Attribute", "Реквизит3", "Р3", type_ref="xs:string")
    root = etree.parse(str(work)).getroot()
    uuids = root.xpath("//@uuid")
    assert len(uuids) == len(set(uuids)), "uuid реквизитов не уникальны"
