#!/usr/bin/env python3
"""PostToolUse-хук: прогнать bsl_guard и rights_guard по изменённому файлу и вернуть находки агенту.

Раньше хук в settings.json брал путь из переменной `$CLAUDE_FILE_PATHS`, которой в Claude Code нет,
поэтому проверки после правок молча не запускались. Формат Claude Code: хук получает JSON на STDIN,
путь к файлу — в `tool_input.file_path`; ответ агенту — через `hookSpecificOutput.additionalContext`,
он попадает в контекст, и агент чинит находку сам.

Никогда не ломает работу агента: не наш вход, нет файла, упал гвард — тихий выход 0. Только stdlib.
"""
import json
import os
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
MAX_OUT = 4000                      # не раздувать контекст агента


def _run(script: str, *args: str) -> tuple[int, str]:
    try:
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        return r.returncode, (r.stdout or "").strip()
    except Exception:
        return 0, ""


def findings_for(path: str) -> list[str]:
    low = path.lower()
    notes = []
    if low.endswith(".bsl"):
        rc, out = _run("bsl_guard.py", path)
        if rc != 0 and out:
            notes.append("bsl_guard — возможен запрос/обращение к БД в цикле:\n" + out)
    if low.endswith((".bsl", ".mdo", ".rights")):
        rc, out = _run("rights_guard.py", "--json", path)
        try:
            items = json.loads(out) if out else []
        except ValueError:
            items = []
        if items:
            lines = [f"{'ОШИБКА' if i.get('severity') == 'error' else 'внимание'} "
                     f"{os.path.basename(i.get('file', ''))}:{i.get('line')} [{i.get('code')}] {i.get('message')}"
                     for i in items]
            notes.append("rights_guard — права/RLS:\n" + "\n".join(lines[:20]))
    return notes


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not path or not os.path.isfile(path):
        return 0
    notes = findings_for(path)
    if notes:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                 "additionalContext": "\n\n".join(notes)[:MAX_OUT]}},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
