"""Смена/расширение типа: реквизит, измерение/ресурс регистра или сам объект
(параметр сеанса / константа / определяемый тип), в обоих форматах.

Что делает:
- адресует типизированный УЗЕЛ по имени (Attribute / Dimension / Resource /
  AddressingAttribute) — либо, если `member=None`, тип самого объекта
  (SessionParameter/Constant/DefinedType);
- задаёт одиночный `type_ref` (ссылочный `cfg:CatalogRef.X` / примитив `xs:string`);
- строит блок `Type`/`type` и квалификаторы (длина строки; точность/масштаб числа)
  С НУЛЯ, если поле бестиповое или у типа ещё нет квалификатора;
- составной тип (несколько типов) сворачивается к одиночному; несоответствующие
  квалификаторы снимаются.

Byte-perfect: отступ И перевод строки (CRLF/LF) вставок берутся из контейнера —
выгрузки Конфигуратора CRLF, поэтому все построенные тайлы/тексты используют
детектированный `nl`, а не литерал "\\n".
"""
from pathlib import Path

from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name


def _is_string_type(type_ref: str) -> bool:
    return type_ref.split(":")[-1].lower() == "string"


def _is_number_type(type_ref: str) -> bool:
    return type_ref.split(":")[-1].lower() in ("decimal", "number")


def _style(container) -> tuple[str, str, str]:
    """(перевод строки, отступ детей контейнера, единица отступа).

    Перевод строки и отступ живут в `container.text` (пробелы перед первым
    ребёнком), напр. "\\r\\n\\t\\t\\t". CRLF выгрузки Конфигуратора несёт \\r.
    Единица: таб (Конфигуратор) или 2 пробела (EDT) — по составу отступа.
    """
    raw = container.text or "\n"
    nl = "\r\n" if "\r" in raw else "\n"
    lead = raw.split("\n")[-1]                 # отступ без перевода строки
    unit = "\t" if "\t" in lead else "  "
    return nl, lead, unit


def edit_type(object_path: Path, member: str | None, type_ref: str,
              length: int | None = None,
              precision: int | None = None,
              scale: int | None = None) -> None:
    if member is not None:
        validate_name(member)
    if length is not None and not _is_string_type(type_ref):
        raise OpPreconditionError("Параметр length применим только к строковому типу")
    if (precision is not None or scale is not None) and not _is_number_type(type_ref):
        raise OpPreconditionError("precision/scale применимы только к числовому типу")

    if str(object_path).endswith(".mdo"):
        _edit_edt(object_path, member, type_ref, length, precision, scale)
    else:
        _edit_configurator(object_path, member, type_ref, length, precision, scale)


def _mk(tag_q: str, text: str | None = None):
    el = etree.Element(tag_q)
    if text is not None:
        el.text = text
    return el


def _build_qualifier(anchor, qual_el, children, nl: str, lead: str, unit: str,
                     tag_fn) -> None:
    """Собрать квалификатор с детьми и вставить после `anchor` (текст типа),
    byte-perfect. `tag_fn(local)` даёт тег с нужным namespace для формата."""
    inner = lead + unit + unit                 # отступ детей квалификатора
    qual_el.text = nl + inner
    kids = [_mk(tag_fn(t), v) for t, v in children]
    for i, k in enumerate(kids):
        qual_el.append(k)
        k.tail = (nl + inner) if i < len(kids) - 1 else (nl + lead + unit)
    cfg.place_after(anchor, qual_el)           # anchor.tail→отступ детей type; qual.tail→закрытие


# ---------- Конфигуратор (*.xml) ----------

def _props_cfg(doc, member: str | None):
    if member is None:
        props = doc.xpath("/md:MetaDataObject/*/md:Properties", namespaces=cfg.NS)
        if not props:
            raise OpPreconditionError("У объекта нет блока Properties")
        return props[0]
    hits = doc.xpath(
        "//md:Attribute[md:Properties/md:Name=$n] | "
        "//md:Dimension[md:Properties/md:Name=$n] | "
        "//md:Resource[md:Properties/md:Name=$n] | "
        "//md:AddressingAttribute[md:Properties/md:Name=$n]",
        n=member, namespaces=cfg.NS)
    if not hits:
        raise OpPreconditionError(f"Поле '{member}' (реквизит/измерение/ресурс) не найдено")
    return hits[0].xpath("md:Properties", namespaces=cfg.NS)[0]


def _edit_configurator(path, member, type_ref, length, precision, scale) -> None:
    doc = cfg.load(path)
    props = _props_cfg(doc, member)
    nl, lead_p, unit = _style(props)           # стиль детей Properties

    type_els = props.xpath("md:Type", namespaces=cfg.NS)
    if type_els:
        type_el = type_els[0]
    else:
        type_el = _mk(cfg.q("md", "Type"))
        kids = list(props)
        if kids:
            cfg.place_after(kids[-1], type_el)
        else:
            props.text = nl + lead_p
            props.append(type_el)
            type_el.tail = nl + lead_p[:-len(unit)] if lead_p.endswith(unit) else nl
        type_el.text = nl + lead_p + unit

    v8s = type_el.xpath("v8:Type", namespaces=cfg.NS)
    if v8s:
        v8s[0].text = type_ref
        v8type = v8s[0]
        cfg.remove_children(type_el, v8s[1:])   # составной → одиночный
    else:
        v8type = _mk(cfg.q("v8", "Type"), type_ref)
        type_el.append(v8type)
    v8type.tail = nl + lead_p                    # закрытие </Type> на уровне детей Properties

    existing_q = type_el.xpath(
        "v8:StringQualifiers | v8:NumberQualifiers | v8:DateQualifiers", namespaces=cfg.NS)
    cfg.remove_children(type_el, existing_q)
    v8type.tail = nl + lead_p

    tag_v8 = lambda t: cfg.q("v8", t)
    if length is not None:
        _build_qualifier(v8type, _mk(cfg.q("v8", "StringQualifiers")),
                         [("Length", str(length))], nl, lead_p, unit, tag_v8)
    elif precision is not None or scale is not None:
        ch = []
        if precision is not None:
            ch.append(("Digits", str(precision)))
        if scale is not None:
            ch.append(("FractionDigits", str(scale)))
        _build_qualifier(v8type, _mk(cfg.q("v8", "NumberQualifiers")),
                         ch, nl, lead_p, unit, tag_v8)

    cfg.save(doc, path)


# ---------- EDT (*.mdo) ----------

def _container_edt(doc, member: str | None):
    if member is None:
        return doc.getroot()
    hits = doc.xpath(
        "//attributes[name=$n] | //dimensions[name=$n] | //resources[name=$n] | "
        "//addressingAttributes[name=$n]", n=member)
    if not hits:
        raise OpPreconditionError(f"Поле '{member}' (реквизит/измерение/ресурс) не найдено")
    return hits[0]


def _edit_edt(path, member, type_ref, length, precision, scale) -> None:
    doc = cfg.load(path)
    container = _container_edt(doc, member)
    nl, lead_c, unit = _style(container)        # стиль детей контейнера

    type_els = container.xpath("type")
    if type_els:
        type_el = type_els[0]
    else:
        type_el = etree.Element("type")
        kids = list(container)
        if kids:
            cfg.place_after(kids[-1], type_el)
        else:
            container.text = nl + lead_c
            container.append(type_el)
            type_el.tail = nl + lead_c[:-len(unit)] if lead_c.endswith(unit) else nl
        type_el.text = nl + lead_c + unit

    types = type_el.xpath("types")
    if types:
        types[0].text = type_ref
        types_el = types[0]
        cfg.remove_children(type_el, types[1:])
    else:
        types_el = _mk("types", type_ref)
        type_el.append(types_el)
    types_el.tail = nl + lead_c

    existing_q = type_el.xpath("stringQualifiers | numberQualifiers | dateQualifiers")
    cfg.remove_children(type_el, existing_q)
    types_el.tail = nl + lead_c

    tag_plain = lambda t: t
    if length is not None:
        _build_qualifier(types_el, etree.Element("stringQualifiers"),
                         [("length", str(length))], nl, lead_c, unit, tag_plain)
    elif precision is not None or scale is not None:
        ch = []
        if precision is not None:
            ch.append(("precision", str(precision)))
        if scale is not None:
            ch.append(("scale", str(scale)))
        _build_qualifier(types_el, etree.Element("numberQualifiers"),
                         ch, nl, lead_c, unit, tag_plain)

    cfg.save(doc, path)
