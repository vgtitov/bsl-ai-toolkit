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
- **Журнал документов:** DocumentJournal (графы — Column). *EDT-тег `columns`
  проверен на реальной EDT-выгрузке ERP 2.5 (2026-07-14).*
- **Сервисы:** HTTPService (URLTemplate +вложенные Method), WebService (Operation).
  *EDT-теги `urlTemplates`/`operations` проверены на реальной EDT-выгрузке ERP 2.5.
  NB: метод HTTP-сервиса в EDT = вложенный `<methods>` — как отдельный вид не поддержан.*

**Важно (клонирование стаба):** для Column/URLTemplate/Operation семантически-
определяющие поля образца НЕ переносятся — `References` графы, `Handler` метода,
`ProcedureName` операции очищаются при создании (иначе клон тащил бы чужие ссылки).
Новый элемент — чистый стаб; заполнить эти поля нужно осознанно после создания.
- **Спец-операции:** Subsystem (состав — `subsystem add-content`), Role (права —
  `role grant`: Конфигуратор `Rights.xml` И EDT `Rights.rights` — формат общий,
  проверено), макет печатной формы (`template add-column`: Конфигуратор
  `Template.xml` И EDT `Template.mxlx` — тот же spreadsheet-XML, проверено;
  бинарный `Template.bin` вне scope), СКД отчёта (`scd add-field`/`set-query`:
  `.xml` и EDT `.dcs`), реквизит управляемой формы (`form add-attribute`:
  Конфигуратор ns logform И EDT `Form.form` ns dt/form — у EDT тип в его нотации
  `String`/`CatalogRef.Имя`).

## Класс-агностично поддержано (add_child работает, фикстуры нет — тех же семейств)
Отсутствуют в базе-доноре УТ, поэтому без фикстуры; структура идентична
протестированным регистрам/планам:
- AccountingRegister (Регистр бухгалтерии), CalculationRegister (Регистр расчёта),
  ChartOfAccounts (План счетов — спец-дети AccountingFlag/ExtDimensionAccountingFlag),
  ChartOfCalculationTypes (План видов расчёта).
При появлении задачи — добавить фикстуру из реального дампа и тест.

## Редактируются сквозными операциями (нет дочерних метаданных)
`set-property` — только СКАЛЯРНЫЕ свойства-листы (флаг `true/false`, значение перечисления,
число, Comment). **Структурные свойства (Type, Synonym, Content) `set-property` НЕ трогает** —
проверка на вложенные элементы отказывает. Отсутствующее свойство — тоже отказ (добавление — отдельная операция).
- FunctionalOption / FunctionalOptionsParameter, DocumentNumerator (Нумератор), Sequence — скалярные флаги/значения.

### Тип — охват (расширено 15.07, покрыто тестами)
`edit-type` задаёт/меняет тип, адресуя узел ПО ИМЕНИ — **`Attribute` / `Dimension` / `Resource` /
`AddressingAttribute`**; без имени (`--member`/`--attribute` опущен) — **тип самого объекта
(`SessionParameter` / `Constant` / `DefinedType`)**. Оба формата (Конфигуратор `.xml` и EDT `.mdo`).
- Блок `Type`/`type` и квалификатор (**длина строки** `--length`; **точность/масштаб числа** `--precision`/`--scale`)
  **строятся С НУЛЯ**, если поле бестиповое или у типа ещё нет квалификатора (byte-perfect: отступ берётся из контейнера).
- Составной тип сворачивается к одиночному; несоответствующие квалификаторы снимаются.
- Новый типизированный элемент удобнее сразу с типом: `add_attribute` / `add_child` (`type_ref` обязателен).
- Тесты: реквизит/измерение/ресурс, параметр сеанса/константа (объект-уровень), тип с нуля + строковый/числовой
  квалификатор, byte-perfect round-trip.
- ⚠ Остаётся человеку/отдельной операции: **составной тип** (несколько типов в одном поле) и **новый ЭЛЕМЕНТ
  управляемой EDT-формы** (`add-field` EDT = P4.3).

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
