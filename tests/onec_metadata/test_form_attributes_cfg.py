# DISCIPLINE_ALLOW_TEST_EDIT: новый набор - колонки реквизита формы, удаление реквизита и команды (Конфигуратор)
"""Реквизит формы «таблица значений»: колонки как у Конфигуратора (Title, Type с квалификаторами),
удаление колонки/реквизита/команды с защитами, заголовок команды. Эталон колонки - выгрузка формы СНГ 8.3.27."""
import pytest
from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops import form_attributes as fa
from tests.onec_metadata.test_form_pages_commands_cfg import HEAD

LF = "{http://v8.1c.ru/8.3/xcf/logform}"
FORM = HEAD + (
    '\t<ChildItems>\r\n'
    '\t\t<Button name="Кнопка" id="1">\r\n'
    '\t\t\t<Type>UsualButton</Type>\r\n'
    '\t\t\t<CommandName>Form.Command.Сформировать</CommandName>\r\n'
    '\t\t\t<ExtendedTooltip name="КнопкаРасширеннаяПодсказка" id="2"/>\r\n'
    '\t\t</Button>\r\n'
    '\t</ChildItems>\r\n'
    '\t<Attributes>\r\n'
    '\t\t<Attribute name="Таблица" id="1">\r\n'
    '\t\t\t<Title>\r\n\t\t\t\t<v8:item>\r\n\t\t\t\t\t<v8:lang>ru</v8:lang>\r\n\t\t\t\t\t<v8:content>Таблица</v8:content>\r\n'
    '\t\t\t\t</v8:item>\r\n\t\t\t</Title>\r\n'
    '\t\t\t<Type>\r\n\t\t\t\t<v8:Type>v8:ValueTable</v8:Type>\r\n\t\t\t</Type>\r\n'
    '\t\t\t<Columns>\r\n'
    '\t\t\t\t<Column name="Цена" id="1">\r\n'
    '\t\t\t\t\t<Title>\r\n\t\t\t\t\t\t<v8:item>\r\n\t\t\t\t\t\t\t<v8:lang>ru</v8:lang>\r\n'
    '\t\t\t\t\t\t\t<v8:content>Цена</v8:content>\r\n\t\t\t\t\t\t</v8:item>\r\n\t\t\t\t\t</Title>\r\n'
    '\t\t\t\t\t<Type>\r\n\t\t\t\t\t\t<v8:Type>xs:decimal</v8:Type>\r\n\t\t\t\t\t\t<v8:NumberQualifiers>\r\n'
    '\t\t\t\t\t\t\t<v8:Digits>10</v8:Digits>\r\n\t\t\t\t\t\t\t<v8:FractionDigits>2</v8:FractionDigits>\r\n'
    '\t\t\t\t\t\t\t<v8:AllowedSign>Any</v8:AllowedSign>\r\n\t\t\t\t\t\t</v8:NumberQualifiers>\r\n'
    '\t\t\t\t\t</Type>\r\n'
    '\t\t\t\t</Column>\r\n'
    '\t\t\t</Columns>\r\n'
    '\t\t</Attribute>\r\n'
    '\t\t<Attribute name="Лишний" id="2">\r\n'
    '\t\t\t<Type>\r\n\t\t\t\t<v8:Type>xs:boolean</v8:Type>\r\n\t\t\t</Type>\r\n'
    '\t\t</Attribute>\r\n'
    '\t</Attributes>\r\n'
    '\t<Commands>\r\n'
    '\t\t<Command name="Сформировать" id="1">\r\n'
    '\t\t\t<Title>\r\n\t\t\t\t<v8:item>\r\n\t\t\t\t\t<v8:lang>ru</v8:lang>\r\n\t\t\t\t\t<v8:content>Сформировать</v8:content>\r\n'
    '\t\t\t\t</v8:item>\r\n\t\t\t</Title>\r\n'
    '\t\t\t<ToolTip>\r\n\t\t\t\t<v8:item>\r\n\t\t\t\t\t<v8:lang>ru</v8:lang>\r\n\t\t\t\t\t<v8:content>Сформировать</v8:content>\r\n'
    '\t\t\t\t</v8:item>\r\n\t\t\t</ToolTip>\r\n'
    '\t\t\t<Action>Сформировать</Action>\r\n'
    '\t\t</Command>\r\n'
    '\t</Commands>\r\n'
    '</Form>')


@pytest.fixture
def form(tmp_path):
    p = tmp_path / "Form.xml"
    p.write_bytes(b"\xef\xbb\xbf" + FORM.encode("utf-8"))
    return p


def _column(p, name):
    return cfg.load(p).getroot().xpath(".//lf:Column[@name=$n]", namespaces=cfg.NS, n=name)[0]


def test_column_round_trip_and_shape(form):
    before = form.read_bytes()
    fa.add_attribute_column(form, "Таблица", "Точка", "String", {"ru": "Точка"})
    col = _column(form, "Точка")
    assert col.get("id") == "2"
    assert [etree.QName(c).localname for c in col.iter() if isinstance(c.tag, str)] == [
        "Column", "Title", "item", "lang", "content", "Type", "Type", "StringQualifiers", "Length", "AllowedLength"]
    text = form.read_bytes().decode("utf-8-sig")
    # отступы как у соседней колонки
    assert '\t\t\t\t<Column name="Точка" id="2">\r\n\t\t\t\t\t<Title>\r\n\t\t\t\t\t\t<v8:item>' in text
    assert '\t\t\t\t\t\t<v8:StringQualifiers>\r\n\t\t\t\t\t\t\t<v8:Length>0</v8:Length>' in text
    assert '\t\t\t\t\t</Type>\r\n\t\t\t\t</Column>\r\n\t\t\t</Columns>' in text
    fa.remove_attribute_column(form, "Таблица", "Точка")
    assert form.read_bytes() == before


def test_column_types_and_guards(form):
    fa.add_attribute_column(form, "Таблица", "Остаток", "Number:10", {"ru": "Остаток"})
    assert _column(form, "Остаток").findtext(".//{http://v8.1c.ru/8.1/data/core}FractionDigits") == "0"
    fa.add_attribute_column(form, "Таблица", "Доступен", "Boolean", {"ru": "Доступен"})
    with pytest.raises(OpPreconditionError):
        fa.add_attribute_column(form, "Таблица", "Доступен", "Boolean", {"ru": "x"})
    with pytest.raises(OpPreconditionError):
        fa.add_attribute_column(form, "Таблица", "Плохой", "Строка", {"ru": "x"})
    fa.remove_attribute_column(form, "Таблица", "Цена")
    fa.remove_attribute_column(form, "Таблица", "Остаток")
    with pytest.raises(OpPreconditionError):
        fa.remove_attribute_column(form, "Таблица", "Доступен")     # последняя колонка


def test_form_attribute_from_scratch(form):
    """Как пишет Конфигуратор: компоновщик настроек и список значений - Title + Type, отступы соседей."""
    fa.add_form_attribute(form, "КомпоновщикНастроек", "dcsset:SettingsComposer", {"ru": "Компоновщик настроек"})
    fa.add_form_attribute(form, "Адрес", "String", {"ru": "Адрес"})
    text = form.read_bytes().decode("utf-8-sig")
    assert ('\t\t<Attribute name="КомпоновщикНастроек" id="3">\r\n\t\t\t<Title>\r\n\t\t\t\t<v8:item>\r\n'
            '\t\t\t\t\t<v8:lang>ru</v8:lang>\r\n\t\t\t\t\t<v8:content>Компоновщик настроек</v8:content>\r\n'
            '\t\t\t\t</v8:item>\r\n\t\t\t</Title>\r\n\t\t\t<Type>\r\n\t\t\t\t<v8:Type>dcsset:SettingsComposer</v8:Type>\r\n'
            '\t\t\t</Type>\r\n\t\t</Attribute>\r\n') in text
    assert '<Attribute name="Адрес" id="4">' in text and '</Attribute>\r\n\t</Attributes>' in text
    assert b"\n" not in form.read_bytes().replace(b"\r\n", b"")
    with pytest.raises(OpPreconditionError):
        fa.add_form_attribute(form, "Адрес", "String", {})
    with pytest.raises(OpPreconditionError):
        fa.add_form_attribute(form, "Плохой", "НетТакого", {})


def test_remove_attribute_and_command(form):
    fa.remove_attribute(form, "Лишний")
    assert b'"\xd0\x9b\xd0\xb8\xd1\x88\xd0\xbd\xd0\xb8\xd0\xb9"' not in form.read_bytes()
    with pytest.raises(OpPreconditionError):
        fa.remove_command(form, "Сформировать")                    # на неё ссылается кнопка
    fa.set_command_title(form, "Сформировать", {"ru": "Проверить товар"})
    cmd = cfg.load(form).getroot().find(LF + "Commands")[0]
    assert cmd.findtext(".//{http://v8.1c.ru/8.1/data/core}content") == "Проверить товар"
    raw = form.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")
