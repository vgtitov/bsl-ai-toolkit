"""1c-meta object add|adopt|delete: golden по эталонам IDE (EDT 2025.2.6.4, выгрузка в XML тем же EDT).

Фикстуры `tests/fixtures/objects/ide` — файлы, которые EDT создал сам (create_metadata, пресеты общего
модуля, modify_metadata для регистров) и выгрузил в формат Конфигуратора; движок должен дать то же байт в
байт, кроме uuid. `skeleton` — манифесты тех же проектов без строк объектов.
"""
import os
import random
import re
import shutil
from pathlib import Path

import pytest

from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops import object as obj

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "objects"
MARK = "МаркерЭталона"
SYN = {"ru": "Маркер синоним"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
KINDS = ["CommonModule", "InformationRegister", "AccumulationRegister", "Catalog", "Document", "Enum",
         "Constant", "DataProcessor", "Report"]
VARIANTS = {"CommonModule": ["ServerCall", "ClientManaged", "ClientServer", "Global", "ServerPrivileged"],
            "InformationRegister": ["Periodic"], "AccumulationRegister": ["Turnover"]}
PARAMS = {
    "CommonModule": {"context": "server"},
    "CommonModule@ServerCall": {"context": "server-call"},
    "CommonModule@ClientManaged": {"context": "client"},
    "CommonModule@ClientServer": {"context": "client-server"},
    "CommonModule@Global": {"context": "global"},
    "CommonModule@ServerPrivileged": {"context": "server", "privileged": True},
    "InformationRegister": {"periodicity": "Nonperiodical", "write_mode": "Independent"},
    "InformationRegister@Periodic": {"periodicity": "Month", "write_mode": "RecorderSubordinate"},
    "AccumulationRegister": {"register_type": "Balance"},
    "AccumulationRegister@Turnover": {"register_type": "Turnovers"},
}
KEYS = [k + (f"@{v}" if v else "") for k in KINDS for v in ["", *VARIANTS.get(k, [])]]


def norm(text: str) -> str:
    """uuid → порядковый номер первого появления; без BOM; LF."""
    seen = {}
    text = text.lstrip("﻿").replace("\r\n", "\n")
    return UUID.sub(lambda m: "U%d" % seen.setdefault(m.group(), len(seen) + 1), text)


def read(p: Path) -> str:
    return p.read_bytes().decode("utf-8")


def project(tmp_path: Path, sk: str) -> Path:
    dst = tmp_path / sk
    shutil.copytree(FIX / "skeleton" / sk, dst)
    return dst


def add_key(src: Path, key: str, prefix: str = ""):
    kind, _, var = key.partition("@")
    return obj.add(src, kind, prefix + MARK + var, SYN, **PARAMS.get(key, {}))


@pytest.mark.parametrize("fmt", ["edt", "cfg"])
@pytest.mark.parametrize("key", KEYS)
def test_add_matches_ide(tmp_path, fmt, key):
    src = project(tmp_path, f"{fmt}_base")
    path = add_key(src, key)
    ide = FIX / "ide" / fmt / f"{key}.{'mdo' if fmt == 'edt' else 'xml'}"
    assert norm(read(path)) == norm(read(ide))
    if fmt == "cfg":
        assert path.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("fmt", ["edt", "cfg"])
def test_manifest_order_matches_ide_in_any_add_order(tmp_path, fmt):
    src = project(tmp_path, f"{fmt}_base")
    kinds = KINDS[:]
    random.Random(7).shuffle(kinds)
    for kind in kinds:
        for v in ["", *VARIANTS.get(kind, [])]:
            add_key(src, kind + (f"@{v}" if v else ""))
    manifest = src / ("Configuration/Configuration.mdo" if fmt == "edt" else "Configuration.xml")
    assert norm(read(manifest)) == norm(read(FIX / "ide" / f"{fmt}_base_manifest.{'mdo' if fmt == 'edt' else 'xml'}"))


@pytest.mark.parametrize("fmt", ["edt", "cfg"])
def test_extension_adopt_and_own_match_ide_manifest(tmp_path, fmt):
    """Как в прогоне EDT: по каждому виду заимствование, потом свой объект мр_…; манифест — как у IDE.
    Конфигуратор: в расширении список объектов был пустым (<ChildObjects> без строк)."""
    base = project(tmp_path, f"{fmt}_base")
    for kind in KINDS:
        add_key(base, kind)
    ext = project(tmp_path, f"{fmt}_ext")
    for kind in KINDS:
        obj.adopt(ext, base, f"{kind}.{MARK}")
        add_key(ext, kind, prefix="мр_")
    manifest = ext / ("Configuration/Configuration.mdo" if fmt == "edt" else "Configuration.xml")
    assert norm(read(manifest)) == norm(read(FIX / "ide" / f"{fmt}_ext_manifest.{'mdo' if fmt == 'edt' else 'xml'}"))


def test_adopted_edt_links_base_and_copies_common_module_flags(tmp_path):
    base = project(tmp_path, "edt_base")
    b_cat = obj.add(base, "Catalog", "Валюты", {})
    b_cm = obj.add(base, "CommonModule", "ОбщийКлиент", {}, context="client")
    ext = project(tmp_path, "edt_ext")
    cat = read(obj.adopt(ext, base, "Справочник.Валюты"))
    base_uuid = re.search(r' uuid="([^"]+)"', read(b_cat)).group(1)
    assert f'extendedConfigurationObject="{base_uuid}"' in cat
    assert re.search(r"<extension xsi:type=\"mdclassExtension:CatalogExtension\">\s*"
                     r"<extendedConfigurationObject>Checked</extendedConfigurationObject>\s*</extension>", cat)
    assert "<objectBelonging>Adopted</objectBelonging>" in cat and "<synonym>" not in cat
    assert re.search(r' uuid="([^"]+)"', cat).group(1) != base_uuid          # свой uuid
    cm = read(obj.adopt(ext, base, "CommonModule.ОбщийКлиент"))
    flags = re.findall(r"^  <(\w+)>true</\1>", cm, re.M)
    assert flags == re.findall(r"^  <(\w+)>true</\1>", read(b_cm), re.M) == [
        "clientManagedApplication", "clientOrdinaryApplication"]


def test_adopted_cfg_links_base_and_copies_flags(tmp_path):
    base = project(tmp_path, "cfg_base")
    b_cm = obj.add(base, "CommonModule", "ОбщийКлиент", {}, context="client")
    ext = project(tmp_path, "cfg_ext")
    cm = read(obj.adopt(ext, base, "CommonModule.ОбщийКлиент"))
    base_uuid = re.search(r'<CommonModule uuid="([^"]+)"', read(b_cm)).group(1)
    assert f"<ExtendedConfigurationObject>{base_uuid}</ExtendedConfigurationObject>" in cm
    assert "<ClientManagedApplication>true</ClientManagedApplication>" in cm
    assert "<Server>false</Server>" in cm


@pytest.mark.parametrize("fmt", ["edt", "cfg"])
def test_adopted_register_copies_controlled_properties(tmp_path, fmt):
    """Проверка в EDT 2025.2.6.4 дала «значение контролируемого свойства Периодичность/Режим записи/Вид
    регистра не совпадает с расширяемой конфигурацией», пока значения не брались из типовой."""
    base = project(tmp_path, f"{fmt}_base")
    obj.add(base, "InformationRegister", "Цены", {}, periodicity="Month", write_mode="RecorderSubordinate")
    obj.add(base, "AccumulationRegister", "Обороты", {}, register_type="Turnovers")
    obj.add(base, "InformationRegister", "Простой", {}, periodicity="Nonperiodical", write_mode="Independent")
    ext = project(tmp_path, f"{fmt}_ext")
    ir = read(obj.adopt(ext, base, "InformationRegister.Цены"))
    ar = read(obj.adopt(ext, base, "AccumulationRegister.Обороты"))
    plain = read(obj.adopt(ext, base, "InformationRegister.Простой"))
    if fmt == "edt":
        assert re.findall(r"^  <(\w+)>(\w+)</\1>", ir, re.M)[-2:] == [
            ("informationRegisterPeriodicity", "Month"), ("writeMode", "RecorderSubordinate")]
        assert "  <registerType>Turnovers</registerType>\n" in ar.replace("\r\n", "\n")
        assert "informationRegisterPeriodicity>Month" not in plain and "<writeMode>Recorder" not in plain
    else:
        assert "<InformationRegisterPeriodicity>Month<" in ir and "<WriteMode>RecorderSubordinate<" in ir
        assert "<RegisterType>Turnovers<" in ar
        assert "<InformationRegisterPeriodicity>Nonperiodical<" in plain


EDT_NUMBER_TYPE = ("  <type>\n    <types>Number</types>\n    <numberQualifiers>\n      <precision>15</precision>\n"
                   "      <scale>2</scale>\n    </numberQualifiers>\n  </type>\n")
# блок, который EDT 2025.2.6.4 сам написал при заимствовании константы Число(15,2) (проба 09.11)
EDT_NUMBER_EXT = ("    <typeExtension>\n      <types>\n        <state>Checked</state>\n        <type>Number</type>\n"
                  "      </types>\n      <numberQualifiers>\n        <precision>15</precision>\n        <scale>2</scale>\n"
                  "      </numberQualifiers>\n    </typeExtension>\n")


def test_adopted_constant_takes_type_from_base_edt(tmp_path):
    base = project(tmp_path, "edt_base")
    b = obj.add(base, "Constant", "КЧисло", {})
    t = read(b)
    b.write_text(re.sub(r"  <type>\n.*?  </type>\n", lambda _: EDT_NUMBER_TYPE, t, flags=re.S), encoding="utf-8")
    ext = project(tmp_path, "edt_ext")
    a = read(obj.adopt(ext, base, "Constant.КЧисло"))
    assert EDT_NUMBER_EXT in a and "String" not in a
    b2 = obj.add(base, "Constant", "КСостав", {})
    b2.write_text(re.sub(r"  <type>\n.*?  </type>\n",
                         lambda _: "  <type>\n    <types>CatalogRef.Валюты</types>\n    <types>Boolean</types>\n  </type>\n",
                         read(b2), flags=re.S), encoding="utf-8")
    a2 = read(obj.adopt(ext, base, "Constant.КСостав"))
    assert re.findall(r"<type>(\S+)</type>", a2) == ["CatalogRef.Валюты", "Boolean"]


def test_adopted_constant_takes_type_from_base_cfg(tmp_path):
    base = project(tmp_path, "cfg_base")
    b = obj.add(base, "Constant", "КЧисло", {})
    block = ("\t\t\t<Type>\n\t\t\t\t<v8:Type>xs:decimal</v8:Type>\n\t\t\t\t<v8:NumberQualifiers>\n"
             "\t\t\t\t\t<v8:Digits>15</v8:Digits>\n\t\t\t\t\t<v8:FractionDigits>2</v8:FractionDigits>\n"
             "\t\t\t\t\t<v8:AllowedSign>Any</v8:AllowedSign>\n\t\t\t\t</v8:NumberQualifiers>\n\t\t\t</Type>\n")
    t = read(b).replace("\r\n", "\n")
    b.write_bytes(re.sub(r"\t\t\t<Type>\n.*?\t\t\t</Type>\n", lambda _: block, t, flags=re.S).encode("utf-8"))
    ext = project(tmp_path, "cfg_ext")
    a = read(obj.adopt(ext, base, "Constant.КЧисло")).replace("\r\n", "\n")
    assert block in a and "xs:string" not in a


def test_adopted_common_module_does_not_carry_privileged(tmp_path):
    """Проба EDT 09.11: privileged и returnValuesReuse у заимствованного не контролируются и не переносятся."""
    base = project(tmp_path, "edt_base")
    obj.add(base, "CommonModule", "Прив", {}, context="server", privileged=True)
    ext = project(tmp_path, "edt_ext")
    a = read(obj.adopt(ext, base, "CommonModule.Прив"))
    assert re.findall(r"^  <(\w+)>true</\1>", a, re.M) == ["server"]      # пресет EDT: только server
    assert "privileged" not in a


def test_delete_rolls_back_when_manifest_write_fails(tmp_path, monkeypatch):
    src = project(tmp_path, "cfg_base")
    path = obj.add(src, "Catalog", "Товары", SYN)
    manifest = src / "Configuration.xml"
    before = manifest.read_bytes()

    real = obj._write_atomic

    def boom(*a, **kw):
        raise OSError("диск занят")

    def written_then_boom(path_, text):                   # манифест уже записан, сбой после
        real(path_, text)
        raise OSError("сбой после записи")
    for fail in (boom, written_then_boom):
        monkeypatch.setattr(obj, "_write_atomic", fail)
        with pytest.raises(OSError):
            obj.delete(src, "Catalog.Товары")
        assert path.is_file() and manifest.read_bytes() == before
        assert not list(src.glob(".object_delete_*"))


@pytest.mark.parametrize("fmt", ["edt", "cfg"])
def test_adopt_refusals(tmp_path, fmt):
    base = project(tmp_path, f"{fmt}_base")
    ext = project(tmp_path, f"{fmt}_ext")
    with pytest.raises(OpPreconditionError, match="нет в основной"):
        obj.adopt(ext, base, "Catalog.НетТакого")
    obj.add(base, "Catalog", "Валюты", {})
    with pytest.raises(OpPreconditionError, match="не расширение"):
        obj.adopt(base, base, "Catalog.Валюты")
    with pytest.raises(OpPreconditionError, match="это расширение"):
        obj.adopt(ext, ext, "Catalog.Валюты")
    obj.adopt(ext, base, "Catalog.Валюты")
    with pytest.raises(OpPreconditionError, match="уже есть"):
        obj.adopt(ext, base, "Catalog.Валюты")


@pytest.mark.parametrize("fmt", ["edt", "cfg"])
def test_delete_refuses_with_references_then_restores_manifest(tmp_path, fmt):
    src = project(tmp_path, f"{fmt}_base")
    manifest = src / ("Configuration/Configuration.mdo" if fmt == "edt" else "Configuration.xml")
    before = manifest.read_bytes()
    obj.add(src, "Catalog", "Товары", SYN)
    cm = obj.add(src, "CommonModule", "Работа", {}, context="server")
    module = cm.parent / "Module.bsl" if fmt == "edt" else cm.parent / "Работа" / "Ext" / "Module.bsl"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("Функция Т()\n\tВозврат Справочники.Товары.ПустаяСсылка();\nКонецФункции\n", encoding="utf-8")
    with pytest.raises(OpPreconditionError, match="Module.bsl:2"):
        obj.delete(src, "Catalog.Товары")
    module.write_text("Процедура Т()\nКонецПроцедуры\n", encoding="utf-8")
    removed = obj.delete(src, "Справочник.Товары")
    assert removed and not any(x.exists() for x in removed)
    obj.delete(src, "CommonModule.Работа")
    assert manifest.read_bytes() == before


def test_delete_common_module_finds_bare_calls(tmp_path):
    src = project(tmp_path, "edt_base")
    obj.add(src, "CommonModule", "Работа", {}, context="server")
    other = obj.add(src, "CommonModule", "Другой", {}, context="server")
    (other.parent / "Module.bsl").write_text("Процедура П()\n\tРабота.Сделать();\nКонецПроцедуры\n", encoding="utf-8")
    with pytest.raises(OpPreconditionError, match="Другой/Module.bsl:2"):
        obj.delete(src, "CommonModule.Работа")


@pytest.mark.parametrize("fmt", ["edt", "cfg"])
def test_delete_adopted_only_when_bare(tmp_path, fmt):
    base = project(tmp_path, f"{fmt}_base")
    obj.add(base, "Catalog", "Валюты", {})
    ext = project(tmp_path, f"{fmt}_ext")
    path = obj.adopt(ext, base, "Catalog.Валюты")
    text = read(path)
    if fmt == "edt":
        path.write_bytes(text.replace("  <extension", "  <attributes uuid=\"x\"/>\n  <extension").encode("utf-8"))
    else:
        path.write_bytes(text.replace("<ChildObjects/>", "<ChildObjects>\n<Attribute/>\n</ChildObjects>").encode("utf-8"))
    with pytest.raises(OpPreconditionError, match="есть своё"):
        obj.delete(ext, "Catalog.Валюты")
    path.write_bytes(text.encode("utf-8"))
    # код расширения может обращаться к объекту типовой и без заимствования — он не держит удаление
    (ext / "Код.bsl").write_text("А = Справочники.Валюты.ПустаяСсылка();\n", encoding="utf-8")
    obj.delete(ext, "Catalog.Валюты")
    assert not path.exists()


@pytest.mark.parametrize("fmt", ["edt", "cfg"])
def test_crlf_project_stays_crlf(tmp_path, fmt):
    src = project(tmp_path, f"{fmt}_base")
    manifest = src / ("Configuration/Configuration.mdo" if fmt == "edt" else "Configuration.xml")
    manifest.write_bytes(manifest.read_bytes().replace(b"\n", b"\r\n"))
    path = obj.add(src, "Catalog", "Товары", SYN)
    for f in (path, manifest):
        b = f.read_bytes()
        assert b.count(b"\n") == b.count(b"\r\n") > 0, f.name


def test_extension_prefix_and_required_params(tmp_path):
    ext = project(tmp_path, "edt_ext")
    with pytest.raises(OpPreconditionError, match="префикса 'мр_'"):
        obj.add(ext, "Catalog", "Товары", {})
    obj.add(ext, "Catalog", "мр_Товары", {})
    base = project(tmp_path, "edt_base")
    with pytest.raises(OpPreconditionError, match="--context"):
        obj.add(base, "CommonModule", "М", {})
    with pytest.raises(OpPreconditionError, match="только с --context server"):
        obj.add(base, "CommonModule", "М", {}, context="client", privileged=True)
    with pytest.raises(OpPreconditionError, match="--periodicity"):
        obj.add(base, "InformationRegister", "Р", {}, write_mode="Independent")
    with pytest.raises(OpPreconditionError, match="допустимо"):
        obj.add(base, "AccumulationRegister", "Р", {}, register_type="Остатки")
    with pytest.raises(OpPreconditionError, match="не относится"):
        obj.add(base, "Catalog", "С", {}, periodicity="Month")
    with pytest.raises(OpPreconditionError, match="не поддержан"):
        obj.add(base, "ChartOfAccounts", "П", {})
    obj.add(base, "Catalog", "Товары", {})
    with pytest.raises(OpPreconditionError, match="уже есть"):
        obj.add(base, "Справочник", "Товары", {})


def test_synonyms_several_languages_and_none(tmp_path):
    src = project(tmp_path, "cfg_base")
    text = read(obj.add(src, "Catalog", "Товары", {"ru": "Товары & услуги", "en": "Goods"}))
    assert "<v8:content>Товары &amp; услуги</v8:content>" in text and "<v8:lang>en</v8:lang>" in text
    assert "<Synonym/>" in read(obj.add(src, "Catalog", "Прочее", {}))
    edt = project(tmp_path, "edt_base")
    t = read(obj.add(edt, "Document", "Заказ", {"ru": "Заказ", "en": "Order"}))
    assert t.count("<synonym>") == 2 and "<key>en</key>" in t
    assert "<synonym>" not in read(obj.add(edt, "Document", "Прочее", {}))


def test_cfg_format_version_mismatch_refused(tmp_path):
    src = project(tmp_path, "cfg_base")
    m = src / "Configuration.xml"
    m.write_bytes(m.read_bytes().replace(b'version="2.20"', b'version="2.18"'))
    with pytest.raises(OpPreconditionError, match="2.18"):
        obj.add(src, "Catalog", "Товары", {})


CORPUS = os.environ.get("OBJECT_CORPUS_EDT_SRC")      # src EDT-проекта типовой, напр. ERP 2.5


@pytest.mark.skipif(not CORPUS, reason="нет OBJECT_CORPUS_EDT_SRC")
@pytest.mark.parametrize("kind", KINDS)
def test_template_tags_exist_in_corpus(kind):
    """Каждое свойство базового эталона встречается у реальных объектов вида (эталон не от «странной»
    версии). Варианты — пресеты самого EDT; их свойства корпус может не знать (в ERP нет
    привилегированных общих модулей)."""
    root = Path(CORPUS) / obj.META["kinds"][kind]["dir"]
    seen = set()
    for f in root.glob("*/*.mdo"):
        seen |= set(re.findall(r"^  <(\w+)", read(f), re.M))
    tpl = (obj.TPL / "edt" / "own" / f"{kind}.mdo").read_text(encoding="utf-8")
    assert set(re.findall(r"^  <(\w+)", tpl, re.M)) <= seen, kind
