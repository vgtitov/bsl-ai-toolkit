"""Оформление элемента управляемой формы в СВОЙСТВАХ формы — EDT (`Form.form`) и Конфигуратор (`Form.xml`).

Зачем: цвета, шрифты, группировку и отображение групп EDT MCP не задаёт, а красить
форму кодом при открытии плохо — форма в IDE выглядит голой, вид проверяется только
запуском. Здесь свойства пишутся туда, где их пишет сама платформа/IDE.

Где и в каком порядке: свойства сериализуются в фиксированном порядке. В EDT часть живёт на
уровне элемента (`items`), часть — в его `extInfo`; в Конфигураторе всё плоско под элементом
(`<UsualGroup>`, `<InputField>`…), имена с большой буквы. Порядок не угадывается, а взят из
корпуса реальных форм: `data/edt_form_order.json` и `data/cfg_form_order.json` (пересборка —
`scripts/learn_edt_form_order.py`). Свойства, которого нет в таблице для вида элемента, —
отказ предусловием (ничего не выдумываем).

Имена свойств — в стиле EDT (`backColor`, `showTitle`); для Конфигуратора допустим и `BackColor`.
Значения (одинаковые для обоих форматов):
  цвет  (имя оканчивается на Color): `style:Имя` | `web:Имя` | `rgb:R,G,B`;
  шрифт (font / *Font): `style:ИмяШрифта[,face=Gilroy][,height=10][,bold][,italic]…[,scale=N]`;
        шрифт-токен стиля без параметров (`style:примШрифтПодпись`) — чистая ссылка;
  картинка (picture / *Picture): `common:ИмяОбщейКартинки` | `std:Find`;
  прочее — текст как есть (`None`, `true`, `WeakSeparation`, `AlwaysHorizontal`);
  пустое значение — удалить свойство (вернуть умолчание).
"""
import json
import math
from pathlib import Path

from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError

_DATA = Path(__file__).resolve().parent.parent / "data"
_ORDER_EDT = json.loads((_DATA / "edt_form_order.json").read_text(encoding="utf-8"))
_ORDER_CFG = json.loads((_DATA / "cfg_form_order.json").read_text(encoding="utf-8"))
# выгрузка Конфигуратора не пишет значение, равное умолчанию платформы (пары EDT ↔ XML-выгрузка)
_DEFAULTS_CFG = json.loads((_DATA / "cfg_form_defaults.json").read_text(encoding="utf-8"))
_XT = cfg.q("xsi", "type")
_XNIL = cfg.q("xsi", "nil")
_LF = cfg.NS["lf"]
_XR = cfg.NS["xr"]
_FONT_FLAGS = ("bold", "italic", "underline", "strikeout")
_PICTURE_KINDS = {"common": "CommonPicture", "std": "StdPicture"}


def set_style(form_path: Path, item: str, props: dict, omit_defaults: bool = False) -> None:
    """Задать элементу `item` (имя элемента; `.` — сама форма) свойства `props`.

    Заданное значение пишется ЯВНО. `omit_defaults=True` (только Конфигуратор) — не писать значение, равное
    умолчанию из `data/cfg_form_defaults.json`, как это делает выгрузка: нужно для сравнения байт в байт с
    выгрузкой. Таблица собрана по корпусу и к версии платформы не привязана, поэтому по умолчанию не сжимаем."""
    form_path = Path(form_path)
    if form_path.suffix == ".form":
        fmt = _Edt()
    elif form_path.suffix == ".xml":
        fmt = _Cfg(omit_defaults)
    else:
        raise OpPreconditionError("set-style: ожидается Form.form (EDT) или Form.xml (Конфигуратор)")
    doc = cfg.load(form_path)
    target = fmt.find_item(doc.getroot(), item)

    # сначала всё проверить и построить, потом менять — при ошибке файл не трогаем
    plan = []
    for name, value in props.items():
        host, order, tag = fmt.host_for(target, name)
        spec = fmt.normalize(host, tag, _parse(tag, value))
        plan.append((host, order, tag, spec))
    for host, order, tag, spec in plan:
        _put(fmt, host, order, tag, spec)
    cfg.save(doc, form_path)


# ---------- значение: разбор один раз, отрисовка — по формату ----------

def _parse(tag: str, value: str):
    low = tag[0].lower() + tag[1:]
    if value == "":
        return ("remove",)
    if low.endswith("Color"):
        kind, _, rest = value.partition(":")
        if kind in ("style", "web") and rest:
            return ("color", kind, rest)
        if kind == "rgb":
            parts = [p.strip() for p in rest.split(",")]
            if len(parts) == 3 and all(p.isdigit() and int(p) <= 255 for p in parts):
                return ("rgb", tuple(int(p) for p in parts))
            raise OpPreconditionError(f"Цвет '{value}': ожидается rgb:R,G,B (0..255)")
        raise OpPreconditionError(f"Цвет '{value}': ожидается style:/web:/rgb:")
    if low == "font" or low.endswith("Font"):
        head, *opts = [p.strip() for p in value.split(",")]
        if not head.startswith("style:") or head == "style:":
            raise OpPreconditionError(f"Шрифт '{value}': ожидается style:ИмяШрифтаСтиля[,…]")
        face = height = scale = None
        flags = set()
        for o in opts:
            k, _, v = o.partition("=")
            if k == "face":
                face = v
            elif k == "height":
                try:
                    height = float(v)
                    if not (math.isfinite(height) and 0 < height <= 1000):
                        raise ValueError
                except ValueError:
                    raise OpPreconditionError(f"Шрифт '{value}': height — не конечное положительное число") from None
            elif k in _FONT_FLAGS and not v:
                flags.add(k)
            elif k == "scale" and v.isascii() and v.isdecimal():
                if not 0 < int(v) <= 1000:
                    raise OpPreconditionError(f"Шрифт '{value}': scale вне 1..1000")
                scale = v
            else:
                raise OpPreconditionError(f"Шрифт '{value}': неизвестный параметр '{o}'")
        # scale EDT/Конфигуратор пишут вместе с faceName (корпус ERP: 238 из 256), у чистой
        # ссылки на шрифт стиля - нет (690 из 703); явный scale=N перекрывает правило
        if scale is None and face:
            scale = "100"
        return ("font", head[len("style:"):], face, height, flags, scale)
    if low == "picture" or low.endswith("Picture"):
        kind, _, name = value.partition(":")
        if kind not in _PICTURE_KINDS or not name:
            raise OpPreconditionError(f"Картинка '{value}': ожидается common:Имя или std:Имя")
        return ("picture", f"{_PICTURE_KINDS[kind]}.{name}")
    return ("text", value)


# ---------- форматы ----------

def _ctx(el) -> str:
    t = etree.QName(el).localname
    x = el.get(_XT)
    return f"{t}[{x}]" if x else t


def _reject(name, ctx, ide):
    raise OpPreconditionError(
        f"Свойство '{name}' не встречается у '{ctx}' в формах {ide} "
        "(нет в таблице порядка) — не задаём наугад")


class _Edt:
    unit = "  "

    def find_item(self, root, item):
        if item in (".", "", None):
            return root
        return _one(root.xpath(".//items[name=$n]", n=item), item)

    def host_for(self, target, name):
        name = name[0].lower() + name[1:]
        own = _ORDER_EDT.get(_ctx(target), [])
        if name in own and name != "extInfo":
            return target, own, name
        ext = target.find("extInfo")
        if ext is not None and name in _ORDER_EDT.get(_ctx(ext), []):
            return ext, _ORDER_EDT[_ctx(ext)], name
        _reject(name, _ctx(target), "EDT")

    def qname(self, tag):
        return tag

    def normalize(self, host, tag, spec):
        return spec                                     # EDT пишет заданное значение явно

    def build(self, tag, spec, ws):
        el = etree.Element(tag)
        inner = ws + self.unit
        kind = spec[0]
        if kind == "text":
            el.text = spec[1]
            return el
        if kind == "color":
            el.set(_XT, "core:ColorRef")
            _sub(el, "color", f"{'Style' if spec[1] == 'style' else 'Web'}.{spec[2]}", inner)
        elif kind == "rgb":
            el.set(_XT, "core:ColorDef")
            for t, v in zip(("red", "green", "blue"), spec[1]):
                if v:                                   # EDT нулевые компоненты не пишет
                    _sub(el, t, str(v), inner)
        elif kind == "font":
            _, ref, face, height, flags, scale = spec
            el.set(_XT, "core:FontRef")
            _sub(el, "font", f"Style.{ref}", inner)
            if face:
                _sub(el, "faceName", face, inner)
            if height is not None:
                _sub(el, "height", f"{height:.1f}", inner)
            if flags:                                   # EDT пишет все четыре флага разом
                for f in _FONT_FLAGS:
                    _sub(el, f, "true" if f in flags else "false", inner)
            if scale:
                _sub(el, "scale", scale, inner)
        elif kind == "picture":
            el.set(_XT, "core:PictureRef")
            _sub(el, "picture", spec[1], inner)
        if len(el):
            el.text = inner
            el[-1].tail = ws
        return el


class _Cfg:
    unit = "\t"

    def __init__(self, omit_defaults=False):
        self.omit_defaults = omit_defaults

    def find_item(self, root, item):
        if item in (".", "", None):
            return root
        return _one(root.xpath(".//lf:ChildItems//*[@name=$n]", namespaces=cfg.NS, n=item), item)

    def host_for(self, target, name):
        name = name[0].upper() + name[1:]
        order = _ORDER_CFG.get(_ctx(target), [])
        if name in order and name not in ("ChildItems", "Events", "ExtendedTooltip", "ContextMenu"):
            return target, order, name
        _reject(name, _ctx(target), "Конфигуратора")

    def qname(self, tag):
        return f"{{{_LF}}}{tag}"

    def normalize(self, host, tag, spec):
        """С omit_defaults значение, равное умолчанию (выгрузка его не пишет), снимаем; иначе пишем явно."""
        if (self.omit_defaults and spec[0] == "text"
                and _DEFAULTS_CFG.get(_ctx(host), {}).get(tag) == spec[1]):
            return ("remove",)
        return spec

    def build(self, tag, spec, ws):
        el = etree.Element(self.qname(tag))
        kind = spec[0]
        if kind == "text" and spec[1] == "xsi:nil":     # напр. <RowFilter xsi:nil="true"/>
            el.set(_XNIL, "true")
        elif kind == "text":
            el.text = spec[1]
        elif kind == "color":
            el.text = f"{spec[1]}:{spec[2]}"
        elif kind == "rgb":
            el.text = "#" + "".join(f"{v:02X}" for v in spec[1])
        elif kind == "font":
            _, ref, face, height, flags, scale = spec
            el.set("ref", f"style:{ref}")
            if face:
                el.set("faceName", face)
            if height is not None:
                el.set("height", str(int(height)) if height.is_integer() else str(height))
            if flags:
                for f in _FONT_FLAGS:
                    el.set(f, "true" if f in flags else "false")
            el.set("kind", "StyleItem")
            if scale:
                el.set("scale", scale)
        elif kind == "picture":
            inner = ws + self.unit
            el.text = inner
            _sub(el, f"{{{_XR}}}Ref", spec[1], inner)
            _sub(el, f"{{{_XR}}}LoadTransparent", "false", ws)
        return el


def _one(hits, item):
    if len(hits) != 1:
        raise OpPreconditionError(
            f"Элемент формы '{item}' не найден" if not hits
            else f"Элемент формы '{item}' неоднозначен ({len(hits)})")
    return hits[0]


# ---------- запись с отступами вывода 1С ----------

def _indent_of(el) -> str:
    """Отступ перед элементом (хвост предыдущего брата или text родителя)."""
    prev = el.getprevious()
    ws = prev.tail if prev is not None else el.getparent().text
    return ws or "\n"


def _child_indent(fmt, host) -> str:
    if len(host):
        return host.text
    return _indent_of(host) + fmt.unit


def _sub(parent, tag, text, tail):
    el = etree.SubElement(parent, tag)
    el.text = text
    el.tail = tail
    return el


def _put(fmt, host, order, tag, spec) -> None:
    existing = host.find(fmt.qname(tag))
    if spec[0] == "remove":
        if existing is not None:
            cfg.remove_children(host, [existing])
        return
    child_ws = _child_indent(fmt, host)
    new = fmt.build(tag, spec, child_ws)
    if existing is not None:
        new.tail = existing.tail
        host.replace(existing, new)
        return
    later = set(order[order.index(tag) + 1:])
    anchor = next((c for c in host if isinstance(c.tag, str)
                   and etree.QName(c).localname in later), None)
    if not len(host):                       # пустой узел: открыть и закрыть
        host.text = child_ws
        new.tail = _indent_of(host)
        host.append(new)
    elif anchor is None:                    # в конец: как place_after последнего
        cfg.place_after(host[-1], new)
    else:                                   # перед первым «более поздним» братом
        ws = _indent_of(anchor)
        anchor.addprevious(new)
        new.tail = ws
