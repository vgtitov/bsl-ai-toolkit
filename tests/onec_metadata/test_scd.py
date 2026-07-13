# DISCIPLINE_ALLOW_TEST_EDIT: переведён на синтетические обезличенные фикстуры
"""Операции над СКД (DataCompositionSchema) на синтетической схеме.

Фикстура — минимальная схема с одним набором данных «Данные» (запрос + 2 поля).
"""
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
DS = "Данные"


def _work(tmp_path):
    p = tmp_path / "scd.xml"
    shutil.copy(FIXTURES / "scd.xml", p)
    return p


def test_add_field(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.scd import add_field

    p = _work(tmp_path)
    add_field(p, dataset=DS, field="Поле3", data_path="Поле3", title="Поле 3")

    doc = cfg.load(p)
    hits = doc.xpath(
        "//dcs:dataSet[dcs:name='Данные']/dcs:field[dcs:dataPath='Поле3']",
        namespaces=cfg.NS)
    assert len(hits) == 1
    field = hits[0]
    assert field.get(cfg.q("xsi", "type")) == "DataSetFieldField"
    titles = field.xpath(".//v8:content/text()", namespaces=cfg.NS)
    assert "Поле 3" in titles


def test_add_field_duplicate_raises(tmp_path):
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.scd import add_field

    p = _work(tmp_path)
    add_field(p, DS, "Поле3", "Поле3", "Поле 3")
    with pytest.raises(OpPreconditionError):
        add_field(p, DS, "Поле3", "Поле3", "Поле 3")


def test_query_get_set_roundtrip(tmp_path):
    from onec_metadata.ops.scd import get_dataset_query, set_dataset_query

    p = _work(tmp_path)
    q0 = get_dataset_query(p, DS)
    assert "ВЫБРАТЬ" in q0.upper()
    set_dataset_query(p, DS, q0 + "\n// touched")
    assert get_dataset_query(p, DS).endswith("// touched")


def test_unknown_dataset_raises(tmp_path):
    from onec_metadata.ops import OpPreconditionError
    from onec_metadata.ops.scd import get_dataset_query

    p = _work(tmp_path)
    with pytest.raises(OpPreconditionError):
        get_dataset_query(p, "НетТакогоНабора")


def test_add_field_then_roundtrip_byte_perfect(tmp_path):
    """После операции файл снова byte-perfect при load/save."""
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.scd import add_field

    p = _work(tmp_path)
    add_field(p, DS, "Поле3", "Поле3", "Поле 3")
    edited = p.read_bytes()
    doc = cfg.load(p)
    out = tmp_path / "rt.xml"
    cfg.save(doc, out)
    assert out.read_bytes() == edited
