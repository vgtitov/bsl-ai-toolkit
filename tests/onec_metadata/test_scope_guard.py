# DISCIPLINE_ALLOW_TEST_EDIT: новый тест по TDD (scope-guard ещё не написан)
"""Офлайн property-level scope-guard: «не тронул незатронутое».

Дополняет apply.dumpload.roundtrip_verify (файловая гранулярность, нужна тест-база)
проверкой БЕЗ платформы и НА УРОВНЕ СВОЙСТВ: блокирует дрейф свойств объектов/
реквизитов, которые правка менять не должна была (баг: у нетронутых объектов
расширения разъехались свойства).
"""
from pathlib import Path

import pytest


def _obj(name, comment="", synonym="Син"):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses">\n'
        f'  <Catalog><Properties><Name>{name}</Name>\n'
        f'    <Synonym>{synonym}</Synonym>\n'
        f'    <Comment>{comment}</Comment>\n'
        '  </Properties></Catalog>\n'
        '</MetaDataObject>\n')


def _tree(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def test_clean_target_change_passes(tmp_path):
    from onec_metadata.apply.scope_guard import assert_in_scope
    before = tmp_path / "before"
    after = tmp_path / "after"
    _tree(before, {"A.xml": _obj("A"), "B.xml": _obj("B")})
    _tree(after, {"A.xml": _obj("A", comment="new"), "B.xml": _obj("B")})
    # A.xml разрешено менять целиком, B.xml трогать нельзя — B не изменился → OK
    assert_in_scope(before, after, {"A.xml": "ALL"})


def test_untouched_object_property_drift_blocked(tmp_path):
    from onec_metadata.apply.scope_guard import assert_in_scope, ScopeViolation
    before = tmp_path / "before"
    after = tmp_path / "after"
    _tree(before, {"A.xml": _obj("A"), "B.xml": _obj("B", synonym="Син")})
    # B.xml — незатронутый объект, но у него разъехалось свойство Synonym
    _tree(after, {"A.xml": _obj("A", comment="new"),
                  "B.xml": _obj("B", synonym="ДругойСиноним")})
    with pytest.raises(ScopeViolation) as ei:
        assert_in_scope(before, after, {"A.xml": "ALL"})
    msg = str(ei.value)
    assert "B.xml" in msg and "Synonym" in msg


def test_out_of_scope_property_in_allowed_file_blocked(tmp_path):
    from onec_metadata.apply.scope_guard import assert_in_scope, ScopeViolation
    before = tmp_path / "before"
    after = tmp_path / "after"
    _tree(before, {"A.xml": _obj("A", comment="", synonym="Син")})
    # в A.xml разрешено менять только Comment, а изменился ещё и Synonym
    _tree(after, {"A.xml": _obj("A", comment="new", synonym="Другой")})
    with pytest.raises(ScopeViolation) as ei:
        assert_in_scope(before, after, {"A.xml": {"Comment"}})
    assert "Synonym" in str(ei.value)


def test_allowed_property_change_passes(tmp_path):
    from onec_metadata.apply.scope_guard import assert_in_scope
    before = tmp_path / "before"
    after = tmp_path / "after"
    _tree(before, {"A.xml": _obj("A", comment="")})
    _tree(after, {"A.xml": _obj("A", comment="new")})
    assert_in_scope(before, after, {"A.xml": {"Comment"}})


def test_new_file_must_be_declared(tmp_path):
    from onec_metadata.apply.scope_guard import assert_in_scope, ScopeViolation
    before = tmp_path / "before"
    after = tmp_path / "after"
    _tree(before, {"A.xml": _obj("A")})
    _tree(after, {"A.xml": _obj("A"), "New.xml": _obj("New")})
    with pytest.raises(ScopeViolation):
        assert_in_scope(before, after, {})
    # объявленный новый файл допускается
    assert_in_scope(before, after, {"New.xml": "ALL"})


def test_ignore_dump_info(tmp_path):
    from onec_metadata.apply.scope_guard import assert_in_scope
    before = tmp_path / "before"
    after = tmp_path / "after"
    _tree(before, {"A.xml": _obj("A"), "ConfigDumpInfo.xml": "<x>1</x>"})
    _tree(after, {"A.xml": _obj("A"), "ConfigDumpInfo.xml": "<x>2</x>"})
    # ConfigDumpInfo.xml всегда игнорируется (платформа переписывает при дампе)
    assert_in_scope(before, after, {})
