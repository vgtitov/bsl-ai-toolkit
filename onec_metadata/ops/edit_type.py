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
from onec_metadata.validate import validate_name, validate_type_ref


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


def edit_type(object_path: Path, member: str | None, type_ref: "str | list[str]",
              length: int | None = None,
              precision: int | None = None,
              scale: int | None = None) -> None:
    """`type_ref` — строка (одиночный тип) или список строк (СОСТАВНОЙ тип: несколько
    типов в одном поле). Квалификаторы (length/precision/scale) — только к одиночному."""
    if member is not None:
        validate_name(member)
    type_refs = [type_ref] if isinstance(type_ref, str) else list(type_ref)
    if not type_refs:
        raise OpPreconditionError("Не задан тип")
    for t in type_refs:
        validate_type_ref(t)
    if len(type_refs) > 1 and (length is not None
                               or precision is not None or scale is not None):
        raise OpPreconditionError(
            "Квалификатор (length/precision/scale) неприменим к составному типу")
    if length is not None and not _is_string_type(type_refs[0]):
        raise OpPreconditionError("Параметр length применим только к строковому типу")
    if (precision is not None or scale is not None) and not _is_number_type(type_refs[0]):
        raise OpPreconditionError("precision/scale применимы только к числовому типу")

    if str(object_path).endswith(".mdo"):
        _edit_edt(object_path, member, type_refs, length, precision, scale)
    else:
        _edit_configurator(object_path, member, type_refs, length, precision, scale)


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


def _ensure_types(type_el, xpath, ns, texts, mk_fn, nl, lead_t, lead_p):
    """Привести число type-элементов (`v8:Type`/`types`) к len(texts) с этими текстами,
    byte-perfect: добить/убрать + нормализовать отступы (все, кроме последнего, — на
    уровне детей type; последний — закрытие). Вернуть последний элемент (якорь для квалификатора)."""
    cur = (lambda: type_el.xpath(xpath, namespaces=ns)) if ns else (lambda: type_el.xpath(xpath))
    existing = cur()
    while len(existing) < len(texts):
        new = mk_fn(texts[len(existing)])
        if existing:
            cfg.place_after(existing[-1], new)
        else:
            type_el.append(new)
        existing = cur()
    for el, t in zip(existing, texts):
        el.text = t
    cfg.remove_children(type_el, existing[len(texts):])
    kept = cur()
    for i, el in enumerate(kept):
        el.tail = (nl + lead_t) if i < len(kept) - 1 else (nl + lead_p)
    return kept[-1]


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


def _edit_configurator(path, member, type_refs, length, precision, scale) -> None:
    doc = cfg.load(path)
    props = _props_cfg(doc, member)
    nl, lead_p, unit = _style(props)           # стиль детей Properties
    lead_t = lead_p + unit

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

    # квалификаторы снять (у составного их нет; у одиночного — пересоберём)
    cfg.remove_children(type_el, type_el.xpath(
        "v8:StringQualifiers | v8:NumberQualifiers | v8:DateQualifiers", namespaces=cfg.NS))
    last = _ensure_types(type_el, "v8:Type", cfg.NS, type_refs,
                         lambda t: _mk(cfg.q("v8", "Type"), t), nl, lead_t, lead_p)

    tag_v8 = lambda t: cfg.q("v8", t)
    if len(type_refs) == 1 and length is not None:
        _build_qualifier(last, _mk(cfg.q("v8", "StringQualifiers")),
                         [("Length", str(length))], nl, lead_p, unit, tag_v8)
    elif len(type_refs) == 1 and (precision is not None or scale is not None):
        ch = []
        if precision is not None:
            ch.append(("Digits", str(precision)))
        if scale is not None:
            ch.append(("FractionDigits", str(scale)))
        _build_qualifier(last, _mk(cfg.q("v8", "NumberQualifiers")),
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


def _edit_edt(path, member, type_refs, length, precision, scale) -> None:
    doc = cfg.load(path)
    container = _container_edt(doc, member)
    nl, lead_c, unit = _style(container)        # стиль детей контейнера
    lead_t = lead_c + unit

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

    cfg.remove_children(type_el, type_el.xpath(
        "stringQualifiers | numberQualifiers | dateQualifiers"))
    last = _ensure_types(type_el, "types", None, type_refs,
                         lambda t: _mk("types", t), nl, lead_t, lead_c)

    tag_plain = lambda t: t
    if len(type_refs) == 1 and length is not None:
        _build_qualifier(last, etree.Element("stringQualifiers"),
                         [("length", str(length))], nl, lead_c, unit, tag_plain)
    elif len(type_refs) == 1 and (precision is not None or scale is not None):
        ch = []
        if precision is not None:
            ch.append(("precision", str(precision)))
        if scale is not None:
            ch.append(("scale", str(scale)))
        _build_qualifier(last, etree.Element("numberQualifiers"),
                         ch, nl, lead_c, unit, tag_plain)

    cfg.save(doc, path)
