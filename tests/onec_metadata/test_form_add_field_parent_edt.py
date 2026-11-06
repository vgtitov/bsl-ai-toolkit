"""form.add_field с parent/position на EDT-форме: поле-колонка внутри таблицы (или группы).

В реальной форме колонка таблицы — <items xsi:type=form:FormField> ВНУТРИ items таблицы,
перед собственными свойствами таблицы (visible, dataPath ...). Образец — поле того же
контейнера; заголовок и обработчики образца не наследуются.
"""
import copy
import difflib
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form import add_field

FIX = Path(__file__).parent / "fixtures"


def _copy(tmp_path):
    work = tmp_path / "Form.form"
    shutil.copy(FIX / "form_valuetable_edt.form", work)
    return work


def _table(doc):
    return doc.getroot().xpath("items[name='Товары']")[0]


def test_add_field_into_table_last(tmp_path):
    work = _copy(tmp_path)
    add_field(work, "ТоварыЦена", "Товары.Цена", parent="Товары")
    doc = cfg.load(work)
    table = _table(doc)
    cols = table.findall("items")
    assert [c.findtext("name") for c in cols] == ["ТоварыНоменклатура", "ТоварыКоличество", "ТоварыЦена"]
    new = cols[-1]
    assert new.xpath("dataPath/segments/text()") == ["Товары.Цена"]
    assert new.xpath("title") == [] and new.xpath("handlers") == []
    assert new.xpath("extendedTooltip/name/text()") == ["ТоварыЦенаРасширеннаяПодсказка"]
    assert new.xpath("contextMenu/name/text()") == ["ТоварыЦенаКонтекстноеМеню"]
    ids = [int(new.findtext("id")), int(new.xpath("extendedTooltip/id/text()")[0]),
           int(new.xpath("contextMenu/id/text()")[0])]
    assert min(ids) > 7 and len(set(ids)) == 3
    # колонки идут до собственных свойств таблицы
    tags = [el.tag for el in table]
    assert tags.index("visible") > tags.index("items") + 2


def test_add_field_position_after(tmp_path):
    work = _copy(tmp_path)
    add_field(work, "ТоварыЦена", "Товары.Цена", parent="Товары", position="after:ТоварыНоменклатура")
    cols = _table(cfg.load(work)).findall("items")
    assert [c.findtext("name") for c in cols] == ["ТоварыНоменклатура", "ТоварыЦена", "ТоварыКоличество"]


def test_add_field_parent_only_additions(tmp_path):
    work = _copy(tmp_path)
    before = (FIX / "form_valuetable_edt.form").read_text(encoding="utf-8").splitlines()
    add_field(work, "ТоварыЦена", "Товары.Цена", parent="Товары")
    after = work.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(before, after, lineterm="", n=0))
    assert [l for l in diff if l.startswith("-") and not l.startswith("---")] == []


def test_add_field_sample_prefers_input_field(tmp_path):
    """Последнее поле контейнера - картинка: образцом берётся поле ввода, не картинка."""
    work = _copy(tmp_path)
    doc = cfg.load(work)
    table = _table(doc)
    pic = copy.deepcopy(table.findall("items")[-1])
    pic.find("name").text = "ТоварыУдалить"
    pic.find("type").text = "PictureField"
    cfg.place_after(table.findall("items")[-1], pic)
    cfg.save(doc, work)
    add_field(work, "ТоварыЦена", "Товары.Цена", parent="Товары")
    new = [c for c in _table(cfg.load(work)).findall("items") if c.findtext("name") == "ТоварыЦена"][0]
    assert new.findtext("type") == "InputField"


def test_set_style_footer_data_path_is_datapath(tmp_path):
    """footerDataPath в EDT - form:DataPath с segments (как в ERP); простой текст EDT выбрасывает."""
    from onec_metadata.ops.form_style import set_style
    work = _copy(tmp_path)
    set_style(work, "ТоварыКоличество", {"footerDataPath": "ИтогоКоличество"})
    col = [c for c in _table(cfg.load(work)).findall("items") if c.findtext("name") == "ТоварыКоличество"][0]
    fdp = col.find("footerDataPath")
    assert fdp.get(cfg.q("xsi", "type")) == "form:DataPath"
    assert fdp.xpath("segments/text()") == ["ИтогоКоличество"]


def test_add_field_parent_errors(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_field(work, "ТоварыЦена", "Товары.Цена", parent="НетТакого")
    with pytest.raises(OpPreconditionError):
        add_field(work, "ТоварыКоличество", "Товары.Количество", parent="Товары")
    with pytest.raises(OpPreconditionError):
        add_field(work, "ТоварыЦена", "Товары.Цена", parent="Товары", position="after:НетТакого")
