"""Список процессов с командной строкой под маской паролей (замена Get-CimInstance/wmic/ps -ef).

  python scripts/proc_list.py --name 1cv8            # фильтр по имени процесса (подстрока)
  python scripts/proc_list.py --name 1cv8 --grep MyBase   # и по командной строке (подстрока)

Маскируются: /P..., /P "...", /Pwd, /ConfigurationRepositoryP..., -p ..., --password/--pwd/--pass,
-Password/-Pwd, pwd=/password= в строках подключения. Вывод: PID<TAB>имя<TAB>строка.
Маска - снижение риска, а не гарантия: незнакомый формат пароля может пройти. Без нужды строку не печатать.
"""
import argparse
import json
import platform
import re
import subprocess
import sys

_VAL = r"(?:\"[^\"]*\"|'[^']*'|\S+)"
_MASKS = [
    (re.compile(r"(?i)(?<!\S)(--(?:password|passwd|pwd|pass))(=|\s+)" + _VAL), r"\1\2***"),
    (re.compile(r"(?i)(?<!\S)(-(?:password|pwd|pass))(:|\s+)" + _VAL), r"\1\2***"),
    (re.compile(r"(?i)(?<!\S)(/ConfigurationRepositoryP)\s*" + _VAL), r"\1***"),
    (re.compile(r"(?i)(?<!\S)(/P)\s*" + _VAL), r"\1***"),
    (re.compile(r"(?<!\S)(-p)(?!ass|wd|\*)(?:\s+|(?=\S))" + _VAL), r"\1 ***"),
    (re.compile(r"(?i)\b(pwd|password|passwd)(\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^;\s\"']+)"), r"\1\2***"),
]


def mask(cmdline: str) -> str:
    for rx, repl in _MASKS:
        cmdline = rx.sub(repl, cmdline)
    return cmdline


def _processes():
    if platform.system() == "Windows":
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress")
        raw = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, check=True).stdout.decode("utf-8", "replace")
        data = json.loads(raw or "[]")
        for p in data if isinstance(data, list) else [data]:
            yield p.get("ProcessId"), p.get("Name") or "", p.get("CommandLine") or ""
    else:
        raw = subprocess.run(["ps", "-eo", "pid=,comm=,args="], capture_output=True, check=True)
        for line in raw.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split(None, 2)
            if len(parts) >= 2:
                yield int(parts[0]), parts[1], parts[2] if len(parts) > 2 else ""


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="подстрока имени процесса (без учёта регистра)")
    ap.add_argument("--grep", help="подстрока командной строки (без учёта регистра; ищется до маски)")
    args = ap.parse_args(argv)
    for pid, name, cmd in _processes():
        if args.name and args.name.lower() not in name.lower():
            continue
        if args.grep and args.grep.lower() not in cmd.lower():
            continue
        print(f"{pid}\t{name}\t{mask(cmd)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
