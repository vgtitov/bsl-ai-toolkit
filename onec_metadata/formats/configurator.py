"""Byte-perfect чтение/запись XML Конфигуратора (DumpConfigToFiles).

Инвариант: save(load(f)) == f побайтно. Проверяется round-trip тестом на всех
фикстурах. Любая операция ops/* меняет только целевые узлы.

Нюанс переводов строк: выгрузка 1С СМЕШАННАЯ — межэлементные отступы CRLF,
но текст внутри узлов (например, запросы СКД) может быть с чистыми LF.
XML-парсер по спецификации нормализует CRLF→LF, теряя различие. Поэтому:
- перед парсингом каждый CRLF защищается как числовая ссылка &#13; + LF
  (парсер восстановит его в текстовом узле как символ \r);
- lxml сериализует \r в тексте как &#13; — после сериализации она
  превращается обратно в литеральный CR.
Так исходные последовательности \r\n и \n восстанавливаются точно.

ИЗВЕСТНОЕ ОГРАНИЧЕНИЕ (Фаза 1): если В ИСХОДНИКЕ уже присутствует РЕАЛЬНАЯ
числовая ссылка &#13; (5 символов, эмитится платформой редко), она на
загрузке резолвится в \r и на сохранении становится 1 байтом CR — round-trip
для такого входа не byte-perfect. На фикстурах и типовых выгрузках УТ не
встречается; round-trip тест поймает регресс, если встретится.
"""
from pathlib import Path

from lxml import etree

NS = {
    "md": "http://v8.1c.ru/8.3/MDClasses",
    "v8": "http://v8.1c.ru/8.1/data/core",
    "xr": "http://v8.1c.ru/8.3/xcf/readable",
    "ss": "http://v8.1c.ru/8.2/data/spreadsheet",
    "dcs": "http://v8.1c.ru/8.1/data-composition-system/schema",
    "dcscor": "http://v8.1c.ru/8.1/data-composition-system/core",
    "dcsset": "http://v8.1c.ru/8.1/data-composition-system/settings",
    "roles": "http://v8.1c.ru/8.2/roles",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xs": "http://www.w3.org/2001/XMLSchema",
}

BOM = "﻿"
XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'


def q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


_PARSER = etree.XMLParser(
    remove_blank_text=False,
    strip_cdata=False,
    resolve_entities=False,
    huge_tree=True,
)


class Doc:
    """Платформенный XML-документ (Конфигуратор ИЛИ EDT): lxml-дерево +
    детали исходного файла (BOM, переводы строк, хвост) для точного
    воспроизведения стиля при сохранении."""

    def __init__(self, tree: etree._ElementTree, decl_newline: str,
                 trailing_newline: str, has_bom: bool = True):
        self.tree = tree
        self.decl_newline = decl_newline        # перевод строки после <?xml?>
        self.trailing_newline = trailing_newline  # хвост файла ("" если нет)
        self.has_bom = has_bom                  # Конфигуратор: да; EDT: нет

    def __getattr__(self, name):
        return getattr(self.tree, name)


def load(path: Path) -> Doc:
    raw = path.read_bytes()
    has_bom = raw.startswith(BOM.encode("utf-8"))
    if has_bom:
        raw = raw[len(BOM.encode("utf-8")):]

    text = raw.decode("utf-8")
    if text.startswith(XML_DECL):
        after = text[len(XML_DECL):]
        decl_newline = "\r\n" if after.startswith("\r\n") else "\n"
        body_text = after[len(decl_newline):]
    else:
        decl_newline = "\n"
        body_text = text

    trailing_newline = ""
    for cand in ("\r\n", "\n"):
        if body_text.endswith(cand):
            trailing_newline = cand
            break

    protected = body_text.rstrip("\r\n").replace("\r\n", "&#13;\n")
    tree = etree.ElementTree(
        etree.fromstring(protected.encode("utf-8"), _PARSER))
    return Doc(tree, decl_newline, trailing_newline, has_bom=has_bom)


def place_after(anchor, new) -> None:
    """Вставить `new` сразу после `anchor` (последнего образца своего вида),
    сохранив отступы вывода 1С.

    Хвост (`.tail`) последнего ребёнка несёт ЗАКРЫВАЮЩИЙ отступ родителя — на
    уровень меньше межэлементного. Наивный `anchor.addnext(new)` оставил бы
    новому элементу либо этот короткий хвост, либо (для не-последнего) верный —
    непредсказуемо. Здесь: новый получает прежний хвост anchor (встаёт как
    последний / перед следующим собратом), а anchor — отступ ребёнка родителя
    (`parent.text`, т.е. отступ перед первым дочерним). Так отступ корректен и
    когда anchor был последним ребёнком, и когда за ним идёт элемент другого
    вида; иначе платформа при выгрузке перевыровняет и даст diff-шум.
    """
    parent = anchor.getparent()
    prev_tail = anchor.tail
    anchor.addnext(new)
    new.tail = prev_tail
    if parent is not None and parent.text is not None:
        anchor.tail = parent.text


def remove_children(container, to_remove) -> None:
    """Удалить элементы `to_remove` из `container`, сохранив отступ закрывающего тега.

    Хвост (`.tail`) последнего ребёнка несёт ЗАКРЫВАЮЩИЙ отступ родителя. Если
    среди удаляемых есть последний ребёнок, новый последний должен унаследовать
    этот закрывающий хвост — иначе он сохранит свой (межэлементный, на уровень
    глубже) и закрывающий тег переотступится (нарушение byte-perfect). Зеркало
    `place_after` для удаления.
    """
    to_remove = list(to_remove)
    if not to_remove:
        return
    kids = list(container)
    closing_tail = kids[-1].tail if kids else None
    removing_last = bool(kids) and kids[-1] in to_remove
    for el in to_remove:
        container.remove(el)
    if removing_last:
        rest = list(container)
        if rest and closing_tail is not None:
            rest[-1].tail = closing_tail


def save(doc: Doc, path: Path) -> None:
    body = etree.tostring(doc.tree, encoding="unicode")
    body = body.replace("&#13;\n", "\r\n").replace("&#13;", "\r")
    bom = BOM if doc.has_bom else ""
    text = bom + XML_DECL + doc.decl_newline + body + doc.trailing_newline
    path.write_bytes(text.encode("utf-8"))
