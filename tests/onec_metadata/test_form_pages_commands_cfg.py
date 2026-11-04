# DISCIPLINE_ALLOW_TEST_EDIT: новый red-набор TDD — страницы, команды, кнопки, динамический список (Конфигуратор)
"""Структура формы Конфигуратора: группа страниц и страница, команда формы, кнопка, реквизит «динамический список».
Эталоны — фрагменты XML-выгрузки Конфигуратора (8.3.27) реальных форм СНГ, имена заменены на нейтральные:
страница — элемент + ExtendedTooltip; кнопка — Type, CommandName, ExtendedTooltip; команда — Title, ToolTip, Action;
динамический список — Type cfg:DynamicList, Settings xsi:type=DynamicList, ListSettings из четырёх частей."""
import re
from pathlib import Path

import pytest
from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form_structure import add_button, add_command, add_group, add_list, move_item

LF = "{http://v8.1c.ru/8.3/xcf/logform}"
HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\r\n<Form xmlns="http://v8.1c.ru/8.3/xcf/logform" '
        'xmlns:app="http://v8.1c.ru/8.2/managed-application/core" xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" '
        'xmlns:dcscor="http://v8.1c.ru/8.1/data-composition-system/core" '
        'xmlns:dcsset="http://v8.1c.ru/8.1/data-composition-system/settings" xmlns:ent="http://v8.1c.ru/8.1/data/enterprise" '
        'xmlns:lf="http://v8.1c.ru/8.2/managed-application/logform" xmlns:style="http://v8.1c.ru/8.1/data/ui/style" '
        'xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" xmlns:v8="http://v8.1c.ru/8.1/data/core" '
        'xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" xmlns:web="http://v8.1c.ru/8.1/data/ui/colors/web" '
        'xmlns:win="http://v8.1c.ru/8.1/data/ui/colors/windows" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="2.20">\r\n')
FORM = HEAD + (
    '\t<AutoCommandBar name="ФормаКоманднаяПанель" id="-1"/>\r\n'
    '\t<ChildItems>\r\n'
    '\t\t<InputField name="Наименование" id="1">\r\n'
    '\t\t\t<DataPath>Объект.Description</DataPath>\r\n'
    '\t\t\t<ContextMenu name="НаименованиеКонтекстноеМеню" id="2"/>\r\n'
    '\t\t\t<ExtendedTooltip name="НаименованиеРасширеннаяПодсказка" id="3"/>\r\n'
    '\t\t</InputField>\r\n'
    '\t</ChildItems>\r\n'
    '\t<Attributes>\r\n'
    '\t\t<Attribute name="Объект" id="1">\r\n'
    '\t\t\t<Type>\r\n\t\t\t\t<v8:Type>cfg:CatalogObject.Пример</v8:Type>\r\n\t\t\t</Type>\r\n'
    '\t\t\t<MainAttribute>true</MainAttribute>\r\n'
    '\t\t</Attribute>\r\n'
    '\t</Attributes>\r\n'
    '</Form>')


@pytest.fixture
def form(tmp_path):
    p = tmp_path / "Form.xml"
    p.write_bytes(b"\xef\xbb\xbf" + FORM.encode("utf-8"))
    return p


def _el(p, name):
    return cfg.load(p).getroot().xpath(".//*[@name=$n]", n=name)[0]


def _shape(el):
    """Теги потомков по порядку, без id и текста заголовков: сверка шаблона."""
    return [etree.QName(c).localname for c in el.iter() if isinstance(c.tag, str)]


def test_pages_and_page_template(form):
    add_group(form, "ГруппаСтраницы", kind="Pages")
    add_group(form, "СтраницаОсновное", parent="ГруппаСтраницы", kind="Page")
    move_item(form, "Наименование", parent="СтраницаОсновное")
    pages = _el(form, "ГруппаСтраницы")
    assert etree.QName(pages).localname == "Pages"
    assert [etree.QName(c).localname for c in pages] == ["ExtendedTooltip", "ChildItems"]
    page = _el(form, "СтраницаОсновное")
    assert etree.QName(page).localname == "Page"
    assert [etree.QName(c).localname for c in page] == ["ExtendedTooltip", "ChildItems"]
    assert page.find(LF + "Behavior") is None            # Behavior только у обычной группы
    assert _el(form, "Наименование").getparent().getparent() is not None


def test_page_only_inside_pages(form):
    with pytest.raises(OpPreconditionError):
        add_group(form, "СтраницаВКорне", kind="Page")
    add_group(form, "ГруппаСтраницы", kind="Pages")
    with pytest.raises(OpPreconditionError):
        add_group(form, "ОбычнаяВСтраницах", parent="ГруппаСтраницы")
    with pytest.raises(OpPreconditionError):
        add_group(form, "Странная", kind="Popup")


def test_command_and_button_like_configurator(form):
    add_command(form, "ЗапуститьОбмен", {"ru": "Запустить обмен", "en": "Run exchange"})
    cmds = cfg.load(form).getroot().find(LF + "Commands")
    assert cmds is not None
    # Commands после Attributes (порядок формы Конфигуратора)
    tags = [etree.QName(c).localname for c in cfg.load(form).getroot() if isinstance(c.tag, str)]
    assert tags.index("Commands") == tags.index("Attributes") + 1
    cmd = cmds.find(LF + "Command")
    assert cmd.get("name") == "ЗапуститьОбмен" and cmd.get("id") == "1"
    assert [etree.QName(c).localname for c in cmd] == ["Title", "ToolTip", "Action"]
    assert cmd.findtext(LF + "Action") == "ЗапуститьОбмен"
    add_button(form, "ЗапуститьОбменКнопка", "Form.Command.ЗапуститьОбмен")
    btn = _el(form, "ЗапуститьОбменКнопка")
    assert [etree.QName(c).localname for c in btn] == ["Type", "CommandName", "ExtendedTooltip"]
    assert btn.findtext(LF + "Type") == "UsualButton"
    assert btn.findtext(LF + "CommandName") == "Form.Command.ЗапуститьОбмен"
    assert int(btn.get("id")) > 3                          # свежий id в пространстве элементов


def test_button_guards(form):
    with pytest.raises(OpPreconditionError):
        add_button(form, "Кнопка", "Form.Command.Нет")     # команды формы нет
    with pytest.raises(OpPreconditionError):
        add_button(form, "Кнопка", "БезТочки")
    add_button(form, "КнопкаОбъекта", "Catalog.Пример.Command.Обновить")   # команда объекта - без проверки формы
    add_command(form, "Ок", {"ru": "Ок"})
    with pytest.raises(OpPreconditionError):
        add_command(form, "Ок", {"ru": "Ок"})


def test_dynamic_list_like_configurator(form):
    add_list(form, "СписокЗаявок", "Catalog.Пример")
    attr = [a for a in cfg.load(form).getroot().find(LF + "Attributes") if a.get("name") == "СписокЗаявок"][0]
    assert attr.get("id") == "2"
    assert attr.find(LF + "Type")[0].text == "cfg:DynamicList"
    s = attr.find(LF + "Settings")
    assert s.get("{http://www.w3.org/2001/XMLSchema-instance}type") == "DynamicList"
    assert [etree.QName(c).localname for c in s] == ["ManualQuery", "DynamicDataRead", "MainTable", "ListSettings"]
    assert s.findtext(LF + "MainTable") == "Catalog.Пример"
    ls = [etree.QName(c).localname for c in s.find(LF + "ListSettings")]
    assert ls == ["filter", "order", "conditionalAppearance", "itemsViewMode", "itemsUserSettingID"]
    text = form.read_bytes().decode("utf-8-sig")
    assert len(set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text))) == 4
    with pytest.raises(OpPreconditionError):
        add_list(form, "Второй", "Catalog.Пример", main=True)     # основной реквизит уже есть
    with pytest.raises(OpPreconditionError):
        add_list(form, "Плохой", "Пример")


def test_table_may_share_name_with_list_attribute(form):
    """Как у типовой формы списка: реквизит «Список» и таблица «Список» - разные пространства имён."""
    from onec_metadata.ops.form_structure import add_table
    add_list(form, "Список", "Catalog.Пример")
    add_table(form, "Список", "Список")
    assert etree.QName(_el(form, "Список")).localname in ("Attribute", "Table")
    with pytest.raises(OpPreconditionError):
        add_table(form, "Список", "Список")                  # а вот второй элемент с тем же именем - конфликт


def test_form_event_like_configurator(form):
    from onec_metadata.ops.form_structure import add_event
    add_event(form, "OnCreateAtServer", "ПриСозданииНаСервере")
    root = cfg.load(form).getroot()
    tags = [etree.QName(c).localname for c in root if isinstance(c.tag, str)]
    assert tags.index("Events") == tags.index("ChildItems") - 1      # Events перед ChildItems (порядок формы)
    ev = root.find(LF + "Events").find(LF + "Event")
    assert ev.get("name") == "OnCreateAtServer" and ev.text == "ПриСозданииНаСервере"
    with pytest.raises(OpPreconditionError):
        add_event(form, "OnCreateAtServer", "Другой")
    with pytest.raises(OpPreconditionError):
        add_event(form, "ПриОткрытии", "ПриОткрытии")


def test_checkbox_like_configurator_and_remove(form):
    from onec_metadata.ops.form_structure import add_checkbox, remove_item
    from onec_metadata.ops.form_style import set_style
    add_group(form, "ГруппаФлаги")
    add_checkbox(form, "Включено", "Объект.Включено", parent="ГруппаФлаги")
    set_style(form, "Включено", {"CheckBoxType": "Switcher", "TitleLocation": "Right"})
    cb = _el(form, "Включено")
    assert [etree.QName(c).localname for c in cb] == ["DataPath", "TitleLocation", "CheckBoxType",
                                                       "ContextMenu", "ExtendedTooltip"]
    assert cb.findtext(LF + "CheckBoxType") == "Switcher"
    remove_item(form, "Включено")
    grp = _el(form, "ГруппаФлаги")
    assert grp.find(LF + "ChildItems") is None                 # опустевшая группа - без ChildItems
    with pytest.raises(OpPreconditionError):
        remove_item(form, "Нет")
    add_checkbox(form, "Флаг2", "Объект.Флаг2", parent="ГруппаФлаги")
    before = form.read_bytes()
    with pytest.raises(OpPreconditionError):
        remove_item(form, "Флаг2РасширеннаяПодсказка")         # служебная часть элемента — не удаляем
    assert form.read_bytes() == before


def test_crlf_and_bom_preserved(form):
    add_group(form, "ГруппаСтраницы", kind="Pages")
    add_command(form, "К", {"ru": "К"})
    add_list(form, "Л", "Catalog.Пример")
    raw = form.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_review_guards_move_pages_command_ref_list_source(form):
    """move не обходит правило страниц; ссылка на команду и основная таблица разбираются по сегментам."""
    from onec_metadata.ops.form_structure import add_button, add_list, move_item
    add_group(form, "Страницы", kind="Pages")
    add_group(form, "Стр1", parent="Страницы", kind="Page")
    add_group(form, "Обычная")
    before = form.read_bytes()
    for bad_move in (("Обычная", "Страницы"), ("Стр1", ".")):
        with pytest.raises(OpPreconditionError):
            move_item(form, *bad_move)
    for cmd in ("foo.bar", "Catalog..Command.X", "Catalog.Товар.X", "CommonCommand.", "Form.Command"):
        with pytest.raises(OpPreconditionError):
            add_button(form, "Кн", cmd)
    for table in (".Товары", "Catalog.", "Unknown.Товары", "Catalog.Тов ары", "Catalog.A.B"):
        with pytest.raises(OpPreconditionError):
            add_list(form, "Список2", table)
    assert form.read_bytes() == before
    add_button(form, "КнОбщая", "CommonCommand.Печать")
    add_button(form, "КнОбъекта", "Catalog.Товары.Command.Открыть")
