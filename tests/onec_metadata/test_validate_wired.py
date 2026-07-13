# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — валидация имени подключена в операции
"""Мутирующие операции отвергают невалидное имя ДО изменения файла."""
import shutil
from pathlib import Path

import pytest

from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.add_child import add_child
from onec_metadata.ops.attribute_add import add_attribute

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("fixture", ["catalog.xml", "catalog.mdo"])
def test_add_attribute_rejects_bad_name_without_touching_file(tmp_path, fixture):
    src = FIX / fixture
    work = tmp_path / fixture
    shutil.copy(src, work)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError, match="Плохое-Имя"):
        add_attribute(work, "Плохое-Имя", "xs:string", "Плохое имя")
    assert work.read_bytes() == before  # файл не изменён


@pytest.mark.parametrize("fixture", ["catalog.xml", "catalog.mdo"])
def test_add_child_rejects_bad_name_without_touching_file(tmp_path, fixture):
    src = FIX / fixture
    work = tmp_path / fixture
    shutil.copy(src, work)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        add_child(work, "Attribute", "1СНачинаетсяСЦифры", "Синоним",
                  type_ref="xs:string")
    assert work.read_bytes() == before
