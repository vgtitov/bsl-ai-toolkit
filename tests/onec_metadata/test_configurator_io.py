# DISCIPLINE_ALLOW_TEST_EDIT: новый тест, TDD red-фаза
"""Byte-perfect round-trip: load→save обязан вернуть исходные байты.

Параметризуется всеми фикстурами каталога fixtures/ — при добавлении новых
(СКД, справочник) они попадают в прогон автоматически.
"""
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
XML_FIXTURES = sorted(p.name for p in FIXTURES.glob("*.xml"))


@pytest.mark.parametrize("name", XML_FIXTURES)
def test_roundtrip_byte_identical(name, tmp_path):
    from onec_metadata.formats import configurator as cfg

    src = FIXTURES / name
    tree = cfg.load(src)
    out = tmp_path / name
    cfg.save(tree, out)
    assert out.read_bytes() == src.read_bytes(), f"round-trip diff в {name}"


def test_fixture_present():
    assert XML_FIXTURES, "нет ни одной XML-фикстуры"
