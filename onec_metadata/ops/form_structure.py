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

_GROUP_KINDS = ("Usual", "Pages", "Page")


def _check_page_parent(name, kind, owner_kind, parent):
    """Страница (Page) — только в группу страниц (Pages), в группу страниц — только страницы."""
    if (kind == "Page") != (owner_kind == "Pages"):
        raise OpPreconditionError(f"'{name}': страница (Page) — только в группу страниц (Pages), "
                                  f"в группу страниц — только страницы (родитель '{parent}' — {owner_kind})")


# Источники динамического списка (основная таблица Вид.Имя)
_LIST_SOURCES = ("Catalog", "Document", "DocumentJournal", "Enum", "ChartOfCharacteristicTypes", "ChartOfAccounts",
                 "ChartOfCalculationTypes", "InformationRegister", "AccumulationRegister", "AccountingRegister",
                 "CalculationRegister", "BusinessProcess", "Task", "ExchangePlan")


# Виды метаданных, у которых бывают свои команды (`<Вид>.<Объект>.Command.<Имя>`)
_COMMAND_OWNERS = _LIST_SOURCES + ("Report", "DataProcessor", "FilterCriterion")


def _check_command_ref(command):
    """Form.Command.<Имя> | CommonCommand.<Имя> | <Вид>.<Объект>.Command.<Имя> — сегменты непустые, имена допустимые."""
    parts = command.split(".")
    ok = (len(parts) == 3 and parts[:2] == ["Form", "Command"]) or (len(parts) == 2 and parts[0] == "CommonCommand") \
        or (len(parts) == 4 and parts[2] == "Command" and parts[0] in _COMMAND_OWNERS)
    if not ok:
        raise OpPreconditionError(f"Команда '{command}': ожидается Form.Command.<Имя>, CommonCommand.<Имя> "
                                  "или <Вид>.<Объект>.Command.<Имя>")
    for p in parts[1:]:
        if p != "Command":
            validate_name(p)


def add_group(form_path: Path, name: str, parent: str = ".", position: str = "last", kind: str = "Usual") -> None:
    """Группа: Usual — обычная (`Behavior=Usual`), Pages — группа страниц, Page — страница (только в Pages).
    Шаблон страниц — как у Конфигуратора: элемент + ExtendedTooltip; отображение — через set-style."""
    if kind not in _GROUP_KINDS:
        raise OpPreconditionError(f"Вид группы '{kind}': ожидается {' | '.join(_GROUP_KINDS)}")

    def build(next_id):
        el = etree.Element(_q("UsualGroup" if kind == "Usual" else kind), name=name, id=str(next_id))
        if kind == "Usual":
            etree.SubElement(el, _q("Behavior")).text = "Usual"
        etree.SubElement(el, _q("ExtendedTooltip"), name=f"{name}РасширеннаяПодсказка", id=str(next_id + 1))
        return el
    _add(form_path, name, parent, position, build, kind=kind)


def add_button(form_path: Path, name: str, command: str, parent: str = ".", position: str = "last") -> None:
    """Кнопка команды: `Form.Command.<Имя>` (команда формы, см. add_command) или команда объекта
    (`Catalog.<Имя>.Command.<Имя>`, `CommonCommand.<Имя>`). Шаблон — как у Конфигуратора:
    Type=UsualButton, CommandName, ExtendedTooltip; заголовок — set-title, вид — set-style."""
    _check_command_ref(command)
    if command.startswith("Form.Command."):
        _, root = _load(form_path)
        cmd_name = command.split(".", 2)[2]
        if not root.xpath("lf:Commands/lf:Command[@name=$n]", namespaces=cfg.NS, n=cmd_name):
            raise OpPreconditionError(f"Команды формы '{cmd_name}' нет: сначала form add-command")

    def build(next_id):
        el = etree.Element(_q("Button"), name=name, id=str(next_id))
        etree.SubElement(el, _q("Type")).text = "UsualButton"
        etree.SubElement(el, _q("CommandName")).text = command
        etree.SubElement(el, _q("ExtendedTooltip"), name=f"{name}РасширеннаяПодсказка", id=str(next_id + 1))
        return el
    _add(form_path, name, parent, position, build)


def add_command(form_path: Path, name: str, titles: dict, action: str = "") -> None:
    """Команда формы: Title и ToolTip на языках titles, Action — обработчик в модуле формы
    (по умолчанию имя команды). id команд — своя нумерация, как у Конфигуратора."""
    validate_name(name)
    if not titles:
        raise OpPreconditionError("add-command: нужен заголовок хотя бы на одном языке")
    doc, root = _load(form_path)
    cmds = root.find(_q("Commands"))
    if cmds is None:
        cmds = etree.Element(_q("Commands"))
        _insert_at(root, cmds, _anchor_after(root, "Form", "Commands"))
    if cmds.xpath("lf:Command[@name=$n]", namespaces=cfg.NS, n=name):
        raise OpPreconditionError(f"Команда формы '{name}' уже существует")
    ids = [int(x) for x in cmds.xpath("lf:Command/@id", namespaces=cfg.NS)]
    el = etree.Element(_q("Command"), name=name, id=str(max(ids, default=0) + 1))
    for tag in ("Title", "ToolTip"):
        t = etree.SubElement(el, _q(tag))
        for lang, text in titles.items():
            item = etree.SubElement(t, f"{{{_V8}}}item")
            etree.SubElement(item, f"{{{_V8}}}lang").text = lang
            etree.SubElement(item, f"{{{_V8}}}content").text = text
    etree.SubElement(el, _q("Action")).text = action or name
    _insert_at(cmds, el, None)
    cfg.save(doc, form_path)


def add_list(form_path: Path, name: str, main_table: str, main: bool = False) -> None:
    """Реквизит формы «динамический список» по основной таблице (`Catalog.X`, `InformationRegister.X`),
    без произвольного запроса; Settings — как пишет Конфигуратор (ListSettings с новыми userSettingID)."""
    import uuid
    validate_name(name)
    kind_, _, tname = main_table.partition(".")
    if kind_ not in _LIST_SOURCES or not tname or "." in tname:
        raise OpPreconditionError(f"Основная таблица '{main_table}': ожидается Вид.Имя, вид — "
                                  f"{', '.join(_LIST_SOURCES)}")
    validate_name(tname)
    doc, root = _load(form_path)
    attrs = root.find(_q("Attributes"))
    if attrs is None:
        attrs = etree.Element(_q("Attributes"))
        _insert_at(root, attrs, _anchor_after(root, "Form", "Attributes"))
    if attrs.xpath("lf:Attribute[@name=$n]", namespaces=cfg.NS, n=name):
        raise OpPreconditionError(f"Реквизит формы '{name}' уже существует")
    if main and attrs.xpath("lf:Attribute[lf:MainAttribute='true']", namespaces=cfg.NS):
        raise OpPreconditionError("Основной реквизит у формы уже есть")
    ids = [int(x) for x in attrs.xpath("lf:Attribute/@id", namespaces=cfg.NS)]
    el = etree.Element(_q("Attribute"), name=name, id=str(max(ids, default=0) + 1))
    t = etree.SubElement(el, _q("Type"))
    etree.SubElement(t, f"{{{_V8}}}Type").text = "cfg:DynamicList"
    if main:
        etree.SubElement(el, _q("MainAttribute")).text = "true"
    s = etree.SubElement(el, _q("Settings"))
    s.set(f"{{{cfg.NS['xsi']}}}type", "DynamicList")
    etree.SubElement(s, _q("ManualQuery")).text = "false"
    etree.SubElement(s, _q("DynamicDataRead")).text = "true"
    etree.SubElement(s, _q("MainTable")).text = main_table
    ls = etree.SubElement(s, _q("ListSettings"))
    dcs = cfg.NS["dcsset"]
    for part in ("filter", "order", "conditionalAppearance"):
        p = etree.SubElement(ls, f"{{{dcs}}}{part}")
        etree.SubElement(p, f"{{{dcs}}}viewMode").text = "Normal"
        etree.SubElement(p, f"{{{dcs}}}userSettingID").text = str(uuid.uuid4())
    etree.SubElement(ls, f"{{{dcs}}}itemsViewMode").text = "Normal"
    etree.SubElement(ls, f"{{{dcs}}}itemsUserSettingID").text = str(uuid.uuid4())
    _insert_at(attrs, el, None)
    cfg.save(doc, form_path)


def add_checkbox(form_path: Path, name: str, data_path: str, parent: str = ".", position: str = "last") -> None:
    """Флажок (CheckBoxField) с привязкой DataPath. Шаблон - как у Конфигуратора: DataPath, CheckBoxType=Auto,
    ContextMenu, ExtendedTooltip; переключатель - set-style CheckBoxType=Switcher, заголовок справа -
    TitleLocation=Right."""
    def build(next_id):
        el = etree.Element(_q("CheckBoxField"), name=name, id=str(next_id))
        etree.SubElement(el, _q("DataPath")).text = data_path
        etree.SubElement(el, _q("CheckBoxType")).text = "Auto"
        etree.SubElement(el, _q("ContextMenu"), name=f"{name}КонтекстноеМеню", id=str(next_id + 1))
        etree.SubElement(el, _q("ExtendedTooltip"), name=f"{name}РасширеннаяПодсказка", id=str(next_id + 2))
        return el
    _add(form_path, name, parent, position, build)


def remove_item(form_path: Path, name: str) -> None:
    """Удалить элемент формы со всем вложенным (не реквизит). Опустевшая группа пишется без ChildItems,
    как у Конфигуратора. Ссылки на элемент из кода модуля - забота вызывающего."""
    doc, root = _load(form_path)
    el = _find(root, name)
    src = el.getparent()
    if etree.QName(src).localname != "ChildItems":      # …РасширеннаяПодсказка/…КонтекстноеМеню — часть элемента
        raise OpPreconditionError(f"'{name}' — служебная часть элемента, а не элемент формы: удалять его нельзя")
    cfg.remove_children(src, [el])
    if not len(src) and src.getparent() is not root:
        cfg.remove_children(src.getparent(), [src])
    cfg.save(doc, form_path)


def add_event(form_path: Path, event: str, handler: str) -> None:
    """Обработчик события самой формы: `<Events><Event name="OnCreateAtServer">Процедура</Event></Events>`,
    место - по таблице порядка формы. Процедура в модуле формы пишется отдельно (код, не метаданные)."""
    validate_name(handler)
    if not event.isascii() or not event.isalpha():
        raise OpPreconditionError(f"Событие '{event}': имя события платформы латиницей, напр. OnCreateAtServer")
    doc, root = _load(form_path)
    events = root.find(_q("Events"))
    if events is None:
        events = etree.Element(_q("Events"))
        _insert_at(root, events, _anchor_after(root, "Form", "Events"))
    if events.xpath("lf:Event[@name=$n]", namespaces=cfg.NS, n=event):
        raise OpPreconditionError(f"У формы уже есть обработчик события '{event}'")
    el = etree.Element(_q("Event"), name=event)
    el.text = handler
    _insert_at(events, el, None)
    cfg.save(doc, form_path)


def _anchor_after(owner, ctx, tag):
    """Первый существующий элемент, который по таблице порядка идёт после tag (None — в конец)."""
    order = _ORDER_CFG.get(ctx, [])
    later = set(order[order.index(tag) + 1:])
    return next((c for c in owner if isinstance(c.tag, str) and etree.QName(c).localname in later), None)


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


# РежимРедактированияКолонки платформы: Вход / ВходПриВводе / Непосредственно
EDIT_MODES = ("Enter", "EnterOnInput", "Directly")


def add_column(form_path: Path, name: str, parent: str, data_path: str, position: str = "last",
               edit_mode: str | None = None) -> None:
    """Колонка таблицы (InputField в таблице или группе колонок): DataPath, ContextMenu, ExtendedTooltip.
    `EditMode` — только если задан (`EnterOnInput` — как у колонок табличной части от конструктора); для
    динамического списка и таблицы только для чтения он не нужен, поэтому по умолчанию не пишем."""
    if edit_mode and edit_mode not in EDIT_MODES:                    # до изменения файла
        raise OpPreconditionError(f"EditMode '{edit_mode}': допустимо {', '.join(EDIT_MODES)}")
    def build(next_id):
        el = etree.Element(_q("InputField"), name=name, id=str(next_id))
        etree.SubElement(el, _q("DataPath")).text = data_path
        if edit_mode:
            etree.SubElement(el, _q("EditMode")).text = edit_mode
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
    _check_page_parent(name, kind, owner_kind, parent)            # move не обходит правило страниц
    dest = _container(root, parent, create=True)
    if el is dest.getparent() or dest.getparent() in el.iterdescendants():
        raise OpPreconditionError(f"Нельзя перенести '{name}' в самого себя или во вложенный элемент")
    src = el.getparent()
    cfg.remove_children(src, [el])
    if not len(src) and src is not dest:             # опустевшую группу Конфигуратор пишет без ChildItems
        cfg.remove_children(src.getparent(), [src])
    _insert(dest, el, position)
    cfg.save(doc, form_path)


def set_title(form_path: Path, name: str, titles: dict, replace_all: bool = False) -> None:
    """Заголовок элемента на языках `titles` ({"ru": "...", "en": "..."}).

    По умолчанию меняет только указанные языки, остальные сохраняет; replace_all — заменить весь Title."""
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
    old = el.find(_q("Title"))
    merged = {}
    if old is not None and not replace_all:
        for it in old.findall(f"{{{_V8}}}item"):
            merged[it.findtext(f"{{{_V8}}}lang")] = it.findtext(f"{{{_V8}}}content") or ""
        if old.get("formatted") is not None:
            title.set("formatted", old.get("formatted"))
    merged.update(titles)
    for lang, text in merged.items():
        item = etree.SubElement(title, f"{{{_V8}}}item")
        etree.SubElement(item, f"{{{_V8}}}lang").text = lang
        etree.SubElement(item, f"{{{_V8}}}content").text = text
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
    # id уникальны на всю форму: служебные элементы (меню, панели) лежат вне корневого ChildItems
    ids = [int(x) for x in root.xpath(".//@id") if x.isdigit()]
    return max([i for i in ids if i > 0], default=0) + 1


def _add(form_path, name, parent, position, build, in_table=False, kind=None):
    validate_name(name)
    doc, root = _load(form_path)
    owner = None if parent in (".", "", None) else _find(root, parent)
    owner_kind = etree.QName(owner).localname if owner is not None else "Form"
    if in_table != (owner_kind in ("Table", "ColumnGroup")):
        raise OpPreconditionError(
            f"'{name}': колонка/группа колонок — только в таблицу или группу колонок; "
            f"прочие элементы — не в таблицу (родитель '{parent}' — {owner_kind})")
    _check_page_parent(name, kind, owner_kind, parent)
    el = build(_next_id(root))
    # проверяем и имена вложенных частей (…РасширеннаяПодсказка, …КонтекстноеМеню, …КоманднаяПанель):
    # их дубль даёт форму с неоднозначными именами, которую Конфигуратор может не загрузить
    # Имена элементов - своё пространство: реквизит или команда формы с тем же именем не конфликт
    # (у типовых форм реквизит «Список» и таблица «Список»).
    for n in [el.get("name")] + el.xpath(".//@name"):
        if root.xpath("(lf:AutoCommandBar | lf:ChildItems)/descendant-or-self::*[@name=$n]",
                      namespaces=cfg.NS, n=n):
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
