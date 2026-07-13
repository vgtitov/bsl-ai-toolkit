# DISCIPLINE_ALLOW_TEST_EDIT: расширение реестра покрытия — добавлен Task
"""Каталог покрытия: какие типы объектов какими операциями поддержаны."""


def test_phase1_coverage():
    from onec_metadata.catalog import SUPPORTED

    assert "add_column" in SUPPORTED["Template"]
    assert "scd_add_field" in SUPPORTED["Report"]
    assert "scd_set_query" in SUPPORTED["Report"]
    assert "attribute_add" in SUPPORTED["Catalog"]
    assert "attribute_add" in SUPPORTED["Document"]


def test_task_coverage():
    from onec_metadata.catalog import SUPPORTED

    assert "add_child" in SUPPORTED["Task"]


def test_catalog_values_are_sets():
    from onec_metadata.catalog import SUPPORTED

    for obj_type, ops in SUPPORTED.items():
        assert isinstance(ops, (set, frozenset)), obj_type
