# DISCIPLINE_ALLOW_TEST_EDIT: новый тест, TDD red-фаза (Фаза 3: generic add_child)
"""Generic-операция add_child: измерения/ресурсы/реквизиты регистров и
значения перечислений — в обоих форматах (Конфигуратор .xml и EDT .mdo)."""
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _work(tmp_path, name):
    p = tmp_path / name
    shutil.copy(FIXTURES / name, p)
    return p


# ---------- Конфигуратор ----------

def test_add_dimension_configurator(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "register.xml")
    add_child(p, kind="Dimension", name="Измерение3", synonym="Измерение 3",
              type_ref="xs:string")
    doc = cfg.load(p)
    names = doc.xpath("//md:Dimension/md:Properties/md:Name/text()",
                      namespaces=cfg.NS)
    assert names == ["Измерение1", "Измерение2", "Измерение3"]
    uuids = doc.xpath("//md:Dimension/@uuid", namespaces=cfg.NS)
    assert len(uuids) == len(set(uuids))


def test_add_resource_configurator(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "register.xml")
    add_child(p, kind="Resource", name="Ресурс2", synonym="Ресурс 2",
              type_ref="xs:decimal")
    doc = cfg.load(p)
    names = doc.xpath("//md:Resource/md:Properties/md:Name/text()",
                      namespaces=cfg.NS)
    assert names == ["Ресурс1", "Ресурс2"]


def test_add_enum_value_configurator(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "enum.xml")
    add_child(p, kind="EnumValue", name="Значение3", synonym="Значение 3")
    doc = cfg.load(p)
    names = doc.xpath("//md:EnumValue/md:Properties/md:Name/text()",
                      namespaces=cfg.NS)
    assert names == ["Значение1", "Значение2", "Значение3"]


def test_duplicate_raises_configurator(tmp_path):
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "register.xml")
    with pytest.raises(OpPreconditionError):
        add_child(p, kind="Dimension", name="Измерение1", synonym="Дубль",
                  type_ref="xs:string")


def test_unknown_kind_raises(tmp_path):
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "register.xml")
    with pytest.raises(OpPreconditionError):
        add_child(p, kind="НетТакогоВида", name="X", synonym="X")


def test_roundtrip_after_add_configurator(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "register.xml")
    add_child(p, kind="Dimension", name="Измерение3", synonym="Измерение 3",
              type_ref="xs:string")
    edited = p.read_bytes()
    doc = cfg.load(p)
    out = tmp_path / "rt.xml"
    cfg.save(doc, out)
    assert out.read_bytes() == edited


# ---------- EDT ----------

def test_add_dimension_edt(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "register.mdo")
    add_child(p, kind="Dimension", name="Измерение2", synonym="Измерение 2",
              type_ref="String")
    doc = cfg.load(p)
    assert doc.xpath("//dimensions/name/text()") == ["Измерение1", "Измерение2"]
    raw = p.read_bytes()
    assert not raw.startswith("﻿".encode("utf-8"))


def test_add_enum_value_edt(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "enum.mdo")
    add_child(p, kind="EnumValue", name="Значение3", synonym="Значение 3")
    doc = cfg.load(p)
    assert doc.xpath("//enumValues/name/text()") == [
        "Значение1", "Значение2", "Значение3"]


def test_duplicate_raises_edt(tmp_path):
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "register.mdo")
    with pytest.raises(OpPreconditionError):
        add_child(p, kind="Resource", name="Ресурс1", synonym="Дубль",
                  type_ref="Number")


def test_roundtrip_after_add_edt(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.add_child import add_child

    p = _work(tmp_path, "enum.mdo")
    add_child(p, kind="EnumValue", name="Значение3", synonym="Значение 3")
    edited = p.read_bytes()
    doc = cfg.load(p)
    out = tmp_path / "rt.mdo"
    cfg.save(doc, out)
    assert out.read_bytes() == edited
