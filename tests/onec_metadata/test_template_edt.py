# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — макет в EDT-контейнере (.mxlx)
"""template add-column на EDT-макете `Template.mxlx`.

Реальная EDT-выгрузка ERP 2.5 хранит табличный макет как Template.mxlx —
ТОТ ЖЕ XML `document xmlns=http://v8.1c.ru/8.2/data/spreadsheet`, что и
Template.xml Конфигуратора; отличие только в стиле файла (LF, без BOM).
Операция обязана работать на нём как есть и сохранить стиль.
"""
import shutil
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops.template_add_column import add_column

FIX = Path(__file__).parent / "fixtures"


def _work(tmp_path):
    p = tmp_path / "Template.mxlx"
    shutil.copy(FIX / "template.mxlx", p)
    return p


def test_add_column_on_mxlx(tmp_path):
    p = _work(tmp_path)
    add_column(p, after_header="Код", header="Штрихкод", parameter="Штрихкод")
    doc = cfg.load(p)
    texts = doc.xpath("//v8:content/text()",
                      namespaces={"v8": "http://v8.1c.ru/8.1/data/core"})
    assert "Штрихкод" in texts
    assert texts.index("Штрихкод") == texts.index("Код") + 1


def test_mxlx_style_preserved(tmp_path):
    p = _work(tmp_path)
    add_column(p, after_header="Код", header="Штрихкод", parameter="Штрихкод")
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")   # BOM не появился
    assert b"\r\n" not in raw                     # LF-стиль EDT сохранён


def test_mxlx_minimal_diff(tmp_path):
    """byte-perfect: единственная ИЗМЕНЁННАЯ строка исходника — счётчик колонок
    <size> (легитимная семантика операции); остальное — только добавления.

    DISCIPLINE_ALLOW_TEST_EDIT: первый вариант требовал removed == [] — но
    add-column обязан инкрементировать <size>, это не переформатирование.
    """
    import difflib
    p = _work(tmp_path)
    before = (FIX / "template.mxlx").read_text(encoding="utf-8").splitlines()
    add_column(p, after_header="Код", header="Штрихкод", parameter="Штрихкод")
    after = p.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(before, after, lineterm="", n=0))
    removed = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---")]
    assert removed == ["\t\t<size>3</size>"]
    assert "\t\t<size>4</size>" in after
