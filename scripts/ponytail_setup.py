#!/usr/bin/env python3
"""Ponytail (github.com/DietrichGebert/ponytail, MIT) в Claude Code и Codex + правило «сначала проверь, что уже есть».

Установщик и обновление набора вызывают скрипт по умолчанию; отключить: флаг --no-ponytail установщика.
Идемпотентно: штатные CLI `claude plugin` и `codex plugin` сами дописывают свои настройки и не
трогают чужие. Не ставит, если пользователь выключил Ponytail: enabled = false у плагина или
defaultMode "off" в ~/.config/ponytail/config.json (команда `/ponytail default off`).

Правило берётся из core/AGENTS.md (блок между метками check-existing-first). Ponytail касается
только кода: документы, постановки, ТЗ, тест-кейсы не сокращаются (сказано в самом правиле).
С --project блок дописывается в AGENTS.md проекта, повторный запуск заменяет блок.

  python scripts/ponytail_setup.py                 # режим по роли: analyst -> off, остальные -> lite
  python scripts/ponytail_setup.py --role analyst  # роль явно (иначе BITRIX_ROLE / ONEC_ACTIVE_PROFILE)
  python scripts/ponytail_setup.py --mode full     # одиночная работа
  python scripts/ponytail_setup.py --project DIR   # плюс правило в DIR/AGENTS.md
  python scripts/ponytail_setup.py --no-plugin --project DIR   # только правило
  python scripts/ponytail_setup.py --dry-run       # только показать, что будет сделано

Режим пишется в ~/.config/ponytail/config.json (defaultMode), только если его там ещё нет.
Сменить потом: команда `/ponytail default full` в агенте или PONYTAIL_DEFAULT_MODE.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN = "ponytail@ponytail"
REPO = "DietrichGebert/ponytail"
MODES = ("off", "lite", "full", "ultra")
ROOT = Path(__file__).resolve().parents[1]
RULE_SRC = ROOT / "core" / "AGENTS.md"
VERIFIED_PLUGIN = "5.3.2"   # версия, на которой измерен запуск хука (Work PC, Windows)
SLOW_HOOK_SEC = 3.0         # у Claude Code на хук 5-15 с; больше 3 с на холодном старте уже тревога
BEGIN, END = "<!-- check-existing-first:begin -->", "<!-- check-existing-first:end -->"


def claude_disabled() -> bool:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        data = json.loads((base / "settings.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
    return isinstance(plugins, dict) and plugins.get(PLUGIN) is False


def codex_disabled() -> bool:
    base = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        text = (base / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        import tomllib
        try:
            return (tomllib.loads(text).get("plugins") or {}).get(PLUGIN, {}).get("enabled") is False
        except tomllib.TOMLDecodeError:
            return False
    except ImportError:  # Python < 3.11: грубая проверка регэкспом
        pass
    m = re.search(r'^\[plugins\."ponytail@ponytail"\]\s*\n((?:(?!\[).*\n?)*)', text, re.M)
    return bool(m and re.search(r"^\s*enabled\s*=\s*false\b", m.group(1), re.M))


def ponytail_config_path() -> Path:
    # тот же порядок, что в самом Ponytail (hooks/ponytail-config.js)
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "ponytail" / "config.json"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "ponytail" / "config.json"
    return Path.home() / ".config" / "ponytail" / "config.json"


def set_default_mode(mode: str, dry: bool) -> str:
    path = ponytail_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return f"{path}: не JSON-объект, не трогаю"
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        return f"{path}: не читается, не трогаю"
    if "defaultMode" in data:
        return f"режим уже задан: {data['defaultMode']} ({path}), не трогаю"
    data["defaultMode"] = mode
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # метка рядом, а не внутри: Ponytail не трогает чужой файл, а любая правка конфига меняет mtime
        marker_path().write_text(json.dumps({"mode": mode, "mtime_ns": path.stat().st_mtime_ns}), encoding="utf-8")
    return f"режим по умолчанию: {mode} ({path})"


def mode_for_role(role: str) -> str:
    # аналитику Ponytail по умолчанию выключен: его результат — текст; правило «только код» действует при любой роли
    return "off" if role.strip().lower() == "analyst" else "lite"


def rule_block() -> str:
    src = RULE_SRC.read_text(encoding="utf-8")
    if src.count(BEGIN) != 1 or src.count(END) != 1 or src.find(END) < src.find(BEGIN):
        raise ValueError("метки повреждены или отсутствуют")
    return BEGIN + src.split(BEGIN, 1)[1].split(END, 1)[0] + END + "\n"


def marker_path() -> Path:
    return ponytail_config_path().with_name("ai-toolkit-default.json")


def user_turned_off() -> bool:
    # off, который записал сам установщик по роли (аналитик), и конфиг с тех пор не менялся — не отказ:
    # плагин можно доставить в агент, появившийся позже, режим остаётся off. Отказ — off, выбранный человеком.
    path = ponytail_config_path()
    try:
        if json.loads(path.read_text(encoding="utf-8-sig")).get("defaultMode") != "off":
            return False
        mtime = path.stat().st_mtime_ns
    except (OSError, ValueError, AttributeError):
        return False
    try:
        mark = json.loads(marker_path().read_text(encoding="utf-8"))
        return not (mark.get("mode") == "off" and mark.get("mtime_ns") == mtime)
    except (OSError, ValueError, AttributeError):
        return True


def write_rule(project: Path, dry: bool) -> str:
    project = project.resolve()
    if project == ROOT:
        return "правило: проект не указан (это каталог набора), пропуск; передай --project <каталог проекта>"
    if not project.is_dir():
        return f"правило: каталога {project} нет, пропуск"
    target = project / "AGENTS.md"
    try:
        old = target.read_text(encoding="utf-8") if target.exists() else ""
    except (OSError, UnicodeError) as e:
        return f"правило: {target} не читается ({e}), не трогаю"
    try:
        block = rule_block()
    except (OSError, IndexError, ValueError) as e:
        return f"правило: в {RULE_SRC} нет целого блока check-existing-first ({e}), пропуск"
    if old.count(BEGIN) != old.count(END) or old.count(BEGIN) > 1 or old.find(END) < old.find(BEGIN):
        return f"правило: метки check-existing-first в {target} повреждены, не трогаю; поправь руками"
    if BEGIN in old:
        head, rest = old.split(BEGIN, 1)
        new = head + block + rest.split(END, 1)[1].lstrip("\n")
    else:
        new = old + ("\n" if old and not old.endswith("\n") else "") + ("\n" if old else "") + block
    if not dry and new != old:
        target.write_text(new, encoding="utf-8")
    hint = ""
    claude_md = project / "CLAUDE.md"
    try:
        if claude_md.is_file() and "@AGENTS.md" not in claude_md.read_text(encoding="utf-8", errors="ignore"):
            hint = "; Claude Code читает его, только если в CLAUDE.md есть строка @AGENTS.md"
    except OSError:
        pass                                              # подсказка необязательна
    return f"правило «сначала проверь, что уже есть» -> {target}{hint}"


def run(cmd: list[str], dry: bool) -> bool:
    if dry:
        print("    $ " + " ".join(cmd))
        return True
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"    ! {cmd[0]}: {e}")
        return False
    if r.returncode != 0:
        print(f"    ! {' '.join(cmd)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.returncode == 0


AGENTS = {
    "claude": (claude_disabled, [["plugin", "marketplace", "add", REPO], ["plugin", "install", PLUGIN]]),
    "codex": (codex_disabled, [["plugin", "marketplace", "add", REPO], ["plugin", "add", PLUGIN]]),
}


def installed_plugin() -> tuple[str, Path] | None:
    """Самая новая версия плагина в кэше Claude Code: (версия, каталог hooks)."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    root = base / "plugins" / "cache" / "ponytail" / "ponytail"
    def key(d: Path):
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", d.name))
    try:
        dirs = [d for d in root.iterdir() if (d / "hooks" / "ponytail-mode-tracker.js").is_file()]
    except OSError:
        return None
    if not dirs:
        return None
    best = max(dirs, key=key)
    return best.name, best / "hooks"


def _timed(cmd: list[str], stdin: str = "") -> float | None:
    import time
    t = time.monotonic()
    try:
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return time.monotonic() - t if r.returncode == 0 else None


def check_hook() -> list[str]:
    """Замер (до 2 x 10 с) холодного старта node и хука Ponytail; предупреждение, если дольше SLOW_HOOK_SEC."""
    node = shutil.which("node")
    found = installed_plugin()
    if not node or not found:
        return []
    ver, hooks = found
    out = [f"  [ok] версия плагина Ponytail: {ver}" + ("" if ver == VERIFIED_PLUGIN else
           f" (проверена {VERIFIED_PLUGIN}: если хук тормозит, сообщи версию)")]
    # хук первым: он и есть холодный запуск, голый node после него уже прогрет
    hook = _timed([node, str(hooks / "ponytail-mode-tracker.js")], '{"prompt":"x","hook_event_name":"UserPromptSubmit"}')
    base = _timed([node, "-e", "1"])
    fmt = lambda v: "ошибка или больше 10 с" if v is None else f"{v:.2f} с"
    out.append(f"  [ok] запуск: node {fmt(base)}, хук {fmt(hook)}")
    if any(v is None or v > SLOW_HOOK_SEC for v in (base, hook)):
        who = "сам node" if base is None or base > SLOW_HOOK_SEC else "хук"
        if os.name == "nt":
            todo = ("Что сделать: 1) исключить node.exe и папку .claude из проверки антивируса (Defender: Параметры защиты от вирусов, Исключения); "
                    "2) замерить Measure-Command { node -e 1 } в PowerShell: дольше 3 с значит антивирус; если здесь быстро, а Claude Code всё равно ругается, подозрение на профиль PowerShell ($PROFILE), замер его не видит; "
                    "3) не держать профиль и .claude на сетевом диске или в OneDrive; 4) прислать эти цифры и версию плагина")
        else:
            todo = ("Что сделать: замерить `time node -e 1`; проверить защиту, которая сканирует node; "
                    "если здесь быстро, а Claude Code ругается, подозрение на профиль оболочки (замер его не видит); "
                    "не держать ~/.claude на сетевом диске; прислать цифры и версию плагина")
        out.append(f"  [!] медленно ({who}): Claude Code прервёт хук по таймауту и покажет «hook timed out». " + todo)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=MODES, help="режим по умолчанию; без флага — по роли: analyst → off, иначе lite")
    ap.add_argument("--role", default=os.environ.get("BITRIX_ROLE") or os.environ.get("ONEC_ACTIVE_PROFILE") or "",
                    help="роль/профиль (по умолчанию из BITRIX_ROLE или ONEC_ACTIVE_PROFILE)")
    ap.add_argument("--project", type=Path, help="каталог проекта: дописать правило в его AGENTS.md")
    ap.add_argument("--no-plugin", action="store_true", help="плагин не ставить, только правило")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    a.mode = a.mode or mode_for_role(a.role)

    if a.project:
        print("  [ok] " + write_rule(a.project, a.dry_run))
    if a.no_plugin:
        return 0
    installed = False
    if user_turned_off():
        print("  [=] Ponytail выключен пользователем (defaultMode off), плагин не трогаю")
        return 0
    for name, (disabled, steps) in AGENTS.items():
        exe = shutil.which(name)
        if not exe:
            print(f"  [-] {name}: CLI не найден, пропуск")
        elif disabled():
            print(f"  [=] {name}: {PLUGIN} выключен пользователем, не включаю")
        elif (run([exe, *steps[0]], a.dry_run) or True) and run([exe, *steps[1]], a.dry_run):   # marketplace add на повторе может вернуть ошибку
            print(f"  [ok] {name}: {PLUGIN}")
            installed = True
        else:
            print(f"  [!] {name}: не поставился (сеть?), вручную: {name} " + " ; ".join(" ".join(s) for s in steps))
    if installed:
        print("  [ok] " + set_default_mode(a.mode, a.dry_run))
    if installed and not a.dry_run:
        for line in check_hook():
            print(line)
    if not shutil.which("node"):
        print("  [!] node не найден: хуки Ponytail молчат, скиллы /ponytail работают")
    return 0  # установка Ponytail необязательна и не должна валить онбординг


if __name__ == "__main__":
    sys.exit(main())
