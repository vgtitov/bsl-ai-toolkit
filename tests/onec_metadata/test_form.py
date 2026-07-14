# DISCIPLINE_ALLOW_TEST_EDIT: P4.1 red-тест — реквизит управляемой формы
"""form.add_attribute: добавить реквизит формы (Attributes) со свежим уникальным
id. Структура фикстуры — с реального сервера УТ, обезличена.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form import add_attribute as form_add_attribute

FIX = Path(__file__).parent / "fixtures"
NS = cfg.NS


def _copy(tmp_path):
    work = tmp_path / "form.xml"
    shutil.copy(FIX / "form.xml", work)
    return work


def test_add_form_attribute(tmp_path):
    work = _copy(tmp_path)
    form_add_attribute(work, "Комментарий", "xs:string")
    doc = cfg.load(work)
    names = doc.xpath("//lf:Attributes/lf:Attribute/@name", namespaces=NS)
    assert names == ["Объект", "Комментарий"]


def test_new_attribute_fresh_unique_id(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: id реквизитов — своё пространство (раздельно с элементами)
    work = _copy(tmp_path)
    form_add_attribute(work, "Комментарий", "xs:string")
    doc = cfg.load(work)
    # уникальность В ПРОСТРАНСТВЕ реквизитов формы
    attr_ids = [int(x) for x in doc.xpath("//lf:Attributes//@id", namespaces=NS)]
    assert len(attr_ids) == len(set(attr_ids)), "id реквизитов формы не уникальны"
    new_id = doc.xpath(
        "//lf:Attribute[@name='Комментарий']/@id", namespaces=NS)[0]
    assert int(new_id) == 2  # max id реквизитов был 1


def test_new_attribute_type_set(tmp_path):
    work = _copy(tmp_path)
    form_add_attribute(work, "Комментарий", "xs:string")
    doc = cfg.load(work)
    types = doc.xpath(
        "//lf:Attribute[@name='Комментарий']/lf:Type/v8:Type/text()", namespaces=NS)
    assert types == ["xs:string"]


def test_clone_not_main_attribute(tmp_path):
    # клон НЕ должен быть основным реквизитом (основной только один)
    work = _copy(tmp_path)
    form_add_attribute(work, "Комментарий", "xs:string")
    doc = cfg.load(work)
    main = doc.xpath(
        "//lf:Attribute[@name='Комментарий']/lf:MainAttribute/text()", namespaces=NS)
    assert main == [] or main == ["false"]


def test_duplicate_name_rejected(tmp_path):
    work = _copy(tmp_path)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        form_add_attribute(work, "Объект", "xs:string")
    assert work.read_bytes() == before


def test_bad_name_rejected(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        form_add_attribute(work, "Плохое-Имя", "xs:string")


def test_composite_type_sample_keeps_close_indent(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: образец с составным типом — отступ </Type> не съезжает
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Form xmlns="http://v8.1c.ru/8.3/xcf/logform" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core">',
        '\t<Attributes>',
        '\t\t<Attribute name="Объект" id="1">',
        '\t\t\t<Type>',
        '\t\t\t\t<v8:Type>xs:string</v8:Type>',
        '\t\t\t\t<v8:Type>xs:decimal</v8:Type>',
        '\t\t\t</Type>',
        '\t\t</Attribute>',
        '\t</Attributes>',
        '</Form>',
    ]
    work = tmp_path / "form.xml"
    work.write_bytes(("﻿" + "\r\n".join(lines)).encode("utf-8"))
    form_add_attribute(work, "Новый", "xs:string")
    txt = work.read_text(encoding="utf-8-sig")
    indent = lambda s: s[:len(s) - len(s.lstrip())]
    opens = [l for l in txt.splitlines() if l.strip() == "<Type>"]
    closes = [l for l in txt.splitlines() if l.strip() == "</Type>"]
    for o, c in zip(opens, closes):
        assert indent(o) == indent(c), f"{o!r} vs {c!r}"


def test_byte_perfect(tmp_path):
    work = _copy(tmp_path)
    form_add_attribute(work, "Комментарий", "xs:string")
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.xml"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw
    assert raw[:3] == b"\xef\xbb\xbf" and b"\r\n" in raw
