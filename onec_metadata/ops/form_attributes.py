"""Реквизиты и команды формы Конфигуратора (Form.xml): колонки реквизита «таблица значений», удаление
реквизита и команды, заголовок команды. Для EDT (Form.form) колонка реквизита-таблицы, её удаление и
удаление реквизита идут в EDT-реализацию onec_metadata.ops.form (EDT MCP их не делает): тот же синтаксис
типа и заголовков, формат - по расширению файла.

Колонка — клон соседней колонки того же реквизита: свежий id в пространстве колонок реквизита,
заголовок, тип. Типы колонок — простые: String (неограниченная строка), Number:<разрядов>[.<дробных>],
Boolean, Date, либо ссылка cfg:CatalogRef.Имя. Ссылки из кода модуля на удаляемое - забота вызывающего.
"""
import copy
import re
from pathlib import Path

from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name

_LF = cfg.NS["lf"]
_V8 = cfg.NS["v8"]


def _q(tag, ns=_LF):
    return f"{{{ns}}}{tag}"


def _load(form_path):
    form_path = Path(form_path)
    if form_path.suffix != ".xml":
        raise OpPreconditionError("Эта операция - только Конфигуратор (Form.xml); для EDT - EDT MCP")
    doc = cfg.load(form_path)
    return doc, doc.getroot()


def _section(root, tag):
    el = root.find(_q(tag))
    if el is None:
        raise OpPreconditionError(f"В форме нет блока {tag}")
    return el


def _attribute(root, name):
    hits = [a for a in _section(root, "Attributes") if isinstance(a.tag, str) and a.get("name") == name]
    if not hits:
        raise OpPreconditionError(f"Реквизита формы '{name}' нет")
    return hits[0]


def _columns(attr):
    cols = attr.find(_q("Columns"))
    if cols is None or not len(cols):
        raise OpPreconditionError(f"У реквизита '{attr.get('name')}' нет колонок-образцов")
    return cols


def _type_children(type_ref):
    """Содержимое <Type> для простого типа колонки."""
    E = lambda tag, text=None: _el(_q(tag, _V8), text)
    if type_ref == "String":
        q = E("StringQualifiers")
        q.append(E("Length", "0"))
        q.append(E("AllowedLength", "Variable"))
        return [E("Type", "xs:string"), q]
    if type_ref == "Boolean":
        return [E("Type", "xs:boolean")]
    if type_ref == "Date":
        q = E("DateQualifiers")
        q.append(E("DateFractions", "DateTime"))
        return [E("Type", "xs:dateTime"), q]
    m = re.fullmatch(r"Number:(\d+)(?:\.(\d+))?", type_ref)
    if m:
        q = E("NumberQualifiers")
        q.append(E("Digits", m.group(1)))
        q.append(E("FractionDigits", m.group(2) or "0"))
        q.append(E("AllowedSign", "Any"))
        return [E("Type", "xs:decimal"), q]
    if re.fullmatch(r"cfg:\w+Ref\.[\wА-Яа-яЁё]+", type_ref) or type_ref in _PLATFORM_TYPES:
        return [E("Type", type_ref)]
    raise OpPreconditionError(f"Тип '{type_ref}': String | Boolean | Date | Number:N[.M] | cfg:CatalogRef.Имя | "
                              + " | ".join(sorted(_PLATFORM_TYPES)))


# платформенные типы реквизитов формы - как их пишет Конфигуратор
_PLATFORM_TYPES = {"v8:ValueListType", "v8:ValueTable", "v8:ValueTree", "dcsset:SettingsComposer", "v8:UUID"}


def _el(tag, text=None):
    e = etree.Element(tag)
    e.text = text
    return e


def _indent(el, depth_ws):
    """Отступы вывода Конфигуратора внутри el (вкладки), el уже на уровне depth_ws."""
    kids = [c for c in el if isinstance(c.tag, str)]
    if not kids:
        return
    el.text = depth_ws + "\t"
    for c in kids:
        _indent(c, depth_ws + "\t")
        c.tail = depth_ws + "\t"
    kids[-1].tail = depth_ws


def _is_edt(form_path) -> bool:
    return Path(form_path).suffix == ".form"


def add_attribute_column(form_path: Path, attribute: str, name: str, type_ref: str, titles: dict) -> None:
    validate_name(name)
    if _is_edt(form_path):
        from onec_metadata.ops.form import add_column_edt
        add_column_edt(Path(form_path), attribute, name, type_ref, titles)
        return
    doc, root = _load(form_path)
    cols = _columns(_attribute(root, attribute))
    existing = [c for c in cols if isinstance(c.tag, str)]
    if name in [c.get("name") for c in existing]:
        raise OpPreconditionError(f"Колонка '{name}' у реквизита '{attribute}' уже есть")
    sample = existing[-1]
    new = copy.deepcopy(sample)
    new.set("name", name)
    new.set("id", str(max(int(c.get("id")) for c in existing) + 1))
    title = _el(_q("Title"))
    for lang, text in titles.items():
        item = etree.SubElement(title, _q("item", _V8))
        etree.SubElement(item, _q("lang", _V8)).text = lang
        etree.SubElement(item, _q("content", _V8)).text = text
    typ = _el(_q("Type"))
    for c in _type_children(type_ref):
        typ.append(c)
    child_ws = new.text or "\n"          # отступ детей колонки
    for part in (title, typ):
        old = new.find(part.tag)
        if old is None:
            raise OpPreconditionError(f"У колонки-образца нет {etree.QName(part).localname}")
        _indent(part, child_ws)
        part.tail = old.tail
        new.replace(old, part)
    cfg.place_after(sample, new)
    cfg.save(doc, form_path)


def add_form_attribute(form_path: Path, name: str, type_ref: str, titles: dict) -> None:
    """Реквизит формы с нуля (без образца): Title + Type, id - следующий в пространстве реквизитов,
    место - после последнего реквизита. Для таблицы значений колонки добавляются add_attribute_column
    (нужна хотя бы одна колонка-образец - её даёт add_attribute_column только при наличии Columns)."""
    validate_name(name)
    doc, root = _load(form_path)
    attrs = _section(root, "Attributes")
    existing = [a for a in attrs if isinstance(a.tag, str)]
    if name in [a.get("name") for a in existing]:
        raise OpPreconditionError(f"Реквизит формы '{name}' уже существует")
    if not existing:
        raise OpPreconditionError("В форме нет реквизитов - добавление первого отдельная операция")
    last = existing[-1]
    new = _el(_q("Attribute"))
    new.set("name", name)
    new.set("id", str(max(int(a.get("id")) for a in existing) + 1))
    if titles:
        title = etree.SubElement(new, _q("Title"))
        for lang, text in titles.items():
            item = etree.SubElement(title, _q("item", _V8))
            etree.SubElement(item, _q("lang", _V8)).text = lang
            etree.SubElement(item, _q("content", _V8)).text = text
    typ = etree.SubElement(new, _q("Type"))
    for c in _type_children(type_ref):
        typ.append(c)
    prev = last.getprevious()
    _indent(new, (prev.tail if prev is not None else attrs.text) or "\n\t\t")   # отступ, с которым стоит реквизит
    cfg.place_after(last, new)
    cfg.save(doc, form_path)


def remove_attribute_column(form_path: Path, attribute: str, name: str) -> None:
    if _is_edt(form_path):
        from onec_metadata.ops.form import remove_attribute_edt
        remove_attribute_edt(Path(form_path), name, parent=attribute)
        return
    doc, root = _load(form_path)
    cols = _columns(_attribute(root, attribute))
    hit = [c for c in cols if isinstance(c.tag, str) and c.get("name") == name]
    if not hit:
        raise OpPreconditionError(f"Колонки '{name}' у реквизита '{attribute}' нет")
    if len([c for c in cols if isinstance(c.tag, str)]) == 1:
        raise OpPreconditionError("Последнюю колонку не удаляем: сначала добавьте новую")
    # как remove_attribute: колонка, к которой привязаны элементы формы (DataPath Реквизит.Колонка[.…]), не удаляется
    path = f"{attribute}.{name}"
    used = root.xpath(".//lf:DataPath[text()=$p or starts-with(text(), concat($p, '.'))]", namespaces=cfg.NS, p=path)
    if used:
        raise OpPreconditionError(f"Колонка '{path}' привязана к элементам формы - сначала удалите их")
    cfg.remove_children(cols, hit)
    cfg.save(doc, form_path)


def remove_attribute(form_path: Path, name: str) -> None:
    if _is_edt(form_path):
        from onec_metadata.ops.form import remove_attribute_edt
        remove_attribute_edt(Path(form_path), name)
        return
    doc, root = _load(form_path)
    attr = _attribute(root, name)
    if attr.findtext(_q("MainAttribute")) == "true":
        raise OpPreconditionError("Основной реквизит формы не удаляем")
    used = root.xpath(".//lf:DataPath[text()=$n or starts-with(text(), concat($n, '.'))]", namespaces=cfg.NS, n=name)
    if used:
        raise OpPreconditionError(f"Реквизит '{name}' привязан к элементам формы - сначала удалите их")
    cfg.remove_children(attr.getparent(), [attr])
    cfg.save(doc, form_path)


def _command(root, name):
    hits = [c for c in _section(root, "Commands") if isinstance(c.tag, str) and c.get("name") == name]
    if not hits:
        raise OpPreconditionError(f"Команды формы '{name}' нет")
    return hits[0]


def remove_command(form_path: Path, name: str) -> None:
    doc, root = _load(form_path)
    cmd = _command(root, name)
    if root.xpath(".//lf:CommandName[text()=$n]", namespaces=cfg.NS, n="Form.Command." + name):
        raise OpPreconditionError(f"На команду '{name}' ссылаются кнопки - сначала удалите их")
    cmds = cmd.getparent()
    cfg.remove_children(cmds, [cmd])
    if not len(cmds):
        cfg.remove_children(root, [cmds])
    cfg.save(doc, form_path)


def set_command_title(form_path: Path, name: str, titles: dict) -> None:
    """Заголовок и подсказка команды - один текст на язык (так пишет Конфигуратор для новой команды)."""
    doc, root = _load(form_path)
    cmd = _command(root, name)
    for tag in ("Title", "ToolTip"):
        el = cmd.find(_q(tag))
        if el is None:
            continue
        items = {it.findtext(_q("lang", _V8)): it for it in el.findall(_q("item", _V8))}
        for lang, text in titles.items():
            if lang not in items:
                raise OpPreconditionError(f"У команды нет языка '{lang}' в {tag} - добавление языка отдельная операция")
            items[lang].find(_q("content", _V8)).text = text
    cfg.save(doc, form_path)
