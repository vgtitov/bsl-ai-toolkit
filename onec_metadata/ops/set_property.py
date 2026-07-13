"""Изменение СУЩЕСТВУЮЩЕГО скалярного свойства объекта или реквизита.

Скалярное = лист с текстом (флаг `true/false`, значение перечисления, число,
текст вроде Comment). Структурные свойства (Synonym, Type, Content — с дочерними
элементами) НЕ трогаем: их правка — отдельные операции. Отсутствующее свойство
(в EDT дефолты опускаются) — отказ предусловием: добавление свойства — отдельная
операция (порядок сериализации значим для минимального диффа).

Диспетчеризация по расширению файла: Конфигуратор (`*.xml`, ns md) / EDT (`*.mdo`).
"""
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name


def set_property(object_path: Path, property_name: str, value: str,
                 attribute: str | None = None) -> None:
    validate_name(property_name)
    if attribute is not None:
        validate_name(attribute)

    if str(object_path).endswith(".mdo"):
        _set_edt(object_path, property_name, value, attribute)
    else:
        _set_configurator(object_path, property_name, value, attribute)


def _apply_scalar(props, property_name: str, value: str, tag_xpath: str,
                  namespaces=None) -> None:
    """Найти свойство-лист в блоке свойств `props` и заменить его текст."""
    hits = props.xpath(tag_xpath, namespaces=namespaces) if namespaces \
        else props.xpath(tag_xpath)
    if not hits:
        raise OpPreconditionError(
            f"Свойство '{property_name}' отсутствует в выгрузке "
            "(добавление свойства — отдельная операция)")
    prop = hits[0]
    if len(prop) > 0:  # есть дочерние элементы → структурное
        raise OpPreconditionError(
            f"Свойство '{property_name}' структурное (есть вложенные элементы); "
            "set-property только для скалярных")
    prop.text = value


# ---------- Конфигуратор (*.xml) ----------

def _set_configurator(path: Path, property_name: str, value: str,
                      attribute: str | None) -> None:
    doc = cfg.load(path)
    root = doc.getroot()
    elems = root.xpath("*")  # только элементы (пропустить узлы-комментарии)
    if not elems:
        raise OpPreconditionError("В файле нет объекта метаданных")
    obj = elems[0]  # объект метаданных — первый элемент-ребёнок MetaDataObject

    if attribute is None:
        props = obj.xpath("md:Properties", namespaces=cfg.NS)
    else:
        props = obj.xpath(
            f".//md:Attribute[md:Properties/md:Name='{attribute}']/md:Properties",
            namespaces=cfg.NS)
        if not props:
            raise OpPreconditionError(f"Реквизит '{attribute}' не найден")
    if not props:
        raise OpPreconditionError("У объекта нет блока Properties")

    _apply_scalar(props[0], property_name, value, f"md:{property_name}", cfg.NS)
    cfg.save(doc, path)


# ---------- EDT (*.mdo) ----------

def _set_edt(path: Path, property_name: str, value: str,
             attribute: str | None) -> None:
    doc = cfg.load(path)
    root = doc.getroot()

    if attribute is None:
        container = root
    else:
        hits = root.xpath(f"attributes[name='{attribute}']")
        if not hits:
            raise OpPreconditionError(f"Реквизит '{attribute}' не найден")
        container = hits[0]

    _apply_scalar(container, property_name, value, property_name)
    cfg.save(doc, path)
