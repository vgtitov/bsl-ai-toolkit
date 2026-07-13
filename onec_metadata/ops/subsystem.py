"""Включение объекта в состав подсистемы (командный интерфейс).

Состав подсистемы:
- Конфигуратор (`*.xml`): элемент `<Content>` со списком
  `<xr:Item xsi:type="xr:MDObjectRef">Тип.Имя</xr:Item>`;
- EDT (`*.mdo`): прямые дети `<content>Тип.Имя</content>` у корня `mdclass:Subsystem`.

Ссылка объекта — «Тип.Имя» (Document.Заказ, Report.Отчет и т.п.): проверяем обе
части как идентификаторы 1С. Новый элемент — копия последнего (наследует отступы),
поэтому подсистема с пустым составом (`<Content/>`) не поддержана: добавление
первого элемента — отдельная операция (как и в add_child без образца).
"""
import copy
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_object_ref


def add_content(subsystem_path: Path, object_ref: str) -> None:
    validate_object_ref(object_ref)
    if str(subsystem_path).endswith(".mdo"):
        _add_content_edt(subsystem_path, object_ref)
    else:
        _add_content_configurator(subsystem_path, object_ref)


# ---------- Конфигуратор (*.xml) ----------

def _add_content_configurator(path: Path, object_ref: str) -> None:
    doc = cfg.load(path)
    contents = doc.xpath("//md:Content", namespaces=cfg.NS)
    if not contents:
        raise OpPreconditionError("У подсистемы нет элемента Content")
    content = contents[0]

    refs = content.xpath("xr:Item/text()", namespaces=cfg.NS)
    if object_ref in refs:
        raise OpPreconditionError(
            f"Объект '{object_ref}' уже в составе подсистемы")

    items = content.xpath("xr:Item", namespaces=cfg.NS)
    if not items:
        raise OpPreconditionError(
            "Состав подсистемы пуст — добавление первого элемента отдельная операция")
    new = copy.deepcopy(items[-1])
    new.text = object_ref
    new.set(cfg.q("xsi", "type"), "xr:MDObjectRef")
    cfg.place_after(items[-1], new)
    cfg.save(doc, path)


# ---------- EDT (*.mdo) ----------

def _add_content_edt(path: Path, object_ref: str) -> None:
    doc = cfg.load(path)
    root = doc.getroot()

    refs = root.xpath("content/text()")
    if object_ref in refs:
        raise OpPreconditionError(
            f"Объект '{object_ref}' уже в составе подсистемы")

    items = root.xpath("content")
    if not items:
        raise OpPreconditionError(
            "Состав подсистемы пуст — добавление первого элемента отдельная операция")
    new = copy.deepcopy(items[-1])
    new.text = object_ref
    cfg.place_after(items[-1], new)
    cfg.save(doc, path)
