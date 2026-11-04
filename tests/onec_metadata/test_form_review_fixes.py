# DISCIPLINE_ALLOW_TEST_EDIT: новые тесты на замечания ревью PR 42 (set_title, id, гонка записи, шрифт)
"""Замечания ревью к формам Конфигуратора: потеря языков в set_title, занятые id вне ChildItems,
затирание чужой правки при записи, неверные числа шрифта."""
import shutil
from pathlib import Path

import pytest
from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form_structure import add_decoration, add_group, set_title
from onec_metadata.ops.form_style import set_style

FIX = Path(__file__).parent / "fixtures"
LF = "{http://v8.1c.ru/8.3/xcf/logform}"
V8 = "{http://v8.1c.ru/8.1/data/core}"


def _form(tmp_path):
    work = tmp_path / "Form.xml"
    shutil.copy(FIX / "form_structure_cfg_golden.xml", work)
    return work


def _langs(work, name):
    el = cfg.load(work).getroot().xpath(".//*[@name=$n]", n=name)[0]
    return {i.findtext(V8 + "lang"): i.findtext(V8 + "content") for i in el.find(LF + "Title")}


def test_set_title_keeps_other_languages(tmp_path):
    work = _form(tmp_path)
    add_decoration(work, "Н1", parent=".")
    set_title(work, "Н1", {"ru": "Один", "en": "One", "kk": "Бір"})
    set_title(work, "Н1", {"ru": "Новый"})
    assert _langs(work, "Н1") == {"ru": "Новый", "en": "One", "kk": "Бір"}


def test_set_title_replace_all_is_explicit(tmp_path):
    work = _form(tmp_path)
    add_decoration(work, "Н1", parent=".")
    set_title(work, "Н1", {"ru": "Один", "en": "One"})
    set_title(work, "Н1", {"ru": "Новый"}, replace_all=True)
    assert _langs(work, "Н1") == {"ru": "Новый"}
    t = cfg.load(work).getroot().xpath(".//*[@name='Н1']")[0].find(LF + "Title")
    assert t.get("formatted") == "false"


def test_new_id_does_not_collide_with_ids_outside_child_items(tmp_path):
    work = _form(tmp_path)
    doc = cfg.load(work)
    root = doc.getroot()
    outside = etree.SubElement(root, LF + "AutoCommandBar", name="Служебная", id="9999")
    cfg.save(doc, work)
    add_group(work, "НоваяГруппа", parent=".")
    el = cfg.load(work).getroot().xpath(".//*[@name='НоваяГруппа']")[0]
    assert int(el.get("id")) > 9999


def test_save_refuses_to_overwrite_concurrent_change(tmp_path):
    work = _form(tmp_path)
    doc = cfg.load(work)                                  # процесс А прочитал
    add_group(work, "Параллельная", parent=".")           # процесс Б успел записать
    with pytest.raises(OpPreconditionError, match="изменён другим процессом"):
        cfg.save(doc, work)                               # А не затирает правку Б
    assert cfg.load(work).getroot().xpath(".//*[@name='Параллельная']")


@pytest.mark.parametrize("font", ["style:NormalTextFont,height=nan", "style:NormalTextFont,height=-3",
                                  "style:NormalTextFont,height=inf", "style:NormalTextFont,face=A,scale=0"])
def test_font_rejects_non_finite_or_non_positive(tmp_path, font):
    work = tmp_path / "Form.xml"
    shutil.copy(FIX / "form_style_cfg.xml", work)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        set_style(work, "ОбщаяИнформация", {"font": font})
    assert work.read_bytes() == before


def test_two_saves_of_one_doc_are_not_a_conflict(tmp_path):
    work = _form(tmp_path)
    doc = cfg.load(work)
    cfg.save(doc, work)
    cfg.save(doc, work)
    assert not list(tmp_path.glob("*.tmp"))
