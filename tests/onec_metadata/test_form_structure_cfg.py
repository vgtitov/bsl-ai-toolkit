# DISCIPLINE_ALLOW_TEST_EDIT: новый red-набор TDD — структура формы Конфигуратора (группы, надписи, перенос)
"""Структура формы в формате Конфигуратора. Главный тест — golden: форма рабочего места, выгруженная САМИМ EDT в XML
(`form_structure_cfg_golden.xml`). Независимым кодом теста из неё убираются добавленные группы и надписи,
затем движок собирает их заново (add_group / add_decoration / move_item / set_title / set_style).
Результат = выгрузка EDT, кроме номеров id (при другом порядке сборки они законно другие)."""
import copy
import re
import shutil
from pathlib import Path

import pytest
from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form_structure import (add_column, add_column_group, add_decoration, add_group,
                                              add_table, move_item, set_title)
from onec_metadata.ops.form_style import set_style

FIX = Path(__file__).parent / "fixtures"
GOLDEN = FIX / "form_structure_cfg_golden.xml"
LF = "{http://v8.1c.ru/8.3/xcf/logform}"
SKIP = {"ChildItems", "ExtendedTooltip", "ContextMenu", "Title", "Events"}
REBUILT = ["ГруппаСтраница", "ГруппаШапка", "ДекорацияЗаголовок", "ДекорацияКоличество",
           "ГруппаСписок", "ДекорацияПодсказка", "ГруппаПоискПоля"]


# ---------- независимые помощники теста (не код движка) ----------

def _by_name(root, name):
    return root.xpath(".//*[@name=$n]", n=name)[0]


def _unwrap(el):
    """Вынести детей группы на её место и удалить группу."""
    parent = el.getparent()
    idx = parent.index(el)
    kids = el.find(LF + "ChildItems")
    for i, k in enumerate(list(kids) if kids is not None else []):
        parent.insert(idx + i, k)
    parent.remove(el)


def _pretty(el, ws="\r\n"):
    kids = [c for c in el if isinstance(c.tag, str)]
    if not kids:
        return
    el.text = ws + "\t"
    for i, c in enumerate(kids):
        c.tail = ws + "\t" if i < len(kids) - 1 else ws
        _pretty(c, ws + "\t")


def _make_base(tmp_path):
    doc = cfg.load(GOLDEN)
    root = doc.getroot()
    _unwrap(_by_name(root, "ГруппаСтраница"))
    for n in ("ГруппаШапка", "ДекорацияПодсказка"):
        el = _by_name(root, n)
        el.getparent().remove(el)
    _unwrap(_by_name(root, "ГруппаПоискПоля"))
    _unwrap(_by_name(root, "ГруппаСписок"))
    _pretty(root)
    base = tmp_path / "Form.xml"
    cfg.save(doc, base)
    return base


def _style_value(el):
    """Значение свойства из выгрузки -> синтаксис set_style."""
    tag = etree.QName(el).localname
    if tag.endswith("Font"):
        v = el.get("ref")
        for a, k in (("faceName", "face"), ("height", "height")):
            if el.get(a):
                v += f",{k}={el.get(a)}"
        v += "".join(f",{f}" for f in ("bold", "italic", "underline", "strikeout") if el.get(f) == "true")
        return v
    if el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
        return "xsi:nil"
    t = el.text or ""
    if tag.endswith("Color") and t.startswith("#"):
        return "rgb:" + ",".join(str(int(t[i:i + 2], 16)) for i in (1, 3, 5))
    return t


def _norm(raw: bytes) -> bytes:
    return re.sub(rb' id="-?\d+"', b' id=""', raw)


def _titles(el):
    t = el.find(LF + "Title")
    return {i.findtext("{http://v8.1c.ru/8.1/data/core}lang"): i.findtext("{http://v8.1c.ru/8.1/data/core}content")
            for i in t} if t is not None else {}


# ---------- тесты ----------

def test_golden_rebuild_equals_edt_export(tmp_path):
    work = _make_base(tmp_path)
    golden_root = cfg.load(GOLDEN).getroot()

    add_group(work, "ГруппаСтраница", parent=".", position="first")
    move_item(work, "ГруппаОтбор", parent="ГруппаСтраница")
    add_group(work, "ГруппаСписок", parent="ГруппаСтраница")
    move_item(work, "Заказы", parent="ГруппаСписок")
    add_decoration(work, "ДекорацияПодсказка", parent="ГруппаСписок")
    move_item(work, "ГруппаКнопок", parent="ГруппаСтраница")
    add_group(work, "ГруппаШапка", parent="ГруппаСтраница", position="first")
    add_decoration(work, "ДекорацияЗаголовок", parent="ГруппаШапка")
    add_decoration(work, "ДекорацияКоличество", parent="ГруппаШапка")
    add_group(work, "ГруппаПоискПоля", parent="ГруппаОтбор")
    for n in ("ПоискПоНомеру", "ОтборКлиент", "КнопкаНайти"):
        move_item(work, n, parent="ГруппаПоискПоля")

    for n in REBUILT:                                   # заголовки и свойства — как в выгрузке EDT
        g = _by_name(golden_root, n)
        if _titles(g):
            set_title(work, n, _titles(g))
        props = {etree.QName(p).localname: _style_value(p) for p in g
                 if isinstance(p.tag, str) and etree.QName(p).localname not in SKIP}
        set_style(work, n, props)

    out = work.read_bytes()
    assert _norm(out) == _norm(GOLDEN.read_bytes())
    # id элементов уникальны в своём пространстве (у реквизитов и команд формы — своя нумерация)
    items = cfg.load(work).getroot().find(LF + "ChildItems")
    ids = [int(x) for x in items.xpath(".//@id")]
    assert len(ids) == len(set(ids))


def test_group_and_label_templates(tmp_path):
    work = _make_base(tmp_path)
    add_group(work, "Г1", parent="ГруппаОтбор")
    add_decoration(work, "Н1", parent="Г1")
    root = cfg.load(work).getroot()
    g = _by_name(root, "Г1")
    assert [etree.QName(c).localname for c in g] == ["Behavior", "ExtendedTooltip", "ChildItems"]
    n = _by_name(root, "Н1")
    assert [c.get("name") for c in n] == ["Н1КонтекстноеМеню", "Н1РасширеннаяПодсказка"]
    gid = int(g.get("id"))
    assert int(n.get("id")) > gid
    raw = work.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\n") == raw.count(b"\r\n")


def test_empty_group_has_no_childitems_after_move_out(tmp_path):
    work = _make_base(tmp_path)
    add_group(work, "Г1", parent=".")
    add_decoration(work, "Н1", parent="Г1")
    move_item(work, "Н1", parent=".")
    g = _by_name(cfg.load(work).getroot(), "Г1")
    assert g.find(LF + "ChildItems") is None


def test_label_title_formatted_false(tmp_path):
    work = _make_base(tmp_path)
    add_decoration(work, "Н1", parent=".")
    set_title(work, "Н1", {"ru": "Текст", "en": "Text"})
    t = _by_name(cfg.load(work).getroot(), "Н1").find(LF + "Title")
    assert t.get("formatted") == "false"


def test_rejects(tmp_path):
    work = _make_base(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_group(work, "ГруппаОтбор")                     # уже есть
    with pytest.raises(OpPreconditionError):
        move_item(work, "ГруппаОтбор", parent="ПоискПоНомеру")   # поле не контейнер
    edt = tmp_path / "Form.form"
    edt.write_text("<x/>", encoding="utf-8")
    with pytest.raises(OpPreconditionError):
        add_group(edt, "Г1")


TABLE_SKIP = SKIP | {"AutoCommandBar", "SearchStringAddition", "ViewStatusAddition", "SearchControlAddition",
                     "DataPath"}


def _copy_props(work, golden_el):
    n = golden_el.get("name")
    if _titles(golden_el):
        set_title(work, n, _titles(golden_el))
    props = {etree.QName(p).localname: _style_value(p) for p in golden_el
             if isinstance(p.tag, str) and etree.QName(p).localname not in TABLE_SKIP}
    if etree.QName(golden_el).localname == "InputField":
        props.setdefault("EditMode", "Enter")   # нет в выгрузке = умолчание; шаблон колонки ставит EnterOnInput
    set_style(work, n, props)


def test_golden_table_rebuild_equals_edt_export(tmp_path):
    """Таблица и колонки собираются движком заново и совпадают с выгрузкой EDT (кроме id).
    Командная панель таблицы и обработчики событий — команды формы (бэклог): переносятся тестом как есть."""
    doc = cfg.load(GOLDEN)
    root = doc.getroot()
    gt = _by_name(root, "Заказы")
    golden_table = copy.deepcopy(gt)
    gt.getparent().remove(gt)
    _pretty(root)
    work = tmp_path / "Form.xml"
    cfg.save(doc, work)

    add_table(work, "Заказы", golden_table.findtext(LF + "DataPath"), parent="ГруппаСписок", position="first")
    cols = [c for c in golden_table.find(LF + "ChildItems")]
    for c in cols:
        add_column(work, c.get("name"), "Заказы", c.findtext(LF + "DataPath"))
    _copy_props(work, golden_table)
    for c in cols:
        _copy_props(work, c)

    wdoc = cfg.load(work)                                   # команды и события — из эталона (бэклог)
    wt = _by_name(wdoc.getroot(), "Заказы")
    old_bar = wt.find(LF + "AutoCommandBar")
    new_bar = golden_table.find(LF + "AutoCommandBar")
    new_bar.tail = old_bar.tail
    wt.replace(old_bar, new_bar)
    ev = golden_table.find(LF + "Events")
    ev.tail = wt.find(LF + "ChildItems").getprevious().tail
    wt.find(LF + "ChildItems").addprevious(ev)
    cfg.save(wdoc, work)

    assert _norm(work.read_bytes()) == _norm(GOLDEN.read_bytes())


def test_column_group_and_guards(tmp_path):
    work = _make_base(tmp_path)
    add_column_group(work, "ГруппаКолонок", parent="Заказы")
    add_column(work, "КолонкаВГруппе", "ГруппаКолонок", "Объект.Т.Реквизит")
    g = _by_name(cfg.load(work).getroot(), "ГруппаКолонок")
    assert [etree.QName(c).localname for c in g] == ["ExtendedTooltip", "ChildItems"]
    col = _by_name(cfg.load(work).getroot(), "КолонкаВГруппе")
    assert [etree.QName(c).localname for c in col] == ["DataPath", "EditMode", "ContextMenu", "ExtendedTooltip"]
    with pytest.raises(OpPreconditionError):
        add_column(work, "Колонка2", "ГруппаОтбор", "Объект.Т.Р")      # не таблица
    with pytest.raises(OpPreconditionError):
        add_group(work, "Г2", parent="Заказы")                         # группа — не в таблицу
    with pytest.raises(OpPreconditionError):
        move_item(work, "КолонкаВГруппе", parent=".")                  # колонку из таблицы не выносим


def test_table_template(tmp_path):
    work = _make_base(tmp_path)
    add_table(work, "Т1", "Объект.Товары", parent=".")
    t = _by_name(cfg.load(work).getroot(), "Т1")
    assert [etree.QName(c).localname for c in t] == ["DataPath", "ContextMenu", "AutoCommandBar", "ExtendedTooltip",
                                                     "SearchStringAddition", "ViewStatusAddition",
                                                     "SearchControlAddition"]
    s = t.find(LF + "SearchStringAddition")
    assert s.get("name") == "Т1СтрокаПоиска" and s.find(LF + "AdditionSource").findtext(LF + "Item") == "Т1"