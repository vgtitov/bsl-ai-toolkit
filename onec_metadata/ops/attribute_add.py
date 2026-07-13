"""Добавление реквизита объекту метаданных (Конфигуратор-XML).

Новый реквизит — deepcopy последнего существующего <Attribute> (наследует
отступы и полный набор свойств, как сгенерировал бы Конфигуратор) с заменой
uuid / Name / Synonym / Type. Объект без единого реквизита-образца в Фазе 1
не поддерживается (создание Attribute «с нуля» — отдельная операция).
"""
import copy
import uuid as uuidlib
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError


def add_attribute(object_xml: Path, name: str, type_ref: str,
                  synonym: str) -> None:
    doc = cfg.load(object_xml)

    names = doc.xpath("//md:Attribute/md:Properties/md:Name/text()",
                      namespaces=cfg.NS)
    if name in names:
        raise OpPreconditionError(f"Реквизит '{name}' уже существует")

    attrs = doc.xpath("//md:Attribute", namespaces=cfg.NS)
    if not attrs:
        raise OpPreconditionError(
            "У объекта нет реквизита-образца (создание с нуля — вне Фазы 1)")
    sample = attrs[-1]

    new = copy.deepcopy(sample)
    new.set("uuid", str(uuidlib.uuid4()))

    props = new.xpath("md:Properties", namespaces=cfg.NS)[0]
    props.xpath("md:Name", namespaces=cfg.NS)[0].text = name

    syn = props.xpath("md:Synonym", namespaces=cfg.NS)[0]
    for content in syn.xpath(".//v8:content", namespaces=cfg.NS):
        content.text = synonym

    type_el = props.xpath("md:Type", namespaces=cfg.NS)[0]
    v8types = type_el.xpath(".//v8:Type", namespaces=cfg.NS)
    if not v8types:
        raise OpPreconditionError("У реквизита-образца нет v8:Type")
    v8types[0].text = type_ref
    for extra in v8types[1:]:  # составной тип образца → одиночный у нового
        extra.getparent().remove(extra)
    # квалификаторы строк/чисел образца не соответствуют новому типу — убрать
    for qual in type_el.xpath(
            ".//v8:StringQualifiers | .//v8:NumberQualifiers | "
            ".//v8:DateQualifiers", namespaces=cfg.NS):
        qual.getparent().remove(qual)

    sample.addnext(new)
    cfg.save(doc, object_xml)
