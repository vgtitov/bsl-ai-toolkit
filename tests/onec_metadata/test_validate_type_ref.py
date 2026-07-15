# DISCIPLINE_ALLOW_TEST_EDIT: новый red-набор TDD — семантическая валидация type_ref
"""validate_type_ref: не пропускать заведомо битую ссылку типа перед подстановкой.
Принимает валидные формы (примитивы, ссылки, с префиксом и без); отвергает пустое,
пробелы, XML-спецсимволы, пустые сегменты/префикс."""
import shutil
from pathlib import Path

import pytest

from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.edit_type import edit_type
from onec_metadata.validate import validate_type_ref

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("good", [
    "xs:string", "xs:decimal", "xs:date", "xs:boolean",
    "cfg:CatalogRef.Товары", "cfg:DocumentRef.ЗаказКлиента", "cfg:EnumRef.СтатусыЗаказов",
    "String", "Number", "Boolean", "CatalogRef.Товары", "DefinedType.Сумма",
])
def test_valid_type_refs_pass(good):
    validate_type_ref(good)          # не должно кидать


@pytest.mark.parametrize("bad", [
    "", "   ", "cfg:", "xs:", ".Товары", "CatalogRef.",
    "CatalogRef..Товары", "плохой тип", "Catalog Ref.Товары",
    "cfg:CatalogRef.Тов<ары", "String&", "Тип.С Пробелом",
])
def test_invalid_type_refs_rejected(bad):
    with pytest.raises(OpPreconditionError):
        validate_type_ref(bad)


def test_edit_type_rejects_broken_ref_and_keeps_file(tmp_path):
    work = tmp_path / "catalog.xml"
    shutil.copy(FIX / "catalog.xml", work)
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        edit_type(work, "Реквизит1", "плохой тип с пробелом")
    assert work.read_bytes() == before      # файл не тронут при отказе
