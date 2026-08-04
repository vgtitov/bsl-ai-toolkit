# Работа с git из Claude Code

Разработчик коммитит и пушит прямо из Claude Code. Нужно три вещи: правильная идентичность по площадке,
токен для push и запрет соавторства Claude. Всё — стандартный git, отдельный «движок» не нужен.

## MCP или CLI?

**CLI** (через Bash-инструмент Claude Code) — основной и рекомендуемый путь: `git status/diff/add/commit/push/branch/
rebase` Claude вызывает нативно, это работает офлайн и одинаково на всех ОС, а commit-msg хук и идентичность
применяются автоматически.

**MCP (gitlab/github) — опционально и сверху**, только для API-сценариев: открыть/ревьюить merge request, работать
с issues, читать пайплайны. Для обычных коммитов/пушей он не нужен. Подключайте его отдельно, когда понадобится
ревью MR из ассистента, а не для базовой работы с git.

## 1. Глобальные git-хуки (обязательно)

Ставятся одной командой три хука:

- **`commit-msg`** — вырезает из сообщений атрибуцию AI-инструментов по СТРУКТУРЕ строки (git-трейлер вида
  `…-by:`/`…-session:` или фраза co-authored/generated/reviewed/assisted/written by + домен вендора), а не
  по списку имён — так ловит любой инструмент, включая ещё не существующие, и не режет бэйр-упоминание
  инструмента как темы коммита (например путь `.claude/skills`);
- **`pre-commit`** — bsl-guard: блокирует staged `*.bsl` с обращением к БД в цикле;
- **`pre-push`** — запускает `scripts/hooks/pre-push` репозитория (если есть) и штатный `git lfs pre-push`.

**Про `git-lfs`.** `pre-push` вызывает его, когда он установлен. Если `git-lfs` в PATH нет, push будет
остановлен **только в репозитории, который реально использует LFS** (`filter=lfs` в `.gitattributes`) —
иначе в удалённый репозиторий уехали бы указатели вместо файлов. В остальных репозиториях хук молча
пропускает. `doctor.py` проверяет наличие `git-lfs` и предупреждает (WARN, не ошибка: нужен не всем).
Поставить: `winget install GitHub.GitLFS`, затем `git lfs install`.

Установка (идемпотентно, чужой хук не затирает):

```bash
uv run scripts/install_git_hooks.py     # или: python scripts/install_git_hooks.py
```

Это ставит `core.hooksPath` (по умолчанию `~/.git-global-hooks`) и кладёт туда хуки из `scripts/git-hooks/`.
Если на машине уже есть свой `commit-msg` — установщик не затрёт его, а подскажет смержить (эталон — `scripts/git-hooks/commit-msg`).

### Свои pre-push проверки в репозитории

Глобальный `pre-push` — точка расширения. Он делает два дела по порядку: запускает
`scripts/hooks/pre-push` **из репозитория**, если такой файл есть, и затем штатный `git lfs pre-push`.
Значит репозиторий может иметь собственные проверки перед push, не трогая toolkit: положите
исполняемый `scripts/hooks/pre-push`, и глобальный хук его подхватит. Ненулевой код возврата
останавливает push; обойти разово — `git push --no-verify`.

Проверка репозитория идёт до LFS, чтобы не тратить трафик на выгрузку бинарников, если push всё равно
будет отклонён. В чужих репозиториях (нет `scripts/hooks/pre-push`) хук просто передаёт управление LFS.

Важно про установку: `pre-push` есть почти на каждой машине — его кладёт `git lfs install`. Установщик
распознаёт этот штатный хук и заменяет своим (наш сам вызывает `git lfs pre-push`, поведение LFS
сохраняется). Любой ДРУГОЙ существующий `pre-push` не затирается — будет предупреждение.

## 2. Идентичность по площадкам (свой Name+email на каждую)

Коммиты и пуши в каждую площадку идут от своего имени/почты. git выбирает идентичность **по адресу origin репозитория**
(`includeIf hasconfig:remote.*.url`, git ≥ 2.36) — не по каталогу. Базовая идентичность — для основной площадки;
для остальных — отдельный файл.

```bash
# Базовая (основная площадка) — глобально:
git config --global user.name  "Имя Фамилия"
git config --global user.email "user@corp.example"

# Доп. площадка (например github) — своя идентичность в отдельном файле:
printf '[user]\n\tname = Имя Фамилия\n\temail = me@personal.example\n' > ~/.gitconfig-github
git config --global \
  "includeIf.hasconfig:remote.*.url:https://*github.com/**.path" "~/.gitconfig-github"
git config --global \
  "includeIf.hasconfig:remote.*.url:git@github.com:**.path"      "~/.gitconfig-github"
```

Проверка: в репозитории площадки `git config user.email` должна показать её почту.

## 3. Токен для push

PAT кладётся в менеджер учёток ОС (НЕ в файл/репозиторий). Тогда `git push` по https не спрашивает пароль.

```bash
# helper, если не задан: Windows → manager, mac → osxkeychain
git config --global credential.helper manager      # пример для Windows

# сохранить токен для площадки (username — логин аккаунта; для PAT часто подходит он же):
printf 'protocol=https\nhost=gitlab.example.ru\nusername=ЛОГИН\npassword=PAT\n\n' | git credential approve
```

Для **self-hosted GitLab** дополнительно (иначе Git Credential Manager уходит в OAuth и даёт `HTTP Basic: Access denied`):

```bash
git config --global credential.https://gitlab.example.ru.provider generic
```

GitHub.com работает без этого. Токен живёт в менеджере учёток, в `.mcp.json`/репозиторий/чат не попадает.

## 4. Каталог репозиториев

Клоны конфигураций/расширений — в каталоге `ONEC_SRC_DIR` (его задаёт onboard; туда же он клонирует репозитории
по профилю). Если нужного репозитория нет локально — склонируй его в этот каталог, и MCP-движок подхватит код.

---

**Итог для ассистента:** настрой хук (п.1), убедись, что идентичность площадки верна (п.2) и токен сохранён (п.3),
дальше работай с git через CLI. Коммиты — от лица разработчика, без соавторства Claude.
