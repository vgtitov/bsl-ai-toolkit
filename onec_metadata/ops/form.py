"""Правка управляемых форм: Конфигуратор (`Form.xml`, ns logform) и EDT
(`Form.form`, ns g5.1c.ru/v8/dt/form). Диспетчеризация по расширению файла.

Отличие от метаданных: элементы формы нумеруются ЦЕЛЫМ `id` (Конфигуратор —
XML-атрибут, EDT — дочерний элемент <id>). Пространства id РАЗДЕЛЬНЫ: реквизиты
формы и элементы нумеруются НЕЗАВИСИМО — в реальной форме реквизит id=1 и элемент
id=1 сосуществуют (проверено и на УТ-выгрузке Конфигуратора, и на EDT-выгрузке
ERP). Новый элемент получает id = max(id в СВОЁМ пространстве) + 1.

P4.1 — `add_attribute`: реквизит формы (клон образца + свежий id + тип; признак
основного реквизита сбрасывается — основной только один). EDT-нюансы: реквизиты —
безпрефиксные блоки <attributes> прямо под корнем; тип — в EDT-нотации
(String / CatalogRef.Имя, НЕ xs:/cfg:); клон чистится от семантики образца
(extInfo/notDefaultUseAlwaysAttributes/квалификаторы). Форма без
реквизита-образца — пока отдельная операция (см. spec forms-design).
"""
import copy
import re
from pathlib import Path

from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name, validate_type_ref

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
    validate_type_ref(type_ref)
    if str(form_path).endswith(".form"):
        _add_attribute_edt(form_path, name, type_ref)
    else:
        _add_attribute_configurator(form_path, name, type_ref)


def _add_attribute_configurator(form_path: Path, name: str, type_ref: str) -> None:
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


# ---------- P4.2: элемент InputField (формат Конфигуратора) ----------

# дети клона-поля, несущие семантику ОБРАЗЦА — не наследуются
_FIELD_SEMANTIC = ("Events", "ChoiceList", "TypeRestriction")


def add_field(form_path: Path, name: str, data_path: str, title: str | None = None,
              parent: str | None = None, position: str | None = None) -> None:
    """Добавить InputField в ChildItems с привязкой DataPath (P4.2).

    id поля и вложенных ContextMenu/ExtendedTooltip — свежие ПОСЛЕДОВАТЕЛЬНЫЕ из
    пространства ЭЛЕМЕНТОВ (поддерево ChildItems); реквизиты формы не затрагиваются.
    Клон последнего InputField-образца; DataPath задаётся явно, семантика образца
    (обработчики, списки выбора) чистится; вложенные части переименовываются
    конвенцией Конфигуратора: <Имя>КонтекстноеМеню / <Имя>ExtendedTooltip.
    EDT-формат (.form): визуальное поле = items xsi:type=form:FormField — см. _add_field_edt."""
    validate_name(name)
    if str(form_path).endswith(".form"):
        if parent:
            _add_field_into_edt(form_path, name, data_path, parent, position)
        else:
            _add_field_edt(form_path, name, data_path, title)
        return
    if parent:
        raise OpPreconditionError("add-field --parent пока только для EDT (Form.form)")
    doc = cfg.load(form_path)
    root = doc.getroot()

    child_items = root.xpath("lf:ChildItems", namespaces=NS)
    if not child_items:
        raise OpPreconditionError("В форме нет блока ChildItems")
    items = child_items[0]

    if items.xpath(f"lf:*[@name='{name}']", namespaces=NS):
        raise OpPreconditionError(f"Элемент формы '{name}' уже существует")

    samples = items.xpath("lf:InputField", namespaces=NS)
    if not samples:
        raise OpPreconditionError(
            "В форме нет InputField-образца (создание с нуля — отдельная операция)")
    sample = samples[-1]

    new = copy.deepcopy(sample)
    _reid(new, _max_id_in(items) + 1)   # id в пространстве ЭЛЕМЕНТОВ формы
    new.set("name", name)

    dp = new.xpath("lf:DataPath", namespaces=NS)
    if not dp:
        raise OpPreconditionError("У InputField-образца нет DataPath")
    dp[0].text = data_path

    # семантика образца не наследуется
    cfg.remove_children(
        new, [el for tag in _FIELD_SEMANTIC
              for el in new.xpath(f"lf:{tag}", namespaces=NS)])
    # вложенные части — под именем нового поля (конвенция Конфигуратора)
    for cm in new.xpath("lf:ContextMenu", namespaces=NS):
        cm.set("name", f"{name}КонтекстноеМеню")
    for tt in new.xpath("lf:ExtendedTooltip", namespaces=NS):
        tt.set("name", f"{name}ExtendedTooltip")
    if title:
        for t in new.xpath("lf:Title", namespaces=NS):
            cfg.remove_children(new, [t])
        # заголовок по умолчанию платформа возьмёт из реквизита; явный Title —
        # отдельная фаза (мультиязычные v8:item), пока не поддержан
        raise OpPreconditionError("title пока не поддержан (мультиязычный блок — P4.3)")

    last = items.xpath("lf:*", namespaces=NS)[-1]
    cfg.place_after(last, new)
    cfg.save(doc, form_path)


# ---------- EDT (Form.form) ----------

# дети клона-реквизита, несущие семантику ОБРАЗЦА (чужие ссылки/настройки) — удалить.
# Список эмпирический: прямые дети <attributes> в реальной EDT-выгрузке ERP 2.5 —
# name/id/valueType/view/edit + семантические main/extInfo/
# notDefaultUseAlwaysAttributes/columns; ничего не выдумываем сверх наблюдаемого.
_EDT_ATTR_SEMANTIC = (
    "main", "extInfo", "notDefaultUseAlwaysAttributes", "columns",
)
# квалификаторы типа образца — с новым типом не совместимы
_EDT_QUALIFIERS = (
    "stringQualifiers", "numberQualifiers", "dateQualifiers",
    "binaryQualifiers", "binaryDataQualifiers",
)


def _add_attribute_edt(form_path: Path, name: str, type_ref: str) -> None:
    doc = cfg.load(form_path)
    root = doc.getroot()

    # реквизиты — безпрефиксные <attributes> прямо под form:Form
    existing = root.xpath("attributes")
    if not existing:
        raise OpPreconditionError(
            "В форме нет реквизита-образца (создание с нуля — отдельная операция)")
    if name in [a.findtext("name") for a in existing]:
        raise OpPreconditionError(f"Реквизит формы '{name}' уже существует")

    # образец: предпочесть НЕосновной реквизит с непустым valueType/types
    sample = None
    for cand in reversed(existing):
        if cand.findtext("main") != "true" and cand.xpath("valueType/types"):
            sample = cand
            break
    if sample is None:
        for cand in reversed(existing):
            if cand.xpath("valueType/types"):
                sample = cand
                break
    if sample is None:
        raise OpPreconditionError(
            "У реквизитов-образцов формы нет valueType/types (создание с нуля — отдельная операция)")

    new = copy.deepcopy(sample)
    new.find("name").text = name
    new.find("id").text = str(_next_attribute_id_edt(root))
    _set_type_edt(new, type_ref)

    cfg.remove_children(
        new, [el for tag in _EDT_ATTR_SEMANTIC for el in new.findall(tag)])

    cfg.place_after(existing[-1], new)
    cfg.save(doc, form_path)


# ---------- тип СУЩЕСТВУЮЩЕГО реквизита формы (оба формата) ----------

# квалификатор примитива -> тип, без которого он не нужен (EDT / Конфигуратор)
# имя квалификатора двоичных данных в EDT по корпусу не подтверждено (реквизитов BinaryData в выгрузке ERP
# нет) - снимаем оба варианта
_EDT_QUALIFIER_OF = {"stringQualifiers": "String", "numberQualifiers": "Number", "dateQualifiers": "Date",
                     "binaryQualifiers": "BinaryData", "binaryDataQualifiers": "BinaryData"}
_CFG_QUALIFIER_OF = {"StringQualifiers": "xs:string", "NumberQualifiers": "xs:decimal",
                     "DateQualifiers": "xs:dateTime", "BinaryDataQualifiers": "xs:base64Binary"}


def set_attribute_type(form_path: Path, name: str, type_refs: list[str]) -> None:
    """Задать тип СУЩЕСТВУЮЩЕМУ реквизиту формы (составной - несколько типов).

    Блок типа создается, если его нет (EDT MCP создает основной реквизит без valueType - форма
    не открывается). Квалификаторы примитивов, которых в новом типе нет, снимаются; оставшимся
    примитивам квалификаторы сохраняются. Признак основного реквизита и прочее не трогаются.
    """
    if not type_refs:
        raise OpPreconditionError("Нужен хотя бы один тип (--type)")
    type_refs = list(dict.fromkeys(type_refs))   # повтор --type не дает дубля в составном типе
    for t in type_refs:
        validate_type_ref(t)
    edt = str(form_path).endswith(".form")
    for t in type_refs:
        # нотация формата: EDT - без префикса (String, CatalogRef.X), Конфигуратор - xs:/cfg:/v8:
        if edt and ":" in t:
            raise OpPreconditionError(f"Тип '{t}': для EDT-формы нотация без префикса (String, CatalogRef.Имя)")
        if not edt and ":" not in t:
            raise OpPreconditionError(f"Тип '{t}': для формы Конфигуратора нужен префикс (xs:string, cfg:CatalogRef.Имя)")
    if edt:
        _set_attribute_type_edt(form_path, name, type_refs)
    else:
        _set_attribute_type_configurator(form_path, name, type_refs)


def _set_attribute_type_edt(form_path: Path, name: str, type_refs: list[str]) -> None:
    doc = cfg.load(form_path)
    attr = next((a for a in doc.getroot().xpath("attributes") if a.findtext("name") == name), None)
    if attr is None:
        raise OpPreconditionError(f"Реквизит формы '{name}' не найден")

    child_indent = attr.text or "\n"
    vt = attr.find("valueType")
    if vt is None:
        anchor = attr.find("id")
        if anchor is None:
            raise OpPreconditionError(f"У реквизита формы '{name}' нет <id> - некуда поставить valueType")
        vt = etree.Element("valueType")
        vt.text = child_indent + "  "
        cfg.place_after(anchor, vt)          # сразу после <id>, как в выгрузке EDT
    _replace_types(vt, "types", type_refs, child_indent)
    stale = [q for tag, prim in _EDT_QUALIFIER_OF.items() if prim not in type_refs for q in vt.findall(tag)]
    cfg.remove_children(vt, stale)
    cfg.save(doc, form_path)


def _set_attribute_type_configurator(form_path: Path, name: str, type_refs: list[str]) -> None:
    doc = cfg.load(form_path)
    hits = doc.getroot().xpath("lf:Attributes/lf:Attribute[@name=$n]", namespaces=NS, n=name)
    if not hits:
        raise OpPreconditionError(f"Реквизит формы '{name}' не найден")
    attr = hits[0]
    if len(attr) == 0:
        raise OpPreconditionError(f"У реквизита формы '{name}' нет дочерних элементов - формат не распознан")

    child_indent = attr.text or "\n"
    type_el = attr.find(cfg.q("lf", "Type"))
    if type_el is None:
        type_el = etree.Element(cfg.q("lf", "Type"))
        type_el.text = child_indent + "\t"
        title = attr.find(cfg.q("lf", "Title"))
        if title is not None:
            cfg.place_after(title, type_el)  # порядок выгрузки: Title, Type, ...
        else:
            type_el.tail = child_indent
            attr.insert(0, type_el)
    _replace_types(type_el, cfg.q("v8", "Type"), type_refs, child_indent)
    stale = [q for tag, prim in _CFG_QUALIFIER_OF.items() if prim not in type_refs
             for q in type_el.findall(cfg.q("v8", tag))]
    cfg.remove_children(type_el, stale)
    cfg.save(doc, form_path)


def _replace_types(container, tag: str, type_refs: list[str], closing_indent: str) -> None:
    """Заменить элементы типа в блоке типа: новые встают на место старых (перед квалификаторами).
    Отступы: у детей - отступ блока (container.text), у последнего - закрывающий (closing_indent)."""
    old = container.findall(tag)
    pos = list(container).index(old[0]) if old else 0
    for el in old:
        container.remove(el)
    for i, t in enumerate(type_refs):
        el = etree.Element(tag)
        el.text = t
        container.insert(pos + i, el)
    kids = list(container)
    for el in kids[:-1]:
        el.tail = container.text
    kids[-1].tail = closing_indent


def remove_attribute_edt(form_path: Path, name: str, parent: str | None = None) -> None:
    """EDT Form.form: удалить реквизит формы или (с `parent`) колонку реквизита-таблицы. Вызывается из
    form_attributes (remove-attribute / remove-attribute-column). Отказ, если на него ссылается элемент
    формы (dataPath / footerDataPath), если это основной реквизит или последняя колонка."""
    doc = cfg.load(form_path)
    root = doc.getroot()
    owner_path = f"{parent}.{name}" if parent else name
    if parent:
        owners = [a for a in root.xpath("attributes") if a.findtext("name") == parent]
        pool = owners[0].findall("columns") if owners else []
    else:
        pool = root.xpath("attributes")
    hits = [a for a in pool if a.findtext("name") == name]
    if not hits:
        raise OpPreconditionError(f"Реквизит формы '{owner_path}' не найден")
    if parent and len(pool) == 1:
        raise OpPreconditionError("Последнюю колонку не удаляем: сначала добавьте новую")
    if not parent and hits[0].findtext("main") == "true":
        raise OpPreconditionError("Основной реквизит формы не удаляем")
    used = [s for s in root.xpath(".//items//segments/text() | .//items/*/segments/text()")
            if s == owner_path or s.startswith(owner_path + ".")]
    if used:
        raise OpPreconditionError(f"На '{owner_path}' ссылаются элементы формы - сначала удалите их")
    cfg.remove_children(hits[0].getparent(), hits)
    cfg.save(doc, form_path)


def _next_attribute_id_edt(root) -> int:
    """id реквизитов формы и колонок их таблиц — ОДНО пространство (реальная форма: таблица
    id=5, её колонки 26..30 и 6), поэтому max берётся по всем <id> реквизитов и колонок."""
    ids = [int(t) for t in root.xpath("attributes/id/text() | attributes/columns/id/text()")
           if t.lstrip("-").isdigit()]
    return max([i for i in ids if i > 0], default=0) + 1


def _set_type_edt(node, type_ref: str, qualifiers=None) -> None:
    """Тип клона: один <types>, квалификаторы образца сняты (с новым типом несовместимы);
    qualifiers - (тег, [(дочерний тег, текст)]) для нового типа или None."""
    vt = node.find("valueType")
    types = vt.findall("types")
    types[0].text = type_ref
    cfg.remove_children(vt, types[1:])
    cfg.remove_children(vt, [q for tag in _EDT_QUALIFIERS for q in vt.findall(tag)])
    if qualifiers:
        tag, children = qualifiers
        q = etree.Element(tag)
        ws = vt.text or "\n"           # отступ детей valueType (хвост types может быть отступом </valueType>)
        inner = ws + "  "
        q.text = inner
        for i, (child, text) in enumerate(children):
            el = etree.SubElement(q, child)
            el.text = text
            el.tail = inner if i < len(children) - 1 else ws
        cfg.place_after(types[0], q)


def edt_type(type_ref: str):
    """Тип колонки в общем синтаксисе form_attributes (String | Boolean | Date | Number:N[.M] |
    cfg:XRef.Имя) - в нотацию EDT: (types, квалификаторы или None). Нотация EDT (Number, CatalogRef.Имя)
    принимается как есть. Корпус ERP: у Number пишутся precision и scale (scale=0 опускается);
    Date без квалификаторов - дата и время (как DateTime у Конфигуратора)."""
    m = re.fullmatch(r"Number:(\d+)(?:\.(\d+))?", type_ref)
    if m:
        children = [("precision", m.group(1))]
        if m.group(2) and int(m.group(2)) > 0:
            children.append(("scale", m.group(2)))
        return "Number", ("numberQualifiers", children)
    if type_ref.startswith("cfg:"):
        return type_ref[len("cfg:"):], None
    return type_ref, None


# дети клона-колонки с семантикой образца: заголовок колонки образца не наследуется
_EDT_COLUMN_SEMANTIC = ("title", "extInfo")


def add_column_edt(form_path: Path, parent: str, name: str, type_ref: str, titles: dict | None = None) -> None:
    """EDT Form.form: колонка реквизита-таблицы формы - клон последней колонки этой таблицы, новый id,
    тип, заголовки на языках (без заголовка образца); встаёт после последней колонки. Вызывается из
    form_attributes.add_attribute_column."""
    validate_name(name)
    types_text, qualifiers = edt_type(type_ref)
    validate_type_ref(types_text)
    doc = cfg.load(form_path)
    root = doc.getroot()

    owners = [a for a in root.xpath("attributes") if a.findtext("name") == parent]
    if not owners:
        raise OpPreconditionError(f"Реквизит формы '{parent}' не найден")
    columns = owners[0].findall("columns")
    if not columns:
        raise OpPreconditionError(
            f"У реквизита '{parent}' нет колонок-образцов (не таблица или пустая таблица)")
    if name in [c.findtext("name") for c in columns]:
        raise OpPreconditionError(f"Колонка '{name}' у реквизита '{parent}' уже существует")

    new = copy.deepcopy(columns[-1])
    new.find("name").text = name
    new.find("id").text = str(_next_attribute_id_edt(root))
    _set_type_edt(new, types_text, qualifiers)
    cfg.remove_children(
        new, [el for tag in _EDT_COLUMN_SEMANTIC for el in new.findall(tag)])
    # заголовки - сразу после <name>, по блоку на язык (как пишет EDT)
    anchor = new.find("name")
    ws = anchor.tail
    for lang, text in (titles or {}).items():
        t = etree.Element("title")
        t.text = ws + "  "
        k = etree.SubElement(t, "key")
        k.text, k.tail = lang, ws + "  "
        v = etree.SubElement(t, "value")
        v.text, v.tail = text, ws
        cfg.place_after(anchor, t)
        anchor = t

    cfg.place_after(columns[-1], new)
    cfg.save(doc, form_path)


# ---------- P4.3: элемент формы EDT (items form:FormField) ----------

def _reid_edt(element, next_id: int) -> int:
    """Переприсвоить id (дочерние <id>) всему поддереву — свежие последовательные
    в порядке документа. EDT нумерует id ДОЧЕРНИМ элементом, не атрибутом."""
    for idel in element.xpath(".//id | ./id"):
        idel.text = str(next_id)
        next_id += 1
    return next_id


def _add_field_edt(form_path: Path, name: str, data_path: str, title: str | None) -> None:
    if title:
        raise OpPreconditionError("title пока не поддержан (мультиязычный блок — P4.3)")
    doc = cfg.load(form_path)
    root = doc.getroot()
    xt = cfg.q("xsi", "type")

    items = root.xpath("items")
    if not items:
        raise OpPreconditionError("В форме нет блока items")
    if name in [i.findtext("name") for i in items]:
        raise OpPreconditionError(f"Элемент формы '{name}' уже существует")

    fields = [i for i in items
              if (i.get(xt) or "").endswith("FormField")]
    if not fields:
        raise OpPreconditionError(
            "В форме нет образца поля (items form:FormField; создание с нуля — отдельная операция)")
    sample = fields[-1]

    # пространство ЭЛЕМЕНТОВ: все <id> вне реквизитов (attributes) — отдельно от них
    el_ids = [int(t) for t in root.xpath(".//id[not(ancestor::attributes)]/text()")
              if t.lstrip("-").isdigit()]
    next_id = max([i for i in el_ids if i > 0], default=0) + 1

    new = copy.deepcopy(sample)
    _reid_edt(new, next_id)                    # свежие id полю и вложенным частям
    new.find("name").text = name

    seg = new.xpath("dataPath/segments")
    if not seg:
        raise OpPreconditionError("У образца поля нет dataPath/segments")
    seg[0].text = data_path

    # вложенные части — под именем нового поля (конвенция)
    for tip in new.xpath("extendedTooltip/name"):
        tip.text = f"{name}РасширеннаяПодсказка"
    for menu in new.xpath("contextMenu/name"):
        menu.text = f"{name}КонтекстноеМеню"

    cfg.place_after(sample, new)
    cfg.save(doc, form_path)


# дети клона-поля с семантикой образца: заголовок, подсказка и обработчики не наследуются
_EDT_FIELD_SEMANTIC = ("title", "toolTip", "handlers")


def _add_field_into_edt(form_path: Path, name: str, data_path: str, parent: str,
                        position: str | None) -> None:
    """Поле внутри контейнера (таблица - колонка, группа): клон последнего поля контейнера,
    свежие id в пространстве элементов, место - среди <items> контейнера (до его свойств).
    position: first | last (умолч.) | before:Имя | after:Имя - среди прямых <items> контейнера."""
    doc = cfg.load(form_path)
    root = doc.getroot()
    xt = cfg.q("xsi", "type")

    every = root.xpath(".//items")
    if name in [i.findtext("name") for i in every]:
        raise OpPreconditionError(f"Элемент формы '{name}' уже существует")
    owners = [i for i in every if i.findtext("name") == parent]
    if not owners:
        raise OpPreconditionError(f"Контейнер '{parent}' не найден среди элементов формы")
    container = owners[0]
    children = container.findall("items")

    samples = [i for i in container.iter("items") if (i.get(xt) or "").endswith("FormField")]
    # образец - поле ввода (картинка/флажок в том же контейнере дали бы поле не того вида)
    samples = [s for s in samples if s.findtext("type") == "InputField"] or samples
    if not samples:
        raise OpPreconditionError(
            f"В контейнере '{parent}' нет поля-образца (создание с нуля - отдельная операция)")

    anchor, after = _edt_insert_anchor(children, position or "last", parent)

    el_ids = [int(t) for t in root.xpath(".//id[not(ancestor::attributes)]/text()")
              if t.lstrip("-").isdigit()]
    new = copy.deepcopy(samples[-1])
    _reid_edt(new, max([i for i in el_ids if i > 0], default=0) + 1)
    new.find("name").text = name
    seg = new.xpath("dataPath/segments")
    if not seg:
        raise OpPreconditionError("У образца поля нет dataPath/segments")
    seg[0].text = data_path
    cfg.remove_children(new, [el for tag in _EDT_FIELD_SEMANTIC for el in new.findall(tag)])
    for tip in new.xpath("extendedTooltip/name"):
        tip.text = f"{name}РасширеннаяПодсказка"
    for menu in new.xpath("contextMenu/name"):
        menu.text = f"{name}КонтекстноеМеню"

    if after:
        cfg.place_after(anchor, new)
    else:
        # отступ перед якорем - хвост предыдущего соседа или текст контейнера
        prev = anchor.getprevious()
        indent = prev.tail if prev is not None else anchor.getparent().text
        anchor.addprevious(new)
        new.tail = indent
    cfg.save(doc, form_path)


def _edt_insert_anchor(children, position: str, parent: str):
    """(якорь, вставить_после) среди прямых <items> контейнера."""
    if position == "last":
        return children[-1], True
    if position == "first":
        return children[0], False
    kind, _, ref = position.partition(":")
    if kind not in ("after", "before") or not ref:
        raise OpPreconditionError("position: first | last | before:Имя | after:Имя")
    hits = [c for c in children if c.findtext("name") == ref]
    if not hits:
        raise OpPreconditionError(f"В контейнере '{parent}' нет элемента '{ref}' для position")
    return hits[0], kind == "after"
