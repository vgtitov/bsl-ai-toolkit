"""Структура управляемой формы в формате Конфигуратора (`Form.xml`, ns logform): группы, надписи,
таблицы, колонки и группы колонок, перенос элементов, заголовки. Для EDT (`Form.form`) структуру делает
EDT MCP — здесь отказ.

Шаблоны — как пишет Конфигуратор (корпус ~4000 форм ERP/СНГ + экспорт EDT в XML):
  группа  `<UsualGroup name id><Behavior>Usual</Behavior><ExtendedTooltip …/>[<ChildItems>]` —
          у пустой группы `ChildItems` нет (1751 форма), `Behavior=Usual` у 94% групп;
  надпись `<LabelDecoration name id><ContextMenu …/><ExtendedTooltip …/>`, заголовок надписи с
          `formatted="false"` (у групп/полей без атрибутов);
  вложенные части: `<Имя>КонтекстноеМеню`, `<Имя>РасширеннаяПодсказка`, id подряд за элементом.
Новый id — max(id элементов формы) + 1; реквизиты формы нумеруются отдельно и не затрагиваются.
Позиция: `first` | `last` (по умолчанию) | `before:<Имя>` | `after:<Имя>`; родитель `.` — корень формы.
Свойства на месте — по таблице порядка Конфигуратора (как `set-style`), отступы — табуляцией.
"""
from pathlib import Path

from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.form_style import _ORDER_CFG
from onec_metadata.validate import validate_name

_LF = cfg.NS["lf"]
_V8 = cfg.NS["v8"]
_UNIT = "\t"
_DECORATIONS = {"Label": "LabelDecoration", "Picture": "PictureDecoration"}


def _q(tag):
    return f"{{{_LF}}}{tag}"


# ---------- операции ----------

def add_group(form_path: Path, name: str, parent: str = ".", position: str = "last") -> None:
    def build(next_id):
        el = etree.Element(_q("UsualGroup"), name=name, id=str(next_id))
        etree.SubElement(el, _q("Behavior")).text = "Usual"
        etree.SubElement(el, _q("ExtendedTooltip"), name=f"{name}РасширеннаяПодсказка", id=str(next_id + 1))
        return el
    _add(form_path, name, parent, position, build)


def add_decoration(form_path: Path, name: str, parent: str = ".", position: str = "last",
                   kind: str = "Label") -> None:
    if kind not in _DECORATIONS:
        raise OpPreconditionError(f"Вид надписи '{kind}': ожидается Label или Picture")

    def build(next_id):
        el = etree.Element(_q(_DECORATIONS[kind]), name=name, id=str(next_id))
        etree.SubElement(el, _q("ContextMenu"), name=f"{name}КонтекстноеМеню", id=str(next_id + 1))
        etree.SubElement(el, _q("ExtendedTooltip"), name=f"{name}РасширеннаяПодсказка", id=str(next_id + 2))
        return el
    _add(form_path, name, parent, position, build)


_ADDITIONS = (("SearchStringAddition", "СтрокаПоиска", "SearchStringRepresentation"),
              ("ViewStatusAddition", "СостояниеПросмотра", "ViewStatusRepresentation"),
              ("SearchControlAddition", "УправлениеПоиском", "SearchControl"))


def add_table(form_path: Path, name: str, data_path: str, parent: str = ".", position: str = "last") -> None:
    """Таблица с привязкой DataPath (табличная часть / таблица значений / динамический список).
    Шаблон — как у всех 3900 таблиц корпуса: ContextMenu, AutoCommandBar, ExtendedTooltip и три
    дополнения (строка поиска, состояние просмотра, управление поиском) со своими меню и подсказками.
    Колонки — `add_column`; команды панели и обработчики событий — не здесь (бэклог)."""
    def build(next_id):
        el = etree.Element(_q("Table"), name=name, id=str(next_id))
        etree.SubElement(el, _q("DataPath")).text = data_path
        ids = iter(range(next_id + 1, next_id + 20))
        etree.SubElement(el, _q("ContextMenu"), name=f"{name}КонтекстноеМеню", id=str(next(ids)))
        etree.SubElement(el, _q("AutoCommandBar"), name=f"{name}КоманднаяПанель", id=str(next(ids)))
        etree.SubElement(el, _q("ExtendedTooltip"), name=f"{name}РасширеннаяПодсказка", id=str(next(ids)))
        for tag, suffix, kind in _ADDITIONS:
            add = etree.SubElement(el, _q(tag), name=f"{name}{suffix}", id=str(next(ids)))
            src = etree.SubElement(add, _q("AdditionSource"))
            etree.SubElement(src, _q("Item")).text = name
            etree.SubElement(src, _q("Type")).text = kind
            etree.SubElement(add, _q("ContextMenu"), name=f"{name}{suffix}КонтекстноеМеню", id=str(next(ids)))
            etree.SubElement(add, _q("ExtendedTooltip"), name=f"{name}{suffix}РасширеннаяПодсказка",
                             id=str(next(ids)))
        return el
    _add(form_path, name, parent, position, build)


def add_column(form_path: Path, name: str, parent: str, data_path: str, position: str = "last") -> None:
    """Колонка таблицы (InputField в таблице или группе колонок): DataPath, EditMode=EnterOnInput
    (как у колонок табличной части, создаваемых конструктором), ContextMenu, ExtendedTooltip."""
    def build(next_id):
        el = etree.Element(_q("InputField"), name=name, id=str(next_id))
        etree.SubElement(el, _q("DataPath")).text = data_path
        etree.SubElement(el, _q("EditMode")).text = "EnterOnInput"
        etree.SubElement(el, _q("ContextMenu"), name=f"{name}КонтекстноеМеню", id=str(next_id + 1))
        etree.SubElement(el, _q("ExtendedTooltip"), name=f"{name}РасширеннаяПодсказка", id=str(next_id + 2))
        return el
    _add(form_path, name, parent, position, build, in_table=True)


def add_column_group(form_path: Path, name: str, parent: str, position: str = "last") -> None:
    """Группа колонок таблицы: ExtendedTooltip (+ ChildItems при первой колонке); Group — через set-style."""
    def build(next_id):
        el = etree.Element(_q("ColumnGroup"), name=name, id=str(next_id))
        etree.SubElement(el, _q("ExtendedTooltip"), name=f"{name}РасширеннаяПодсказка", id=str(next_id + 1))
        return el
    _add(form_path, name, parent, position, build, in_table=True)


def move_item(form_path: Path, name: str, parent: str = ".", position: str = "last") -> None:
    doc, root = _load(form_path)
    el = _find(root, name)
    kind = etree.QName(el).localname
    owner_kind = "Form" if parent in (".", "", None) else etree.QName(_find(root, parent)).localname
    to_table = owner_kind in ("Table", "ColumnGroup")
    from_table = etree.QName(el.getparent().getparent()).localname in ("Table", "ColumnGroup")
    if kind == "ColumnGroup" and not to_table or to_table != from_table:
        raise OpPreconditionError(f"'{name}': колонки переносятся только внутри таблицы, "
                                  f"прочие элементы — вне таблиц (родитель '{parent}' — {owner_kind})")
    dest = _container(root, parent, create=True)
    if el is dest.getparent() or dest.getparent() in el.iterdescendants():
        raise OpPreconditionError(f"Нельзя перенести '{name}' в самого себя или во вложенный элемент")
    src = el.getparent()
    cfg.remove_children(src, [el])
    if not len(src) and src is not dest:             # опустевшую группу Конфигуратор пишет без ChildItems
        cfg.remove_children(src.getparent(), [src])
    _insert(dest, el, position)
    cfg.save(doc, form_path)


def set_title(form_path: Path, name: str, titles: dict) -> None:
    """Заголовок элемента на языках `titles` ({"ru": "...", "en": "..."}) — порядок языков как задан."""
    if not titles:
        raise OpPreconditionError("set-title: нужен хотя бы один язык")
    doc, root = _load(form_path)
    el = root if name in (".", "", None) else _find(root, name)
    ctx = etree.QName(el).localname
    order = _ORDER_CFG.get(ctx, [])
    if "Title" not in order:
        raise OpPreconditionError(f"У '{ctx}' заголовка в формах Конфигуратора нет")
    title = etree.Element(_q("Title"))
    if ctx.endswith("Decoration"):
        title.set("formatted", "false")
    for lang, text in titles.items():
        item = etree.SubElement(title, f"{{{_V8}}}item")
        etree.SubElement(item, f"{{{_V8}}}lang").text = lang
        etree.SubElement(item, f"{{{_V8}}}content").text = text
    old = el.find(_q("Title"))
    if old is not None:
        ws = _indent_before(old)
        title.tail = old.tail
        el.replace(old, title)
        _reindent(title, ws)
    else:
        later = set(order[order.index("Title") + 1:])
        anchor = next((c for c in el if isinstance(c.tag, str) and etree.QName(c).localname in later), None)
        _insert_at(el, title, anchor)
    cfg.save(doc, form_path)


# ---------- общее ----------

def _load(form_path):
    form_path = Path(form_path)
    if form_path.suffix != ".xml":
        raise OpPreconditionError("Структура формы EDT (Form.form) — через EDT MCP; здесь Конфигуратор (Form.xml)")
    doc = cfg.load(form_path)
    return doc, doc.getroot()


def _items(root):
    return root.find(_q("ChildItems"))


def _find(root, name):
    hits = root.xpath(".//lf:ChildItems//*[@name=$n]", namespaces=cfg.NS, n=name)
    if len(hits) != 1:
        raise OpPreconditionError(f"Элемент формы '{name}' не найден" if not hits
                                  else f"Элемент формы '{name}' неоднозначен ({len(hits)})")
    return hits[0]


def _container(root, parent, create):
    """ChildItems родителя (корня формы или группы); при create — создать по таблице порядка."""
    owner = root if parent in (".", "", None) else _find(root, parent)
    kids = owner.find(_q("ChildItems"))
    if kids is None:
        if not create:
            raise OpPreconditionError(f"У '{parent}' нет вложенных элементов")
        ctx = etree.QName(owner).localname
        order = _ORDER_CFG.get(ctx, [])
        if "ChildItems" not in order:
            raise OpPreconditionError(f"'{parent}' ({ctx}) не может содержать элементы")
        kids = etree.Element(_q("ChildItems"))
        later = set(order[order.index("ChildItems") + 1:])
        anchor = next((c for c in owner if isinstance(c.tag, str) and etree.QName(c).localname in later), None)
        _insert_at(owner, kids, anchor)
    return kids


def _next_id(root):
    items = _items(root)
    ids = [int(x) for x in items.xpath(".//@id")] if items is not None else []
    return max([i for i in ids if i > 0], default=0) + 1


def _add(form_path, name, parent, position, build, in_table=False):
    validate_name(name)
    doc, root = _load(form_path)
    owner = None if parent in (".", "", None) else _find(root, parent)
    owner_kind = etree.QName(owner).localname if owner is not None else "Form"
    if in_table != (owner_kind in ("Table", "ColumnGroup")):
        raise OpPreconditionError(
            f"'{name}': колонка/группа колонок — только в таблицу или группу колонок; "
            f"прочие элементы — не в таблицу (родитель '{parent}' — {owner_kind})")
    el = build(_next_id(root))
    # проверяем и имена вложенных частей (…РасширеннаяПодсказка, …КонтекстноеМеню, …КоманднаяПанель):
    # их дубль даёт форму с неоднозначными именами, которую Конфигуратор может не загрузить
    for n in [el.get("name")] + el.xpath(".//@name"):
        if root.xpath(".//*[@name=$n]", n=n):
            raise OpPreconditionError(f"Элемент формы '{n}' уже существует")
    # пустая форма без корневого ChildItems: контейнер создаётся на своём месте по таблице порядка
    _insert(_container(root, parent, create=True), el, position)
    cfg.save(doc, form_path)


def _insert(container, el, position):
    kids = [c for c in container if isinstance(c.tag, str)]
    position = position or "last"
    if position == "last":
        anchor = None
    elif position == "first":
        anchor = kids[0] if kids else None
    elif position.startswith(("before:", "after:")):
        how, _, ref = position.partition(":")
        sib = next((c for c in kids if c.get("name") == ref), None)
        if sib is None:
            raise OpPreconditionError(f"Позиция '{position}': '{ref}' не найден среди соседей")
        anchor = sib if how == "before" else sib.getnext()
    else:
        raise OpPreconditionError(f"Позиция '{position}': first | last | before:Имя | after:Имя")
    _insert_at(container, el, anchor)


def _indent_before(el):
    prev = el.getprevious()
    return (prev.tail if prev is not None else el.getparent().text) or "\n"


def _insert_at(host, el, anchor):
    """Вставить el в host перед anchor (None — в конец) с отступами вывода 1С."""
    if not len(host):
        ws = _indent_before(host) + _UNIT
        host.text = ws
        el.tail = _indent_before(host)
        host.append(el)
    elif anchor is None:
        ws = host.text
        cfg.place_after(host[-1], el)
    else:
        ws = _indent_before(anchor)
        anchor.addprevious(el)
        el.tail = ws
    _reindent(el, ws)


def _reindent(el, ws):
    """Отступы поддерева el, стоящего на отступе ws (строка «перевод + табы»)."""
    kids = [c for c in el if isinstance(c.tag, str)]
    if not kids:
        if el.text is not None and not el.text.strip():
            el.text = None
        return
    inner = ws + _UNIT
    el.text = inner
    for i, c in enumerate(kids):
        c.tail = inner if i < len(kids) - 1 else ws
        _reindent(c, inner)
