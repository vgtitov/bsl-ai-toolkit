# DISCIPLINE_ALLOW_TEST_EDIT: покрытие add_child для BusinessProcess и ExchangePlan
"""add_child реквизита на объектах BusinessProcess и ExchangePlan (оба формата).
Фикстуры — обезличенная структура, снятая с реального сервера УТ.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops.add_child import add_child

FIX = Path(__file__).parent / "fixtures"
NS = cfg.NS

CASES = [
    ("businessprocess.xml", "cfg"),
    ("businessprocess.mdo", "edt"),
    ("exchangeplan.xml", "cfg"),
    ("exchangeplan.mdo", "edt"),
]


@pytest.mark.parametrize("fixture,fmt", CASES)
def test_add_attribute_via_child(tmp_path, fixture, fmt):
    work = tmp_path / fixture
    shutil.copy(FIX / fixture, work)
    add_child(work, "Attribute", "Реквизит2", "Реквизит 2",
              type_ref="cfg:CatalogRef.Товары" if fmt == "cfg" else "CatalogRef.Товары")
    doc = cfg.load(work)
    if fmt == "cfg":
        names = doc.xpath("//md:Attribute/md:Properties/md:Name/text()", namespaces=NS)
    else:
        names = doc.xpath("//attributes/name/text()")
    assert names == ["Реквизит1", "Реквизит2"]


@pytest.mark.parametrize("fixture,fmt", CASES)
def test_byte_perfect_round_trip(tmp_path, fixture, fmt):
    work = tmp_path / fixture
    shutil.copy(FIX / fixture, work)
    add_child(work, "Attribute", "Реквизит2", "Реквизит 2",
              type_ref="cfg:CatalogRef.Товары" if fmt == "cfg" else "CatalogRef.Товары")
    # DISCIPLINE_ALLOW_TEST_EDIT: усиление — явная проверка сохранения BOM+CRLF
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / ("rt_" + fixture)
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw
    if fmt == "cfg":  # реальные объектные файлы Конфигуратора — BOM+CRLF; add_child их сохраняет
        assert raw[:3] == b"\xef\xbb\xbf"
        assert b"\r\n" in raw
        assert raw.count(b"\n") == raw.count(b"\r\n")  # нет одиночных LF
