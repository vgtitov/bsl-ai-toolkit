# DISCIPLINE_ALLOW_TEST_EDIT: переведён на синтетические обезличенные фикстуры
"""Операция add_column: вставка колонки в макет табличного документа.

Фикстура — синтетический макет (3 колонки; заголовки Артикул/Код/Цена,
строка данных с параметрами). Якорь вставки — «Код».
"""
import shutil
from pathlib import Path

import pytest
from lxml import etree

FIXTURES = Path(__file__).parent / "fixtures"
COLUMNS_ID = "c1000000-0000-0000-0000-000000000001"


def _work(tmp_path):
    p = tmp_path / "template.xml"
    shutil.copy(FIXTURES / "template.xml", p)
    return p


def _load(p):
    from onec_metadata.formats import configurator as cfg
    return cfg.load(p)


def _texts(doc):
    return list(doc.xpath("//v8:content/text()", namespaces={
        "v8": "http://v8.1c.ru/8.1/data/core"}))


def _params(doc):
    return list(doc.xpath("//*[local-name()='parameter']/text()"))


def test_add_column_after_anchor(tmp_path):
    from onec_metadata.ops.template_add_column import add_column

    work = _work(tmp_path)
    before = work.read_bytes()

    add_column(work, after_header="Код", header="Сертификат",
               parameter="Сертификат")

    doc = _load(work)
    texts = _texts(doc)
    assert "Сертификат" in texts
    assert texts.index("Сертификат") == texts.index("Код") + 1

    params = _params(doc)
    assert "Сертификат" in params
    assert params.index("Сертификат") == params.index("КодЗначение") + 1

    # набор колонок вырос 3 -> 4
    ln = lambda e: etree.QName(e).localname
    for cs in doc.getroot():
        if ln(cs) != "columns":
            continue
        cid = next((k.text for k in cs.iter() if ln(k) == "id"), None)
        if cid == COLUMNS_ID:
            size = next(k.text for k in cs if ln(k) == "size")
            items = [k for k in cs if ln(k) == "columnsItem"]
            assert size == "4"
            assert len(items) == 4

    after = work.read_bytes()
    assert after != before
    assert "Код".encode("utf-8") in after


def test_missing_anchor_raises(tmp_path):
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.template_add_column import add_column

    work = _work(tmp_path)
    with pytest.raises(OpPreconditionError):
        add_column(work, after_header="НетТакойКолонки", header="X", parameter="X")


def test_duplicate_raises(tmp_path):
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.template_add_column import add_column

    work = _work(tmp_path)
    add_column(work, after_header="Код", header="Сертификат", parameter="Сертификат")
    with pytest.raises(OpPreconditionError):
        add_column(work, after_header="Код", header="Сертификат", parameter="Сертификат")


def test_roundtrip_after_op(tmp_path):
    """После операции файл остаётся byte-perfect при повторном load/save."""
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.template_add_column import add_column

    work = _work(tmp_path)
    add_column(work, after_header="Код", header="Сертификат", parameter="Сертификат")
    edited = work.read_bytes()
    doc = cfg.load(work)
    out = tmp_path / "rt.xml"
    cfg.save(doc, out)
    assert out.read_bytes() == edited


def test_add_after_last_column_roundtrips(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: вставка после ПОСЛЕДНЕЙ колонки — idempotent-инвариант
    """Вставка после последней колонки (путь tail-swap) остаётся
    byte-perfect при повторном load/save."""
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.template_add_column import add_column

    work = _work(tmp_path)
    add_column(work, after_header="Цена", header="Итого", parameter="Итого")
    edited = work.read_bytes()
    doc = cfg.load(work)
    out = tmp_path / "rt.xml"
    cfg.save(doc, out)
    assert out.read_bytes() == edited
