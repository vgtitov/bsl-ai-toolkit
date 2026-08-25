# План миграции: `claude-1c-toolkit` → `bsl-ai-toolkit` (AI-универсальный OSS)

> Статус: **черновик на утверждение** (2026-07-24). Автор: Слава (vgtitov).
> Цель: превратить Claude-специфичный `claude-1c-toolkit` в **AI-агностик** продукт
> уровня эталона `bitrix-ai-toolkit`, оформить как публичный OSS по лучшим практикам
> 2026, войти в 1С-рейтинги/индексы, **не сломав** корп-форк команды `claude-1c-team`.

---

## 1. Анализ (факты, проверено по репозиториям)

### Что имеем
- **`claude-1c-toolkit`** (github `vgtitov`, MIT): заявлен «org-agnostic», но структурно
  Claude-специфичен — имя `claude-`, правила в `CLAUDE.md`, навыки в `skills/`.
  **Нет** `core/` + `adapters/` + `AGENTS.md` + `build.sh`. **Нет** OSS-гигиены
  (CONTRIBUTING/CoC/SECURITY/CHANGELOG, шаблонов Issue/PR, бейджей). В `.github/` — только
  `tests.yml`. При этом начинка богатая: 6 скиллов, движок `onec_metadata` (byte-perfect,
  ~215 тестов), 4 MCP (`onec_mcp`, `bsl_ls_mcp`, `onec_ops_mcp`, `onec_data_mcp`),
  расширение `ai_debug`, 509 зелёных тестов, кросс-платформенный `onboard`.
- **`bitrix-ai-toolkit`** — эталон «как надо»: `core/` (AGENTS.md + skills + linters + mcp)
  → `adapters/` (8 агентов) → `build.sh`; полная OSS-гигиена и бейджи. Три оси
  переносимости: **AGENTS.md** (правила) + **SKILL.md** (навыки) + **MCP** (инструменты).
- **`claude-1c-team`** — корп-форк на `gitlab.<компания>/<группа>/claude-1c-team` (НЕ GitHub,
  доступ только из корп-сети с Work PC). Связь = **вендоринг** (`scripts/sync-core.*` +
  `scripts/sync-skills.*` по whitelist путей, живут в team-репо). Push в корп-GitLab — только
  с Work PC. Уже висит задача P6 (довендорить `onec_metadata` + скилл метаданных).

### Разрыв до эталона
| Ось | Эталон `bitrix-ai-toolkit` | Сейчас `claude-1c-toolkit` |
|---|---|---|
| Имя | нейтральное, не `claude-` | `claude-` (нарушает то же правило) |
| Единый источник | `core/` | нет (правила в корневом `CLAUDE.md`) |
| Мульти-агент | `adapters/` (8) + `build.sh` | нет |
| Правила | `AGENTS.md` (стандарт) | `CLAUDE.md` (Claude-нативно) |
| OSS-гигиена | CONTRIBUTING/CoC/SECURITY/CHANGELOG | нет |
| `.github/` | PR + 2 Issue-шаблона + CI | только CI |
| README | бейджи + «не привязан к AI» | без бейджей, «с Claude Code» |

### Решения (утверждены)
- **Имя:** `bsl-ai-toolkit` (репо `vgtitov/bsl-ai-toolkit`), заголовок README —
  «bsl-ai-toolkit — AI-first разработка 1С (мульти-агентный контур)».
- **Переход:** переименование существующего репо (GitHub Rename → звёзды/история/issue
  сохраняются, старые URL авто-редиректят). Сам rename делает владелец в вебе.
- **Лицензия:** остаётся **MIT** (как у bitrix — единый бренд).

---

## 2. Список задач (по фазам)

### Фаза A. Реструктуризация в AI-агностик ядро
- [ ] A1. Создать `core/AGENTS.md` — перенести правила из `CLAUDE.md`, обезличить от Claude
      (нейтральная лексика «AI-агент», а не «Claude Code»).
- [ ] A2. Перенести `skills/` → `core/skills/` (6 скиллов), сохранив структуру SKILL.md +
      references. Обезличить упоминания Claude внутри.
- [ ] A3. Создать `core/mcp/servers.json` — профиль MCP (onec, bsl_ls, onec_ops, onec_data),
      пути через env (свести из `mcp/dev.mcp.json`).
- [ ] A4. (Опц.) `core/linters/` — конфиг BSL Language Server + правила guard-скриптов,
      если выносимо в единый источник.

### Фаза B. Адаптеры под разные AI
- [ ] B1. `adapters/claude/` — эталон: `CLAUDE.md` = `@core/AGENTS.md` + Claude-спец
      (скиллы читаются нативно), `settings.json` (хуки git).
- [ ] B2. `adapters/codex/` — нативный `AGENTS.md` (Codex читает его напрямую).
- [ ] B3. `adapters/gemini/` — `GEMINI.md` → символьная ссылка на `core/AGENTS.md`.
- [ ] B4. `adapters/{cursor,copilot,cline,windsurf,aider}/` — тонкие адаптеры (rulesync /
      ручной фолбэк): `.cursor/rules`, `.github/copilot-instructions.md` и т.д.
- [ ] B5. `adapters/README.md` — матрица «агент → как подключается».

### Фаза C. Сборка
- [ ] C1. `build.sh` — генерация из `core/`: корневые `CLAUDE.md`, `AGENTS.md`, `.mcp.json`,
      `.claude/`. Сгенерированное коммитим (репо работает сразу после клона).
      Правило: правь `core/` → пересобирай, не редактируй сгенерированное.
- [ ] C2. Корневые symlink/generated: `AGENTS.md`, `GEMINI.md → core/AGENTS.md`.

### Фаза D. OSS-гигиена (лучшие практики 2026)
- [ ] D1. `README.md` — переписать: имя, бейджи, «не привязан к AI», состав, идея, принципы.
- [ ] D2. `CONTRIBUTING.md` — как контрибьютить (люди И AI-агенты), стиль коммитов, тесты.
- [ ] D3. `CODE_OF_CONDUCT.md` (Contributor Covenant).
- [ ] D4. `SECURITY.md` — как сообщать об уязвимостях (важно: инструмент трогает прод-1С).
- [ ] D5. `CHANGELOG.md` (Keep a Changelog) — стартовая запись про миграцию.
- [ ] D6. `.github/PULL_REQUEST_TEMPLATE.md`.
- [ ] D7. `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.md`.
- [ ] D8. Бейджи shields.io по образцу ниши (`1c-syntax/bsl-language-server`): CI, License,
      PRs welcome, AGENTS.md, 1C 8.3, MCP, (позже — SonarCloud/Telegram).

### Фаза E. Тестирование
- [ ] E1. Прогнать полный `tests/` (цель — 509 зелёных без регресса).
- [ ] E2. Прогнать `build.sh` в чистом клоне → проверить, что генерируемые файлы
      совпадают с закоммиченными (детерминизм сборки).
- [ ] E3. `onboard/onboard.sh` самотест (idемпотентность) + `scripts/doctor.py`.
- [ ] E4. Проверить, что MCP-профили валидны (JSON parse), пути через env не сломаны.

### Фаза F. Исправление (по итогам E)
- [ ] F1. Устранить регрессы/расхождения сборки. Корневая причина → фикс, один параметр
      за раз, повторный замер. Ничего «зелёного» без показанного вывода.

### Фаза G. Документация и гайды
- [ ] G1. `docs/AI_INSTALL_GUIDE.md` — переписать под мульти-агент (раздел на агента).
- [ ] G2. `README` состав/дерево — обновить под новую структуру.
- [ ] G3. `docs/architecture` — зафиксировать оси переносимости (AGENTS/SKILL/MCP).
- [ ] G4. Обновить `CLAUDE.md` (в claude-projects, локальный) и память о новом имени.

### Фаза H. Онбординг
- [ ] H1. `onboard/*` — учесть мульти-агент (сборка из core, выбор адаптера).
- [ ] H2. `demo/`-подобный быстрый старт (по образцу bitrix `demo/SETUP.md`) — опц.

### Фаза I. Team-репо (не сломать) + миграция подхода
- [ ] I1. Обновить `docs/team-localization-template.md` под новую структуру (core/adapters).
- [ ] I2. Подготовить **точный handoff для Work PC**: правки `scripts/sync-core.*` и
      `scripts/sync-skills.*` в team-репо (whitelist: новые пути `core/…`, вендоринг
      `onec_metadata/**` + `bin/1c-meta`, скилл `1c-metadata`), обновить дефолтный URL
      toolkit → `bsl-ai-toolkit`. **Push в корп-GitLab делает Слава/команда с Work PC.**
- [ ] I3. Миграционная заметка для команды: что переименовалось, что делать при `git pull`,
      что подход теперь AI-агностик (можно Cursor/Copilot, не только Claude).

### Фаза J. Узнаваемость: 1С-рейтинги и индексы (после публикации)
- [ ] J1. GitHub Topics: `1c`, `1c-enterprise`, `bsl`, `ai`, `mcp`, `mcp-server`,
      `model-context-protocol`, `claude`, `skills`, `developer-tools`, `edt`
      (⚠️ НЕ `1c-bitrix`). Автопопадание в тематический поиск + `elftorgcom/awesome-1c-ai`.
- [ ] J2. PR/Issue в **`Untru/1c-mcp`** (Awesome 1C MCP Servers, живой, 132★, есть шаблон
      `new-server.yml`) — разделы MCP + skills + rules + метаданные, точное попадание.
- [ ] J3. Сабмит в **Glama MCP Registry** (glama.ai/mcp/servers) — авто-индексация.
- [ ] J4. PR в **`artbear/awesome-1c`** (канонический, 154★) — раздел AI-инструментов.
- [ ] J5. PR/анонс в **`1c-neurofish/awesome-onec-neurofish`** + TG @onec_neurofish.
- [ ] J6. Статья-анонс на **Infostart** + выкладка в маркетплейс (бесплатно/1 стартмани).
- [ ] J7. Мировые каталоги: `punkpeye/awesome-mcp-servers`, `hesreallyhim/awesome-claude-code`,
      `travisvn/awesome-claude-skills`, официальный MCP Registry.
- [ ] J8. Упомянуть совместимость с `1c-syntax/mdclasses`/`bsl-parser` (знак качества у core-сообщества).

**Что делаю я (агент) сам:** A–I полностью (код, доки, handoff), J1/J-тексты (готовые PR-записи,
тело статьи, markdown-бейджи). **Что делаешь ты:** GitHub Rename репо; выставить Topics
и Description в вебе; создать PR/Issue в чужие awesome-репо (или дать мне добро на push
через github-MCP от твоего имени); push team-репо с Work PC; публикация на Infostart.

---

## 3. Гарантии «не сломать»
- Вендоринг team тянет **по whitelist путей** → любое перемещение файлов сопровождается
  правкой whitelist (Фаза I2). До push team-репо ничего в корп-сети не меняется.
- Rename на GitHub даёт авто-редиректы → внешние ссылки и `git remote` team продолжают
  работать; URL всё равно обновим явно.
- Сборка детерминирована и закоммичена → клон работает без запуска `build.sh`.
- Корп-специфику (config/*.<компания>.*, storage_maps) в публичный toolkit НЕ вносим.

## 4. Риски
- `build.sh`/rulesync для не-Claude агентов — фолбэк ручной; проверяем только Claude (эталон),
  остальное помечаем «черновик/по образцу», как в bitrix.
- Team-push зависит от доступа Work PC к корп-GitLab (Mac не достаёт) — это ручной шаг.
- Чужие awesome-репо принимают по своим критериям (лицензия, тесты, README) — закрываем OSS-гигиеной.
