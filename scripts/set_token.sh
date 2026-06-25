#!/usr/bin/env bash
# Подключение к ЦЕНТРАЛЬНОМУ erp-1c одним шагом (org-agnostic, mac/Linux): кладёт URL и bearer-токен центра
# в окружение пользователя (~/.zshrc или ~/.bashrc) и печатает готовый блок для вставки в Claude Code —
# дальше ассистент читает доку (docs/docker.md / CONNECT.md локализации) и настраивает всё сам.
#
# Клиентская сторона центрального сервиса (docker-compose + Caddy) — пара к scripts/switch_erp.py. Токен НИКУДА
# не пишется кроме env пользователя: не в git, не в .mcp.json. switch_erp подставит его плейсхолдером
# ${ERP1C_TOKEN} только при старте MCP. Локализации вендорят скрипт и задают свои URL/профиль.
#
# Запуск:
#   bash scripts/set_token.sh --url http://host:8000/sse                 # спросит токен, профиль dev
#   bash scripts/set_token.sh --url http://host:8000/sse --profile analyst
#   ERP1C_TOKEN=... bash scripts/set_token.sh --url http://host:8000/sse # токен из env (не печатается)
set -euo pipefail
URL="${ERP1C_URL:-}"; PROFILE="dev"; TOKEN="${ERP1C_TOKEN:-}"; NOTEST=0
while [ $# -gt 0 ]; do case "$1" in
  --url)     URL="$2"; shift 2;;
  --profile) PROFILE="$2"; shift 2;;
  --token)   TOKEN="$2"; shift 2;;
  --no-test) NOTEST=1; shift;;
  *) echo "неизвестный аргумент: $1"; exit 1;;
esac; done

say(){ printf '\n== %s ==\n' "$1"; }
ok(){ printf '  [ok] %s\n' "$1"; }
warn(){ printf '  [!] %s\n' "$1"; }

# --- URL центра ---
if [ -z "$URL" ]; then printf 'URL центра (например http://host:8000/sse): '; read -r URL; fi
[ -z "$URL" ] && { warn "URL не задан — отмена."; exit 1; }

# --- Токен ---
if [ -z "$TOKEN" ]; then
  printf 'Вставь bearer-токен центра (выдаёт владелец центра; ввод скрыт): '
  stty -echo 2>/dev/null || true; read -r TOKEN; stty echo 2>/dev/null || true; printf '\n'
fi
TOKEN="$(printf '%s' "$TOKEN" | tr -d '[:space:]')"
[ -z "$TOKEN" ] && { warn "Токен пустой — отмена."; exit 1; }

# --- Записать в rc-файл (идемпотентно) + текущую сессию ---
RC="$HOME/.zshrc"; [ -n "${BASH_VERSION:-}" ] && [ ! -f "$HOME/.zshrc" ] && RC="$HOME/.bashrc"
say "Записываю доступ к центру в $RC"
touch "$RC"
grep -v -E '^export ERP1C_(URL|TOKEN|MODE)=' "$RC" > "$RC.tmp" && mv "$RC.tmp" "$RC"
{ echo "export ERP1C_URL=\"$URL\""; echo "export ERP1C_TOKEN=\"$TOKEN\""; echo "export ERP1C_MODE=\"auto\""; } >> "$RC"
export ERP1C_URL="$URL"; export ERP1C_TOKEN="$TOKEN"; export ERP1C_MODE="auto"
ok "ERP1C_URL = $URL"; ok "ERP1C_TOKEN = <скрыт, длина ${#TOKEN}>"; ok "ERP1C_MODE = auto"

# --- Необязательная проверка связи ---
if [ "$NOTEST" -eq 0 ] && command -v curl >/dev/null 2>&1; then
  say "Проверка центра (необязательная)"
  code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" --max-time 5 "$URL" || echo "000")"
  case "$code" in
    200) ok "центр ответил 200 — токен верный";;
    401) warn "401 — токен не принят";;
    000) warn "связь не проверена (сеть до центра) — не блокер";;
    *)   warn "ответ $code — проверь токен/URL";;
  esac
fi

# --- Готовый блок для вставки в Claude Code ---
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
printf '\n=== ГОТОВО. Скопируй блок ниже целиком и вставь в Claude Code ===\n'
printf -- '------------------------------------------------------------------------\n'
cat <<EOF
Прочитай документацию по подключению к центру (docs/docker.md, в локализации — docs/CONNECT.md) и настрой инструменты 1С для Claude Code.
Профиль: $PROFILE.
Центр настроен: ERP1C_URL и ERP1C_TOKEN заданы в моём окружении (режим central, токен подставится сам).
Пройди: onboard -> switch_erp (--mode central) -> self-test (find_object на наборе репозиториев центра).
EOF
printf -- '------------------------------------------------------------------------\n'
printf '\nОткрой НОВЫЙ терминал и запусти Claude Code там (или: source %s) — env подхватывается новыми процессами.\n' "$RC"
printf 'Дока (если хочешь руками): %s/docs/docker.md\n' "$REPO_ROOT"
