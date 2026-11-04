# DISCIPLINE_ALLOW_TEST_EDIT: новый red-набор TDD — form set-style для EDT (.form)
"""set_style для EDT-формы (.form): оформление элемента в свойствах формы, а не кодом.

Свойство кладётся туда, где его пишет EDT: на уровень элемента или в его extInfo,
в позицию по порядку сериализации EDT (таблица выведена из корпуса форм ERP).
Byte-perfect: кроме добавленных/заменённых строк файл не меняется, CRLF сохраняется."""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form_style import set_style

FIX = Path(__file__).parent / "fixtures"


def _copy(tmp_path):
    work = tmp_path / "form_style_edt.form"
    shutil.copy(FIX / "form_style_edt.form", work)
    return work


def _lines(p):
    return p.read_bytes().decode("utf-8").split("\r\n")


def _no_lone_lf(work):
    raw = work.read_bytes()
    return raw.count(b"\n") == raw.count(b"\r\n")


def _item(work, name):
    root = cfg.load(work).getroot()
    hits = root.xpath(f".//items[name='{name}']")
    assert len(hits) == 1
    return hits[0]


def _only_added(before, after):
    """Все строки исходника остались на месте и в том же порядке (только вставки)."""
    it = iter(after)
    return all(any(line == x for x in it) for line in before)


def test_group_back_color_goes_to_extinfo_as_style_ref(tmp_path):
    work = _copy(tmp_path)
    before = _lines(work)
    set_style(work, "ГруппаСписок", {"backColor": "style:примЦветБелый"})
    grp = _item(work, "ГруппаСписок")
    ext = grp.find("extInfo")
    bc = ext.find("backColor")
    assert bc is not None
    assert bc.findtext("color") == "Style.примЦветБелый"
    assert bc.get(cfg.q("xsi", "type")) == "core:ColorRef"
    assert _no_lone_lf(work)
    # пустой <extInfo .../> раскрывается в <extInfo ...>…</extInfo>; прочие строки целы
    after = _lines(work)
    assert _only_added([l for l in before if "<extInfo" not in l],
                       [l for l in after if "<extInfo" not in l])


def test_order_follows_edt_serialization(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "ГруппаСписок", {"backColor": "style:примЦветБелый",
                                     "group": "Vertical",
                                     "representation": "WeakSeparation",
                                     "showTitle": "true"})
    ext = _item(work, "ГруппаСписок").find("extInfo")
    tags = [c.tag for c in ext]
    assert tags == ["group", "representation", "showTitle", "backColor"]


def test_item_level_title_font_inserted_before_tooltip(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "ГруппаСписок",
              {"titleFont": "style:NormalTextFont,face=Gilroy Bold,height=11"})
    grp = _item(work, "ГруппаСписок")
    tags = [c.tag for c in grp]
    assert tags.index("titleFont") < tags.index("extendedTooltip")
    assert tags.index("titleFont") > tags.index("title")
    tf = grp.find("titleFont")
    assert tf.findtext("font") == "Style.NormalTextFont"
    assert tf.findtext("faceName") == "Gilroy Bold"
    assert tf.findtext("height") == "11.0"
    assert tf.findtext("scale") == "100"
    assert tf.find("bold") is None


def test_font_flags_written_all_four_when_any_given(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "ДекорацияЗаголовок",
              {"font": "style:NormalTextFont,face=Gilroy,height=14,bold"})
    f = _item(work, "ДекорацияЗаголовок").find("font")
    assert [c.tag for c in f] == ["font", "faceName", "height", "bold", "italic",
                                  "underline", "strikeout", "scale"]
    assert f.findtext("bold") == "true" and f.findtext("italic") == "false"


def test_rgb_color_omits_zero_components(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "ДекорацияЗаголовок", {"textColor": "rgb:0,152,157"})
    tc = _item(work, "ДекорацияЗаголовок").find("textColor")
    assert tc.get(cfg.q("xsi", "type")) == "core:ColorDef"
    assert [c.tag for c in tc] == ["green", "blue"]


def test_replace_is_idempotent(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "ГруппаСписок", {"backColor": "style:примЦветБелый"})
    once = work.read_bytes()
    set_style(work, "ГруппаСписок", {"backColor": "style:примЦветБелый"})
    assert work.read_bytes() == once
    set_style(work, "ГруппаСписок", {"backColor": "style:примЦветФона"})
    ext = _item(work, "ГруппаСписок").find("extInfo")
    assert len(ext.findall("backColor")) == 1
    assert ext.find("backColor").findtext("color") == "Style.примЦветФона"


def test_form_root_command_bar_location(tmp_path):
    work = _copy(tmp_path)
    set_style(work, ".", {"commandBarLocation": "None"})
    root = cfg.load(work).getroot()
    tags = [c.tag for c in root]
    assert root.findtext("commandBarLocation") == "None"
    assert tags.index("commandBarLocation") > max(i for i, t in enumerate(tags) if t == "items")
    assert tags.index("commandBarLocation") < tags.index("autoCommandBar")


def test_only_insertions_byte_perfect(tmp_path):
    work = _copy(tmp_path)
    before = _lines(work)
    set_style(work, "ЗаказыСумма", {"titleTextColor": "rgb:74,74,74"})
    after = _lines(work)
    assert _only_added(before, after)
    assert len(after) - len(before) == 5   # <titleTextColor> + 3 компоненты + </titleTextColor>
    assert _no_lone_lf(work)


def test_unknown_property_rejected_file_untouched(tmp_path):
    work = _copy(tmp_path)
    raw = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        set_style(work, "ГруппаСписок", {"zzz": "1"})
    assert work.read_bytes() == raw


def test_unknown_item_rejected(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        set_style(work, "НетТакого", {"backColor": "style:примЦветБелый"})


def test_unknown_file_kind_rejected(tmp_path):
    p = tmp_path / "Form.txt"
    p.write_text("<x/>", encoding="utf-8")
    with pytest.raises(OpPreconditionError):
        set_style(p, "Группа", {"backColor": "style:примЦветБелый"})


def test_empty_value_removes_property(tmp_path):
    work = _copy(tmp_path)
    raw = work.read_bytes()
    set_style(work, "ЗаказыСумма", {"titleTextColor": "rgb:74,74,74"})
    set_style(work, "ЗаказыСумма", {"titleTextColor": ""})
    assert work.read_bytes() == raw


def test_button_picture_common_ref(tmp_path):
    work = _copy(tmp_path)
    set_style(work, "КнопкаВыбрать", {"representation": "Picture",
                                      "picture": "common:примКнопкаЗагрузитьВРабочееМесто",
                                      "shapeRepresentation": "None"})
    btn = _item(work, "КнопкаВыбрать")
    pic = btn.find("picture")
    assert pic.get(cfg.q("xsi", "type")) == "core:PictureRef"
    assert pic.findtext("picture") == "CommonPicture.примКнопкаЗагрузитьВРабочееМесто"
    tags = [c.tag for c in btn]
    assert tags.index("representation") < tags.index("picture") < tags.index("shapeRepresentation")


def test_picture_bad_prefix_rejected(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        set_style(work, "КнопкаВыбрать", {"picture": "примКнопка"})

def test_pure_style_font_token_no_scale(tmp_path):
    """Ссылка на шрифт-токен стиля без параметров: только <font>Style.X</font> (как в ERP)."""
    work = _copy(tmp_path)
    set_style(work, "ДекорацияЗаголовок", {"font": "style:примШрифтЗаголовокФормы"})
    f = _item(work, "ДекорацияЗаголовок").find("font")
    assert [c.tag for c in f] == ["font"]
    assert f.findtext("font") == "Style.примШрифтЗаголовокФормы"