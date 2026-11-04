# DISCIPLINE_ALLOW_TEST_EDIT: новый red-набор TDD — form set-style для Конфигуратора (Form.xml)
"""set_style для формы в выгрузке Конфигуратора (`Form.xml`, ns logform): свойства плоско
под элементом, имена с большой буквы, порядок — по корпусу выгрузок (cfg_form_order.json).
Имена свойств принимаются и в стиле EDT (backColor) — так один рецепт работает в обоих форматах.
Byte-perfect: BOM, CRLF и табуляция сохраняются, дифф — только вставленные/заменённые строки."""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form_style import set_style

FIX = Path(__file__).parent / "fixtures"
NS = {"lf": "http://v8.1c.ru/8.3/xcf/logform", "xr": "http://v8.1c.ru/8.3/xcf/readable"}


def _copy(tmp_path):
    work = tmp_path / "Form.xml"
    shutil.copy(FIX / "form_style_cfg.xml", work)
    return work


def _el(work, name):
    root = cfg.load(work).getroot()
    hits = root.xpath(".//*[@name=$n]", n=name)
    assert len(hits) == 1
    return hits[0]


def _tags(el):
    return [c.tag.split("}")[1] for c in el if isinstance(c.tag, str)]


def _lines(p):
    return p.read_bytes().decode("utf-8-sig").split("\r\n")


def _only_added(before, after):
    it = iter(after)
    return all(any(line == x for x in it) for line in before)


def test_group_props_flat_in_order_with_edt_names(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "ГруппаИсторияИзменений", {"backColor": "style:примЦветБелый",
                                               "group": "Vertical",
                                               "representation": "NormalSeparation",
                                               "showTitle": "false"})
    grp = _el(work, "ГруппаИсторияИзменений")
    assert grp.find("lf:BackColor", NS).text == "style:примЦветБелый"
    t = _tags(grp)
    assert t.index("Group") < t.index("Representation") < t.index("ShowTitle") < t.index("BackColor")
    assert t.index("BackColor") < t.index("ChildItems")


def test_rgb_becomes_hex(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "ОбщаяИнформация", {"TextColor": "rgb:1,152,157"})
    assert _el(work, "ОбщаяИнформация").find("lf:TextColor", NS).text == "#01989D"


def test_font_attributes(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "ОбщаяИнформация", {"font": "style:NormalTextFont,face=Gilroy,height=14,bold"})
    f = _el(work, "ОбщаяИнформация").find("lf:Font", NS)
    assert list(f.attrib) == ["ref", "faceName", "height", "bold", "italic", "underline",
                              "strikeout", "kind", "scale"]
    assert f.get("ref") == "style:NormalTextFont" and f.get("height") == "14"
    assert f.get("bold") == "true" and f.get("kind") == "StyleItem"
    assert len(f) == 0


def test_picture_ref_block(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "ФормаКнопкаЗакрыть", {"representation": "Picture",
                                           "picture": "common:примКнопкаНайти"})
    pic = _el(work, "ФормаКнопкаЗакрыть").find("lf:Picture", NS)
    assert pic.find("xr:Ref", NS).text == "CommonPicture.примКнопкаНайти"
    assert pic.find("xr:LoadTransparent", NS).text == "false"


def test_form_root_command_bar(tmp_path):
    work = _copy(tmp_path)
    set_style(work, ".", {"commandBarLocation": "None"})
    root = cfg.load(work).getroot()
    assert root.find("lf:CommandBarLocation", NS).text == "None"
    t = _tags(root)
    assert t.index("CommandBarLocation") < t.index("AutoCommandBar")


def test_byte_perfect_only_insertions_bom_crlf_tabs(tmp_path):
    work = _copy(tmp_path)
    before = _lines(work)
    set_style(work, "ИсторияИзмененийВерсияМодуля", {"titleTextColor": "rgb:74,74,74",
                                                     "font": "style:NormalTextFont,height=8"})
    raw = work.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.count(b"\n") == raw.count(b"\r\n")
    after = _lines(work)
    assert _only_added(before, after)
    assert len(after) - len(before) == 2
    added = [l for l in after if "#4A4A4A" in l or 'height="8"' in l]
    assert len(added) == 2 and all(l.startswith("\t") and " " not in l[:l.index("<")] for l in added)


def test_idempotent_and_remove(tmp_path):
    work = _copy(tmp_path)
    raw = work.read_bytes()
    set_style(work, "ОбщаяИнформация", {"textColor": "style:примЦветБелый"})
    once = work.read_bytes()
    set_style(work, "ОбщаяИнформация", {"textColor": "style:примЦветБелый"})
    assert work.read_bytes() == once
    set_style(work, "ОбщаяИнформация", {"textColor": ""})
    assert work.read_bytes() == raw


def test_unknown_property_rejected(tmp_path):
    work = _copy(tmp_path)
    raw = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        set_style(work, "ОбщаяИнформация", {"zzz": "1"})
    assert work.read_bytes() == raw


def test_explicit_value_written_even_if_default(tmp_path):
    """Заданное значение пишется явно, даже если совпало с таблицей умолчаний (она не привязана к версии)."""
    work = _copy(tmp_path)
    set_style(work, "ГруппаИсторияИзменений", {"united": "true"})
    assert _el(work, "ГруппаИсторияИзменений").find("lf:United", NS).text == "true"


def test_omit_defaults_like_platform_dump(tmp_path):
    """С omit_defaults — как выгрузка Конфигуратора: United=true у группы снимается."""
    work = _copy(tmp_path)
    set_style(work, "ГруппаИсторияИзменений", {"united": "false"})
    assert _el(work, "ГруппаИсторияИзменений").find("lf:United", NS).text == "false"
    set_style(work, "ГруппаИсторияИзменений", {"united": "true"}, omit_defaults=True)
    assert _el(work, "ГруппаИсторияИзменений").find("lf:United", NS) is None

def test_pure_style_font_token_no_scale(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "ОбщаяИнформация", {"font": "style:примШрифтПодпись"})
    f = _el(work, "ОбщаяИнформация").find("lf:Font", NS)
    assert dict(f.attrib) == {"ref": "style:примШрифтПодпись", "kind": "StyleItem"}