"""Generic-добавление дочернего элемента объекту метаданных.

Виды: Attribute, Dimension, Resource, AddressingAttribute (типизированные),
EnumValue, TabularSection, Command (без типа) — в обоих форматах (Конфигуратор
`*.xml` и EDT `*.mdo`).

Опциональный `parent` — имя табличной части: элемент добавляется ВНУТРЬ неё
(например реквизит табличной части), иначе — в корень объекта.

Паттерн: deepcopy последнего элемента того же вида В ТОЙ ЖЕ ОБЛАСТИ (наследует
отступы/структуру, как сгенерировала бы IDE), замена uuid/имени/синонима/типа.
Поиск и вставка — по ПРЯМЫМ детям области (а не потомкам), чтобы корневые и
вложенные (в ТЧ) элементы не путались.
"""
import copy
import uuid as uuidlib
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name


def _regen_uuids(element) -> None:
    """Присвоить свежий uuid КАЖДОМУ элементу поддерева с атрибутом uuid.

    deepcopy копирует ВСЕ вложенные идентификаторы; без регенерации новый
    элемент дублирует uuid'ы образца (напр. вложенные реквизиты табличной части)
    → коллизия в конфигурации.
    """
    for el in element.xpath("descendant-or-self::*[@uuid]"):
        el.set("uuid", str(uuidlib.uuid4()))


def _reidentify_cfg(new, old_name: str, new_name: str) -> None:
    """Сделать идентификаторы нового поддерева уникальными (Конфигуратор).

    Помимо uuid: у элементов, ПОРОЖДАЮЩИХ тип (табличная часть), в InternalInfo
    есть GeneratedType с TypeId/ValueId и именем вида `Тип.Объект.Имя`. Их тоже
    регенерируем, а имя порождаемого типа перенацеливаем на новое имя элемента.
    """
    _regen_uuids(new)
    for tag in ("TypeId", "ValueId"):
        for el in new.xpath(f".//xr:{tag}", namespaces=cfg.NS):
            el.text = str(uuidlib.uuid4())
    for gt in new.xpath(".//xr:GeneratedType", namespaces=cfg.NS):
        nm = gt.get("name")
        if nm and old_name and nm.endswith("." + old_name):
            gt.set("name", nm[: -len(old_name)] + new_name)

# вид → (тег Конфигуратора (ns md), тег EDT, типизирован?)
KINDS = {
    "Attribute": ("Attribute", "attributes", True),
    "Dimension": ("Dimension", "dimensions", True),
    "Resource": ("Resource", "resources", True),
    "EnumValue": ("EnumValue", "enumValues", False),
    "TabularSection": ("TabularSection", "tabularSections", False),
    "Command": ("Command", "commands", False),
    # реквизит адресации Задачи (Task) — типизированный, как обычный реквизит
    "AddressingAttribute": ("AddressingAttribute", "addressingAttributes", True),
}


def add_child(object_path: Path, kind: str, name: str, synonym: str,
              type_ref: str | None = None, parent: str | None = None) -> None:
    if kind not in KINDS:
        raise OpPreconditionError(
            f"Неизвестный вид '{kind}'. Поддержаны: {', '.join(sorted(KINDS))}")
    validate_name(name)
    cfg_tag, edt_tag, typed = KINDS[kind]
    if typed and not type_ref:
        raise OpPreconditionError(f"Для вида '{kind}' обязателен type_ref")
    tref = type_ref if typed else None

    if str(object_path).endswith(".mdo"):
        _add_edt(object_path, edt_tag, name, synonym, tref, parent)
    else:
        _add_configurator(object_path, cfg_tag, name, synonym, tref, parent)


# ---------- Конфигуратор (*.xml) ----------

def _cfg_scope(doc, parent: str | None):
    """Элемент ChildObjects: корневой объекта или указанной табличной части."""
    if parent is None:
        roots = doc.xpath(
            "//md:ChildObjects[not(ancestor::md:TabularSection)]",
            namespaces=cfg.NS)
        if not roots:
            raise OpPreconditionError("У объекта нет корневого ChildObjects")
        return roots[0]
    ts = doc.xpath(
        f"//md:TabularSection[md:Properties/md:Name='{parent}']/md:ChildObjects",
        namespaces=cfg.NS)
    if not ts:
        raise OpPreconditionError(f"Табличная часть '{parent}' не найдена")
    return ts[0]


def _add_configurator(path: Path, tag: str, name: str, synonym: str,
                      type_ref: str | None, parent: str | None) -> None:
    doc = cfg.load(path)
    scope = _cfg_scope(doc, parent)

    names = scope.xpath(f"md:{tag}/md:Properties/md:Name/text()",
                        namespaces=cfg.NS)
    if name in names:
        raise OpPreconditionError(f"{tag} '{name}' уже существует")

    samples = scope.xpath(f"md:{tag}", namespaces=cfg.NS)
    if not samples:
        raise OpPreconditionError(
            f"В области нет элемента-образца {tag} (создание с нуля — отдельная операция)")
    sample = samples[-1]

    new = copy.deepcopy(sample)
    old_name = (new.xpath("md:Properties/md:Name/text()",
                          namespaces=cfg.NS) or [""])[0]
    _reidentify_cfg(new, old_name, name)
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
        to_remove = list(v8types[1:]) + type_els[0].xpath(
            "v8:StringQualifiers | v8:NumberQualifiers | v8:DateQualifiers",
            namespaces=cfg.NS)
        cfg.remove_children(type_els[0], to_remove)

    cfg.place_after(sample, new)
    cfg.save(doc, path)


# ---------- EDT (*.mdo) ----------

def _edt_scope(doc, parent: str | None):
    """Элемент-контейнер: корневой объект (mdclass:*) или табличная часть."""
    if parent is None:
        return doc.getroot()
    ts = doc.xpath(f"//tabularSections[name='{parent}']")
    if not ts:
        raise OpPreconditionError(f"Табличная часть '{parent}' не найдена")
    return ts[0]


def _add_edt(path: Path, tag: str, name: str, synonym: str,
             type_ref: str | None, parent: str | None) -> None:
    doc = cfg.load(path)
    scope = _edt_scope(doc, parent)

    names = scope.xpath(f"{tag}/name/text()")
    if name in names:
        raise OpPreconditionError(f"{tag} '{name}' уже существует")

    samples = scope.xpath(tag)
    if not samples:
        raise OpPreconditionError(
            f"В области нет элемента-образца {tag} (создание с нуля — отдельная операция)")
    sample = samples[-1]

    new = copy.deepcopy(sample)
    _regen_uuids(new)  # свежие uuid всему поддереву (вложенные элементы тоже)
    new.xpath("name")[0].text = name
    for value in new.xpath("synonym/value"):
        value.text = synonym

    if type_ref is not None:
        type_els = new.xpath("type")
        if not type_els or not type_els[0].xpath("types"):
            raise OpPreconditionError(f"У образца {tag} нет type/types")
        types = type_els[0].xpath("types")
        types[0].text = type_ref
        to_remove = list(types[1:]) + type_els[0].xpath(
            "stringQualifiers | numberQualifiers | dateQualifiers")
        cfg.remove_children(type_els[0], to_remove)

    cfg.place_after(sample, new)
    cfg.save(doc, path)
