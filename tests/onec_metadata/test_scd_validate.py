# DISCIPLINE_ALLOW_TEST_EDIT: новый тест по TDD (валидатор ещё не написан)
"""Smoke-валидатор целостности схемы компоновки данных (СКД).

Ловит поломку СКД при правке: пропавший набор данных, поле без dataPath,
дубль поля, битую ссылку связи наборов, невалидный XML — ДО загрузки в 1С.
"""
import shutil
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _v(path):
    from onec_metadata.ops.scd import validate_schema
    return validate_schema(Path(path))


def test_good_schema_no_issues():
    assert _v(FIXTURES / "scd.xml") == []


def test_not_a_schema_flagged(tmp_path):
    p = tmp_path / "x.xml"
    p.write_text('<?xml version="1.0"?><Foo><bar/></Foo>', encoding="utf-8")
    issues = _v(p)
    assert issues and any("компоновк" in s.lower() or "схем" in s.lower()
                          for s in issues)


def test_malformed_xml_flagged(tmp_path):
    p = tmp_path / "x.xml"
    p.write_text("<DataCompositionSchema><dataSet>", encoding="utf-8")
    assert _v(p)  # непустой список проблем, без исключения


def test_no_dataset_flagged(tmp_path):
    p = tmp_path / "s.xml"
    p.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<DataCompositionSchema '
        'xmlns="http://v8.1c.ru/8.1/data-composition-system/schema">\n'
        '  <dataSource><name>И1</name></dataSource>\n'
        '</DataCompositionSchema>\n', encoding="utf-8")
    assert any("набор" in s.lower() for s in _v(p))


def test_duplicate_datapath_flagged(tmp_path):
    src = (FIXTURES / "scd.xml").read_text(encoding="utf-8")
    # продублировать dataPath 'Поле2' → 'Поле1'
    broken = src.replace("<dataPath>Поле2</dataPath>", "<dataPath>Поле1</dataPath>")
    p = tmp_path / "s.xml"
    p.write_text(broken, encoding="utf-8")
    assert any("дубл" in s.lower() and "Поле1" in s for s in _v(p))


def test_field_without_datapath_flagged(tmp_path):
    src = (FIXTURES / "scd.xml").read_text(encoding="utf-8")
    broken = src.replace("<dataPath>Поле2</dataPath>\n", "")
    p = tmp_path / "s.xml"
    p.write_text(broken, encoding="utf-8")
    assert any("dataPath" in s for s in _v(p))


def test_broken_dataset_link_flagged(tmp_path):
    src = (FIXTURES / "scd.xml").read_text(encoding="utf-8")
    link = (
        "\t<dataSetLink>\n"
        "\t\t<sourceDataSet>Данные</sourceDataSet>\n"
        "\t\t<destinationDataSet>НетТакого</destinationDataSet>\n"
        "\t</dataSetLink>\n")
    broken = src.replace("</DataCompositionSchema>", link + "</DataCompositionSchema>")
    p = tmp_path / "s.xml"
    p.write_text(broken, encoding="utf-8")
    assert any("НетТакого" in s for s in _v(p))


def test_validate_after_add_field_stays_valid(tmp_path):
    from onec_metadata.ops.scd import add_field
    p = tmp_path / "scd.xml"
    shutil.copy(FIXTURES / "scd.xml", p)
    add_field(p, "Данные", "Поле3", "Поле3", "Поле 3")
    assert _v(p) == []
