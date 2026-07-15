"""Добавление реквизита объекту метаданных.

Поддерживаются оба формата исходников (диспетчеризация по расширению файла):
- Конфигуратор (`*.xml`): <Attribute> в ChildObjects (MDClasses);
- EDT (`*.mdo`): блок <attributes> (mdclass).

Новый реквизит — deepcopy последнего существующего (наследует отступы и
структуру, как сгенерировала бы IDE) с заменой uuid / имени / синонима / типа.
Объект без единого реквизита-образца не поддерживается (создание «с нуля» —
отдельная операция).
"""
import copy
import uuid as uuidlib
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name, validate_type_ref


def add_attribute(object_path: Path, name: str, type_ref: str,
                  synonym: str) -> None:
    validate_name(name)
    validate_type_ref(type_ref)
    if str(object_path).endswith(".mdo"):
        _add_attribute_edt(object_path, name, type_ref, synonym)
    else:
        _add_attribute_configurator(object_path, name, type_ref, synonym)


def _add_attribute_configurator(object_xml: Path, name: str, type_ref: str,
                                synonym: str) -> None:
    doc = cfg.load(object_xml)

    names = doc.xpath("//md:Attribute/md:Properties/md:Name/text()",
                      namespaces=cfg.NS)
    if name in names:
        raise OpPreconditionError(f"Реквизит '{name}' уже существует")

    attrs = doc.xpath("//md:Attribute", namespaces=cfg.NS)
    if not attrs:
        raise OpPreconditionError(
            "У объекта нет реквизита-образца (создание с нуля — отдельная операция)")
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
    # составной тип образца → одиночный у нового; квалификаторы образца не
    # соответствуют новому типу — убрать (с сохранением отступа закрывающего тега)
    to_remove = list(v8types[1:]) + type_el.xpath(
        "v8:StringQualifiers | v8:NumberQualifiers | v8:DateQualifiers",
        namespaces=cfg.NS)
    cfg.remove_children(type_el, to_remove)

    cfg.place_after(sample, new)
    cfg.save(doc, object_xml)


def _add_attribute_edt(mdo_path: Path, name: str, type_ref: str,
                       synonym: str) -> None:
    doc = cfg.load(mdo_path)

    names = doc.xpath("//attributes/name/text()")
    if name in names:
        raise OpPreconditionError(f"Реквизит '{name}' уже существует")

    attrs = doc.xpath("//attributes")
    if not attrs:
        raise OpPreconditionError(
            "У объекта нет реквизита-образца (создание с нуля — отдельная операция)")
    sample = attrs[-1]

    new = copy.deepcopy(sample)
    new.set("uuid", str(uuidlib.uuid4()))

    new.xpath("name")[0].text = name
    for value in new.xpath("synonym/value"):
        value.text = synonym

    type_el = new.xpath("type")[0]
    types = type_el.xpath("types")
    if not types:
        raise OpPreconditionError("У реквизита-образца нет type/types")
    types[0].text = type_ref
    to_remove = list(types[1:]) + type_el.xpath(
        "stringQualifiers | numberQualifiers | dateQualifiers")
    cfg.remove_children(type_el, to_remove)

    cfg.place_after(sample, new)
    cfg.save(doc, mdo_path)
