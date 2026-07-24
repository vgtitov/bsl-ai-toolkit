# /// script
# dependencies = []
# ///
"""doctor — единый health-check рабочего окружения Claude Code для 1С: пререквизиты, каждый MCP-сервер из
`.mcp.json` рабочего каталога и доступы. По каждому пункту — ЗЕЛЁНЫЙ/жёлтый/КРАСНЫЙ с причиной и подсказкой.
Один вызов «проверь всё» вместо ручных проверок (собрано по разбору установочной встречи).

Запуск (из рабочего каталога, где .mcp.json):  uv run "$ONEC_TOOLKIT_DIR/scripts/doctor.py"
                                                uv run ".../doctor.py" --config <путь к .mcp.json>
Только стандартная библиотека, кросс-платформенно. Секреты не печатает (только «задан/не задан», длина).
Код возврата: 0 если нет КРАСНЫХ, иначе 1.
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
from urllib.error import HTTPError
from pathlib import Path
from urllib.parse import urlparse

_VAR = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}')


def resolve_env(value: str, env: dict) -> str:
    """Подстановка ${VAR} и ${VAR:-default} из env (пусто/не задано → default или '')."""
    def repl(m):
        v = env.get(m.group(1))
        if v is None or v == "":
            return m.group(2) if m.group(2) is not None else ""
        return v
    return _VAR.sub(repl, value)


def env_refs(value: str):
    """Имена переменных окружения, на которые ссылается строка."""
    return [m.group(1) for m in _VAR.finditer(value or "")]


def plan_for_server(name: str, spec: dict, env: dict) -> dict:
    """План проверки MCP-сервера по типу: http/sse (проба URL), stdio (команда+файл)."""
    t = spec.get("type", "stdio")
    if t in ("http", "sse"):
        needs = list(env_refs(spec.get("url", "")))
        headers = {}
        for k, h in (spec.get("headers") or {}).items():
            needs += env_refs(h)
            headers[k] = resolve_env(h, env)          # заголовки (в т.ч. Authorization) для пробы /health
        return {"name": name, "type": t, "kind": t,
                "target": resolve_env(spec.get("url", ""), env),
                "headers": headers,
                "needs_env": sorted(set(needs))}
    args = spec.get("args", []) or []
    file = None
    if "-jar" in args:
        i = args.index("-jar")
        if i + 1 < len(args):
            file = resolve_env(args[i + 1], env)
    else:
        for a in args:                     # uv run --quiet ${SCRIPT} → файл = последний arg с переменной
            if env_refs(a):
                file = resolve_env(a, env)
    needs = list(env_refs(spec.get("command", "")))
    for a in args:
        needs += env_refs(a)
    for v in (spec.get("env") or {}).values():
        needs += env_refs(v)
    return {"name": name, "type": t, "kind": "stdio",
            "command": resolve_env(spec.get("command", ""), env),
            "file": file, "needs_env": sorted(set(needs))}


# ---------- живые проверки ----------

OK, WARN, BAD = "OK", "WARN", "BAD"


def _http_ok(url: str, headers=None, timeout=6):
    """GET <host:port>/health (для MCP-сервера) с заголовками из .mcp.json (в т.ч. Authorization); True/деталь.
    Сервер считается ЖИВЫМ, если он ОТВЕТИЛ по HTTP — даже 404/405: у MCP-сервера маршрута /health может
    не быть, но сам ответ доказывает, что сервер слушает и авторизация прошла. 401/403 — авторизация НЕ
    прошла (реальная проблема: проверь токен в заголовке). Отказ соединения/таймаут — сервер не запущен.
    localhost — МИМО прокси (иначе HTTP(S)_PROXY роутит 127.0.0.1 через прокси и даёт ложную ошибку)."""
    p = urlparse(url)
    health = f"{p.scheme}://{p.netloc}/health"
    local = (p.hostname or "") in ("127.0.0.1", "localhost", "::1")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local \
        else urllib.request.build_opener()
    req = urllib.request.Request(health, headers=headers or {})
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(400).decode("utf-8", "replace")
            return True, f"HTTP {getattr(r, 'status', 200)} {body[:120]}"
    except HTTPError as e:
        # сервер ответил HTTP-статусом → он ЖИВ
        if e.code in (401, 403):
            return False, (
                f"HTTP {e.code}: авторизация не прошла — сервер жив, но токен не принят. "
                "Если в Claude Code канал при этом работает, то токен устарел именно в ЭТОЙ "
                "консоли: set_token пишет в пользовательские переменные, их видят только "
                "новые процессы — перезапусти консоль или прогони set_token заново "
                "(docs/setup-actions-required.md §4). Маршрут /health тут ни при чём: его "
                "отсутствие доктор считает нормой")
        if 400 <= e.code < 500:
            return True, f"HTTP {e.code}: сервер отвечает (/health не реализован — норма для MCP)"
        return False, f"HTTP {e.code}: сервер вернул ошибку"
    except Exception as e:
        # запасной вариант — просто достучаться до порта
        try:
            host = p.hostname or "127.0.0.1"; port = p.port or 80
            with socket.create_connection((host, port), timeout=3):
                return False, f"порт {host}:{port} открыт, но HTTP не ответил ({type(e).__name__})"
        except Exception:
            return False, f"недоступно ({type(e).__name__}): сервер не запущен?"


def check_unsafe_action_protection(base: str | None = None):
    """Защита от опасных действий: с ней батч-прогоны ступени 2 ВИСНУТ на модальном окне.

    ПОДКЛЮЧЕНИЙ НЕ ДЕЛАЕТ — только читает маски `conf.cfg`. Опрашивать сеансом каждую базу
    контура (их бывают десятки, по VPN — минуты на базу) дороже, чем ловимая проблема,
    поэтому проверка конкретной базы ленивая: она делается в момент обращения к ней
    (`apply.smoke`), а здесь — по желанию, через `--base`.

    Снят ли флаг у конкретного ПОЛЬЗОВАТЕЛЯ ИБ, снаружи базы не видно, поэтому вердикт
    мягкий: WARN с инструкцией, а не КРАСНЫЙ."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        import detect_tools
        from onec_metadata.apply import protection
        root = detect_tools.find_platform()
    except Exception as e:
        return [(WARN, "1С: защита от опасных действий", f"не смог проверить ({type(e).__name__})")]
    if not root:
        return []                       # платформы нет — ступени 1–2 тут и не запускают

    name = "1С: защита от опасных действий"
    hint = ("батч-прогоны (ступень 2) повиснут на модальном окне, если у пользователя ИБ "
            "не снят флаг. Способы — docs/setup-actions-required.md §1")
    cfg = protection.conf_cfg_path(root)
    masks = protection.masks_from_conf(cfg)
    res = []
    if masks:
        res.append((OK, name, f"маски в {cfg}: {'; '.join(masks)}"))
    else:
        res.append((WARN, name, f"масок в conf.cfg нет — {hint}"))
    if base:
        covered = protection.status_for_base(base, root) == protection.OFF_BY_MASK
        res.append((OK if covered else WARN, f"{name}: база {base}",
                    "под маской — окна не будет" if covered
                    else f"маска не покрывает — {hint}"))
    return res


def check_prereqs():
    res = []
    for c, hint in [("git", "winget install Git.Git"), ("uv", "https://astral.sh/uv"),
                    ("java", "нужен JRE/JDK (EDT его ставит)"), ("claude", "Claude Code CLI"),
                    ("rg", "ripgrep: winget install BurntSushi.ripgrep.MSVC")]:
        if shutil.which(c):
            res.append((OK, f"prereq {c}", "найден"))
        else:
            res.append((BAD, f"prereq {c}", f"НЕ найден — {hint}"))
    if os.name == "nt":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "$PSVersionTable.PSVersion.Major"], capture_output=True, text=True, timeout=15)
            major = (out.stdout or "").strip()
            res.append((OK if major.isdigit() and int(major) >= 5 else WARN,
                        "PowerShell", f"версия major={major or '?'} (нужно ≥5)"))
        except Exception as e:
            res.append((WARN, "PowerShell", f"не смог определить ({type(e).__name__})"))
    return res


def check_server(plan: dict, env: dict):
    name = plan["name"]
    missing = [v for v in plan["needs_env"] if not env.get(v)]
    if plan["kind"] in ("http", "sse"):
        if missing:
            return (BAD, f"mcp {name} ({plan['kind']})", f"не задан env: {', '.join(missing)}")
        if not plan["target"]:
            return (BAD, f"mcp {name}", "пустой URL")
        ok, detail = _http_ok(plan["target"], plan.get("headers"))
        return (OK if ok else BAD, f"mcp {name} ({plan['kind']})", f"{plan['target']}: {detail}")
    # stdio
    if plan["command"] and not shutil.which(plan["command"]) and not Path(plan["command"]).exists():
        return (BAD, f"mcp {name} (stdio)", f"команда '{plan['command']}' не найдена")
    if plan["file"] and not Path(plan["file"]).exists():
        return (BAD, f"mcp {name} (stdio)", f"файл не найден: {plan['file']}"
                + (" — задай env" if missing else ""))
    if missing:
        return (WARN, f"mcp {name} (stdio)", f"не задан env: {', '.join(missing)}")
    return (OK, f"mcp {name} (stdio)", "команда и файл на месте")


def main():
    # Windows-консоль нередко в cp1251 → печать '≥'/кириллицы падала UnicodeEncodeError. Форсим UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="health-check окружения Claude Code для 1С")
    ap.add_argument("--config", help="путь к .mcp.json (по умолчанию ./.mcp.json)")
    ap.add_argument("--base", help="строка соединения КОНКРЕТНОЙ базы — проверить её маску "
                                   "защиты от опасных действий (подключение не выполняется; "
                                   "по всем базам контура доктор не ходит намеренно)")
    ns = ap.parse_args()
    env = dict(os.environ)
    results = []

    results += check_prereqs()
    results += check_unsafe_action_protection(ns.base)

    cfg = Path(ns.config) if ns.config else Path.cwd() / ".mcp.json"
    if not cfg.exists():
        results.append((BAD, ".mcp.json", f"не найден в {cfg} — запусти onboard или перейди в рабочий каталог"))
    else:
        try:
            servers = json.loads(cfg.read_text(encoding="utf-8")).get("mcpServers", {})
            for nm, spec in servers.items():
                results.append(check_server(plan_for_server(nm, spec, env), env))
            results.append((OK, ".mcp.json", f"{len(servers)} серверов: {', '.join(servers)}"))
        except Exception as e:
            results.append((BAD, ".mcp.json", f"не разобран: {e}"))

    if env.get("ONEC_SRC_DIR"):
        ok = Path(env["ONEC_SRC_DIR"]).exists()
        results.append((OK if ok else WARN, "env ONEC_SRC_DIR",
                        env["ONEC_SRC_DIR"] + ("" if ok else " — каталог не существует")))

    mark = {OK: "[ OK ]", WARN: "[ !  ]", BAD: "[FAIL]"}
    print("=== doctor: окружение Claude Code для 1С ===")
    for status, name, detail in results:
        print(f"{mark[status]} {name}: {detail}")
    bad = sum(1 for s, _, _ in results if s == BAD)
    warn = sum(1 for s, _, _ in results if s == WARN)
    print(f"--- итог: {len(results)-bad-warn} ok, {warn} предупреждений, {bad} проблем ---")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
