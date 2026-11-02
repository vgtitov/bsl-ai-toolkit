#!/usr/bin/env python3
"""Проверка текста перед публикацией в публичный issue, PR, комментарий или карточку roadmap.

  python scripts/public_check.py черновик.md [--stoplist ~/приватно/stoplist.txt]
  cat черновик.md | python scripts/public_check.py -
  python scripts/public_check.py --self-test

Находит общие признаки внутренних данных (почта, телефон, IP, внутренние хосты, ключи и токены, личные пути)
и слова из приватного стоп-листа. Стоп-лист (имена организаций, людей, внутренних систем, конкурентов)
лежит ВНЕ репозитория: путь в --stoplist или env PUBLIC_STOPLIST, по слову или /регулярному выражению/ на строку,
`#` комментарий. В репозиторий список не кладём: он сам был бы утечкой.
Это не замена чтению: темы, которые скрипт не увидит (анализ других систем, сравнения, болевые вопросы),
перечислены в чек-листе в конце вывода. Код выхода: 0 чисто, 1 есть находки, 2 ошибка запуска.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

PATTERNS = [
    ("почта", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("телефон", re.compile(r"(?<![\w.])\+?\d[\d\s()-]{8,}\d(?![\w.])")),
    ("ip-адрес", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("внутренний хост", re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:local|lan|corp|internal|intra|intranet|localdomain)\b", re.I)),
    ("служебный поддомен", re.compile(r"\b(?:gitlab|jira|confluence|sd|wiki|sonar|zabbix|nexus|jenkins)\.(?!com\b|org\b|io\b)[\w-]+(?:\.[\w-]+)+", re.I)),
    ("ключ или токен", re.compile(r"\b(?:glpat-|ghp_|gho_|github_pat_|xox[bp]-|sk-[A-Za-z0-9]|AKIA)[\w-]{8,}|\b(?:password|passwd|token|secret|api[_-]?key)\s*[=:]\s*\S{6,}", re.I)),
    ("длинная случайная строка", re.compile(r"\b[A-Fa-f0-9]{40,}\b|\b[A-Za-z0-9+/_-]{40,}={0,2}(?=\s|$)")),
    ("личный путь", re.compile(r"/Users/[^/\s]+|/home/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+", re.I)),
]

CHECKLIST = """Чек-лист перед публикацией (скрипт этого не видит, читайте сами):
 [ ] нет анализа других систем и сравнений с конкурентами или чужими продуктами
 [ ] нет внутренних систем, баз, процессов, названий проектов и команд
 [ ] нет имён организаций и людей (кроме автора обсуждения)
 [ ] нет чувствительных и болевых тем, оценок, приоритетов, сроков и оценок трудозатрат
 [ ] примеры, логи и пути обезличены (вымышленные имена, не выгрузка из рабочей базы)
 [ ] формулировка через пользу людям и статус, а не через чужой опыт или нашу слабость"""


def load_stoplist(path: str | None) -> list[tuple[str, re.Pattern]]:
    path = path or os.environ.get("PUBLIC_STOPLIST") or ""
    if not path:
        return []
    out = []
    for raw in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        rx = re.compile(s[1:-1], re.I) if len(s) > 2 and s[0] == s[-1] == "/" else re.compile(r"(?<!\w)" + re.escape(s) + r"(?!\w)", re.I)
        out.append(("стоп-лист", rx))
    return out


def check(text: str, stop: list | None = None) -> list[tuple[int, str]]:
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for kind, rx in PATTERNS + (stop or []):
            if rx.search(line):
                found.append((n, kind))  # само значение не печатаем: оно может быть тем, что нельзя светить
    return found


def self_test() -> None:
    bad = {"a@b.ru": "почта", "+375 29 123-45-67": "телефон", "ip 10.1.2.3": "ip-адрес", "srv.corp.local": "внутренний хост",
           "https://gitlab.example.ru/x": "служебный поддомен", "token = abcdef123456": "ключ или токен",
           "/Users/ivan/x": "личный путь", "ghp_" + "a" * 20: "ключ или токен"}
    for t, kind in bad.items():
        assert kind in [k for _, k in check(t)], (t, check(t))
    assert check("Смена точности числа теряет флаг nonNegative в edit-type. Версия v2.7.3, коммит 4f9d115.") == []
    stop = [("стоп-лист", re.compile(r"(?<!\w)Секретинфо(?!\w)", re.I))]
    assert check("тут секретинфо есть", stop) == [(1, "стоп-лист")]
    assert check("тут секретинфоблок нет", stop) == []
    print("self-test ok")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="файл или - для stdin")
    ap.add_argument("--stoplist")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        self_test()
        return 0
    if not a.file:
        ap.error("нужен файл или -")
    try:
        text = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
        stop = load_stoplist(a.stoplist)
    except OSError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2
    if not stop:
        print("стоп-лист не задан (--stoplist или PUBLIC_STOPLIST): имена организаций и людей скрипт не ищет.", file=sys.stderr)
    found = check(text, stop)
    for n, kind in found:
        print(f"строка {n}: {kind}")
    print(CHECKLIST)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
