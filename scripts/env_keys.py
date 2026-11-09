"""Имена ключей из .env - без значений (безопасная замена cat/Get-Content/Select-String по .env).

  python scripts/env_keys.py                 # .env в корне репо (или текущем каталоге)
  python scripts/env_keys.py --filled        # + «задан»/«пусто» у каждого ключа
  python scripts/env_keys.py --filled путь\\к\\.env

Значения не печатаются никогда: ни целиком, ни частично, ни длиной.
"""
import argparse
import re
import sys
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def parse_keys(text: str):
    """[(ключ, задано_ли_значение)] в порядке файла; комментарии и пустые строки пропускаются."""
    out = []
    for line in text.lstrip("﻿").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        value = m.group(2).strip()
        if value[:1] not in ("'", '"'):
            value = value.split(" #", 1)[0].strip()  # хвостовой комментарий
        out.append((m.group(1), value.strip("'\"") != ""))
    return out


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="файл .env (по умолчанию .env в корне репо team или текущем каталоге)")
    ap.add_argument("--filled", action="store_true", help="показать, задано ли значение (без самого значения)")
    args = ap.parse_args(argv)
    candidates = [Path(args.path)] if args.path else [Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        print("Файл .env не найден: " + ", ".join(str(p) for p in candidates), file=sys.stderr)
        return 1
    for key, filled in parse_keys(path.read_text(encoding="utf-8", errors="replace")):
        print(f"{key}\t{'задан' if filled else 'пусто'}" if args.filled else key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
