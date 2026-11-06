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
import hashlib
import os
import re
import shutil
import tempfile
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
    "lf": "http://v8.1c.ru/8.3/xcf/logform",
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
        self.src_path = None
        self.src_hash = None                    # sha1 файла при чтении: save не затирает чужую правку
        self.quot_texts = {}                    # тексты, которые lxml пишет иначе исходника (см. load)

    def __getattr__(self, name):
        return getattr(self.tree, name)


def load(path: Path) -> Doc:
    raw = raw_all = path.read_bytes()
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
    doc = Doc(tree, decl_newline, trailing_newline, has_bom=has_bom)
    doc.src_hash = hashlib.sha1(raw_all).hexdigest()
    doc.src_path = path.resolve()
    # lxml пишет текст по-своему: раскрывает &quot; и числовые ссылки, экранирует сырой '>' - без
    # восстановления любая правка дает diff-шум в чужих текстах. Дерево не трогаем (операции видят обычные
    # символы): запоминаем такие фрагменты исходника, save вернет их нетронутым.
    doc.quot_texts = _quote_texts(protected)
    return doc


_TAG = re.compile(r"<[^>]*>")


def _lxml_text(seg: str) -> str:
    """Текстовый фрагмент исходника ровно так, как его запишет lxml после разбора."""
    return etree.tostring(etree.fromstring("<x>" + seg + "</x>", _PARSER), encoding="unicode")[3:-4]


def _quote_texts(protected: str) -> dict:
    """Текстовые фрагменты, которые lxml запишет иначе, чем в исходнике (&quot;, сырой '>', числовые
    ссылки): ключ - запись lxml, значение - очередь исходных записей в порядке документа. Очередь, а не
    одно значение: одинаковые тексты бывают записаны по-разному (A &quot;B&quot; и A "B")."""
    texts: dict = {}
    for seg in _TAG.split(protected):
        if "&" in seg or ">" in seg or '"' in seg:
            texts.setdefault(_lxml_text(seg), []).append(seg)
    # фрагменты, которые lxml и так пишет как в исходнике, восстанавливать незачем
    return {k: v for k, v in texts.items() if any(s != k for s in v)}


def _restore_quot(body: str, quot_texts: dict) -> str:
    """Текстовым фрагментам, совпавшим с исходными, вернуть исходную запись по очереди документа.
    Измененный и новый текст остается как записал lxml. Граница тега по '>' надежна: lxml экранирует
    '>' в атрибутах."""
    # shortcut: привязка по значению и порядку, не по узлу - если в одном файле один и тот же текст записан
    # по-разному И операция переставит такие узлы, записи поменяются местами. Один сериализатор пишет
    # файл единообразно, в выгрузках такого нет; понадобится - хранить путь узла (text/tail).
    queues = {k: list(v) for k, v in quot_texts.items()}
    parts = re.split(r"(<[^>]*>)", body)
    for i in range(0, len(parts), 2):
        q = queues.get(parts[i]) if parts[i] else None
        if q:
            parts[i] = q.pop(0)
    return "".join(parts)


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
    path = path.resolve()                        # через symlink пишем в целевой файл, ссылка остаётся
    body = etree.tostring(doc.tree, encoding="unicode")
    if doc.quot_texts:                           # до замены &#13;: ключи сняты с защищенного текста
        body = _restore_quot(body, doc.quot_texts)
    body = body.replace("&#13;\n", "\r\n").replace("&#13;", "\r")
    bom = BOM if doc.has_bom else ""
    text = bom + XML_DECL + doc.decl_newline + body + doc.trailing_newline
    data = text.encode("utf-8")
    _check_unchanged(doc, path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            shutil.copymode(path, tmp)          # mkstemp даёт 0600, права файла сохраняем
        _check_unchanged(doc, path)             # окно до замены минимально; строгая блокировка - вне объёма
        # ponytail: окно между проверкой и заменой остаётся (микросекунды), строгая защита - lock-файл;
        # жёсткая ссылка на Form.xml расходится с копией, fsync каталога не делаем (Windows)
        os.replace(tmp, path)                   # целиком или никак
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    doc.src_path = path
    doc.src_hash = hashlib.sha1(data).hexdigest()  # повторный save того же Doc не считается чужой правкой


def _check_unchanged(doc: Doc, path: Path) -> None:
    if not (doc.src_hash and doc.src_path == path):
        return
    if not path.exists() or hashlib.sha1(path.read_bytes()).hexdigest() != doc.src_hash:
        from onec_metadata.ops import OpPreconditionError
        raise OpPreconditionError(f"{path.name}: файл изменён другим процессом после чтения, "
                                  "правка не записана, повторите команду")
