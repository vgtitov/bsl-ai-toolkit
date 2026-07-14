# Карта покрытия видов объектов метаданных

Статус каждого стандартного вида объекта 1С относительно `onec_metadata`.
Механизм `add_child` **класс-агностичен** — технически работает на любом объекте
с дочерним элементом-образцом; ниже «протестировано» = есть фикстура и тест.
Сквозные операции `set-property` / `edit-type` применимы к любому объекту.

## Протестировано (add_child + фикстуры, оба/один формат)
- **Ссылочные и объектные:** Catalog, Document, DataProcessor, Report,
  Task (+AddressingAttribute), BusinessProcess, ExchangePlan,
  ChartOfCharacteristicTypes.
- **Регистры:** InformationRegister (Регистр сведений), AccumulationRegister
  (Регистр накопления) — измерения/ресурсы/реквизиты.
- **Перечисление:** Enum (значения).
- **Журнал документов:** DocumentJournal (графы — Column). *Конфигуратор; EDT-тег
  `columns` — конвенция, без реального .mdo-образца (непроверено).*
- **Сервисы:** HTTPService (URLTemplate +вложенные Method), WebService (Operation).
  *Конфигуратор; EDT-теги `urlTemplates`/`operations` — конвенция, непроверено.*

**Важно (клонирование стаба):** для Column/URLTemplate/Operation семантически-
определяющие поля образца НЕ переносятся — `References` графы, `Handler` метода,
`ProcedureName` операции очищаются при создании (иначе клон тащил бы чужие ссылки).
Новый элемент — чистый стаб; заполнить эти поля нужно осознанно после создания.
- **Спец-операции:** Subsystem (состав — `subsystem add-content`), Role (права —
  `role grant`), макет печатной формы (`template add-column`), СКД отчёта
  (`scd add-field`/`set-query`).

## Класс-агностично поддержано (add_child работает, фикстуры нет — тех же семейств)
Отсутствуют в базе-доноре УТ, поэтому без фикстуры; структура идентична
протестированным регистрам/планам:
- AccountingRegister (Регистр бухгалтерии), CalculationRegister (Регистр расчёта),
  ChartOfAccounts (План счетов — спец-дети AccountingFlag/ExtDimensionAccountingFlag),
  ChartOfCalculationTypes (План видов расчёта).
При появлении задачи — добавить фикстуру из реального дампа и тест.

## Редактируются сквозными операциями (нет дочерних метаданных)
`set-property` / `edit-type` (тип/флаги/значения):
- Constant (Константа — Type → `edit-type`), SessionParameter (Параметр сеанса — Type),
  DefinedType (Определяемый тип — Type), FunctionalOption / FunctionalOptionsParameter,
  DocumentNumerator (Нумератор), Sequence (Последовательность).

## Композиционные (состав по ссылкам — как `subsystem add-content`)
Отдельная операция состава (в бэклоге, паттерн как у подсистемы/прав):
- CommonAttribute (Общий реквизит — состав `Content`/автоиспользование),
  FilterCriterion (Критерий отбора — состав типов).

## Вне scope инструмента (лист/код/ресурс — не структурные метаданные)
Правит человек в IDE или это не про метаданные:
- CommonModule (только модуль), CommonForm (форма — см. P4), CommonCommand,
  CommonPicture, CommonTemplate, CommandGroup, StyleItem, Style, Language,
  SettingsStorage, ScheduledJob, EventSubscription, WSReference, XDTOPackage.

## Формы (все объекты, у кого есть формы) — отдельный блок P4
Управляемые формы (Form у Catalog/Document/…, CommonForm) — см.
`docs/superpowers/specs/*-forms-*` (спека) и соответствующие операции.
