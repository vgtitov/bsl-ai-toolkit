# /// script
# dependencies = []
# ///
"""Детектор анти-паттернов производительности BSL: обращение к БД ВНУТРИ цикла.

Ловит баг, который AI/человек регулярно допускает (напр. печатная форма делала
`Запрос…Выполнить()` внутри `Пока/Для`): создание/выполнение Запроса, подъём
объекта (`.ПолучитьОбъект`) и чтение реквизита через `.Ссылка.` — в теле цикла.
Это ENFORCEMENT к правилу performance.md §2 и ai-pitfalls (строка 8), а не текст.

Чистый stdlib, кроссплатформенно. Комментарии и строковые литералы вырезаются,
поэтому ключевые слова внутри строк/запросов не дают ложных срабатываний.

Запуск:
  python scripts/bsl_guard.py <файл|каталог> ...   # exit 1 если есть находки
  python scripts/bsl_guard.py --json <path>        # находки в JSON
  python scripts/bsl_guard.py --quiet <path>       # только exit-код
Точка входа для pre-commit — scripts/git-hooks/pre-commit (только staged *.bsl).
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# --- Windows-консоль по умолчанию cp1251; печать кириллицы её роняет ------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


@dataclass
class Finding:
    file: str
    line: int
    code: str
    message: str


# Открытие/закрытие цикла. \bЦикл\b НЕ матчит внутри 'КонецЦикла' (нет границы
# слова перед 'Ц' и после 'л' → 'Цикла'), что и требуется.
_LOOP_OPEN = re.compile(r"\bЦикл\b|\bDo\b", re.IGNORECASE)
_LOOP_CLOSE = re.compile(r"\bКонецЦикла\b|\bEndDo\b", re.IGNORECASE)

# Анти-паттерны (код, регэксп, сообщение). Все ищутся ТОЛЬКО в теле цикла.
_PATTERNS = [
    ("new-query-in-loop",
     re.compile(r"\bНовый\s+Запрос\b|\bNew\s+Query\b", re.IGNORECASE),
     "создание объекта Запрос в цикле — вынеси создание Запроса ДО цикла, "
     "в цикле меняй только параметры"),
    ("query-exec-in-loop",
     re.compile(r"\.Выполнить\w*\s*\(|\.Execute\w*\s*\(", re.IGNORECASE),
     "выполнение запроса (.Выполнить) в цикле — переделай на ОДИН пакетный "
     "запрос вне цикла"),
    ("get-object-in-loop",
     re.compile(r"\.ПолучитьОбъект\s*\(|\.GetObject\s*\(", re.IGNORECASE),
     "подъём объекта (.ПолучитьОбъект) в цикле — читай реквизиты запросом/"
     "пачкой, не .ПолучитьОбъект()"),
    ("ref-dot-in-loop",
     re.compile(r"\.Ссылка\.[А-Яа-яA-Za-z_]"),
     "обращение к БД через .Ссылка.<Реквизит> в цикле — читай пачкой "
     "(ОбщегоНазначения.ЗначенияРеквизитовОбъектов)"),
]


def _strip_comments_and_strings(text: str) -> str:
    """Заменить содержимое строковых литералов и комментариев пробелами,
    сохранив переносы строк (номера строк не смещаются).

    BSL: строка `"…"`, `""` внутри — экранированная кавычка; `//` — комментарий
    до конца строки (вне строки). Многострочные строки-запросы (с `|`) гасятся
    целиком до закрывающей кавычки — для нас важно лишь убрать их содержимое.
    """
    out = []
    i, n = 0, len(text)
    in_string = False
    in_comment = False
    while i < n:
        ch = text[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
                out.append(ch)
            else:
                out.append(" ")
            i += 1
            continue
        if in_string:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':  # экранированная кавычка
                    out.append("  ")
                    i += 2
                    continue
                in_string = False
                out.append(" ")
            elif ch == "\n":
                out.append(ch)
            else:
                out.append(" ")
            i += 1
            continue
        # обычный код
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            in_comment = True
            out.append("  ")
            i += 2
            continue
        if ch == '"':
            in_string = True
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Объявление и конец процедуры/функции (для межпроцедурного анализа).
_PROC_OPEN = re.compile(
    r"^\s*(?:Функция|Процедура|Function|Procedure)\s+([\wА-Яа-яЁё]+)", re.IGNORECASE)
_PROC_CLOSE = re.compile(
    r"^\s*(?:КонецФункции|КонецПроцедуры|EndFunction|EndProcedure)\b", re.IGNORECASE)


def _module_procs(lines):
    """Карта процедур модуля: имя → (первая_строка, последняя_строка), 1-based."""
    procs = {}
    current, start = None, 0
    for lineno, line in enumerate(lines, start=1):
        m = _PROC_OPEN.match(line)
        if m:
            current, start = m.group(1), lineno
            continue
        if current and _PROC_CLOSE.match(line):
            procs[current] = (start, lineno)
            current = None
    return procs


def _db_access_procs(lines, procs):
    """Имена процедур, обращающихся к БД (сами или транзитивно через вызовы)."""
    direct = set()
    for name, (a, b) in procs.items():
        body = "\n".join(lines[a:b - 1])  # тело без строки объявления
        if any(rx.search(body) for _, rx, _ in _PATTERNS):
            direct.add(name)
    # граф вызовов: только вызовы БЕЗ точки перед именем (свой модуль)
    call_rx = {name: re.compile(r"(?<![\wА-Яа-яЁё.])" + re.escape(name) + r"\s*\(")
               for name in procs}
    calls = {name: set() for name in procs}
    for name, (a, b) in procs.items():
        body = "\n".join(lines[a:b - 1])
        for other in procs:
            if other != name and call_rx[other].search(body):
                calls[name].add(other)
    # транзитивное замыкание
    tainted = set(direct)
    changed = True
    while changed:
        changed = False
        for name, callees in calls.items():
            if name not in tainted and callees & tainted:
                tainted.add(name)
                changed = True
    return tainted, call_rx


def scan_text(text: str, filename: str = "<text>") -> list:
    """Вернуть список Finding по анти-паттернам «обращение к БД в цикле».

    Два прохода: прямые паттерны в теле цикла + межпроцедурный (вызов в цикле
    функции СВОЕГО модуля, которая — сама или транзитивно — лезет в БД; боевой
    пропуск: запрос сидел в функции, из цикла звали функцию).
    """
    clean = _strip_comments_and_strings(text)
    lines = clean.split("\n")
    procs = _module_procs(lines)
    tainted, call_rx = _db_access_procs(lines, procs)

    findings = []
    depth = 0  # глубина вложенности циклов ДО текущей строки
    for lineno, line in enumerate(lines, start=1):
        opens = [m.start() for m in _LOOP_OPEN.finditer(line)]
        closes = [m.start() for m in _LOOP_CLOSE.finditer(line)]

        def _eff_depth(pos):
            return depth + sum(1 for o in opens if o < pos) \
                         - sum(1 for c in closes if c < pos)

        for code, rx, msg in _PATTERNS:
            for m in rx.finditer(line):
                if _eff_depth(m.start()) >= 1:
                    findings.append(Finding(filename, lineno, code, msg))
        if not _PROC_OPEN.match(line):  # объявление — не вызов
            for name in tainted:
                for m in call_rx[name].finditer(line):
                    if _eff_depth(m.start()) >= 1:
                        findings.append(Finding(
                            filename, lineno, "db-call-in-loop",
                            f"вызов {name}() в цикле, а внутри неё — обращение "
                            "к БД (напрямую или по цепочке вызовов) — переделай "
                            "на ОДИН пакетный запрос до цикла"))
        depth += len(opens) - len(closes)
        if depth < 0:
            depth = 0
    return findings


def scan_file(path: Path) -> list:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        try:
            text = path.read_text(encoding="cp1251")
        except OSError:
            return []
    return scan_text(text, str(path))


def _iter_bsl(paths):
    for p in paths:
        p = Path(p)
        if p.is_dir():
            yield from sorted(p.rglob("*.bsl"))
        elif p.suffix.lower() == ".bsl" and p.is_file():
            yield p


def scan_paths(paths) -> list:
    findings = []
    for f in _iter_bsl(paths):
        findings.extend(scan_file(f))
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="bsl_guard",
        description="Детектор обращения к БД (Запрос/ПолучитьОбъект/.Ссылка.) в цикле.")
    ap.add_argument("paths", nargs="+", help="файлы .bsl или каталоги")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    ap.add_argument("--quiet", action="store_true", help="без вывода, только exit")
    ns = ap.parse_args(argv)

    findings = scan_paths(ns.paths)
    if ns.json:
        print(json.dumps([f.__dict__ for f in findings], ensure_ascii=False, indent=2))
    elif not ns.quiet:
        for f in findings:
            print(f"{f.file}:{f.line} — [{f.code}] {f.message}")
        if findings:
            print(f"\n[!] запрос/обращение к БД в цикле: {len(findings)} — "
                  "переделай пакетно (performance.md §2).", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
