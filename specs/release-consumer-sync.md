# Release and consumer pin synchronization — specification

## Объектив

Устранить разрыв «фикс в `main`, но потребители продолжают устанавливать старый
pin»: сделать релиз toolkit и обновление pin двумя явными, проверяемыми
транзакциями. Toolkit остаётся полностью organization-agnostic.

## Требования

- **R1.** Toolkit содержит CLI release-guard, который до создания локального
  аннотированного тега проверяет strict SemVer, отсутствие/монотонность тега,
  чистый release-branch, равенство HEAD удалённой release-ветке, секцию версии в
  `CHANGELOG.md` и успешный полный verification command.
- **R2.** Release-guard никогда не выполняет push; при любом провале не создаёт
  тег и возвращает ненулевой код с конкретной причиной.
- **R3.** GitHub release workflow повторяет immutable-проверки, запускает полный
  тестовый набор до публикации Release и работает на полной git-истории.
- **R4.** Toolkit содержит generic consumer-pin CLI с операциями `check` и
  `bump`; upstream URL, pin-файл и target tag передаются аргументами.
- **R5.** Consumer-pin CLI принимает только один strict SemVer в pin-файле,
  проверяет существование тега upstream, при `bump` меняет только pin-файл и
  fail-closed обрабатывает сеть/неизвестный тег.
- **R6.** В toolkit нет названий организаций, внутренних доменов, организационных
  профилей, контуров или credential-данных; документация использует только
  нейтральные placeholders.
- **R7.** Generic-документация фиксирует роли upstream/consumer, порядок
  CHANGELOG → test → tag → Release → consumer MR/PR → onboard, подключение к
  pre-push/CI и восстановление после неотправленного ошибочного тега.
- **R8.** Новое поведение покрыто тестами на временных git-репозиториях: все
  перечисленные позитивные и негативные сценарии дизайна воспроизводимы.
- **R9.** Первый patch-релиз после реализации включает ранее слитые SSH UTF-8
  fix и EDT-safe pre-push fix вместе с release-contract; затем реальный consumer
  обновляет pin отдельным review-изменением и выпускает свой patch-тег.

## Edge-cases / что не входит

- Toolkit не ищет consumer-репозитории и не выполняет в них commit/push/MR.
- Автоматические scheduled dependency-MR не входят в эту итерацию.
- Опубликованный тег не перемещается и не переиспользуется.
- Consumer вправе осознанно отставать от latest; `check` проверяет валидность
  текущего pin, а не требует latest.
- Организационные GitLab/GitHub URL и CI-конфигурация остаются в consumer.

## Definition of Done

- [ ] R1–R8 реализованы в toolkit и подтверждены тестами.
- [ ] Полный набор toolkit проходит без ошибок.
- [ ] GitHub PR реализации слит в `main`, release workflow зелёный.
- [ ] Создан новый аннотированный patch-тег и GitHub Release.
- [ ] В consumer создано, проверено и слито отдельное изменение pin.
- [ ] Consumer pin указывает на новый существующий toolkit-тег.
- [ ] Consumer выпустил следующий patch-тег по своей политике.
- [ ] Итоговое review по R1–R9 записано с SHA, URL и выводами проверок.
