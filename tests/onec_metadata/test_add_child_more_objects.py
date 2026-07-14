# DISCIPLINE_ALLOW_TEST_EDIT: P2-добор — DataProcessor/Report/DocumentJournal/HTTP/WebService
"""add_child на дополнительных видах объектов (Конфигуратор). Структура фикстур
снята с реального сервера УТ, обезличена. Column/URLTemplate/Operation — новые
виды дочерних без типа.
"""
import shutil
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops.add_child import add_child

FIX = Path(__file__).parent / "fixtures"
NS = cfg.NS

# (фикстура, вид, имя-образца-первого-элемента-того-же-вида)
CASES = [
    ("dataprocessor.xml", "Attribute", "Реквизит1", True),
    ("report.xml", "Attribute", "Реквизит1", True),
    ("documentjournal.xml", "Column", "Графа1", False),
    ("httpservice.xml", "URLTemplate", "Шаблон1", False),
    ("webservice.xml", "Operation", "Операция1", False),
]


def _names(doc, tag):
    return doc.xpath(f"//md:{tag}/md:Properties/md:Name/text()", namespaces=NS)


def test_add_child_new_objects(tmp_path):
    for fixture, kind, existing, typed in CASES:
        work = tmp_path / fixture
        shutil.copy(FIX / fixture, work)
        kwargs = {"type_ref": "xs:string"} if typed else {}
        add_child(work, kind, "Новый", "Новый", **kwargs)
        doc = cfg.load(work)
        assert _names(doc, kind) == [existing, "Новый"], fixture


def test_clone_clears_semantic_references(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: клон не должен тащить чужие ссылки/обработчики
    # Column: References образца НЕ переносятся (иначе новая графа ссылается на чужое)
    work = tmp_path / "documentjournal.xml"
    shutil.copy(FIX / "documentjournal.xml", work)
    add_child(work, "Column", "НоваяГрафа", "Новая графа")
    doc = cfg.load(work)
    new_col = doc.xpath(
        "//md:Column[md:Properties/md:Name='НоваяГрафа']", namespaces=NS)[0]
    assert new_col.xpath(".//xr:Item", namespaces=NS) == []

    # URLTemplate: Handler вложенного Method очищен
    work = tmp_path / "httpservice.xml"
    shutil.copy(FIX / "httpservice.xml", work)
    add_child(work, "URLTemplate", "НовыйШаблон", "Новый шаблон")
    doc = cfg.load(work)
    new_ut = doc.xpath(
        "//md:URLTemplate[md:Properties/md:Name='НовыйШаблон']", namespaces=NS)[0]
    handlers = new_ut.xpath(".//md:Handler/text()", namespaces=NS)
    assert handlers == [] or all(h == "" for h in handlers)

    # Operation: ProcedureName очищен
    work = tmp_path / "webservice.xml"
    shutil.copy(FIX / "webservice.xml", work)
    add_child(work, "Operation", "НоваяОперация", "Новая операция")
    doc = cfg.load(work)
    new_op = doc.xpath(
        "//md:Operation[md:Properties/md:Name='НоваяОперация']", namespaces=NS)[0]
    pn = new_op.xpath("md:Properties/md:ProcedureName/text()", namespaces=NS)
    assert pn == [] or pn == [""]


def test_new_objects_byte_perfect(tmp_path):
    for fixture, kind, _existing, typed in CASES:
        work = tmp_path / fixture
        shutil.copy(FIX / fixture, work)
        kwargs = {"type_ref": "xs:string"} if typed else {}
        add_child(work, kind, "Новый", "Новый", **kwargs)
        raw = work.read_bytes()
        doc = cfg.load(work)
        tmp2 = tmp_path / ("rt_" + fixture)
        cfg.save(doc, tmp2)
        assert tmp2.read_bytes() == raw, fixture
        assert raw[:3] == b"\xef\xbb\xbf" and b"\r\n" in raw
