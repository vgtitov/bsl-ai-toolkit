"""Вставка колонки в макет табличного документа (spreadsheet XML, «моксель»).

Как это устроено в выгрузке Конфигуратора:
- сетка листа разбита на наборы колонок <columns> (id + size + columnsItem);
- строка <row> первой дочкой несёт <columnsID> — ссылку на набор;
- ячейки <c> внутри строки позиционные; атрибут-элемент <i> задаёт явный
  индекс (пропуск колонок), дальше индексация продолжается неявно (+1);
- текст ячейки — <tl><v8:item><v8:content>, параметр — <parameter>.

Операция «как человек в Конфигураторе»: вставить колонку после якорной во всех
строках набора — копией якорной ячейки (наследует формат), сдвинуть явные
индексы <i> и columnsItem/index правее точки вставки, увеличить size набора.
Объединения (merge), пересекающие точку вставки в строках набора, в Фазе 1 не
поддерживаются — операция отказывает до записи.
"""
import copy
from pathlib import Path

from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError


def _ln(el) -> str:
    return etree.QName(el).localname


def _row_of(el):
    cur = el
    while cur is not None and _ln(cur) != "row":
        cur = cur.getparent()
    return cur


def _cell_of(el):
    """Внешняя ячейка <c>, чей родитель — <row>."""
    cur = el
    while cur is not None:
        parent = cur.getparent()
        if _ln(cur) == "c" and parent is not None and _ln(parent) == "row":
            return cur
        cur = parent
    return None


def _row_columns_id(row) -> str | None:
    for k in row:
        if _ln(k) == "columnsID":
            return k.text
    return None


def _row_cells(row):
    return [k for k in row if _ln(k) == "c"]


def _cell_index(cell) -> int:
    """Ординал колонки ячейки с учётом явных <i> у неё и предшественниц."""
    idx = -1
    for c in _row_cells(_row_of(cell)):
        explicit = next((k.text for k in c if _ln(k) == "i"), None)
        idx = int(explicit) if explicit is not None else idx + 1
        if c is cell:
            return idx
    raise OpPreconditionError("Ячейка не найдена в своей строке")


def _cell_at_index(row, index: int):
    idx = -1
    for c in _row_cells(row):
        explicit = next((k.text for k in c if _ln(k) == "i"), None)
        idx = int(explicit) if explicit is not None else idx + 1
        if idx == index:
            return c
        if idx > index:
            return None
    return None


def _set_cell_text(cell, text: str) -> None:
    for t in cell.iter():
        if _ln(t) == "content":
            # пустой текст → None: сериализуется как самозакрытый <content/>,
            # что идемпотентно при повторном load/save (минимальный дифф).
            t.text = text or None


def _set_cell_parameter(cell, name: str) -> None:
    for t in cell.iter():
        if _ln(t) == "parameter":
            t.text = name


def _shift_explicit_indexes(row, from_index: int) -> None:
    for c in _row_cells(row):
        for k in c:
            if _ln(k) == "i" and int(k.text) >= from_index:
                k.text = str(int(k.text) + 1)


def _fix_tails_after_insert(src, new, siblings) -> None:
    """После src.addnext(new) поправить хвосты, если src был последним.

    Хвост последнего элемента несёт закрывающий отступ родителя (на уровень
    меньше). При вставке после последнего новый элемент должен унаследовать
    этот закрывающий хвост, а бывший последний — получить обычный
    межэлементный отступ (как у прочих собратьев). Иначе платформа при
    следующей выгрузке перевыровняет отступы и даст diff-шум.
    """
    if len(siblings) < 2 or src is not siblings[-1]:
        return
    regular_tail = siblings[0].tail
    new.tail = src.tail
    src.tail = regular_tail


def _columns_set(root, columns_id: str):
    for cs in root:
        if _ln(cs) != "columns":
            continue
        cid = next((k.text for k in cs.iter() if _ln(k) == "id"), None)
        if cid == columns_id:
            return cs
    return None


def add_column(template_path: Path, after_header: str, header: str,
               parameter: str) -> None:
    doc = cfg.load(template_path)
    root = doc.getroot()
    v8ns = {"v8": cfg.NS["v8"]}

    if doc.xpath(f"//v8:content[text()='{header}']", namespaces=v8ns):
        raise OpPreconditionError(f"Колонка '{header}' уже есть в макете")
    if doc.xpath(f"//*[local-name()='parameter'][text()='{parameter}']"):
        raise OpPreconditionError(f"Параметр '{parameter}' уже есть в макете")

    anchors = doc.xpath(f"//v8:content[text()='{after_header}']", namespaces=v8ns)
    if not anchors:
        raise OpPreconditionError(f"Не найден заголовок-якорь '{after_header}'")
    header_cell = _cell_of(anchors[0])
    if header_cell is None:
        raise OpPreconditionError(f"Якорь '{after_header}' вне ячейки таблицы")

    header_row = _row_of(header_cell)
    columns_id = _row_columns_id(header_row)
    if not columns_id:
        raise OpPreconditionError("Строка якоря не привязана к набору колонок")

    cset = _columns_set(root, columns_id)
    if cset is None:
        raise OpPreconditionError(f"Набор колонок {columns_id} не найден")

    anchor_idx = _cell_index(header_cell)
    size_el = next(k for k in cset if _ln(k) == "size")
    old_size = int(size_el.text)
    insert_idx = anchor_idx + 1

    # затронутые строки: все строки документа с этим columnsID
    rows = [r for r in root.iter() if _ln(r) == "row"
            and _row_columns_id(r) == columns_id]

    # merge-гард: объединения в строках набора правее точки вставки
    row_numbers = set()
    rownum = 0
    for ri in root:
        if _ln(ri) == "rowsItem":
            rownum += 1
            row = next((k for k in ri if _ln(k) == "row"), None)
            if row is not None and _row_columns_id(row) == columns_id:
                row_numbers.add(rownum)
    for m in root:
        if _ln(m) != "merge":
            continue
        vals = {_ln(k): int(k.text) for k in m if k.text and k.text.strip().lstrip("-").isdigit()}
        if vals.get("r") in row_numbers and vals.get("c", -1) + vals.get("w", 0) >= insert_idx:
            raise OpPreconditionError(
                "Вставка пересекает объединение ячеек (merge) — не поддерживается в Фазе 1")

    # 1) набор колонок: size+1, сдвиг index у columnsItem, новый columnsItem
    size_el.text = str(old_size + 1)
    items = [k for k in cset if _ln(k) == "columnsItem"]
    anchor_item = None
    for it in items:
        idx_el = next((k for k in it if _ln(k) == "index"), None)
        if idx_el is None:
            continue
        iv = int(idx_el.text)
        if iv == anchor_idx:
            anchor_item = it
        if iv >= insert_idx:
            idx_el.text = str(iv + 1)
    template_item = anchor_item if anchor_item is not None else (items[-1] if items else None)
    if template_item is not None:
        new_item = copy.deepcopy(template_item)
        idx_el = next(k for k in new_item if _ln(k) == "index")
        idx_el.text = str(insert_idx)
        template_item.addnext(new_item)
        _fix_tails_after_insert(template_item, new_item, items)

    # 2) строки: вставка копии ячейки на позиции anchor_idx (если она есть в строке)
    for row in rows:
        src = _cell_at_index(row, anchor_idx)
        if src is None:
            continue  # строка короче (например, «Итого») — не трогаем
        _shift_explicit_indexes(row, insert_idx)
        new_cell = copy.deepcopy(src)
        # убрать явный индекс у копии: она встаёт сразу за источником
        for k in list(new_cell):
            if _ln(k) == "i":
                new_cell.remove(k)
        if row is header_row:
            _set_cell_text(new_cell, header)
            _set_cell_parameter(new_cell, parameter)  # на случай параметра в шапке
        else:
            _set_cell_parameter(new_cell, parameter)
            _set_cell_text(new_cell, "")
        cells = _row_cells(row)
        src.addnext(new_cell)
        _fix_tails_after_insert(src, new_cell, cells)

    cfg.save(doc, template_path)
