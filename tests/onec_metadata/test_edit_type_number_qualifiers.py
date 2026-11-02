"""edit_type: смена точности или масштаба числа не теряет квалификаторы, которые команда не назвала
(знак «неотрицательное», незаданный масштаб). Фикстуры синтетические."""
import shutil
from pathlib import Path

from onec_metadata.ops.edit_type import edit_type

FIX = Path(__file__).parent / "fixtures"


def _copy(tmp_path, name):
    work = tmp_path / name
    shutil.copy(FIX / name, work)
    return work


def test_precision_keeps_nonnegative_edt(tmp_path):
    work = _copy(tmp_path, "number_nonneg.mdo")
    edit_type(work, "Ревизия", "Number", precision=20)
    txt = work.read_text(encoding="utf-8")
    assert "<precision>20</precision>" in txt and "<nonNegative>true</nonNegative>" in txt
    assert txt.index("<precision>") < txt.index("<nonNegative>")


def test_scale_keeps_precision_and_sign_edt(tmp_path):
    work = _copy(tmp_path, "number_nonneg.mdo")
    edit_type(work, "Ревизия", "Number", scale=2)
    txt = work.read_text(encoding="utf-8")
    assert "<precision>18</precision>" in txt and "<scale>2</scale>" in txt and "<nonNegative>true</nonNegative>" in txt


def test_precision_keeps_sign_and_scale_cfg(tmp_path):
    work = _copy(tmp_path, "number_nonneg.xml")
    edit_type(work, "Ревизия", "xs:decimal", precision=20)
    txt = work.read_text(encoding="utf-8")
    assert "<v8:Digits>20</v8:Digits>" in txt
    assert "<v8:FractionDigits>0</v8:FractionDigits>" in txt
    assert "<v8:AllowedSign>Nonnegative</v8:AllowedSign>" in txt


def test_switch_from_number_to_string_drops_number_qualifiers_edt(tmp_path):
    work = _copy(tmp_path, "number_nonneg.mdo")
    edit_type(work, "Ревизия", "xs:string", length=10)
    txt = work.read_text(encoding="utf-8")
    assert "nonNegative" not in txt and "<length>10</length>" in txt


def test_precision_keeps_scale_edt(tmp_path):
    work = _copy(tmp_path, "number_nonneg.mdo")
    edit_type(work, "Ревизия", "Number", scale=3)
    edit_type(work, "Ревизия", "Number", precision=20)
    txt = work.read_text(encoding="utf-8")
    assert "<precision>20</precision>" in txt and "<scale>3</scale>" in txt and "<nonNegative>true</nonNegative>" in txt


def test_unexpected_node_refuses_and_keeps_file_edt(tmp_path):
    import pytest
    work = _copy(tmp_path, "number_nonneg.mdo")
    work.write_text(work.read_text(encoding="utf-8").replace("<nonNegative>true</nonNegative>",
                    '<nonNegative>true</nonNegative><rule mode="strict"><v>1</v></rule>'), encoding="utf-8")
    before = work.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        edit_type(work, "Ревизия", "Number", precision=20)
    assert work.read_text(encoding="utf-8") == before


def _refuses(tmp_path, old, new):
    import pytest
    work = _copy(tmp_path, "number_nonneg.mdo")
    txt = work.read_text(encoding="utf-8")
    assert old in txt
    work.write_text(txt.replace(old, new), encoding="utf-8")
    before = work.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        edit_type(work, "Ревизия", "Number", precision=20)
    assert work.read_text(encoding="utf-8") == before


def test_comment_inside_qualifier_refuses_edt(tmp_path):
    _refuses(tmp_path, "<nonNegative>true</nonNegative>", "<nonNegative>true</nonNegative><!-- note -->")


def test_duplicate_scalar_refuses_edt(tmp_path):
    _refuses(tmp_path, "<nonNegative>true</nonNegative>", "<nonNegative>true</nonNegative><nonNegative>false</nonNegative>")
