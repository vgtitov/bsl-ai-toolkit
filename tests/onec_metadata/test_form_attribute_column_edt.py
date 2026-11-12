"""Колонка реквизита-таблицы формы и удаление реквизита/колонки для EDT (Form.form) через общий API
form_attributes (те же команды, что для Конфигуратора; формат - по расширению файла).

Структура снята с реальной EDT-формы подбора: колонки - безпрефиксные
блоки <columns> внутри <attributes> реквизита ValueTable; их <id> делят ОДНО пространство с id реквизитов
формы (у таблицы id=5, у её колонок 26..30 и 6).
"""
import difflib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form import add_attribute
from onec_metadata.ops.form_attributes import add_attribute_column, remove_attribute, remove_attribute_column

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[2]
NSF = {"form": "http://g5.1c.ru/v8/dt/form"}


def _copy(tmp_path):
    work = tmp_path / "Form.form"
    shutil.copy(FIX / "form_valuetable_edt.form", work)
    return work


def _columns(doc, table):
    return doc.xpath(f"/form:Form/attributes[name='{table}']/columns", namespaces=NSF)


def _col(doc, table, name):
    return [c for c in _columns(doc, table) if c.findtext("name") == name][0]


def test_add_column_edt(tmp_path):
    work = _copy(tmp_path)
    add_attribute_column(work, "Товары", "Цена", "String", {})
    doc = cfg.load(work)
    cols = _columns(doc, "Товары")
    assert [c.findtext("name") for c in cols] == ["Номенклатура", "Количество", "Цена"]
    new = cols[-1]
    assert new.xpath("valueType/types/text()") == ["String"]
    assert new.xpath("valueType/numberQualifiers") == []   # квалификаторы образца сняты
    assert new.xpath("title") == []                        # заголовок образца не наследуется
    # id: общее пространство реквизитов и колонок: max(1,4,3,7,5) + 1
    assert new.xpath("id/text()") == ["8"]
    assert doc.xpath("/form:Form/attributes[name='Цена']", namespaces=NSF) == []


def test_add_column_edt_titles_and_types(tmp_path):
    work = _copy(tmp_path)
    add_attribute_column(work, "Товары", "Цена", "Number:15.2", {"ru": "Цена", "en": "Price"})
    add_attribute_column(work, "Товары", "Штук", "Number:10", {})
    add_attribute_column(work, "Товары", "Склад", "cfg:CatalogRef.Склады", {})
    doc = cfg.load(work)
    price = _col(doc, "Товары", "Цена")
    assert [el.tag for el in price][:4] == ["name", "title", "title", "id"]   # заголовки - сразу после name
    assert price.xpath("title/key/text()") == ["ru", "en"]
    assert price.xpath("title/value/text()") == ["Цена", "Price"]
    assert price.xpath("valueType/types/text()") == ["Number"]
    assert price.xpath("valueType/numberQualifiers/precision/text()") == ["15"]
    assert price.xpath("valueType/numberQualifiers/scale/text()") == ["2"]
    pcs = _col(doc, "Товары", "Штук")
    assert pcs.xpath("valueType/numberQualifiers/precision/text()") == ["10"]
    assert pcs.xpath("valueType/numberQualifiers/scale") == []              # scale=0 EDT не пишет
    assert _col(doc, "Товары", "Склад").xpath("valueType/types/text()") == ["CatalogRef.Склады"]
    # отступы как у EDT: квалификатор на уровне types, его дети - на уровень глубже
    text = work.read_text(encoding="utf-8")
    assert "        <types>Number</types>\n        <numberQualifiers>\n          <precision>15</precision>\n" \
           "          <scale>2</scale>\n        </numberQualifiers>\n      </valueType>" in text


def test_add_column_edt_only_additions(tmp_path):
    work = _copy(tmp_path)
    before = (FIX / "form_valuetable_edt.form").read_text(encoding="utf-8").splitlines()
    add_attribute_column(work, "Товары", "Цена", "Number:15.2", {"ru": "Цена"})
    after = work.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(before, after, lineterm="", n=0))
    assert [l for l in diff if l.startswith("-") and not l.startswith("---")] == []
    raw = work.read_bytes()
    assert b"\r\n" not in raw and not raw.startswith(b"\xef\xbb\xbf")


def test_add_column_edt_duplicate_raises(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_attribute_column(work, "Товары", "Количество", "Number:10", {})


def test_add_column_edt_parent_missing_or_not_table(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_attribute_column(work, "НетТакого", "Цена", "String", {})
    with pytest.raises(OpPreconditionError):
        add_attribute_column(work, "Поиск", "Цена", "String", {})       # не таблица: колонок нет


def test_add_first_column_to_new_table_edt(tmp_path):
    """Новый реквизит-таблица без колонок: первая колонка - по каркасу EDT, а не отказ «нет образца»."""
    work = _copy(tmp_path)
    add_attribute(work, "Единицы", "ValueTable", {"ru": "Единицы", "en": "Units"})
    add_attribute_column(work, "Единицы", "Выдать", "Boolean", {"ru": "Выдать", "en": "Issue"})
    add_attribute_column(work, "Единицы", "Сумма", "Number:15.2", {"ru": "Сумма"})   # дальше - клоном
    doc = cfg.load(work)
    cols = _columns(doc, "Единицы")
    assert [c.findtext("name") for c in cols] == ["Выдать", "Сумма"]
    first = cols[0]
    assert [el.tag for el in first] == ["name", "title", "title", "id", "valueType", "view", "edit"]
    assert first.xpath("valueType/types/text()") == ["Boolean"]
    assert first.xpath("view/common/text()") == ["true"] and first.xpath("edit/common/text()") == ["true"]
    assert first.xpath("id/text()") == ["9"]                      # реквизит получил 8
    assert cols[1].xpath("valueType/numberQualifiers/scale/text()") == ["2"]
    # отступы как у EDT: колонка на уровне детей реквизита, закрытие реквизита на своём уровне
    text = work.read_text(encoding="utf-8")
    assert "\n    </edit>\n    <columns>\n      <name>Выдать</name>\n" in text
    assert "      </edit>\n    </columns>\n  </attributes>" in text


def test_add_first_column_not_table_edt(tmp_path):
    work = _copy(tmp_path)
    add_attribute(work, "Флаг", "Boolean")
    with pytest.raises(OpPreconditionError):
        add_attribute_column(work, "Флаг", "Колонка", "String", {})


def test_add_attribute_edt_titles_not_inherited(tmp_path):
    """Клон реквизита-образца не тянет его заголовок; заданные заголовки - сразу после name."""
    work = _copy(tmp_path)
    add_attribute(work, "Без", "String")
    add_attribute(work, "С", "String", {"ru": "Свой", "en": "Own"})
    doc = cfg.load(work)
    plain = doc.xpath("/form:Form/attributes[name='Без']", namespaces=NSF)[0]
    titled = doc.xpath("/form:Form/attributes[name='С']", namespaces=NSF)[0]
    assert plain.xpath("title") == []
    assert [el.tag for el in titled][:3] == ["name", "title", "title"]
    assert titled.xpath("title/value/text()") == ["Свой", "Own"]


def test_add_attribute_titles_configurator_refused(tmp_path):
    work = tmp_path / "Form.xml"
    work.write_text("<Form/>", encoding="utf-8")
    with pytest.raises(OpPreconditionError):
        add_attribute(work, "Поле", "String", {"ru": "Поле"})


def test_add_attribute_edt_id_skips_column_ids(tmp_path):
    """Реквизит верхнего уровня не получает id, уже занятый колонкой таблицы."""
    work = _copy(tmp_path)
    add_attribute(work, "Комментарий", "String")
    doc = cfg.load(work)
    a = doc.xpath("/form:Form/attributes[name='Комментарий']", namespaces=NSF)[0]
    assert a.xpath("id/text()") == ["8"]


def test_remove_column_edt_roundtrip(tmp_path):
    work = _copy(tmp_path)
    before = (FIX / "form_valuetable_edt.form").read_text(encoding="utf-8")
    add_attribute_column(work, "Товары", "Цена", "String", {"ru": "Цена"})
    remove_attribute_column(work, "Товары", "Цена")
    assert work.read_text(encoding="utf-8") == before          # добавить+удалить = исходный файл
    with pytest.raises(OpPreconditionError):
        remove_attribute_column(work, "Товары", "Цена")         # нет такой колонки


def test_remove_edt_refuses_referenced_last_and_main(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        remove_attribute_column(work, "Товары", "Количество")  # поле ТоварыКоличество ссылается
    with pytest.raises(OpPreconditionError):
        remove_attribute(work, "Объект")                       # основной реквизит
    with pytest.raises(OpPreconditionError):
        remove_attribute(work, "Товары")                       # таблица привязана к элементу
    remove_attribute(work, "Поиск")                            # без ссылок - удаляется
    doc = cfg.load(work)
    assert doc.xpath("/form:Form/attributes[name='Поиск']", namespaces=NSF) == []


def test_remove_last_column_edt_refused(tmp_path):
    work = _copy(tmp_path)
    doc = cfg.load(work)
    cols = _columns(doc, "Товары")
    cfg.remove_children(cols[0].getparent(), cols[:1])        # оставить одну колонку (Количество)
    cfg.save(doc, work)
    with pytest.raises(OpPreconditionError):
        remove_attribute_column(work, "Товары", "Количество")


def test_cli_form_commands_unique():
    """CLI собирается: у form нет двух подкоманд с одним именем (argparse упал бы)."""
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "1c-meta"), "form", "remove-attribute", "--help"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
