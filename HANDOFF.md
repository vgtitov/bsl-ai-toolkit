# HANDOFF — состояние и план (для продолжения в новом сеансе)

Личный продукт владельца: **AI-first разработка 1С с Claude Code**. Развивается ВНЕ контура организация. организация —
отдельная локализация (`claude-1c-team` в корп-GitLab). Цепочка: toolkit (upstream, личный) → локализация (downstream).

## Что готово (на 2026-06-19)
- **Скиллы** `1c-dev`, `1c-analyst` (обезличенные): рабочий цикл реализации, IDE-гейт, ЧТЗ-поведение, reuse-first БСП,
  стандарты v8-code-style, производительность/кэш, шаблон конвенций. References на месте.
- **MCP** `mcp/erp_mcp.py` — чтение кода 1С (ripgrep, auto-discovery слоёв по `ONEC_SRC_DIR`), транспорт по env
  `MCP_TRANSPORT` (stdio локально / sse в Docker), `MCP_HOST`/`MCP_PORT`.
- **Docker:** `Dockerfile` + `docker-compose.yml` — центральный erp-1c по HTTP (исходники монтируются read-only в /src).
  Образ собран (`claude-1c-toolkit/erp-1c:latest`, ~253MB), smoke-тест ок (Uvicorn на :8000).
- **Тесты:** `tests/test_erp_mcp.py` — 6 шт, **6 passed** (`uv run --with mcp --with pytest pytest tests/ -q`).
- **Доки:** README (позиционирование+адаптация), CLAUDE.md (правила, версии настраиваются), docs/example.md
  (синтетический пример dev-цикла), docs/docker.md, LICENSE (MIT), onboard.ps1/sh.
- **Git:** локальный репо, коммиты до `57a3c73`. remote `origin` =
  `https://github.com/vgtitov/claude-1c-toolkit.git` (private). Аутентификация GitHub сохранена в Windows
  Credential Manager (`credential.helper=manager`) — push без повторного запроса.
- Проверено grep'ом: внутренних данных организация в toolkit НЕТ.

## Кто что делает (разделение человек / ассистент)
- **Ассистент** разрабатывает, коммитит и **сам пушит** toolkit в личный GitHub `vgtitov` (private). Это основной рабочий цикл.
- **Человек (разово/редко):** создаёт пустой GitHub-репо (сделано); первичный вход GitHub в окне Credential Manager
  (сделано, сохранено); ротация PAT и push для локализации организации в корп-GitLab (`claude-1c-team` — отдельный контур).
- Локализация под организацию ведётся ОТДЕЛЬНО; фирменное — только в корп-GitLab, в toolkit не попадает.

## План дальше (личный продукт)
1. **CI:** GitHub Actions — прогон pytest на push (workflow `.github/workflows/tests.yml`). ✅ Добавлен (ставит ripgrep + uv, гоняет `pytest tests/`). ✅ Первый push выполнен (`main` → `origin`, upstream-трекинг), CI прошёл зелёным (run на `5ca4bb6`, `completed success`). ⚠ Для push workflow-файлов токену нужен scope `workflow` (классический PAT) — учтено.
2. **Развитие toolkit:** довести скиллы (больше reference-кейсов), описание-триггеры скиллов, при желании публичный релиз.
3. **Центральный MCP по HTTP:** ✅ обкатан вживую через docker-compose на боевом наборе (`ONEC_SRC_DIR=C:\dev\tvg\rp`, 11 слоёв). Схема: `erp-1c` (SSE) только во внутренней сети + сайдкар `proxy` (Caddy) с bearer-авторизацией наружу. Проверено: 401 без/с неверным токеном, проход с верным, протокол MCP (init/list_tools/list_modules) сквозь Caddy, контейнер видит 11 слоёв из read-only `/src`. Конфиг слоёв пробрасывается (`config/layers.example.toml`). Файлы: `docker-compose.yml`, `caddy/Caddyfile`, `.env.example`, `docs/docker.md`. ⚠ Windows bind-mount медленный для тяжёлого поиска — на Linux-сервере ок.
4. **Затем — локализация под организацию** (ОТДЕЛЬНЫМ аккаунтом компании): взять ядро из toolkit, наложить фирменные
   специфики в downstream-репозитории (контуры, фирменные конвенции, профили). Улучшения ядра тянуть из toolkit.
5. **Пилот** (владелец + 1–2 разработчика) → правки → раскатка на команду.

## Как продолжить в новом сеансе
Скажи ассистенту: «продолжаем развивать `claude-1c-toolkit` (личный продукт), пуш в мой GitHub; потом локализация под
организацию». Ассистент: читает этот HANDOFF + память (`team-1c-skills-enablement`), берёт следующий пункт плана.
Локализация под организацию ведётся ОТДЕЛЬНО (под рабочим аккаунтом/контуром), фирменное — только в корп-GitLab.

## Карта репозиториев
- **`claude-1c-toolkit`** (этот) — личный продукт, → GitHub `vgtitov` (private). Источник ядра методологии.
- **`claude-1c-team`** — локализация под организацию, → корп-GitLab `team/claude-1c-team`. Фирменные специфики.
  Политика синхронизации — в его `docs/SYNC_POLICY.md`.
