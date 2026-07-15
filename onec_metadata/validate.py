"""Семантическая валидация перед структурной правкой.

Задача — не дать создать объект с именем, которое 1С отвергнет при загрузке
конфигурации (тогда правка «как человек» ломает конфигурацию). Проверяем ДО
изменения файла, ошибку поднимаем как OpPreconditionError (файлы не тронуты).

Правила идентификатора 1С (имя объекта/реквизита/измерения/…):
- непустой;
- первый символ — буква (латиница ИЛИ кириллица) или подчёркивание;
- остальные — буквы, цифры или подчёркивание;
- длина не больше 255 символов.
Пробелы, пунктуация и служебные символы недопустимы.
"""
import re

from onec_metadata.ops import OpPreconditionError

MAX_NAME_LEN = 255
# Идентификатор 1С — ТОЛЬКО латиница, кириллица (вкл. Ёё), ASCII-цифры и '_'.
# Явные классы, а не \w: \w под Unicode пропустил бы чужие алфавиты и не-ASCII
# цифры (北京, café, арабо-индийские цифры), которые 1С отвергает при загрузке.
_LETTER = "A-Za-zА-Яа-яЁё"
_NAME_RE = re.compile(rf"[{_LETTER}_][{_LETTER}0-9_]*\Z")


def validate_name(name: str) -> None:
    """Проверить, что `name` — допустимый идентификатор 1С; иначе OpPreconditionError."""
    if not name:
        raise OpPreconditionError("Имя не может быть пустым")
    if len(name) > MAX_NAME_LEN:
        raise OpPreconditionError(
            f"Имя '{name[:20]}…' длиннее {MAX_NAME_LEN} символов ({len(name)})")
    if not _NAME_RE.match(name):
        raise OpPreconditionError(
            f"Недопустимое имя '{name}': идентификатор 1С начинается с буквы или "
            "'_' и состоит из букв, цифр и '_' (без пробелов и пунктуации)")


def validate_type_ref(type_ref: str) -> None:
    """Ссылка ТИПА перед подстановкой: примитив (`xs:string`/`String`) или ссылочный
    тип (`cfg:CatalogRef.Товары`/`CatalogRef.Товары`), с префиксом (`xs:`/`cfg:`/…) или без.
    Отвергает заведомо битое: пустое, пробелы, XML-спецсимволы, пустой префикс/сегмент.
    НЕ проверяет соответствие формату контура (это за вызывающим) — только «не мусор»."""
    if not type_ref or not type_ref.strip():
        raise OpPreconditionError("Тип не может быть пустым")
    if re.search(r"\s", type_ref):
        raise OpPreconditionError(f"Тип '{type_ref}' содержит пробел")
    if re.search(r"""[<>&"']""", type_ref):
        raise OpPreconditionError(f"Тип '{type_ref}' содержит недопустимый символ (<>&\"')")
    body = type_ref
    if ":" in type_ref:
        prefix, _, body = type_ref.partition(":")
        if not re.match(r"[A-Za-z][A-Za-z0-9]*\Z", prefix):
            raise OpPreconditionError(f"Недопустимый префикс типа в '{type_ref}'")
        if not body:
            raise OpPreconditionError(f"Пустой тип после префикса ':' в '{type_ref}'")
    segments = body.split(".")
    if any(not s for s in segments):
        raise OpPreconditionError(
            f"Тип '{type_ref}' имеет пустой сегмент (лишняя/крайняя точка)")
    for s in segments:
        validate_name(s)              # каждый сегмент — идентификатор 1С


def validate_object_ref(object_ref: str) -> None:
    """Ссылка объекта вида 'Тип.Имя' (Document.Заказ): обе части — идентификаторы."""
    parts = object_ref.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise OpPreconditionError(
            f"Ссылка '{object_ref}' должна быть вида 'Тип.Имя' (напр. Document.Заказ)")
    validate_name(parts[0])
    validate_name(parts[1])


def validate_ref_path(ref: str) -> None:
    """Ссылка на объект ИЛИ его подобъект: 'Тип.Имя' или 'Тип.Имя.Вид.Имя2'
    (напр. Catalog.Товары.TabularSection.Состав) — каждая часть идентификатор,
    частей не меньше двух. Права роли допускают ссылки на ТЧ/реквизиты."""
    parts = ref.split(".")
    if len(parts) < 2 or any(not p for p in parts):
        raise OpPreconditionError(
            f"Ссылка '{ref}' должна быть вида 'Тип.Имя' или 'Тип.Имя.Вид.Имя' "
            "(напр. Catalog.Товары.TabularSection.Состав)")
    for p in parts:
        validate_name(p)
