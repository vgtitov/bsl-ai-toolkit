# DISCIPLINE_ALLOW_TEST_EDIT: новый тест, TDD red-фаза (Фаза 2: СКД в EDT .dcs)
"""СКД в EDT (Template.dcs) — тот же DataCompositionSchema XML, но стиль файла
другой (без BOM, LF). Операции scd.* обязаны работать и сохранять стиль."""
import shutil
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _work(tmp_path):
    p = tmp_path / "Template.dcs"
    shutil.copy(FIXTURES / "schema.dcs", p)
    return p


def test_add_field_on_dcs(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.scd import add_field

    p = _work(tmp_path)
    add_field(p, dataset="Данные", field="Поле3", data_path="Поле3",
              title="Поле 3")

    doc = cfg.load(p)
    assert doc.xpath(
        "//dcs:dataSet[dcs:name='Данные']/dcs:field[dcs:dataPath='Поле3']",
        namespaces=cfg.NS)

    raw = p.read_bytes()
    assert not raw.startswith("﻿".encode("utf-8"))  # BOM не появился
    assert b"\r\n" not in raw                        # LF-стиль сохранён


def test_query_edit_on_dcs(tmp_path):
    from onec_metadata.ops.scd import get_dataset_query, set_dataset_query

    p = _work(tmp_path)
    q = get_dataset_query(p, "Данные")
    set_dataset_query(p, "Данные", q + "\n// edt")
    assert get_dataset_query(p, "Данные").endswith("// edt")


def test_dcs_roundtrip_after_op(tmp_path):
    from onec_metadata.formats import configurator as cfg
    from onec_metadata.ops.scd import add_field

    p = _work(tmp_path)
    add_field(p, "Данные", "Поле3", "Поле3", "Поле 3")
    edited = p.read_bytes()
    doc = cfg.load(p)
    out = tmp_path / "rt.dcs"
    cfg.save(doc, out)
    assert out.read_bytes() == edited
