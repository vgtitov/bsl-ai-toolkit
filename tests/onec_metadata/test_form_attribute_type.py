# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — смена типа СУЩЕСТВУЮЩЕГО реквизита формы
"""form.set_attribute_type: тип существующего реквизита формы, EDT (`Form.form`) и Конфигуратор (`Form.xml`).

Случай из практики (DCM-5994, 06.11.2026): EDT MCP создал форму обработки, а основной реквизит `Объект`
остался БЕЗ valueType — форма не открывается, а ни MCP, ни add-attribute (только новый реквизит) тип
существующему не задают. Операция ставит тип, создавая valueType/Type при отсутствии, и снимает
квалификаторы примитивов, которых в новом типе нет.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form import set_attribute_type

FIX = Path(__file__).parent / "fixtures"
NS_FORM = {"form": "http://g5.1c.ru/v8/dt/form"}


def _edt(tmp_path, text=None):
    work = tmp_path / "Form.form"
    shutil.copy(FIX / "form.form", work)
    if text is not None:
        work.write_text(text, encoding="utf-8")
    return work


def _attr(doc, name):
    hits = doc.xpath(f"/form:Form/attributes[name='{name}']", namespaces=NS_FORM)
    return hits[0] if hits else None


def test_edt_replace_drops_stale_qualifiers(tmp_path):
    work = _edt(tmp_path)
    set_attribute_type(work, "КодЯзыка", ["CatalogRef.Языки"])
    a = _attr(cfg.load(work), "КодЯзыка")
    assert a.xpath("valueType/types/text()") == ["CatalogRef.Языки"]
    assert a.xpath("valueType/stringQualifiers") == []


def test_edt_keeps_qualifier_of_remaining_primitive(tmp_path):
    work = _edt(tmp_path)
    set_attribute_type(work, "КодЯзыка", ["String", "CatalogRef.Языки"])
    a = _attr(cfg.load(work), "КодЯзыка")
    assert a.xpath("valueType/types/text()") == ["String", "CatalogRef.Языки"]
    assert a.xpath("valueType/stringQualifiers/length/text()") == ["10"]


def test_edt_creates_missing_value_type_after_id(tmp_path):
    """Реквизит без valueType (как создал EDT MCP): блок создаётся сразу после <id>, с отступами EDT."""
    src = (FIX / "form.form").read_text(encoding="utf-8")
    start = src.index("<valueType>\n      <types>DynamicList</types>\n    </valueType>\n    ")
    end = src.index("<view>", start)
    work = _edt(tmp_path, src[:start] + src[end:])
    assert _attr(cfg.load(work), "Список").xpath("valueType") == []

    set_attribute_type(work, "Список", ["DataProcessorObject.АРМ"])

    text = work.read_text(encoding="utf-8")
    assert ("<id>1</id>\n    <valueType>\n      <types>DataProcessorObject.АРМ</types>\n"
            "    </valueType>\n    <view>") in text
    a = _attr(cfg.load(work), "Список")
    assert [c.tag for c in a][:4] == ["name", "id", "valueType", "view"]


def test_edt_missing_attribute_raises(tmp_path):
    work = _edt(tmp_path)
    with pytest.raises(OpPreconditionError):
        set_attribute_type(work, "НетТакого", ["String"])


def test_edt_rejects_garbage_type(tmp_path):
    work = _edt(tmp_path)
    with pytest.raises(OpPreconditionError):
        set_attribute_type(work, "КодЯзыка", ["Catalog Ref"])


def test_cfg_replace_main_attribute_type(tmp_path):
    work = tmp_path / "Form.xml"
    shutil.copy(FIX / "form.xml", work)
    set_attribute_type(work, "Объект", ["cfg:DataProcessorObject.АРМ"])
    doc = cfg.load(work)
    types = doc.getroot().xpath("lf:Attributes/lf:Attribute[@name='Объект']/lf:Type/v8:Type/text()",
                                namespaces=cfg.NS)
    assert types == ["cfg:DataProcessorObject.АРМ"]
    text = work.read_text(encoding="utf-8-sig")
    assert "\t\t\t<Type>\n\t\t\t\t<v8:Type>cfg:DataProcessorObject.АРМ</v8:Type>\n\t\t\t</Type>" in text


def test_cfg_creates_missing_type(tmp_path):
    work = tmp_path / "Form.xml"
    src = (FIX / "form.xml").read_text(encoding="utf-8-sig")
    src = src.replace("\t\t\t<Type>\n\t\t\t\t<v8:Type>cfg:CatalogObject.Товары</v8:Type>\n\t\t\t</Type>\n", "", 1)
    work.write_text(src, encoding="utf-8")
    set_attribute_type(work, "Объект", ["cfg:CatalogObject.Товары"])
    assert "\t\t\t<Type>\n\t\t\t\t<v8:Type>cfg:CatalogObject.Товары</v8:Type>\n\t\t\t</Type>\n" \
        "\t\t\t<MainAttribute>" in work.read_text(encoding="utf-8-sig")


def test_save_keeps_escaped_quotes_in_text(tmp_path):
    """EDT пишет кавычку в тексте как &quot;. lxml при разборе ее раскрывает - сохранение обязано вернуть
    как было, иначе любая правка движка дает diff-шум в чужих заголовках (найдено на DCM-5994)."""
    src = (FIX / "form.form").read_text(encoding="utf-8")
    src = src.replace("<name>КодЯзыка</name>", "<name>КодЯзыка</name>\n    <!--x-->", 1)
    marker = '<value>Код &quot;языка&quot; и &lt;тег&gt;</value>'
    src = src.replace("<!--x-->", "<title><key>ru</key>" + marker + "</title>", 1)
    work = _edt(tmp_path, src)
    set_attribute_type(work, "Список", ["DynamicList"])
    out = work.read_text(encoding="utf-8")
    assert marker in out
    assert out == src


def test_save_mixed_quotes_round_trip(tmp_path):
    """Смешанный исходник: в одном тексте &quot;, в другом обычная кавычка - каждая остается как была."""
    src = (FIX / "form.form").read_text(encoding="utf-8")
    block = ('<title><key>ru</key><value>A &quot;B&quot;</value></title>'
             '<title><key>en</key><value>C "D"</value></title>')
    src = src.replace("<name>КодЯзыка</name>", "<name>КодЯзыка</name>\n    " + block, 1)
    work = _edt(tmp_path, src)
    set_attribute_type(work, "Список", ["DynamicList"])
    assert work.read_text(encoding="utf-8") == src


def test_save_char_ref_of_sentinel_is_not_turned_into_quote(tmp_path):
    """Если служебный символ уже есть в исходнике ссылкой, защита кавычек отключается - данные не портятся."""
    src = (FIX / "form.form").read_text(encoding="utf-8")
    src = src.replace("<name>КодЯзыка</name>",
                      "<name>КодЯзыка</name>\n    <title><key>ru</key><value>x&#x0e000;y</value></title>", 1)
    work = _edt(tmp_path, src)
    set_attribute_type(work, "Список", ["DynamicList"])
    out = cfg.load(work)
    assert out.getroot().xpath("//attributes[name='КодЯзыка']/title/value/text()") == ["xy"]


def test_edt_binary_qualifier_dropped_under_either_name(tmp_path):
    src = (FIX / "form.form").read_text(encoding="utf-8")
    src = src.replace("<types>String</types>\n      <stringQualifiers>\n        <length>10</length>\n      </stringQualifiers>",
                      "<types>BinaryData</types>\n      <binaryDataQualifiers>\n        <length>10</length>\n"
                      "      </binaryDataQualifiers>", 1)
    work = _edt(tmp_path, src)
    set_attribute_type(work, "КодЯзыка", ["String"])
    a = _attr(cfg.load(work), "КодЯзыка")
    assert a.xpath("valueType/binaryDataQualifiers") == []
    assert a.xpath("valueType/types/text()") == ["String"]


def test_save_quot_kept_even_if_first_private_char_is_taken(tmp_path):
    """Занятый служебный символ не отключает защиту: берется следующий свободный."""
    src = (FIX / "form.form").read_text(encoding="utf-8")
    block = '<title><key>ru</key><value>x&#xE000;y A &quot;B&quot;</value></title>'
    src = src.replace("<name>КодЯзыка</name>", "<name>КодЯзыка</name>\n    " + block, 1)
    work = _edt(tmp_path, src)
    set_attribute_type(work, "Список", ["DynamicList"])
    out = work.read_text(encoding="utf-8")
    assert "A &quot;B&quot;" in out
    assert "xy" in out    # данные с занятым символом не испорчены


def test_duplicate_types_are_collapsed(tmp_path):
    work = _edt(tmp_path)
    set_attribute_type(work, "КодЯзыка", ["String", "String", "CatalogRef.Языки"])
    assert _attr(cfg.load(work), "КодЯзыка").xpath("valueType/types/text()") == ["String", "CatalogRef.Языки"]
