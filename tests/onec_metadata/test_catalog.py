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


def test_subsystem_coverage():
    # DISCIPLINE_ALLOW_TEST_EDIT: покрытие подсистемы
    from onec_metadata.catalog import SUPPORTED

    assert "subsystem_add_content" in SUPPORTED["Subsystem"]


def test_role_coverage():
    # DISCIPLINE_ALLOW_TEST_EDIT: покрытие роли/прав
    from onec_metadata.catalog import SUPPORTED

    assert "role_grant_right" in SUPPORTED["Role"]


def test_businessprocess_exchangeplan_coverage():
    # DISCIPLINE_ALLOW_TEST_EDIT: покрытие BusinessProcess/ExchangePlan
    from onec_metadata.catalog import SUPPORTED

    assert "add_child" in SUPPORTED["BusinessProcess"]
    assert "add_child" in SUPPORTED["ExchangePlan"]


def test_catalog_values_are_sets():
    from onec_metadata.catalog import SUPPORTED

    for obj_type, ops in SUPPORTED.items():
        assert isinstance(ops, (set, frozenset)), obj_type
