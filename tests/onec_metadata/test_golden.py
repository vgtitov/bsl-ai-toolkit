# DISCIPLINE_ALLOW_TEST_EDIT: новый набор — golden-эталоны результата операций
"""Golden-тесты: применить операцию к базовой фикстуре и сравнить результат ПОБАЙТНО
с записанным эталоном. Страховка от «вакуумных» byte-perfect тестов (round-trip истинен
для любого self-consistent файла и не ловит дрейф самой операции — реальный кейс: CRLF-регресс).

Регенерация эталонов (после ОСОЗНАННОЙ проверки корректности вывода):
    GOLDEN_REGEN=1 python -m pytest tests/onec_metadata/test_golden.py -q
"""
import os
import shutil
from pathlib import Path

import pytest

# DISCIPLINE_ALLOW_TEST_EDIT: убран неиспользуемый импорт add_attribute (исключён из golden выше)
from onec_metadata.ops.edit_type import edit_type
from onec_metadata.ops.form import add_field as form_add_field
from onec_metadata.ops.synonym import set_synonym

FIX = Path(__file__).parent / "fixtures"
GOLD = FIX / "golden"
REGEN = os.environ.get("GOLDEN_REGEN") == "1"

# (базовая фикстура, операция, имя эталона)
CASES = [
    ("catalog.xml", lambda w: edit_type(w, "Реквизит1", "cfg:CatalogRef.Товары"),
     "edit_type_ref_cfg.golden"),
    ("untyped.xml", lambda w: edit_type(w, "БезТипа", "xs:string", length=100),
     "edit_type_scratch_str_cfg.golden"),
    ("untyped.mdo", lambda w: edit_type(w, "БезТипа", "Number", precision=10, scale=3),
     "edit_type_scratch_num_edt.golden"),
    ("catalog.mdo", lambda w: edit_type(w, "Реквизит1", ["CatalogRef.Товары", "CatalogRef.Услуги"]),
     "edit_type_composite_edt.golden"),
    ("sessionparameter.xml", lambda w: edit_type(w, None, "cfg:CatalogRef.Пользователи"),
     "edit_type_object_cfg.golden"),
    # DISCIPLINE_ALLOW_TEST_EDIT: add_attribute/add_child исключены из golden — генерируют
    # случайный uuid4 (недетерминированный вывод, golden это поймал); их byte-perfect покрыт
    # своими тестами. Golden — для детерминированных операций (id переприсваиваются последовательно).
    ("form_field_edt.form", lambda w: form_add_field(w, "Артикул", "Объект.Артикул"),
     "form_add_field_edt.golden"),
    ("register.xml", lambda w: set_synonym(w, "Измерение1", "Новое наименование"),
     "set_synonym_cfg.golden"),
]


@pytest.mark.parametrize("base,op,golden", CASES, ids=[c[2] for c in CASES])
def test_golden(base, op, golden, tmp_path):
    work = tmp_path / base
    shutil.copy(FIX / base, work)
    op(work)
    gpath = GOLD / golden
    if REGEN:
        GOLD.mkdir(exist_ok=True)
        gpath.write_bytes(work.read_bytes())
        pytest.skip(f"эталон перезаписан: {golden}")
    assert gpath.exists(), f"нет эталона {golden} (сгенерируй GOLDEN_REGEN=1)"
    assert work.read_bytes() == gpath.read_bytes(), f"вывод разошёлся с эталоном {golden}"
