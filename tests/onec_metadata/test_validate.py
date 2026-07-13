# DISCIPLINE_ALLOW_TEST_EDIT: новый red-тест TDD для модуля validate (создание, не подгон ассертов)
"""Семантическая валидация идентификаторов метаданных 1С."""
import pytest

from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name


@pytest.mark.parametrize("name", [
    "Сертификат",           # кириллица
    "НомерСертификата",     # CamelCase кириллица
    "Code",                 # латиница
    "_Служебный",           # ведущее подчёркивание
    "Поле_1",               # цифра не первым символом
    "a",                    # один символ
    "х" * 255,              # граничная длина
])
def test_valid_names_pass(name):
    validate_name(name)  # не должно бросать


@pytest.mark.parametrize("name,reason", [
    ("", "пустое"),
    ("   ", "пробелы"),
    ("1Поле", "начинается с цифры"),
    ("Имя объекта", "пробел внутри"),
    ("Поле-2", "дефис"),
    ("Цена,Валюта", "запятая"),
    ("Таб.Часть", "точка"),
    ("Имя\tтаб", "таб-символ"),
    ("Имя\n", "перевод строки в конце"),
    ("х" * 256, "длиннее 255"),
    # DISCIPLINE_ALLOW_TEST_EDIT: регресс на чужие алфавиты/цифры (фикс \\w-дыры)
    ("北京", "иероглифы"),
    ("café", "латиница с диакритикой"),
    ("Ωмега", "греческая буква"),
    ("Поле١", "арабо-индийская цифра"),
])
def test_invalid_names_raise(name, reason):
    with pytest.raises(OpPreconditionError):
        validate_name(name)


def test_error_names_the_field():
    with pytest.raises(OpPreconditionError, match="Цена-Товара"):
        validate_name("Цена-Товара")
