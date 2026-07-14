# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — реквизит формы в формате EDT (.form)
"""form.add_attribute на EDT-форме `Form.form`.

Структура снята с реальной EDT-выгрузки ERP 2.5 (Forms/*/Form.form):
корень form:Form (ns g5.1c.ru/v8/dt/form), реквизиты — безпрефиксные блоки
<attributes> с ДОЧЕРНИМ <id>; id реквизитов и элементов (<items>) — раздельные
пространства; основной реквизит помечен <main>true</main>.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form import add_attribute

FIX = Path(__file__).parent / "fixtures"


def _copy(tmp_path):
    work = tmp_path / "Form.form"
    shutil.copy(FIX / "form.form", work)
    return work


def _attr(doc, name):
    hits = doc.xpath(f"/form:Form/attributes[name='{name}']",
                     namespaces={"form": "http://g5.1c.ru/v8/dt/form"})
    return hits[0] if hits else None


def test_add_attribute_edt(tmp_path):
    work = _copy(tmp_path)
    add_attribute(work, "Комментарий", "String")
    doc = cfg.load(work)
    a = _attr(doc, "Комментарий")
    assert a is not None
    assert a.xpath("valueType/types/text()") == ["String"]
    # id: max в пространстве реквизитов (3) + 1, элементы (<items> id=1) не влияют
    assert a.xpath("id/text()") == ["4"]
    assert a.xpath("view/common/text()") == ["true"]


def test_add_attribute_edt_clone_is_clean(tmp_path):
    """Клон-образец чистится от семантики образца: main/extInfo/notDefault…/квалификаторы."""
    work = _copy(tmp_path)
    add_attribute(work, "Контрагент", "CatalogRef.Контрагенты")
    doc = cfg.load(work)
    a = _attr(doc, "Контрагент")
    assert a.xpath("valueType/types/text()") == ["CatalogRef.Контрагенты"]
    assert a.xpath("main") == []
    assert a.xpath("extInfo") == []
    assert a.xpath("notDefaultUseAlwaysAttributes") == []
    assert a.xpath("valueType/stringQualifiers") == []


def test_add_attribute_edt_duplicate_raises(tmp_path):
    work = _copy(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_attribute(work, "КодЯзыка", "String")


def test_add_attribute_edt_style_preserved(tmp_path):
    work = _copy(tmp_path)
    add_attribute(work, "Комментарий", "String")
    raw = work.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")   # BOM не появился
    assert b"\r\n" not in raw                     # LF-стиль EDT сохранён
    assert b'xsi:type="form:DynamicListExtInfo"' in raw  # образец не тронут


def test_add_attribute_edt_existing_untouched(tmp_path):
    """Минимальность: существующие реквизиты и элементы формы не изменились."""
    import difflib
    work = _copy(tmp_path)
    before = (FIX / "form.form").read_text(encoding="utf-8").splitlines()
    add_attribute(work, "Комментарий", "String")
    after = work.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(before, after, lineterm="", n=0))
    removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
    assert removed == []


def _copy_fx(tmp_path, fx):
    work = tmp_path / "Form.form"
    shutil.copy(FIX / fx, work)
    return work


def test_add_attribute_edt_main_only_sample_cleaned(tmp_path):
    """Fallback: единственный типизированный образец — ОСНОВНОЙ реквизит с
    семантикой (main/extInfo/notDefaultUseAlwaysAttributes). Клон обязан быть
    чистым — этот тест падает, если убрать чистку _EDT_ATTR_SEMANTIC."""
    work = _copy_fx(tmp_path, "form_mainonly.form")
    add_attribute(work, "Комментарий", "String")
    doc = cfg.load(work)
    a = _attr(doc, "Комментарий")
    assert a is not None
    assert a.xpath("valueType/types/text()") == ["String"]
    assert a.xpath("main") == []                        # второго main не появилось
    assert a.xpath("extInfo") == []                     # чужой mainTable не унаследован
    assert a.xpath("notDefaultUseAlwaysAttributes") == []
    # исходный main-реквизит не тронут
    orig = _attr(doc, "Список")
    assert orig.xpath("main/text()") == ["true"]
    assert orig.xpath("extInfo/mainTable/text()") == ["Catalog.Товары"]


def test_add_attribute_edt_sample_not_last(tmp_path):
    """Образец (не-main КодЯзыка, первый) ≠ якорь вставки (последний реквизит):
    новый блок встаёт ПОСЛЕ последнего <attributes>, id = max+1 по всем."""
    work = _copy_fx(tmp_path, "form_mainlast.form")
    add_attribute(work, "Комментарий", "String")
    doc = cfg.load(work)
    names = doc.xpath("/form:Form/attributes/name/text()",
                      namespaces={"form": "http://g5.1c.ru/v8/dt/form"})
    assert names == ["КодЯзыка", "Список", "Комментарий"]
    a = _attr(doc, "Комментарий")
    assert a.xpath("id/text()") == ["6"]                # max(3,5)+1
    assert a.xpath("valueType/stringQualifiers") == []  # квалификаторы образца сняты
    raw = work.read_bytes()
    assert b"\r\n" not in raw
