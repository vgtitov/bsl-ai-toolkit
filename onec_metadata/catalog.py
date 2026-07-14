"""Реестр ПРОВЕРЕННОГО ПОКРЫТИЯ: тип объекта метаданных → операции с тестами.

Это карта «что реально протестировано на данном виде объекта», а НЕ рантайм-
предохранитель. Настоящие предохранители (см. архитектурный аудит
docs/architecture-review-2026-07.md и CLAUDE.md):
  1) round-trip verify платформой в ТЕСТ-базе — платформа отвергает невалидное;
  2) scope-граница — только безопасные аддитивные и скалярные правки; деструктив
     с каскадом ссылок (rename/delete/move) вне scope.
`is_supported` — потребляемый API для запроса покрытия (напр. advisory-warning в
CLI на неохваченный тестами вид). Механизм add_child класс-агностичен и может
работать и на не перечисленных здесь видах — реестр отражает ПРОВЕРЕННОЕ, а не
технически возможное.
"""

# Сквозные операции — применимы к любому виду объекта (не привязаны к SUPPORTED).
CROSS_CUTTING: frozenset = frozenset({"set_property"})

SUPPORTED: dict[str, frozenset] = {
    # макеты табличных документов (печатные формы) — Конфигуратор-XML
    "Template": frozenset({"add_column"}),
    # отчёты: СКД-макеты (оба формата) + реквизиты/ТЧ как объект (add_child)
    "Report": frozenset({"scd_add_field", "scd_set_query", "scd_get_query",
                         "add_child"}),
    # реквизиты/ТЧ/команды объектов — оба формата (.xml / .mdo)
    # add_child: Attribute/TabularSection/Command (+ реквизиты ТЧ через parent)
    "Catalog": frozenset({"attribute_add", "add_child"}),
    "Document": frozenset({"attribute_add", "add_child"}),
    # регистры: измерения/ресурсы/реквизиты — оба формата
    "InformationRegister": frozenset({"add_child"}),
    "AccumulationRegister": frozenset({"add_child"}),
    # перечисления: значения — оба формата
    "Enum": frozenset({"add_child"}),
    # задача: реквизиты + реквизиты адресации (AddressingAttribute) + команды
    "Task": frozenset({"add_child"}),
    # бизнес-процесс и план обмена: реквизиты (add_child, проверено) — оба формата;
    # ТЧ/команды доступны тем же generic-механизмом, но покрыты тестом только реквизиты
    "BusinessProcess": frozenset({"add_child"}),
    "ExchangePlan": frozenset({"add_child"}),
    # план видов характеристик: реквизиты (add_child) — оба формата
    "ChartOfCharacteristicTypes": frozenset({"add_child"}),
    # обработка как объект: реквизиты/ТЧ/команды — как Catalog (Report — выше)
    "DataProcessor": frozenset({"add_child"}),
    # журнал документов: графы (Column); HTTP/веб-сервисы: шаблоны URL / операции
    "DocumentJournal": frozenset({"add_child"}),
    "HTTPService": frozenset({"add_child"}),
    "WebService": frozenset({"add_child"}),
    # подсистема: включение объекта в состав (командный интерфейс) — оба формата
    "Subsystem": frozenset({"subsystem_add_content"}),
    # роль: выдать/установить право объекта (Rights.xml Конфигуратора)
    "Role": frozenset({"role_grant_right"}),
    # управляемая форма (P4): реквизит формы (id-пространство, ns logform)
    "Form": frozenset({"form_add_attribute"}),
}


def is_supported(object_class: str, operation: str) -> bool:
    """Проверено ли покрытие `operation` для вида `object_class`.

    Сквозные операции (CROSS_CUTTING, напр. set_property) поддержаны для любого
    вида. Для остальных — операция должна быть в SUPPORTED[object_class].
    """
    if operation in CROSS_CUTTING:
        return True
    return operation in SUPPORTED.get(object_class, frozenset())
