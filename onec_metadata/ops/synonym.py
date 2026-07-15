"""Правка СИНОНИМА («Наименование») — презентационный текст, ссылок не ломает
(в отличие от переименования идентификатора). Меняет текст СУЩЕСТВУЮЩЕГО языкового
элемента синонима у реквизита/измерения/ресурса (member) или самого объекта
(member=None). Оба формата. Добавление НОВОГО языка — отдельная операция.

Синоним — свободный текст (пробелы/пунктуация/«&» допустимы; lxml экранирует
спецсимволы, byte-perfect сохраняется через round-trip формат-слоя).

Konfigurator: `Synonym/v8:item[v8:lang=L]/v8:content`.
EDT: `synonym[key=L]/value` (synonym — повторяемый элемент по языкам).
"""
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.ops.edit_type import _props_cfg, _container_edt


def set_synonym(object_path: Path, member: str | None, text: str,
                lang: str = "ru") -> None:
    if str(object_path).endswith(".mdo"):
        _set_edt(object_path, member, text, lang)
    else:
        _set_configurator(object_path, member, text, lang)


def _set_configurator(path, member, text, lang) -> None:
    doc = cfg.load(path)
    props = _props_cfg(doc, member)
    syns = props.xpath("md:Synonym", namespaces=cfg.NS)
    if not syns:
        raise OpPreconditionError("Нет блока Synonym (добавление синонима — отдельная операция)")
    items = syns[0].xpath("v8:item[v8:lang=$l]", l=lang, namespaces=cfg.NS)
    if not items:
        raise OpPreconditionError(
            f"В синониме нет языка '{lang}' (добавление языка — отдельная операция)")
    content = items[0].xpath("v8:content", namespaces=cfg.NS)
    if not content:
        raise OpPreconditionError(f"У языка '{lang}' нет v8:content")
    content[0].text = text
    cfg.save(doc, path)


def _set_edt(path, member, text, lang) -> None:
    doc = cfg.load(path)
    container = _container_edt(doc, member)
    syns = container.xpath("synonym[key=$l]", l=lang)
    if not syns:
        raise OpPreconditionError(
            f"Нет синонима языка '{lang}' (добавление языка — отдельная операция)")
    val = syns[0].xpath("value")
    if not val:
        raise OpPreconditionError(f"У синонима языка '{lang}' нет value")
    val[0].text = text
    cfg.save(doc, path)
