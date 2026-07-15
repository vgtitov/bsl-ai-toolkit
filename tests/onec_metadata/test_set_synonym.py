# DISCIPLINE_ALLOW_TEST_EDIT: новый red-набор TDD — set_synonym (безопасная правка презентации)
"""set_synonym: сменить текст синонима («Наименование») существующего языка —
у реквизита/измерения/ресурса (member) или самого объекта (member=None), оба формата.
Синоним — свободный текст (пробелы/пунктуация допустимы), ссылок не ломает. Byte-perfect."""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.synonym import set_synonym

FIX = Path(__file__).parent / "fixtures"
NS = cfg.NS


def _copy(tmp_path, name):
    work = tmp_path / name
    shutil.copy(FIX / name, work)
    return work


def _no_lone_lf(work):
    raw = work.read_bytes()
    return raw.count(b"\n") == raw.count(b"\r\n")


def test_member_synonym_cfg(tmp_path):
    work = _copy(tmp_path, "register.xml")
    set_synonym(work, "Измерение1", "Новое наименование измерения")
    doc = cfg.load(work)
    d = doc.xpath("//md:Dimension[md:Properties/md:Name='Измерение1']", namespaces=NS)[0]
    assert d.xpath(".//v8:content/text()", namespaces=NS) == ["Новое наименование измерения"]
    assert _no_lone_lf(work)


def test_object_synonym_cfg(tmp_path):
    work = _copy(tmp_path, "sessionparameter.xml")
    set_synonym(work, None, "Параметр сессии клиента")
    doc = cfg.load(work)
    assert doc.xpath("//md:SessionParameter/md:Properties/md:Synonym//v8:content/text()",
                     namespaces=NS) == ["Параметр сессии клиента"]
    assert _no_lone_lf(work)


def test_object_synonym_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    set_synonym(work, None, "Справочник товаров")
    doc = cfg.load(work)
    assert doc.xpath("/*/synonym[key='ru']/value/text()") == ["Справочник товаров"]
    assert _no_lone_lf(work)


def test_missing_lang_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "register.xml")
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        set_synonym(work, "Измерение1", "X", lang="en")
    assert work.read_bytes() == before


def test_free_text_ok_cfg(tmp_path):
    # синоним — свободный текст: пробелы, точка, спецсимвол «&» (lxml экранирует)
    work = _copy(tmp_path, "register.xml")
    set_synonym(work, "Измерение1", "Товары & услуги, шт.")
    doc = cfg.load(work)
    d = doc.xpath("//md:Dimension[md:Properties/md:Name='Измерение1']", namespaces=NS)[0]
    assert d.xpath(".//v8:content/text()", namespaces=NS) == ["Товары & услуги, шт."]
    assert _no_lone_lf(work)


def test_byte_perfect_cfg(tmp_path):
    work = _copy(tmp_path, "register.xml")
    set_synonym(work, "Измерение1", "Другое имя")
    raw = work.read_bytes()
    tmp2 = tmp_path / "rt.xml"
    cfg.save(cfg.load(work), tmp2)
    assert tmp2.read_bytes() == raw
