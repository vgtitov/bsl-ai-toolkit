"""Права роли: выдать/установить право объекта в файле прав роли.

Права роли Конфигуратора лежат отдельным файлом `Roles/<Роль>/Ext/Rights.xml`
(namespace `http://v8.1c.ru/8.2/roles`): заголовочные флаги + список
`<object><name>Тип.Имя</name><right><name>Право</name><value>true|false</value>…`.

`grant_right`:
- объект уже есть, право есть → устанавливаем значение (идемпотентно);
- объект есть, права нет → добавляем право (deepcopy последнего права объекта);
- объекта нет → создаём запись объекта (deepcopy последнего `<object>`, одно право).
Пустой файл прав (без объекта-образца) не поддержан — как и прочие операции без
образца. Права EDT (`Roles/<Роль>/Rights.rights`) — ТОТ ЖЕ формат/namespace
(корень дополнительно несёт xsi:type="Rights", отступы табами) — работает этот же
код; проверено на структуре реальной EDT-выгрузки ERP 2.5 (test_role_edt).
"""
import copy
from pathlib import Path

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name, validate_ref_path

NS = cfg.NS


def grant_right(rights_path: Path, object_ref: str, right_name: str,
                value: bool = True) -> None:
    validate_ref_path(object_ref)  # права могут ссылаться и на ТЧ/реквизит
    validate_name(right_name)
    val_text = "true" if value else "false"

    doc = cfg.load(rights_path)
    root = doc.getroot()

    target = _find_object(root, object_ref)
    if target is not None:
        _grant_in_object(target, right_name, val_text)
    else:
        _create_object(root, object_ref, right_name, val_text)
    cfg.save(doc, rights_path)


def _find_object(root, object_ref):
    for obj in root.xpath("roles:object", namespaces=NS):
        nm = obj.xpath("roles:name/text()", namespaces=NS)
        if nm and nm[0] == object_ref:
            return obj
    return None


def _grant_in_object(obj, right_name: str, val_text: str) -> None:
    rights = obj.xpath("roles:right", namespaces=NS)
    for rt in rights:
        rn = rt.xpath("roles:name/text()", namespaces=NS)
        if rn and rn[0] == right_name:  # право есть → установить значение
            rt.xpath("roles:value", namespaces=NS)[0].text = val_text
            return
    if not rights:
        raise OpPreconditionError(
            f"У объекта нет права-образца (создание с нуля — отдельная операция)")
    new = copy.deepcopy(rights[-1])
    new.xpath("roles:name", namespaces=NS)[0].text = right_name
    new.xpath("roles:value", namespaces=NS)[0].text = val_text
    cfg.place_after(rights[-1], new)


def _create_object(root, object_ref: str, right_name: str, val_text: str) -> None:
    samples = root.xpath("roles:object", namespaces=NS)
    if not samples:
        raise OpPreconditionError(
            "В файле прав нет объекта-образца (пустой Rights.xml — отдельная операция)")
    sample = samples[-1]
    new = copy.deepcopy(sample)
    new.xpath("roles:name", namespaces=NS)[0].text = object_ref
    rights = new.xpath("roles:right", namespaces=NS)
    # оставить одно право (последнее — сохраняет закрывающий отступ), задать имя/значение
    rights[-1].xpath("roles:name", namespaces=NS)[0].text = right_name
    rights[-1].xpath("roles:value", namespaces=NS)[0].text = val_text
    for extra in rights[:-1]:
        extra.getparent().remove(extra)
    cfg.place_after(sample, new)
