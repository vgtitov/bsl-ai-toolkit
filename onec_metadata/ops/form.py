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
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name

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


def add_field(form_path: Path, name: str, data_path: str, title: str | None = None) -> None:
    """Добавить InputField в ChildItems с привязкой DataPath (P4.2).

    id поля и вложенных ContextMenu/ExtendedTooltip — свежие ПОСЛЕДОВАТЕЛЬНЫЕ из
    пространства ЭЛЕМЕНТОВ (поддерево ChildItems); реквизиты формы не затрагиваются.
    Клон последнего InputField-образца; DataPath задаётся явно, семантика образца
    (обработчики, списки выбора) чистится; вложенные части переименовываются
    конвенцией Конфигуратора: <Имя>КонтекстноеМеню / <Имя>ExtendedTooltip.
    EDT-формат (.form) для полей — P4.3, пока не поддержан."""
    validate_name(name)
    if str(form_path).endswith(".form"):
        raise OpPreconditionError(
            "add-field для EDT-формы (.form) пока не поддержан (P4.3)")
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
    "binaryQualifiers",
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

    # id: max по ПРОСТРАНСТВУ реквизитов формы (прямые <id> блоков attributes) + 1
    ids = [int(a.findtext("id")) for a in existing if (a.findtext("id") or "").lstrip("-").isdigit()]
    new.find("id").text = str(max([i for i in ids if i > 0], default=0) + 1)

    vt = new.find("valueType")
    types = vt.findall("types")
    types[0].text = type_ref
    cfg.remove_children(vt, types[1:])
    cfg.remove_children(vt, [q for tag in _EDT_QUALIFIERS for q in vt.findall(tag)])

    cfg.remove_children(
        new, [el for tag in _EDT_ATTR_SEMANTIC for el in new.findall(tag)])

    cfg.place_after(existing[-1], new)
    cfg.save(doc, form_path)
