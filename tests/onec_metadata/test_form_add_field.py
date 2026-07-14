# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — P4.2 form add-field (InputField)
"""form.add_field: элемент InputField в ChildItems управляемой формы (Конфигуратор).

Требования спеки P4.2 (docs/superpowers/specs/2026-07-14-forms-design.md):
привязка DataPath к реквизиту; свежие ПОСЛЕДОВАТЕЛЬНЫЕ id для поля и вложенных
ContextMenu/ExtendedTooltip из пространства ЭЛЕМЕНТОВ (реквизиты — отдельное
пространство, доказано реальными выгрузками); клон образца, семантика образца
(DataPath, обработчики) не наследуется; byte-perfect стиль.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form import add_field

FIX = Path(__file__).parent / "fixtures"
NS = {"lf": "http://v8.1c.ru/8.3/xcf/logform"}


def _copy(tmp_path):
    work = tmp_path / "form.xml"
    shutil.copy(FIX / "form.xml", work)
    return work


def _field(doc, name):
    hits = doc.xpath(f"//lf:ChildItems/lf:InputField[@name='{name}']", namespaces=NS)
    return hits[0] if hits else None


def test_add_field(tmp_path):
    work = _copy(tmp_path)
    add_field(work, "Артикул", "Объект.Артикул")
    doc = cfg.load(work)
    f = _field(doc, "Артикул")
    assert f is not None
    assert f.xpath("lf:DataPath/text()", namespaces=NS) == ["Объект.Артикул"]
    # id из пространства ЭЛЕМЕНТОВ: в фикстуре max элемент-id = 3 → поле 4, вложенные 5 и 6
    assert f.get("id") == "4"
    nested = sorted(int(e.get("id")) for e in f.xpath(".//*[@id]"))
    assert nested == [5, 6]
    # вложенные части переименованы под новое поле, а не унаследованы от образца
    cm = f.xpath("lf:ContextMenu", namespaces=NS)[0]
    tt = f.xpath("lf:ExtendedTooltip", namespaces=NS)[0]
    assert cm.get("name") == "АртикулКонтекстноеМеню"
    assert tt.get("name") == "АртикулExtendedTooltip"
    # реквизиты формы не тронуты (отдельное id-пространство)
    assert doc.xpath("//lf:Attributes/lf:Attribute/@id", namespaces=NS) == ["1"]


def test_add_field_duplicate_raises(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_field(work, "Наименование", "Объект.Наименование")


def test_add_field_style_and_minimal_diff(tmp_path):
    import difflib
    work = _copy(tmp_path)
    before_raw = (FIX / "form.xml").read_bytes()
    add_field(work, "Артикул", "Объект.Артикул")
    raw = work.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") == before_raw.startswith(b"\xef\xbb\xbf")  # BOM сохранён
    assert (b"\r\n" in raw) == (b"\r\n" in before_raw)                                 # EOL-стиль сохранён
    before = before_raw.decode("utf-8-sig").splitlines()
    after = raw.decode("utf-8-sig").splitlines()
    diff = list(difflib.unified_diff(before, after, lineterm="", n=0))
    removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
    assert removed == []


def test_add_field_second_sequential_ids(tmp_path):
    """Повторное добавление продолжает нумерацию элементов (7,8,9)."""
    work = _copy(tmp_path)
    add_field(work, "Артикул", "Объект.Артикул")
    add_field(work, "Цена", "Объект.Цена")
    doc = cfg.load(work)
    f = _field(doc, "Цена")
    assert f.get("id") == "7"
    assert sorted(int(e.get("id")) for e in f.xpath(".//*[@id]")) == [8, 9]
