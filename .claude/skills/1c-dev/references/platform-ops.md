# Операции с платформой 1С (компиляция/дамп/валидация форм, СКД, ролей, метаданных)

Для МЕХАНИЧЕСКИХ операций над конфигурацией (cf-edit/dump/validate, form-compile/decompile, db-*, skd-*, role-*, web-* и т.п.) использовать готовый набор **cc-1c-skills** (Nikolay-Shirokov, MIT, 390★, ~68 скиллов, нативно под Claude Code, полный цикл 1С 8.3).

Установка (на Work PC, где есть платформа 1С / EDT / CLI конфигуратора):
```
# как плагин/submodule; см. репо github.com/Nikolay-Shirokov/cc-1c-skills (.claude/skills/*)
git submodule add https://github.com/Nikolay-Shirokov/cc-1c-skills.git external/cc-1c-skills   # вариант
# либо склонировать и скопировать нужные .claude/skills/* в ~/.claude/skills
```
На Mac эти скиллы НЕ запустятся (нет 1С-платформы под Mac) — поэтому скилл разработки отдельно покрывает чтение/ревью на любой ОС.

Альтернативы для адаптации: comol/ai_rules_1c (rules/subagents BSL), Desko77/cursor-1c-skills (BSL-стандарты, анти-паттерны, EDT, БСП API, оптимизация запросов).
