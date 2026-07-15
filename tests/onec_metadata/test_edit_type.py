# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD — edit-type (смена/расширение типа реквизита)
"""edit_type: сменить тип СУЩЕСТВУЮЩЕГО реквизита на одиночный тип (квалификаторы
снимаются) или изменить длину существующего строкового квалификатора.
"""
import shutil
from pathlib import Path

import pytest

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.edit_type import edit_type

FIX = Path(__file__).parent / "fixtures"
NS = cfg.NS


def _copy(tmp_path, name):
    work = tmp_path / name
    shutil.copy(FIX / name, work)
    return work


# ---------- Конфигуратор ----------

def test_change_to_ref_type_cfg(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: усиление — отступ </Type> после удаления квалификаторов
    work = _copy(tmp_path, "catalog.xml")
    edit_type(work, "Реквизит1", "cfg:CatalogRef.Товары")
    doc = cfg.load(work)
    a = doc.xpath("//md:Attribute[md:Properties/md:Name='Реквизит1']", namespaces=NS)[0]
    assert a.xpath(".//v8:Type/text()", namespaces=NS) == ["cfg:CatalogRef.Товары"]
    # ссылочный тип — квалификаторы сняты
    assert a.xpath(".//v8:StringQualifiers", namespaces=NS) == []
    # отступ закрывающего </Type> не съехал (byte-perfect после удаления)
    txt = work.read_text(encoding="utf-8-sig")
    open_type = next(l for l in txt.splitlines() if l.strip() == "<Type>")
    close_type = next(l for l in txt.splitlines() if l.strip() == "</Type>")
    indent = lambda s: s[:len(s) - len(s.lstrip())]
    assert indent(open_type) == indent(close_type)


def test_extend_string_length_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    edit_type(work, "Реквизит1", "xs:string", length=50)
    doc = cfg.load(work)
    a = doc.xpath("//md:Attribute[md:Properties/md:Name='Реквизит1']", namespaces=NS)[0]
    assert a.xpath(".//v8:Type/text()", namespaces=NS) == ["xs:string"]
    assert a.xpath(".//v8:StringQualifiers/v8:Length/text()", namespaces=NS) == ["50"]


def test_length_on_non_string_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        edit_type(work, "Реквизит1", "cfg:CatalogRef.Товары", length=50)
    assert work.read_bytes() == before


def test_absent_attribute_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    with pytest.raises(OpPreconditionError):
        edit_type(work, "НетТакого", "xs:string")


def test_bad_attribute_name_rejected_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    with pytest.raises(OpPreconditionError):
        edit_type(work, "Плохое-Имя", "xs:string")


def test_byte_perfect_cfg(tmp_path):
    work = _copy(tmp_path, "catalog.xml")
    edit_type(work, "Реквизит1", "xs:string", length=50)
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.xml"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw


# ---------- EDT ----------

def test_change_to_ref_type_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    edit_type(work, "Реквизит1", "CatalogRef.Товары")
    doc = cfg.load(work)
    a = doc.xpath("//attributes[name='Реквизит1']")[0]
    assert a.xpath("type/types/text()") == ["CatalogRef.Товары"]
    assert a.xpath("type/stringQualifiers") == []


def test_extend_string_length_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    edit_type(work, "Реквизит1", "String", length=50)
    doc = cfg.load(work)
    a = doc.xpath("//attributes[name='Реквизит1']")[0]
    assert a.xpath("type/types/text()") == ["String"]
    assert a.xpath("type/stringQualifiers/length/text()") == ["50"]


def test_byte_perfect_edt(tmp_path):
    work = _copy(tmp_path, "catalog.mdo")
    edit_type(work, "Реквизит1", "String", length=50)
    raw = work.read_bytes()
    doc = cfg.load(work)
    tmp2 = tmp_path / "rt.mdo"
    cfg.save(doc, tmp2)
    assert tmp2.read_bytes() == raw


# DISCIPLINE_ALLOW_TEST_EDIT: усиление byte-perfect-проверки — тавтологичный round-trip
# заменён на детект одинокого LF (ловит CRLF-регресс, который нашло ревью).
def _roundtrips(work, tmp_path, suffix):
    """Реальная проверка стиля после правки: все фикстуры CRLF, поэтому ни одного
    ОДИНОКОГО LF (каждый \\n — часть \\r\\n) + формат-слой round-trips.
    Прежний save(load)==f сюда НЕ годится: он истинен для любого файла."""
    raw = work.read_bytes()
    if raw.count(b"\n") != raw.count(b"\r\n"):
        return False                       # появился LF-разрыв в CRLF-файле
    tmp2 = tmp_path / f"rt{suffix}"
    cfg.save(cfg.load(work), tmp2)
    return tmp2.read_bytes() == raw


# ---------- P4: измерения/ресурсы регистра ----------

def test_change_dimension_type_cfg(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — edit-type адресует Dimension, не только Attribute
    work = _copy(tmp_path, "register.xml")
    edit_type(work, "Измерение1", "xs:string", length=20)
    doc = cfg.load(work)
    d = doc.xpath("//md:Dimension[md:Properties/md:Name='Измерение1']", namespaces=NS)[0]
    assert d.xpath(".//v8:Type/text()", namespaces=NS) == ["xs:string"]
    assert d.xpath(".//v8:StringQualifiers/v8:Length/text()", namespaces=NS) == ["20"]
    assert _roundtrips(work, tmp_path, ".xml")


def test_change_resource_type_cfg(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — Resource
    work = _copy(tmp_path, "register.xml")
    edit_type(work, "Ресурс1", "cfg:CatalogRef.ТестовыйСправочник")
    doc = cfg.load(work)
    r = doc.xpath("//md:Resource[md:Properties/md:Name='Ресурс1']", namespaces=NS)[0]
    assert r.xpath(".//v8:Type/text()", namespaces=NS) == ["cfg:CatalogRef.ТестовыйСправочник"]
    assert _roundtrips(work, tmp_path, ".xml")


def test_change_dimension_type_edt(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — EDT dimensions
    work = _copy(tmp_path, "register.mdo")
    edit_type(work, "Измерение1", "CatalogRef.Товары")
    doc = cfg.load(work)
    d = doc.xpath("//dimensions[name='Измерение1']")[0]
    assert d.xpath("type/types/text()") == ["CatalogRef.Товары"]
    assert _roundtrips(work, tmp_path, ".mdo")


def test_change_resource_type_edt(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — EDT resources
    work = _copy(tmp_path, "register.mdo")
    edit_type(work, "Ресурс1", "Number", precision=15, scale=2)
    doc = cfg.load(work)
    r = doc.xpath("//resources[name='Ресурс1']")[0]
    assert r.xpath("type/types/text()") == ["Number"]
    assert r.xpath("type/numberQualifiers/precision/text()") == ["15"]
    assert r.xpath("type/numberQualifiers/scale/text()") == ["2"]
    assert _roundtrips(work, tmp_path, ".mdo")


# ---------- P4: объект-уровневый тип (SessionParameter/Constant), member=None ----------

def test_object_level_type_sessionparameter_cfg(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — тип самого объекта
    work = _copy(tmp_path, "sessionparameter.xml")
    edit_type(work, None, "cfg:CatalogRef.ТестовыйСправочник")
    doc = cfg.load(work)
    t = doc.xpath("//md:SessionParameter/md:Properties/md:Type/v8:Type/text()", namespaces=NS)
    assert t == ["cfg:CatalogRef.ТестовыйСправочник"]
    assert _roundtrips(work, tmp_path, ".xml")


def test_object_level_type_constant_edt(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — тип константы EDT
    work = _copy(tmp_path, "constant.mdo")
    edit_type(work, None, "String", length=10)
    doc = cfg.load(work)
    assert doc.xpath("/mdclass:Constant/type/types/text()",
                     namespaces={"mdclass": "http://g5.1c.ru/v8/dt/metadata/mdclass"}) == ["String"]
    assert doc.xpath("/mdclass:Constant/type/stringQualifiers/length/text()",
                     namespaces={"mdclass": "http://g5.1c.ru/v8/dt/metadata/mdclass"}) == ["10"]
    assert _roundtrips(work, tmp_path, ".mdo")


# ---------- P4: тип «с нуля» на бестиповом поле ----------

def test_type_from_scratch_ref_cfg(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — создать блок Type у поля без типа
    work = _copy(tmp_path, "untyped.xml")
    edit_type(work, "БезТипа", "cfg:CatalogRef.ТестовыйСправочник")
    doc = cfg.load(work)
    a = doc.xpath("//md:Attribute[md:Properties/md:Name='БезТипа']", namespaces=NS)[0]
    assert a.xpath("md:Properties/md:Type/v8:Type/text()", namespaces=NS) == ["cfg:CatalogRef.ТестовыйСправочник"]
    assert _roundtrips(work, tmp_path, ".xml")


def test_type_from_scratch_string_length_cfg(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — тип с нуля + строковый квалификатор
    work = _copy(tmp_path, "untyped.xml")
    edit_type(work, "БезТипа", "xs:string", length=100)
    doc = cfg.load(work)
    a = doc.xpath("//md:Attribute[md:Properties/md:Name='БезТипа']", namespaces=NS)[0]
    assert a.xpath("md:Properties/md:Type/v8:Type/text()", namespaces=NS) == ["xs:string"]
    assert a.xpath("md:Properties/md:Type/v8:StringQualifiers/v8:Length/text()", namespaces=NS) == ["100"]
    assert _roundtrips(work, tmp_path, ".xml")


def test_type_from_scratch_number_edt(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — тип с нуля EDT + числовой квалификатор
    work = _copy(tmp_path, "untyped.mdo")
    edit_type(work, "БезТипа", "Number", precision=10, scale=3)
    doc = cfg.load(work)
    a = doc.xpath("//attributes[name='БезТипа']")[0]
    assert a.xpath("type/types/text()") == ["Number"]
    assert a.xpath("type/numberQualifiers/precision/text()") == ["10"]
    assert a.xpath("type/numberQualifiers/scale/text()") == ["3"]
    assert _roundtrips(work, tmp_path, ".mdo")


# ---------- P4: составной тип (несколько типов в одном поле) ----------

def test_composite_type_cfg(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — составной тип, несколько v8:Type + отступы вставки
    work = _copy(tmp_path, "catalog.xml")
    edit_type(work, "Реквизит1", ["cfg:CatalogRef.Товары", "cfg:CatalogRef.Услуги"])
    doc = cfg.load(work)
    a = doc.xpath("//md:Attribute[md:Properties/md:Name='Реквизит1']", namespaces=NS)[0]
    assert a.xpath(".//v8:Type/text()", namespaces=NS) == ["cfg:CatalogRef.Товары", "cfg:CatalogRef.Услуги"]
    assert a.xpath(".//v8:StringQualifiers", namespaces=NS) == []   # у составного квалификаторов нет
    assert _roundtrips(work, tmp_path, ".xml")
    # DISCIPLINE_ALLOW_TEST_EDIT: исправление ошибочного ассерта — считал <v8:Type> ВСЕГО файла
    # (ловил соседний реквизит); проверяем byte-perfect отступы вставки по tail'ам ИМЕННО этого реквизита.
    v8els = a.xpath(".//v8:Type", namespaces=NS)
    assert len(v8els) == 2
    ind = lambda el: (el.tail or "").split("\n")[-1]
    lead_child, lead_close = ind(v8els[0]), ind(v8els[1])   # 6 табов / 5 табов
    assert lead_child.startswith(lead_close) and len(lead_child) > len(lead_close)


def test_composite_type_edt(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — составной тип EDT, несколько types
    work = _copy(tmp_path, "catalog.mdo")
    edit_type(work, "Реквизит1", ["CatalogRef.Товары", "CatalogRef.Услуги", "String"])
    doc = cfg.load(work)
    a = doc.xpath("//attributes[name='Реквизит1']")[0]
    assert a.xpath("type/types/text()") == ["CatalogRef.Товары", "CatalogRef.Услуги", "String"]
    assert _roundtrips(work, tmp_path, ".mdo")


def test_composite_then_single_collapses_cfg(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — из составного обратно в одиночный
    work = _copy(tmp_path, "catalog.xml")
    edit_type(work, "Реквизит1", ["cfg:CatalogRef.Товары", "cfg:CatalogRef.Услуги"])
    edit_type(work, "Реквизит1", "xs:string", length=15)
    doc = cfg.load(work)
    a = doc.xpath("//md:Attribute[md:Properties/md:Name='Реквизит1']", namespaces=NS)[0]
    assert a.xpath(".//v8:Type/text()", namespaces=NS) == ["xs:string"]
    assert a.xpath(".//v8:StringQualifiers/v8:Length/text()", namespaces=NS) == ["15"]
    assert _roundtrips(work, tmp_path, ".xml")


def test_composite_with_qualifier_rejected(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: red — квалификатор к составному типу — отказ
    work = _copy(tmp_path, "catalog.xml")
    before = work.read_bytes()
    with pytest.raises(OpPreconditionError):
        edit_type(work, "Реквизит1", ["xs:string", "cfg:CatalogRef.Товары"], length=10)
    assert work.read_bytes() == before
