"""form.add_attribute с --parent: колонка реквизита-таблицы формы (EDT Form.form).

Структура снята с реальной EDT-формы (askАРММенеджераПоПродажам/ФормаПодбора): колонки —
безпрефиксные блоки <columns> внутри <attributes> реквизита ValueTable; их <id> делят
ОДНО пространство с id реквизитов формы (у таблицы id=5, у её колонок 26..30 и 6).
"""
import difflib
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form import add_attribute

FIX = Path(__file__).parent / "fixtures"
NSF = {"form": "http://g5.1c.ru/v8/dt/form"}


def _copy(tmp_path):
    work = tmp_path / "Form.form"
    shutil.copy(FIX / "form_valuetable_edt.form", work)
    return work


def _columns(doc, table):
    return doc.xpath(f"/form:Form/attributes[name='{table}']/columns", namespaces=NSF)


def test_add_column_edt(tmp_path):
    work = _copy(tmp_path)
    add_attribute(work, "Цена", "String", parent="Товары")
    doc = cfg.load(work)
    cols = _columns(doc, "Товары")
    assert [c.findtext("name") for c in cols] == ["Номенклатура", "Количество", "Цена"]
    new = cols[-1]
    assert new.xpath("valueType/types/text()") == ["String"]
    assert new.xpath("valueType/numberQualifiers") == []   # квалификаторы образца сняты
    assert new.xpath("title") == []                        # заголовок образца не наследуется
    # id: общее пространство реквизитов и колонок: max(1,4,3,7,5) + 1
    assert new.xpath("id/text()") == ["8"]
    # верхнего реквизита с таким именем не появилось
    assert doc.xpath("/form:Form/attributes[name='Цена']", namespaces=NSF) == []


def test_add_column_edt_only_additions(tmp_path):
    work = _copy(tmp_path)
    before = (FIX / "form_valuetable_edt.form").read_text(encoding="utf-8").splitlines()
    add_attribute(work, "Цена", "String", parent="Товары")
    after = work.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(before, after, lineterm="", n=0))
    assert [l for l in diff if l.startswith("-") and not l.startswith("---")] == []
    raw = work.read_bytes()
    assert b"\r\n" not in raw and not raw.startswith(b"\xef\xbb\xbf")


def test_add_column_edt_duplicate_raises(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_attribute(work, "Количество", "Number", parent="Товары")


def test_add_column_edt_parent_missing_or_not_table(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_attribute(work, "Цена", "String", parent="НетТакого")
    with pytest.raises(OpPreconditionError):
        add_attribute(work, "Цена", "String", parent="Поиск")   # не таблица: колонок нет


def test_add_attribute_edt_id_skips_column_ids(tmp_path):
    """Реквизит верхнего уровня не получает id, уже занятый колонкой таблицы."""
    work = _copy(tmp_path)
    add_attribute(work, "Комментарий", "String")
    doc = cfg.load(work)
    a = doc.xpath("/form:Form/attributes[name='Комментарий']", namespaces=NSF)[0]
    assert a.xpath("id/text()") == ["8"]


def test_add_column_configurator_not_supported_yet(tmp_path):
    work = tmp_path / "Form.xml"
    shutil.copy(FIX / "form.xml", work)
    with pytest.raises(OpPreconditionError):
        add_attribute(work, "Цена", "xs:string", parent="Товары")
