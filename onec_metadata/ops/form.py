"""Правка управляемых форм (формат Конфигуратора, ns logform).

Отличие от метаданных: элементы формы нумеруются ЦЕЛЫМ атрибутом `id`. Пространства
id РАЗДЕЛЬНЫ: реквизиты формы (`<Attributes>`) и элементы (`<ChildItems>` со своими
вложенными ContextMenu/ExtendedTooltip) нумеруются НЕЗАВИСИМО — в реальной форме
Attribute id=1 и InputField id=1 сосуществуют. Новый элемент получает id =
max(id в СВОЁМ пространстве) + 1; элемент с вложенными частями — несколько
последовательных id.

P4.1 — `add_attribute`: реквизит формы в <Attributes> (клон образца + свежий id +
тип; MainAttribute сбрасывается — основной реквизит только один). Форма без
реквизита-образца — пока отдельная операция (см. spec forms-design).
"""
import copy
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name

NS = cfg.NS


def _max_id_in(scope) -> int:
    """Максимальный id в пределах ПРОСТРАНСТВА (поддерева) — реквизиты и элементы
    формы нумеруются раздельно. Отрицательные служебные id (напр. -1) игнорируем."""
    ids = [int(x) for x in scope.xpath(".//@id | ./@id")]
    positive = [i for i in ids if i > 0]
    return max(positive) if positive else 0


def _reid(element, next_id: int) -> int:
    """Переприсвоить id всему поддереву (свежие последовательные). Вернуть след. id."""
    for el in element.xpath("descendant-or-self::*[@id]"):
        el.set("id", str(next_id))
        next_id += 1
    return next_id


def add_attribute(form_path: Path, name: str, type_ref: str) -> None:
    validate_name(name)
    doc = cfg.load(form_path)
    root = doc.getroot()

    attrs_els = root.xpath("lf:Attributes", namespaces=NS)
    if not attrs_els:
        raise OpPreconditionError("В форме нет блока Attributes")
    attributes = attrs_els[0]

    existing = attributes.xpath("lf:Attribute", namespaces=NS)
    if name in [a.get("name") for a in existing]:
        raise OpPreconditionError(f"Реквизит формы '{name}' уже существует")
    if not existing:
        raise OpPreconditionError(
            "В форме нет реквизита-образца (создание с нуля — отдельная операция)")
    sample = existing[-1]

    new = copy.deepcopy(sample)
    _reid(new, _max_id_in(attributes) + 1)  # id в пространстве реквизитов формы
    new.set("name", name)

    v8types = new.xpath("lf:Type/v8:Type", namespaces=NS)
    if not v8types:
        raise OpPreconditionError("У реквизита-образца формы нет Type/v8:Type")
    v8types[0].text = type_ref
    # удаление лишних типов с сохранением отступа закрывающего </Type>
    cfg.remove_children(v8types[0].getparent(), v8types[1:])

    # основной реквизит только один — у клона сбросить
    for main in new.xpath("lf:MainAttribute", namespaces=NS):
        main.text = "false"

    cfg.place_after(sample, new)
    cfg.save(doc, form_path)
