"""Generic-добавление дочернего элемента объекту метаданных.

Покрывает виды: Attribute (реквизит), Dimension (измерение регистра),
Resource (ресурс регистра), EnumValue (значение перечисления) — в обоих
форматах исходников (Конфигуратор `*.xml` / EDT `*.mdo`).

Паттерн тот же, что в attribute_add: deepcopy последнего элемента того же
вида (наследует отступы/структуру, как сгенерировала бы IDE), замена
uuid / имени / синонима / типа. Требуется элемент-образец того же вида.
"""
import copy
import uuid as uuidlib
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError

# вид → (тег Конфигуратора (ns md), тег EDT, элемент типизирован?)
KINDS = {
    "Attribute": ("Attribute", "attributes", True),
    "Dimension": ("Dimension", "dimensions", True),
    "Resource": ("Resource", "resources", True),
    "EnumValue": ("EnumValue", "enumValues", False),
}


def add_child(object_path: Path, kind: str, name: str, synonym: str,
              type_ref: str | None = None) -> None:
    if kind not in KINDS:
        raise OpPreconditionError(
            f"Неизвестный вид '{kind}'. Поддержаны: {', '.join(sorted(KINDS))}")
    cfg_tag, edt_tag, typed = KINDS[kind]
    if typed and not type_ref:
        raise OpPreconditionError(f"Для вида '{kind}' обязателен type_ref")

    if str(object_path).endswith(".mdo"):
        _add_edt(object_path, edt_tag, name, synonym, type_ref if typed else None)
    else:
        _add_configurator(object_path, cfg_tag, name, synonym,
                          type_ref if typed else None)


def _add_configurator(path: Path, tag: str, name: str, synonym: str,
                      type_ref: str | None) -> None:
    doc = cfg.load(path)

    names = doc.xpath(f"//md:{tag}/md:Properties/md:Name/text()",
                      namespaces=cfg.NS)
    if name in names:
        raise OpPreconditionError(f"{tag} '{name}' уже существует")

    samples = doc.xpath(f"//md:{tag}", namespaces=cfg.NS)
    if not samples:
        raise OpPreconditionError(
            f"У объекта нет элемента-образца {tag} (создание с нуля — отдельная операция)")
    sample = samples[-1]

    new = copy.deepcopy(sample)
    new.set("uuid", str(uuidlib.uuid4()))
    props = new.xpath("md:Properties", namespaces=cfg.NS)[0]
    props.xpath("md:Name", namespaces=cfg.NS)[0].text = name
    for content in props.xpath("md:Synonym//v8:content", namespaces=cfg.NS):
        content.text = synonym

    if type_ref is not None:
        type_els = props.xpath("md:Type", namespaces=cfg.NS)
        if not type_els:
            raise OpPreconditionError(f"У образца {tag} нет Type")
        v8types = type_els[0].xpath(".//v8:Type", namespaces=cfg.NS)
        if not v8types:
            raise OpPreconditionError(f"У образца {tag} нет v8:Type")
        v8types[0].text = type_ref
        for extra in v8types[1:]:
            extra.getparent().remove(extra)
        for qual in type_els[0].xpath(
                ".//v8:StringQualifiers | .//v8:NumberQualifiers | "
                ".//v8:DateQualifiers", namespaces=cfg.NS):
            qual.getparent().remove(qual)

    sample.addnext(new)
    cfg.save(doc, path)


def _add_edt(path: Path, tag: str, name: str, synonym: str,
             type_ref: str | None) -> None:
    doc = cfg.load(path)

    names = doc.xpath(f"//{tag}/name/text()")
    if name in names:
        raise OpPreconditionError(f"{tag} '{name}' уже существует")

    samples = doc.xpath(f"//{tag}")
    if not samples:
        raise OpPreconditionError(
            f"У объекта нет элемента-образца {tag} (создание с нуля — отдельная операция)")
    sample = samples[-1]

    new = copy.deepcopy(sample)
    new.set("uuid", str(uuidlib.uuid4()))
    new.xpath("name")[0].text = name
    for value in new.xpath("synonym/value"):
        value.text = synonym

    if type_ref is not None:
        type_els = new.xpath("type")
        if not type_els or not type_els[0].xpath("types"):
            raise OpPreconditionError(f"У образца {tag} нет type/types")
        types = type_els[0].xpath("types")
        types[0].text = type_ref
        for extra in types[1:]:
            type_els[0].remove(extra)
        for qual in type_els[0].xpath(
                "stringQualifiers | numberQualifiers | dateQualifiers"):
            type_els[0].remove(qual)

    sample.addnext(new)
    cfg.save(doc, path)
