# DISCIPLINE_ALLOW_TEST_EDIT: новый тест, TDD red-фаза (кода продукта ещё нет)
from pathlib import Path
import pytest
from onec_metadata.formats.detect import detect_format, SourceFormat, UnknownFormatError


def test_configurator_tree(tmp_path):
    (tmp_path / "Configuration.xml").write_text("<x/>", encoding="utf-8")
    assert detect_format(tmp_path) is SourceFormat.CONFIGURATOR


def test_edt_tree(tmp_path):
    (tmp_path / ".project").write_text("<x/>", encoding="utf-8")
    (tmp_path / "src").mkdir()
    assert detect_format(tmp_path) is SourceFormat.EDT


def test_unknown_tree(tmp_path):
    with pytest.raises(UnknownFormatError):
        detect_format(tmp_path)
