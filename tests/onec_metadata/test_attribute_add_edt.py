# DISCIPLINE_ALLOW_TEST_EDIT: новый тест, TDD red-фаза (Фаза 2: EDT .mdo)
"""attribute add для EDT-формата (.mdo): единая точка add_attribute
диспетчеризует по расширению файла."""
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
EDT_NS = {"mdclass": "http://g5.1c.ru/v8/dt/metadata/mdclass"}


def _work(tmp_path):
    p = tmp_path / "catalog.mdo"
    shutil.copy(FIXTURES / "catalog.mdo", p)
    return p


def test_add_attribute_edt(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.attribute_add import add_attribute

    p = _work(tmp_path)
    add_attribute(p, name="Реквизит3", type_ref="String", synonym="Реквизит 3")

    doc = cfg.load(p)
    names = doc.xpath("//attributes/name/text()")
    assert "Реквизит3" in names
    # порядок: новый после последнего существующего
    assert names == ["Реквизит1", "Реквизит2", "Реквизит3"]

    # свой uuid, не совпадающий с образцом
    uuids = doc.xpath("//attributes/@uuid")
    assert len(uuids) == len(set(uuids))

    new = doc.xpath("//attributes[name='Реквизит3']")[0]
    assert new.xpath("synonym/value/text()") == ["Реквизит 3"]
    assert new.xpath("type/types/text()") == ["String"]


def test_add_attribute_edt_duplicate_raises(tmp_path):
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.attribute_add import add_attribute

    p = _work(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_attribute(p, "Реквизит1", "String", "Дубль")


def test_add_attribute_edt_roundtrip(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.attribute_add import add_attribute

    p = _work(tmp_path)
    add_attribute(p, "Реквизит3", "Boolean", "Реквизит 3")
    edited = p.read_bytes()
    doc = cfg.load(p)
    out = tmp_path / "rt.mdo"
    cfg.save(doc, out)
    assert out.read_bytes() == edited
    # стиль EDT сохранён: нет BOM, CRLF на месте
    assert not edited.startswith("﻿".encode("utf-8"))
    assert b"\r\n" in edited
