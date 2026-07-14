"""Смена/расширение типа СУЩЕСТВУЮЩЕГО реквизита.

Два безопасных случая:
- сменить тип на одиночный `type_ref` (напр. ссылочный `cfg:CatalogRef.Товары`
  или простой `xs:date`) — лишние типы и квалификаторы снимаются;
- расширить строку: `type_ref` строковый + `length` — меняем ТЕКСТ существующего
  строкового квалификатора длины.

Создание квалификатора «с нуля» (был не-строковый тип → строка с длиной) не
поддержано: сборка блока квалификаторов с нуля хрупка для byte-perfect — отдельная
операция. Составной тип (несколько типов) — тоже отдельно. Оба формата.
"""
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name


def _is_string_type(type_ref: str) -> bool:
    return type_ref.split(":")[-1].lower() == "string"


def edit_type(object_path: Path, attribute: str, type_ref: str,
              length: int | None = None) -> None:
    validate_name(attribute)
    if length is not None and not _is_string_type(type_ref):
        raise OpPreconditionError(
            "Параметр length применим только к строковому типу")

    if str(object_path).endswith(".mdo"):
        _edit_edt(object_path, attribute, type_ref, length)
    else:
        _edit_configurator(object_path, attribute, type_ref, length)


# ---------- Конфигуратор (*.xml) ----------

def _edit_configurator(path: Path, attribute: str, type_ref: str,
                       length: int | None) -> None:
    doc = cfg.load(path)
    attrs = doc.xpath(
        f"//md:Attribute[md:Properties/md:Name='{attribute}']", namespaces=cfg.NS)
    if not attrs:
        raise OpPreconditionError(f"Реквизит '{attribute}' не найден")
    type_els = attrs[0].xpath("md:Properties/md:Type", namespaces=cfg.NS)
    if not type_els:
        raise OpPreconditionError(f"У реквизита '{attribute}' нет блока Type")
    type_el = type_els[0]

    v8types = type_el.xpath("v8:Type", namespaces=cfg.NS)
    if not v8types:
        raise OpPreconditionError(f"У реквизита '{attribute}' нет v8:Type")
    v8types[0].text = type_ref
    to_remove = list(v8types[1:])

    if length is not None:
        lengths = type_el.xpath("v8:StringQualifiers/v8:Length", namespaces=cfg.NS)
        if not lengths:
            raise OpPreconditionError(
                "У реквизита нет строкового квалификатора длины "
                "(создание с нуля — отдельная операция)")
        lengths[0].text = str(length)
    else:
        to_remove += type_el.xpath(
            "v8:StringQualifiers | v8:NumberQualifiers | v8:DateQualifiers",
            namespaces=cfg.NS)
    cfg.remove_children(type_el, to_remove)
    cfg.save(doc, path)


# ---------- EDT (*.mdo) ----------

def _edit_edt(path: Path, attribute: str, type_ref: str,
              length: int | None) -> None:
    doc = cfg.load(path)
    attrs = doc.xpath(f"//attributes[name='{attribute}']")
    if not attrs:
        raise OpPreconditionError(f"Реквизит '{attribute}' не найден")
    type_els = attrs[0].xpath("type")
    if not type_els:
        raise OpPreconditionError(f"У реквизита '{attribute}' нет блока type")
    type_el = type_els[0]

    types = type_el.xpath("types")
    if not types:
        raise OpPreconditionError(f"У реквизита '{attribute}' нет type/types")
    types[0].text = type_ref
    to_remove = list(types[1:])

    if length is not None:
        lengths = type_el.xpath("stringQualifiers/length")
        if not lengths:
            raise OpPreconditionError(
                "У реквизита нет строкового квалификатора длины "
                "(создание с нуля — отдельная операция)")
        lengths[0].text = str(length)
    else:
        to_remove += type_el.xpath(
            "stringQualifiers | numberQualifiers | dateQualifiers")
    cfg.remove_children(type_el, to_remove)
    cfg.save(doc, path)
