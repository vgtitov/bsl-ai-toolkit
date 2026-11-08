# onec_metadata: создание, заимствование и удаление объектов — проект решения

Статус: проект (2026-11-08), реализация не начата. Требования и уроки — `docs/create-object-in-extension.md`,
пункт P3 в `onec_metadata/BACKLOG.md`. Цель: движок создаёт и заимствует объект **любого вида**, который
можно создать в конфигураторе или EDT, в обоих форматах (Конфигуратор `*.xml`, EDT `*.mdo`); человеку
остаётся проверить результат в IDE, а не создавать объект руками.

## 1. Факты из корпуса (ERP 2.5.14.82 + два расширения, EDT-выгрузки)

- **Видов верхнего уровня в типовой — 39** (Catalogs 435, CommonModules 1579, InformationRegisters 454, …,
  WSReferences 1). В расширениях встречаются собственные и заимствованные почти всех ссылочных видов.
- **Собственный объект EDT:** `uuid`, `producedTypes` (у регистра сведений — selection/list/manager/
  recordSet/recordKey/record/recordManager), `name`, `synonym` по языкам, свойства. `objectBelonging` нет.
- **Заимствованный объект EDT:** свой `uuid`, атрибут корня `extendedConfigurationObject="<uuid типового>"`,
  `producedTypes` **с новыми id** (не совпадают с типовыми — проверено на `Catalog.Валюты`), `name`,
  `<objectBelonging>Adopted</objectBelonging>`, `<extension xsi:type="mdclassExtension:<Вид>Extension">` с
  контролируемыми свойствами (`<extendedConfigurationObject>Checked</…>`). Дочерние не перечисляются.
- **Манифест `Configuration.mdo`:** строки `<catalogs>Catalog.Имя</catalogs>`; виды идут сплошными блоками в
  каноническом порядке (порядок типовой: subsystems, styleItems, …, businessProcesses, tasks); внутри блока
  в расширениях — **порядок добавления** (не по алфавиту), в типовой — по алфавиту. Правило вставки: в конец
  блока своего вида; блока нет — на место вида по канону.
- **Конфигуратор:** то же содержимое в другой сериализации (`InternalInfo/xr:GeneratedType` с
  `TypeId`/`ValueId`, `ObjectBelonging`, `ExtendedConfigurationObject` в `Properties`, `<ChildObjects>` в
  `Configuration.xml`; BOM + CRLF; версия формата из `Configuration.xml`).

## 2. Подход: эталон от IDE, а не от донора

Клон соседнего объекта («донор», как в `add_child --donor`) для объекта целиком плох: тащит чужие свойства
(иерархия, длина кода, периодичность, владельцы, проведение) и ссылки. Вместо этого **эталон нового объекта
каждого вида снимается один раз у самой IDE**: во временном проекте EDT (не в общем `sandbox`) через EDT MCP
`create_metadata` создаётся объект каждого вида с именем-маркером, в проекте-расширении — `adopt_metadata_object`
для каждого заимствуемого вида; затем `export_configuration_to_xml` даёт ту же пару в формате Конфигуратора.
Это один и тот же объект в двух форматах — как пара EDT/XML у `form set-style` (`learn_*_form_*.py`).

Эталоны кладутся в `onec_metadata/templates/objects/{edt,cfg}/{own,adopted}/<Вид>.(mdo|xml)` с
подстановками: `{{name}}`, `{{uuid}}`, `{{type:<категория>}}` / `{{value:<категория>}}`, `{{base_uuid}}`,
`{{synonym}}`. Скрипт снятия — `scripts/learn_object_templates.py` (повторяемый: новая версия EDT/платформы
→ переснять, тест покажет дифф). Версия формата и версия EDT пишутся в шапку набора; на цель с другой
версией формата — отказ предусловием, а не «авось совпадёт».

Вид, который EDT MCP создать не может, — **[проверить]** на первом прогоне; для него эталон из корпуса
(минимальный реальный объект вида, обезличенный, со сбросом свойств), с пометкой в `COVERAGE_MAP`.

## 3. Операции

- `object add --kind <Вид> --name <Имя> [--synonym ru=… --synonym en=…] [параметры вида]` — собственный
  объект: файл(ы) по эталону, свежие uuid и id типов, регистрация в манифесте, пустой модуль там, где он есть
  у вида (общий модуль — всегда). Для расширения: имя обязано начинаться с `namePrefix` (`ан`, `ask`) — иначе
  отказ.
- `object adopt --ref <Вид.Имя>` — заимствование: uuid типового и его наличие читаются из исходников основной
  конфигурации (`--base <каталог проекта типовой>`); нет объекта в типовой — отказ. Вложенная подсистема —
  с родителем по §1.6 требований.
- `object delete --ref <Вид.Имя>` — свой объект: удалить файлы и строку манифеста, **отказ, если на имя есть
  ссылки** в проекте (поиск по `Вид.Имя` и `ВидМенеджер/Ссылка.Имя` во всех `*.mdo|*.xml|*.bsl`, список
  находок в ошибке). Заимствованный: снять заимствование (§1.3) с тем же отказом по ссылкам на свои дочерние.
- **Параметры видов с обязательными связями** (без них объект не загрузится или бессмыслен) — отдельной
  таблицей в коде, по одной строке на вид, проверка до записи:
  общий модуль — флаги контекста (`--server/--client/--external-connection/--call-server/--privileged`;
  `--server` включает и `externalConnection`, грабля DEV1C-2368); регламентное задание — `--method
  Модуль.Процедура`; подписка — `--source`, `--event`, `--handler`; регистр накопления — `--kind
  Balance|Turnover`; регистр сведений — `--periodicity`, `--write-mode Independent|RecorderSubordinate`;
  регистр бухгалтерии — `--chart-of-accounts`; регистр расчёта — `--chart-of-calculation-types`; журнал
  документов — `--documents`; общая картинка и общий макет — `--file` (содержимое) и `--template-type`.
  Отсутствие обязательного параметра — отказ с подсказкой.
- Регистратор регистра (`RegisterRecords` документа) — не часть `object add`: отдельная операция состава,
  как `subsystem add-content`.

## 4. Что нельзя в расширении

Какие виды платформа не даёт создать собственными в расширении (кандидаты: план счетов, план видов
расчёта, регистры бухгалтерии и расчёта, последовательность, внешние источники) — **[проверить]**: прогон
`create_metadata` в проекте-расширении покажет отказ EDT; таблица запрета — из этого прогона, не по памяти.
Для основной конфигурации запретов нет.

## 5. Проверка (DoD)

1. **Golden:** для каждого вида результат `object add`/`adopt` = эталон IDE с подстановкой тех же имён
   (байт в байт, кроме uuid и id типов); тест на каждый вид и формат.
2. **Свойство корпуса:** множество тегов эталона ⊆ тегов реальных объектов вида в корпусе (ловит эталон,
   снятый у «странной» версии EDT).
3. **EDT:** результат во временном проекте → `get_project_errors` пусто.
4. **Конфигуратор:** загрузка в тестовую базу частичной загрузкой + обратная выгрузка: дифф ровно целевых
   файлов (платформа канонизирует: `Native` без `ObjectBelonging`). Нужна тестовая база — только с
   разрешения владельца; на прод — никогда.
5. Adversarial-ревью и второе мнение перед PR.

## 6. Порядок работ (PR в toolkit, каждый отдельно ревьюится)

1. Скрипт снятия эталонов + каркас `object add|adopt|delete` + виды: CommonModule, InformationRegister,
   AccumulationRegister, Catalog, Document, Enum, Constant, DataProcessor, Report — оба формата; `adopt` для
   всех типогенерирующих видов; `delete`.
2. Служебные виды: Role (+ пустой `Rights`), ScheduledJob, EventSubscription, Subsystem (вкл. вложенные),
   SessionParameter, DefinedType, FunctionalOption(+Parameter), CommonAttribute, CommonForm, CommonCommand,
   CommandGroup, CommonTemplate, CommonPicture, StyleItem, Style, Language.
3. Остальное: ExchangePlan, ChartOfCharacteristicTypes, ChartOfAccounts, ChartOfCalculationTypes,
   AccountingRegister, CalculationRegister, BusinessProcess, Task, DocumentJournal, DocumentNumerator,
   Sequence, FilterCriterion, SettingsStorage, XDTOPackage, WebService, HTTPService, WSReference.

После каждого PR: `COVERAGE_MAP.md` (вид переходит из «вне scope» в «поддержано»), скилл `1c-metadata`,
CHANGELOG; в team — гейт 3 и запись, что ручного шага в IDE для этих видов больше нет.

## 7. Что нужно перед стартом

- Запущенный EDT 2025.2.6.4 с MCP (`EDT_MCP_AUTO_START`) — для снятия эталонов; баз 1С на этом шаге не нужно.
- Тестовая база для п. 5.4 — когда освободится (Конфигуратор: `Dev_YI` на testing).
