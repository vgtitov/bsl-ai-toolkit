# DISCIPLINE_ALLOW_TEST_EDIT: red-тест TDD — создание без образца через донор (P3)
"""Когда в объекте нет элемента-образца нужного вида, add_child берёт образец из
объекта-ДОНОРА (однотипного объекта конфигурации) и регенерирует идентификаторы.
Сохраняет версия-корректность (образец реальный из той же конфигурации).
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.add_child import add_child

FIX = Path(__file__).parent / "fixtures"
NS = cfg.NS


def test_add_first_attribute_from_donor(tmp_path):
    work = tmp_path / "catalog_noattr.xml"
    shutil.copy(FIX / "catalog_noattr.xml", work)
    add_child(work, "Attribute", "Реквизит1", "Реквизит 1",
              type_ref="xs:string", donor=FIX / "catalog.xml")
    doc = cfg.load(work)
    names = doc.xpath("//md:Attribute/md:Properties/md:Name/text()", namespaces=NS)
    assert names == ["Реквизит1"]
    # идентификатор свежий (не совпал с донорским)
    donor = cfg.load(FIX / "catalog.xml")
    donor_uuids = set(donor.xpath("//md:Attribute/@uuid", namespaces=NS))
    new_uuid = doc.xpath("//md:Attribute/@uuid", namespaces=NS)[0]
    assert new_uuid not in donor_uuids


def test_no_sample_no_donor_still_errors(tmp_path):
    work = tmp_path / "catalog_noattr.xml"
    shutil.copy(FIX / "catalog_noattr.xml", work)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        add_child(work, "Attribute", "Реквизит1", "Реквизит 1", type_ref="xs:string")
    assert work.read_bytes() == before


def test_donor_used_only_when_no_local_sample(tmp_path):
    # если локальный образец ЕСТЬ — донор игнорируется (берём локальный)
    work = tmp_path / "catalog.xml"
    shutil.copy(FIX / "catalog.xml", work)
    add_child(work, "Attribute", "Реквизит3", "Р3", type_ref="xs:string",
              donor=FIX / "document.xml")
    doc = cfg.load(work)
    names = doc.xpath("//md:Attribute/md:Properties/md:Name/text()", namespaces=NS)
    assert names == ["Реквизит1", "Реквизит2", "Реквизит3"]


def test_donor_byte_perfect_round_trip(tmp_path):
    work = tmp_path / "catalog_noattr.xml"
    shutil.copy(FIX / "catalog_noattr.xml", work)
    add_child(work, "Attribute", "Реквизит1", "Реквизит 1",
              type_ref="xs:string", donor=FIX / "catalog.xml")
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.xml"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw


def test_donor_inserted_indent_matches_root_level(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: внутренний отступ вставленного из донора элемента
    # даже если у донора реквизит есть ТОЛЬКО в ТЧ (глубже) — берём корневой уровень
    work = tmp_path / "catalog_noattr.xml"
    shutil.copy(FIX / "catalog_noattr.xml", work)
    # донор с реквизитом на КОРНЕВОМ уровне (catalog.xml) — а document_ts только в ТЧ
    add_child(work, "Attribute", "X", "X", type_ref="xs:string",
              donor=FIX / "catalog.xml")
    txt = work.read_text(encoding="utf-8-sig")
    indent = lambda s: s[:len(s) - len(s.lstrip())]
    attr_open = next(l for l in txt.splitlines() if "<Attribute uuid" in l)
    props = next(l for l in txt.splitlines() if l.strip() == "<Properties>"
                 and indent(l) == indent(attr_open) + "\t")
    # Attribute на 3 табах, Properties на 4 — корневой уровень
    assert indent(attr_open) == "\t\t\t"
    assert indent(props) == "\t\t\t\t"


def test_donor_with_only_ts_attribute_rejected(tmp_path):
    # донор, где реквизит есть ТОЛЬКО внутри ТЧ (нет корневого) → отказ
    work = tmp_path / "catalog_noattr.xml"
    shutil.copy(FIX / "catalog_noattr.xml", work)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        add_child(work, "Attribute", "X", "X", type_ref="xs:string",
                  donor=FIX / "document_ts.xml")
    assert work.read_bytes() == before


def test_donor_tabular_section_rejected(tmp_path):
    # донор ТЧ (порождает тип) не поддержан
    work = tmp_path / "catalog_noattr.xml"
    shutil.copy(FIX / "catalog_noattr.xml", work)
    with pytest.raises(OpPreconditionError):
        add_child(work, "TabularSection", "НоваяТЧ", "Новая ТЧ",
                  donor=FIX / "document_ts.xml")
