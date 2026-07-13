"""Реестр покрытия: тип объекта метаданных → поддержанные операции.

Это одновременно документ покрытия и предохранитель: операции вне реестра
агент НЕ выполняет программно — их делает человек в IDE (см. CLAUDE.md).
Дорожная карта расширения реестра — Фазы 2–5 в спеке
docs/superpowers/specs/2026-07-12-1c-metadata-design.md:
  Фаза 2 — те же операции для формата EDT;
  Фаза 3 — регистры, ПВХ, перечисления, планы обмена, роли, подсистемы,
           командный интерфейс, определяемые типы, HTTP/web-сервисы;
  Фаза 4 — внешние файлы .epf/.erf и расширения .cfe;
  Фаза 5 — интеграция с БСП «Дополнительные отчёты и обработки».
"""

SUPPORTED: dict[str, frozenset] = {
    # макеты табличных документов (печатные формы) — Конфигуратор-XML
    "Template": frozenset({"add_column"}),
    # отчёты: их СКД-макеты — оба формата (.xml Конфигуратора и .dcs EDT)
    "Report": frozenset({"scd_add_field", "scd_set_query", "scd_get_query"}),
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
    # подсистема: включение объекта в состав (командный интерфейс) — оба формата
    "Subsystem": frozenset({"subsystem_add_content"}),
}
