#!/usr/bin/env python3
"""Обезличивание текстов перед отправкой во внешние модели. Один файл, только stdlib.

  depersonalize(text, *, profile="toolkit", level="standard", kind="text", destination="external",
                data_class=None, dictionary=None, session=None, ...) -> Result
  restore(text, mapping) / restore_checked(text, mapping, sent_tokens=...)
  scan(text, dictionary=None) -> list[Leak]

Профиль toolkit: универсальный. Секреты и токены не обезличиваются, а блокируют результат. Карта соответствий только
локально, 0600. Словарь имён и компаний: необязательный JSON: --dict, переменная DEPERSONALIZE_DICT или --dict-auto
(файл .depersonalize.dict.json в текущем каталоге).

  python depersonalize.py FILE [--profile toolkit] [--level standard] [--out F] [--map-out F] [--dict D.json]
Коды возврата: 0 чисто, 3 утечки или секрет, 2 ошибка. Файлы читать и писать строго в UTF-8.
"""
from __future__ import annotations

import argparse
import base64
import bisect
import collections
import functools
import contextlib
import hashlib
import hmac
import html
import json
import math
import os
import re
import sys
import threading
import time
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ENGINE_VERSION = "1.0.0"
FORMAT_VERSION = 1
MAX_BYTES = 8 * 1024 * 1024
MAX_DICT = 5000
STATE_DIR = Path.home() / ".depersonalize"
HARD_CLASSES: set = set()
LEVELS = ("light", "standard", "strict")
KINDS = ("text", "code", "diff", "json", "config")
PH_RX = re.compile(r"\[([А-ЯЁA-Z]+)_(\d+)\]")


class _Deadline(threading.local):
    value = None            # у каждого потока свой дедлайн


_DL = _Deadline()
class _Stats(threading.local):
    def __init__(self):
        self.c = collections.Counter()                      # счётчики намеренно оставленных значений (версии, loopback), свои у каждого потока


_ST = _Stats()


class _StatsProxy:
    def __getattr__(self, name):
        return getattr(_ST.c, name)

    def __getitem__(self, k):
        return _ST.c[k]

    def __setitem__(self, k, v):
        _ST.c[k] = v

    def __iter__(self):
        return iter(_ST.c)

    def __bool__(self):
        return bool(_ST.c)

    def __len__(self):
        return len(_ST.c)


_STATS = _StatsProxy()


def _tick():
    """Кооперативный таймаут: проверка в длинных циклах (регулярные выражения линейные и прерываются между вызовами)."""
    if _DL.value is not None and time.monotonic() > _DL.value:
        raise TimeoutError("timeout")


def configure(state_dir=None):
    """Каталог состояния (карты, ключ, журнал)."""
    global STATE_DIR
    if state_dir:
        STATE_DIR = Path(state_dir).expanduser()


# ---------------------------------------------------------------- данные

@dataclass
class Dictionary:
    persons: list = field(default_factory=list)     # группы: [["Иван Петров", "Ваня"], ...]
    orgs: list = field(default_factory=list)
    users: list = field(default_factory=list)
    hosts: list = field(default_factory=list)
    terms: list = field(default_factory=list)       # любые прочие строки (точное совпадение)

    @classmethod
    def from_dict(cls, d: dict) -> "Dictionary":
        unknown = sorted(set(d) - set(cls.__dataclass_fields__))
        if unknown:                                          # неизвестные ключи не игнорируем молча: словарь мог рассчитывать на другую версию модуля
            raise ValueError("в словаре неизвестные ключи: " + ", ".join(map(str, unknown)))
        return cls(**{k: d.get(k) or [] for k in cls.__dataclass_fields__})

    @classmethod
    def from_json(cls, path) -> "Dictionary":
        p = Path(path)
        if p.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("dictionary_too_large")
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def size(self) -> int:
        n = 0
        for k in self.__dataclass_fields__:
            for x in getattr(self, k):
                n += len(x) if isinstance(x, (list, tuple)) else 1
        return n

    def too_big(self) -> bool:
        return self.size() > MAX_DICT or any(len(str(w)) > 200 for k in self.__dataclass_fields__ for x in getattr(self, k)
                                              for w in (x if isinstance(x, (list, tuple)) else [x]))


@dataclass
class Leak:
    kind: str           # категория: что это за остаток (значение не раскрывается)
    start: int          # кодовые точки Unicode строки clean
    end: int
    line: int
    col: int = 0

    def __str__(self):
        return f"{self.kind}@{self.line}:{self.col}"


@dataclass
class Result:
    ok: bool
    clean: str = ""
    mapping: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)
    leaks: list = field(default_factory=list)
    reason: str = ""
    review: bool = False
    review_token: str = ""
    sent_tokens: frozenset = frozenset()


@dataclass
class RestoreResult:
    text: str
    complete: bool
    unknown: list
    damaged: list
    missing: list


# ---------------------------------------------------------------- нормализация

_LAT2CYR = str.maketrans("abcehkmoptxy", "авсенкмортху")
_CYR2LAT = str.maketrans("авсенкмортху", "abcehkmoptxy")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = "".join(" " if unicodedata.category(c) == "Zs" else c for c in t if unicodedata.category(c) != "Cf")
    return t.replace("\r\n", "\n")


@functools.lru_cache(maxsize=6)
def _skel_big(s: str) -> str:
    return s.lower().replace("ё", "е").translate(_LAT2CYR)


def skel(s: str) -> str:
    """Длина сохраняется: нижний регистр, ё=е, латинские двойники кириллицы приведены к кириллице. Большие тексты кэшируются (один и тот же текст нормализуется много раз)."""
    return _skel_big(s) if len(s) > 20000 else s.lower().replace("ё", "е").translate(_LAT2CYR)


@functools.lru_cache(maxsize=4)
def _lat_big(s: str) -> str:
    return s.lower().translate(_CYR2LAT)


def _lat(s: str) -> str:
    return _lat_big(s) if len(s) > 20000 else s.lower().translate(_CYR2LAT)


# ---------------------------------------------------------------- проверки идентификаторов

def _luhn(d: str) -> bool:
    s, alt = 0, False
    for ch in reversed(d):
        n = int(ch)
        if alt:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        s += n
        alt = not alt
    return s % 10 == 0


def _inn_ok(d: str) -> bool:
    def cs(w):
        return sum(int(a) * b for a, b in zip(d, w)) % 11 % 10
    if len(d) == 10:
        return cs((2, 4, 10, 3, 5, 9, 4, 6, 8)) == int(d[9])
    if len(d) == 12:
        return cs((7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == int(d[10]) and cs((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == int(d[11])
    return False


def _iban_ok(s: str) -> bool:
    s = s.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    n = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(n) % 97 == 1


def _entropy(s: str) -> float:
    return -sum(s.count(c) / len(s) * math.log2(s.count(c) / len(s)) for c in set(s))


# ---------------------------------------------------------------- секреты

_TOKEN_RXS = [
    ("приватный ключ", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ("aws", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("gitlab", re.compile(r"\bglpat-[\w-]{16,}")),
    ("openai", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("slack", re.compile(r"\bxox[baprs]-[\w-]{10,}")),
    ("jwt", re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}")),
    ("telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("пароль в url", re.compile(r"://[^/\s:@]+:[^/\s@]{3,}@")),
]
_SECRET_KEY = (r"[\w.-]{0,40}(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|credential"
               r"|client[_-]?secret)[\w.-]{0,40}")
_KV_LAT = re.compile(r"(?i)(?<![\w])(?P<key>" + _SECRET_KEY + r")[\"']?\s*[:=]\s*(?P<val>[<\[{«][^>\]}»\n]{1,40}[>\]}»](?=[\s,;\"')]|$)|\"\"[^\"\n]*\"\"|\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)")
_KV_CYR = re.compile(r"(?i)(?<![\w])(?P<key>[\w.-]{0,40}(?:пароль|секрет|токен)[\w.-]{0,40})[\"']?\s*[:=]\s*(?P<val>[<\[{«][^>\]}»\n]{1,40}[>\]}»](?=[\s,;\"')]|$)|\"\"[^\"\n]*\"\"|\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)")
_KEY_SKIP = re.compile(r"(?i)(?:_|\b)(?:count|limit|length|len|ttl|name|field|label|id|type|url|file|path|policy|required|enabled|"
                       r"max|min|usage|num|expires?|hint)\w*$|^(?:max|num)_?tokens?$")
_SAFE_VAL = [re.compile(p) for p in (
    r"\$\{[A-Za-z_]\w*\}", r"\{\{\s*[A-Za-z_][\w.]*\s*\}\}", r"(?!)", r"\$[A-Za-z_]\w*", r"\$env:[A-Za-z_]\w*",
    r"os\.environ\[[\"'][A-Za-z_]\w*[\"']\]", r"(?:settings|config|conf|env|secrets|vault|self|this|process\.env)(?:\.[A-Za-z_]\w*)+", r"(?!)",
    r"[*xX•#]{3,}|…|\.\.\.", r"<[A-Za-zА-Яа-яЁё _-]{2,30}>", r"`(?:[A-Z][A-Z0-9_]{2,}|[a-z][a-z0-9]*(?:_[a-z0-9]+)+)`", r"(?i)none|null|nil|true|false|undefined|\"\"|''|%\d+|%[sd]|\{\d+\}|&[A-Za-zА-Яа-яЁё_]+|\$\d+|_\d{1,3}_|#\d{1,3}#|%\d{1,3}%|<\d{1,3}>|\[\d{1,3}\]", r"\[[А-ЯЁA-Z]+_\d+\]")]
_CODE_REFS = [re.compile(r"[A-Za-z_А-Яа-яЁё][\wА-Яа-яЁё]*(?:\.[A-Za-z_А-Яа-яЁё][\wА-Яа-яЁё]*)+"), re.compile(r"[A-Za-z_А-Яа-яЁё][\wА-Яа-яЁё.]*\(\s*(?:[A-Za-z_А-Яа-яЁё][\wА-Яа-яЁё]*)?\s*\)")]
_IDENT = re.compile(r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*")
_HEX = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{32,}(?![A-Za-z0-9])")
_B64 = re.compile(r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_=-]{32,}(?![A-Za-z0-9+/_=-])")


_BRACKET_RX = re.compile(r"[<\[{«][A-Za-zА-Яа-яЁё _.-]{2,40}[>\]}»]")
_PH_WORD = re.compile(r"(?i)(?<![A-Za-z])(?:pwd|pass(?:word)?|secret|token|key|user|name|value|redacted)(?![A-Za-z])|(?<![А-Яа-яЁё])(?:парол|секрет|токен|ключ|значени|имя|имен|пользовател|кластер|скрыт|ваш)")


class _BracketWords:
    """Скобочный плейсхолдер со словами: <cluster pwd>, [пароль кластера]; обязательно слово-метка подстановки (произвольный текст в скобках не плейсхолдер)."""

    @staticmethod
    def fullmatch(v):
        return _BRACKET_RX.fullmatch(v) if _PH_WORD.search(v) else None


_BRACKET_WORDS = _BracketWords()
_PW_WORDS = re.compile(r"x{3,}|X{3,}|\*{3,}")                  # только явные маски; слова «пароль», «password» могут быть настоящим паролем


def _prose_ru(lit: str) -> bool:
    """Текст сообщения: пять и более русских слов (от 30 знаков) через пробелы или подчёркивания, без цифр и спецзнаков. Потолок: длинная фраза-пароль."""
    return (len(lit) >= 12 and not re.search(r"\d", lit) and re.fullmatch(r"[А-Яа-яЁё\s_.,:;!?()«»\-]+", lit) is not None
            and len(re.findall(r"[А-Яа-яЁё]{2,}", lit)) >= 5 and len(lit) >= 30
            and lit.lstrip()[:1].isupper()                              # предложение начинается с заглавной; фраза-пароль обычно строчная
            and (re.search(r"[,.;:!?]|\s[-—]\s", lit) is not None or len(re.findall(r"[А-Яа-яЁё]{2,}", lit)) >= 9))   # знаки препинания или от девяти слов


def _safe_value(val: str, in_code: bool) -> bool:
    v = val.strip().rstrip(";,)")
    for q in ("\"", "'"):
        if v.endswith(q) and v.count(q) == 1:               # закрывающая кавычка строки, в которой стоит значение
            v = v[:-1]
    if _PW_WORDS.fullmatch(v.strip("\"'")) and not re.search(r"\d", v):
        return True
    if _BRACKET_WORDS.fullmatch(v.strip("\"'")) or (re.fullmatch(r"%\??[A-Za-zА-Яа-яЁё_.\-]{1,40}%", v.strip("\"'")) and _PH_WORD.search(v)):
        return True                                         # <cluster pwd>, [пароль кластера], текст сообщения из русских слов: не значение                                         # слово-заглушка в документации (Pwd=пароль, Pwd=password), не значение
    if len(v) >= 4 and v[:2] == '""' and v[-2:] == '""':
        v = '"' + v[2:-2] + '"'                             # BSL: "" внутри строки означает кавычку
    if v[:1] in "\"'" and v[-1:] == v[:1] and len(v) >= 2:
        v = v[1:-1]
        if not v:
            return True
        return any(r.fullmatch(v) for r in _SAFE_VAL[:5] + _SAFE_VAL[8:])     # внутри кавычек только шаблоны и маски
    if any(r.fullmatch(v) for r in _SAFE_VAL):
        return True
    if in_code and any(r.fullmatch(v) for r in _CODE_REFS):       # свойство и вызов без аргументов-литералов: ссылка только в коде, не в dotenv и тексте
        return True
    return in_code and bool(_IDENT.fullmatch(v)) and not re.search(r"\d", v) and len(v) <= 40   # в коде идентификатор без цифр: ссылка на переменную


_URL_MASK = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"')\]]+")
_PLURAL_KEYS = {"tokens", "secrets", "passwords", "credentials", "токены", "секреты", "пароли"}


def _stmt_end(text: str, pos: int):
    """Конец оператора: ; или перевод строки вне строкового литерала. None, если литерал не закрыт (разбор неясен: небезопасно)."""
    q = ""
    for i in range(pos, min(len(text), pos + 4000)):
        ch = text[i]
        if q:
            if ch == q:
                q = ""
        elif ch in "\"'":
            q = ch
        elif ch in ";\n":
            return i
    return None if q else min(len(text), pos + 4000)


def _nstr_text(lit: str) -> bool:
    """Содержимое НСтр: «ru = 'Текст'; en = 'Text'» - все тексты не секреты, если это русские или английские слова без цифр."""
    parts = re.findall(r"\b[a-z]{2,3}\s*=\s*'([^']*)'", lit)
    return bool(parts) and re.fullmatch(r"(?:\s*[a-z]{2,3}\s*=\s*'[^']*'\s*;?)+", lit.strip()) is not None and all(
        (len(re.findall(r"[А-Яа-яЁёA-Za-z]{2,}", x)) >= 3 and not re.search(r"\d", x) and " " in x) for x in parts)    # внутри НСтр уже подтверждён текст интерфейса


def _strip_first_call_args(v: str):
    """(выражение без аргументов первого вызова, аргументы) или (v, None), если вызова нет или скобки не сбалансированы."""
    m = re.match(r"\s*[\wА-Яа-яЁё.]+\s*\(", v)
    if not m:
        return v, None
    depth, q = 1, ""
    for k in range(m.end(), len(v)):
        ch = v[k]
        if q:
            q = "" if ch == q else q
        elif ch in "\"'":
            q = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return v[:m.end()] + v[k:], v[m.end():k]
    return v, None


def _split_top_args(args: str) -> list:
    out, cur, depth, q = [], [], 0, ""
    for ch in args:
        if q:
            q = "" if ch == q else q
        elif ch in "\"'":
            q = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return out


def _code_expr_safe(expr: str) -> bool:
    """Выражение кода (до ;): ссылка, вызов, пустая строка, плейсхолдер. Вызов вида Цепочка.Идентификаторов(…): литерал внутри аргументов
    допустим, если это имя (слово или идентификатор без цифр и символов): Хранилище.Получить(Ключ, "Токен"); форма пароля (цифры, символы,
    регистр с цифрами) секрет. У getenv/get/getattr литерал не в первом аргументе это значение по умолчанию (секрет). Литерал вне
    аргументов (getenv("X") or "пароль") и прямое присваивание строки секретом считаются."""
    if re.fullmatch(r"[-+]?(?:0[xX][0-9a-fA-F]{6,}|\d[\d_.,]{2,})", expr.strip()):
        return False                                        # числовой литерал из трёх и более знаков (PIN, пароль-число) не ссылка
    nstr = False                                            # НСтр(...) при присваивании секретному имени проверяется как обычный вызов; свойства интерфейса отсеяны по имени раньше
    v = re.sub(r"\[\s*\"[^\"\n]*\"\s*\]", "", expr)                  # индекс по строковому ключу Параметры["Пароль"]
    call = re.match(r"\s*([\wА-Яа-яЁё.]+)\s*\(", v)                              # справа вызов: цепочка идентификаторов, затем скобка
    forces_secret = bool(call and re.search(r"(?i)(?:^|\.)(?:str|string|bytes|bytearray|decode|encode|b64\w*|base64\w*|unhexlify|fromhex|строка|формат|format|join|"
                                            r"encrypt|decrypt|hash\w*|md5|sha\w*|secret\w*|token\w*|password\w*|пароль\w*|секрет\w*|токен\w*)$", call.group(1)))
    reader = call is not None and not forces_secret          # вызов, который сам кодирует или задаёт секрет (str, Base64, Строка, Format), литерал не оправдывает
    known_reader = bool(call and re.search(_READERS, call.group(1)))      # известное чтение настроек или хранилища: имя ключа любым словом
    default_style = bool(call and re.search(r"(?i)(?:^|\.)(?:getenv|get|getattr|environ|setdefault|pop|getordefault|\w*default\w*|\w*ИлиУстановить|\w*ПоУмолчанию\w*)$", call.group(1)))   # Python/JS: второй аргумент это значение по умолчанию
    outer, args = _strip_first_call_args(v)
    ok = lambda lit: (not lit.strip() or any(r.fullmatch(lit.strip()) for r in _SAFE_VAL[:5] + _SAFE_VAL[8:])      # noqa: E731
                      or _BRACKET_WORDS.fullmatch(lit.strip()) is not None)

    def lits(x: str):
        dq = re.findall(r'"((?:[^"\n]|"")*)"', x)
        rest = re.sub(r'"(?:[^"\n]|"")*"', '""', x)                  # одинарные кавычки ищем вне двойных (внутри НСтр они часть текста)
        return dq + re.findall(r"'((?:[^'\n]|'')*)'", rest)
    for lit in lits(outer):
        if not ok(lit):
            return False
    if args is not None:
        for n, arg in enumerate(_split_top_args(args)):
            for lit in lits(arg):
                nm = lit.strip()
                word = len(nm) <= 41 and re.fullmatch(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_. ]{0,40}", nm) is not None
                key2 = len(nm) <= 32 and len(re.findall(r"[A-ZА-ЯЁ][^A-ZА-ЯЁ_]*|[a-zа-яё]+", nm)) <= 3 and re.fullmatch(r"(?:[A-Z][a-z]+){2,}|(?:[А-ЯЁ][а-яё]+){2,}|[a-z]+(?:_[a-z]+)+|[A-Z]+(?:_[A-Z]+)+|[a-z]+(?:[A-Z][a-z]+)+", nm) is not None
                if ok(lit) or (reader and (n == 0 or not default_style) and ((word and known_reader) or key2)):
                    continue
                return False                                # литерал-имя (слово, идентификатор) безопасен в любом аргументе вызова; по форме пароль (цифры, символы) или значение по умолчанию getenv/get секрет
    return True


_READERS = (r"(?i)(?:^|\.)(?:получить\w*|прочитать\w*|прочесть\w*|читать\w*|загрузить\w*|значениенастройки\w*|значениепараметра\w*|"
            r"настройк\w*|хранилищ\w*|get|getenv|getattr|read\w*|load\w*|fetch\w*|lookup|resolve|settings?|config\w*|environ|param\w*)$")   # расширяемый список чтений
_UI_PROP = re.compile(r"\.(?:заголовок|подсказка|текстподсказки|текстсообщения|представление|заголовокстраницы|title|tooltip|caption|description|описание)$")
_FLAG = re.compile(r"(?i)(?<![\w-])--?[\w-]{0,30}?(?:password|passwd|(?<![A-Za-z])pass|pwd|secret|token|пароль|секрет|токен)(?:=|[ \t]+)(?P<val>[<\[{«][^>\]}»\n]{1,40}[>\]}»](?=[\s,;\"')]|$)|\"[^\"\n]*\"|'[^'\n]*'|[^\s\"']+)")


def _shape(v: str) -> str:
    """Форма значения без самого значения: я - кириллица, a/A - латиница, 9 - цифра."""
    out = []
    for ch in v[:24]:
        out.append("9" if ch.isdigit() else "я" if "а" <= ch.lower() <= "я" or ch in "ёЁ" else "A" if ch.isupper() else "a" if ch.isalpha() else "_")
    return "".join(out)


_LIST_ASSIGN = re.compile(r"(?i)[\w.]*(?:колонк|поля\b|поле\b|полей|columns?|fields?|names?\b|имена|список|перечень|реквизит|измерен|ресурс|таблиц|\blist\b)[\w.]*\s*[:=]\s*[\"'(\[]*$")
_LIST_HINT = re.compile(r"(?i)колонк|поля\b|поле\b|полей|columns?|fields?|names?\b|имена|список|перечень|реквизит|измерен|ресурс|таблиц|\blist\b")


def _name_like(t: str) -> bool:
    """Токен собран из слов в CamelCase/snake_case (имена колонок и полей), а не случайная строка: не длиннее 64 знаков, не меньше трёх слов
    по три и более букв, слова начинаются с заглавной либо целиком строчные или заглавные (аббревиатура до 6 букв: XML, HTTP), без однобуквенных
    обрывков, у слов с тремя и более строчными буквами есть гласная и нет четырёх согласных подряд, цифры одной-трёх короткими группами.
    Потолок: произносимый ключ из настоящих слов не распознаётся как случайный."""
    if len(t) > 64:
        return False
    parts = [x for x in re.split(r"[_\-]+", t) if x]
    words = []
    for part in parts:
        words += re.findall(r"[A-Z]{2,}(?=[A-Z][a-z])|[A-Z][a-z]+|[A-Z]{2,}|[a-z]+|\d+", part)
    if "".join(words) != "".join(parts):
        return False
    letters = [w for w in words if not w.isdigit()]
    digits = [w for w in words if w.isdigit()]
    long_suffix = len(digits) > 3                           # суффикс версии имени вида Имя_3_0_1_1: до пяти групп по 1-2 цифры после двух и более слов
    if (len(digits) > 5 or any(len(d) > (2 if long_suffix else 4) for d in digits) or any(len(w) < 2 for w in letters)
            or sum(len(w) >= 3 for w in letters) < (2 if long_suffix else 3)):
        return False
    for w in letters:
        if w.isupper():
            if len(w) > 6:
                return False                                # длинная заглавная строка: не аббревиатура
            continue
        if len(w) >= 3 and (not re.search(r"[aeiouyAEIOUY]", w) or re.search(r"[^aeiouyAEIOUY]{4}", w)):
            return False                                    # «слово» без гласных или с четырьмя согласными подряд: случайная строка
    return True


_OBJ_CHAIN = re.compile(r"(?=[^.]*[А-Яа-яЁё]|[A-Z])[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*(?:\.[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)+")


def find_secrets(text: str, kind: str = "text", strings=None, comments=None, where=None, details=None, fallback: bool = False) -> list:
    """Типы найденных секретов (значения не возвращаются). Пустой список: секретов нет. where: сюда дописываются номера строк."""
    found = set()

    def mark(name, pos, key="", val="", call=""):
        found.add(name)
        if where is not None:
            where.append(text.count("\n", 0, pos) + 1)
        if details is not None and len(details) < 20:                  # диагностика: где и какой формы, значение не раскрывается
            ls = text.rfind("\n", 0, pos) + 1
            details.append({"type": name, "line": text.count("\n", 0, pos) + 1, "col": pos - ls + 1, "key": key[:40], "len": len(val), "shape": _shape(val), **({"call": call[:60]} if call else {})})
    for view in (text, _lat(text)):
        for name, rx in _TOKEN_RXS:
            if rx.search(view):
                found.add(name)
    for rx, view in ((_KV_LAT, _lat(text)), (_KV_CYR, skel(text))):
        for m in rx.finditer(view):
            if _KEY_SKIP.search(m.group("key")):
                continue
            if kind == "code" and _UI_PROP.search(m.group("key").lower()):
                continue                                    # Элементы.СменитьПароль.Заголовок = ...: свойство интерфейса, не секрет
            pos = m.start("val")
            vtxt = text[m.start("val"):m.end("val")]
            sep = text[m.end("key"):m.start("val")]
            bare_word = re.fullmatch(r"[A-Za-zА-Яа-яЁё…,.:\-–—*`]+", vtxt) is not None
            if bare_word and "=" not in sep:               # проза «Токены: расход…», но не присваивание «пароль = ромашка»
                plural = m.group("key").lower().strip("_.-") in _PLURAL_KEYS
                more = re.match(r"[ \t]+[A-Za-zА-Яа-яЁё]", text[m.end("val"):m.end("val") + 3]) is not None
                if plural or (rx is _KV_CYR and more):
                    continue
            in_code = kind == "code" and not _in(strings, pos) and not _in(comments, pos)
            if fallback and kind == "code" and _OBJ_CHAIN.fullmatch(text[m.start("val"):m.end("val")].rstrip(";,)")):
                lo_ = max(0, m.start() - 300)                          # окно вокруг совпадения: на очень длинных строках разбор не квадратичный
                r_ = text.rfind("\n", lo_, m.start())
                ls_ = r_ + 1 if r_ >= 0 else lo_
                ne_ = text.find("\n", m.end(), m.end() + 300)
                line_ = text[ls_:ne_ if ne_ >= 0 else m.end() + 300]
                outside = (r_ >= 0 or lo_ == 0) and text.count('"', ls_, m.start("key")) % 2 == 0     # граница строки вне окна: чётность неизвестна, считаем «внутри строки»
                connstr = re.search(r"(?i)\b(?:srvr|server|ref|usr|database)\s*=", line_) is not None and m.group("key").lower() in ("pwd", "password")
                if outside and not connstr:
                    continue                                # код разобран построчно: цепочка Объект.Свойство вне строкового литерала и вне строки подключения ссылка, не значение
            if kind == "code" and text[pos:pos + 1] in ("\"", "'") and not _in(strings, pos) and not _in(comments, pos) and \
                    (_in(strings, pos - 1)):
                continue                                    # «"Pwd=" + Пароль»: после = строка закончилась, дальше выражение кода, не значение
            if in_code:
                cut = _stmt_end(text, pos)
                if cut is not None and _code_expr_safe(text[pos:cut]):
                    continue                                # присваивание из переменной, свойства, вызова, пустой строки: имя «Пароль» не секрет
            tail = text[m.end("val"):m.end("val") + 40].split("\n")[0]
            if re.match(r"\s*(?:\+|\|\||\?\?|or\b|and\b|:-)", tail) or not _safe_value(text[m.start("val"):m.end("val")], in_code):
                cm_ = re.match(r"\s*([\wА-Яа-яЁё.]+)\s*\(", text[pos:pos + 120]) if (kind == "code" and in_code) else None      # имя вызова только из кода, не из строки или комментария
                mark("значение секретного поля", pos, m.group("key"), text[m.start("val"):m.end("val")], cm_.group(1) if cm_ else "")
    for m in _FLAG.finditer(text):                          # ключи командной строки: -pwd=значение, --password значение
        v = m.group("val")
        if v.startswith("-") or _safe_value(v, False) or re.fullmatch(r"[%&$][\w{}%]*", v):
            continue
        pos = m.start("val")
        if kind == "code" and text[pos:pos + 1] in ("\"", "'") and _in(strings, pos - 1) and not _in(strings, pos):
            continue                                        # значение закончилось вместе со строкой: дальше выражение кода
        mark("значение секретного поля", pos, "флаг командной строки", v)
    text_nu = _URL_MASK.sub(lambda m: " " * len(m.group()), text)       # идентификаторы в ссылках не секреты (сама ссылка заменяется)
    for m in _HEX.finditer(text_nu):
        ls = text.rfind("\n", 0, m.start()) + 1
        le = text.find("\n", m.end())
        line = text[ls:le if le >= 0 else len(text)]
        ok_ctx = (re.match(r"index [0-9a-f]{7,40}\.\.[0-9a-f]{7,40}", line) or re.fullmatch(r"commit [0-9a-f]{40}(?:[0-9a-f]{24})?\s*", line)
                  or (kind == "config" and re.match(r"CI_COMMIT_SHA=[0-9a-f]{40}(?:[0-9a-f]{24})?\s*$", line))
                  or text[max(0, m.start() - 7):m.start()] == "sha256:" and len(m.group()) == 64)
        if not ok_ctx and len(m.group()) == 32 and re.search(r"(?i)(?:guid|uuid|уникальныйидентификатор)[\s:=\"'(]{0,12}$", text[max(0, m.start() - 30):m.start()]):
            ok_ctx = True                                   # 128 бит рядом с GUID/UUID в коде 1С: идентификатор объекта, не ключ
        if not ok_ctx:
            mark("hex-ключ", m.start(), "", m.group())
    for m in _B64.finditer(text_nu):
        t = m.group()
        before = text_nu[max(0, m.start() - 30):m.start()].split("\n")[-1]
        if re.search(r"(?i)\b(?:id|идентификатор\w*|spreadsheet|folder|sheet|doc(?:ument)?)\b[\s:=`\"']*$", before):
            continue                                        # идентификатор файла или таблицы, не ключ
        if kind == "code" and not _in(strings, m.start()) and not _in(comments, m.start()):
            continue                                        # в коде токен возможен только в строке или комментарии; идентификаторы не токены
        ls3 = text_nu.rfind("\n", max(0, m.start() - 100), m.start()) + 1 or max(0, m.start() - 100)
        secret_ctx = re.search(r"(?i)(?:authorization|tokens?|secret|api[-_ ]?key|bearer|passw\w*|pwd|парол\w*|секрет\w*|токен\w*|ключ\w*)[\"'\]]*\s*[:=]\s*[\"'(\[{]*$",
                               text_nu[ls3:m.start()].rsplit(";", 1)[-1]) is not None          # секретный ключ непосредственно перед значением, в пределах оператора
        if _name_like(t) and not (secret_ctx and (re.search(r"\d", t) or kind == "code")):
            continue                                        # CamelCase из слов (перечни колонок и полей, имена) без секретного ключа перед значением
        if (re.search(r"\d", t) and re.search(r"[A-Za-z]", t) and not re.fullmatch(r"[0-9a-fA-F]+", t)
                and _entropy(t) >= 4.0 and t.count("/") + t.count("-") < 3):
            mark("высокоэнтропийный токен", m.start(), "", t)
    return sorted(found)


def _in(spans, pos) -> bool:
    if not spans:
        return False
    i = bisect.bisect_right(spans, (pos, 1 << 60)) - 1
    return i >= 0 and spans[i][0] <= pos < spans[i][1]


# ---------------------------------------------------------------- код: строки и комментарии

def _py_regions(text: str):
    import io
    import tokenize
    strings, comments = [], []
    lines = text.split("\n")
    offs = [0]
    for ln in lines:
        offs.append(offs[-1] + len(ln) + 1)

    def at(pos):
        return offs[pos[0] - 1] + pos[1]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            s0, e0 = at(tok.start), at(tok.end)
            if tok.type == tokenize.COMMENT:
                comments.append((s0 + 1, e0))
            elif tok.type == tokenize.STRING:
                body = tok.string
                m = re.match(r"(?i)[rbuf]{0,3}('{3}|\"{3}|'|\")", body)
                q = len(m.group(1))
                strings.append((s0 + m.end(1) - q + q, e0 - q))
            elif getattr(tokenize, "FSTRING_MIDDLE", None) is not None and tok.type == tokenize.FSTRING_MIDDLE:
                strings.append((s0, e0))
    except (tokenize.TokenError, IndentationError, SyntaxError) as e:
        row = getattr(e, "lineno", None) or (e.args[1][0] if len(e.args) > 1 and isinstance(e.args[1], tuple) and isinstance(e.args[1][0], int) else None)
        raise CodeParseError("ошибка токенизации Python", offs[row - 1] if row and row - 1 < len(offs) else None)
    return strings, comments


_BSL_CONT = re.compile(r"[ \t]*(?:\||//)")


class CodeParseError(ValueError):
    """Разбор кода не удался: что именно и где (позиция), без значений."""

    def __init__(self, what: str, pos: int | None = None):
        super().__init__("code_parse_failed")
        self.what, self.pos = what, pos


_SPECIAL = re.compile(r"[\"'/#\-]")


def code_regions(text: str, lang: str = "generic", partial: bool = False):
    """(строки, комментарии): списки (начало, конец) внутренностей. ValueError('code_parse_failed') при неясной разметке.
    partial=True: незакрытый /* не ошибка, третье значение True (для diff, где комментарий идёт через строки)."""
    if lang == "python" and not partial:
        return _py_regions(text)
    strings, comments, open_block = [], [], False
    n, i = len(text), 0
    line_c = {"bsl": ("//",), "python": ("#",), "pyline": ("#",), "sql": ("--",), "generic": ("//", "#", "--")}[lang]
    block = lang in ("sql", "generic")
    ticks = 0
    while i < n:
        mm = _SPECIAL.search(text, i)                      # прыжок к следующему значимому символу вместо шага по одному
        if mm is None:
            break
        i = mm.start()
        ticks += 1
        if not ticks & 0x3FFF:
            _tick()
        c = text[i]
        lc = next((m for m in line_c if text.startswith(m, i)), None)
        if lc:
            e = text.find("\n", i)
            e = n if e < 0 else e
            comments.append((i + len(lc), e))
            i = e
        elif block and text.startswith("/*", i):
            e = text.find("*/", i + 2)
            if e < 0:
                if not partial:
                    raise CodeParseError("незакрытый блочный комментарий", i)
                comments.append((i + 2, n))
                open_block = True
                break
            comments.append((i + 2, e))
            i = e + 2
        elif c in "\"'":
            q, j = c, i + 1
            if lang == "python" and text.startswith(q * 3, i):
                e = text.find(q * 3, i + 3)
                if e < 0:
                    raise CodeParseError("незакрытая тройная строка", i)
                strings.append((i + 3, e))
                i = e + 3
                continue
            if lang in ("bsl",) and c == "'":
                i += 1
                continue                                   # BSL: '20260101' литерал даты, не строка
            while True:
                if j >= n:
                    raise CodeParseError("незакрытый строковый литерал", i)
                d = text[j]
                if d == q:
                    if text[j + 1:j + 2] == q and lang in ("bsl", "sql", "generic"):
                        j += 2
                        continue
                    break
                if d == "\\" and lang in ("python", "pyline", "generic"):
                    j += 2
                    continue
                if d == "\n":
                    if lang == "bsl" and _BSL_CONT.match(text, j + 1):          # строка продолжения «|…» или комментарий «//» внутри многострочной строки
                        cm = re.compile(r"[ \t]*//").match(text, j + 1)
                        if cm:                                                    # строка-комментарий внутри литерала: кавычки в ней не считаются
                            nl = text.find("\n", cm.end())
                            j = n if nl < 0 else nl
                            continue
                        j += 1
                        continue
                    if lang in ("sql", "generic"):
                        j += 1
                        continue
                    raise CodeParseError("конец строки внутри литерала без продолжения «|»", j)
                j += 1
            strings.append((i + 1, j))
            i = j + 1
        else:
            i += 1
    return (strings, comments, open_block) if partial else (strings, comments)


def _fallback_regions(text: str, lang: str):
    """Файл целиком не разобрать: надёжного построчного разбора лексики не бывает (литералы и комментарии переходят через строки),
    поэтому обезличивается и проверяется весь текст как обычный (идентификаторы тоже; синтаксис не гарантируется). Отчёт: parse_fallback."""
    return [(0, len(text))], []


def _regions(text: str, lang: str, rep: dict):
    """(строки, комментарии); при неразборе файла целиком отчёт получает parse_error (тип, строка, колонка) и parse_fallback."""
    try:
        return code_regions(text, lang)
    except CodeParseError as e:
        ls = text.rfind("\n", 0, e.pos) + 1 if e.pos is not None else 0
        rep["parse_error"] = {"type": e.what, "line": text.count("\n", 0, e.pos) + 1 if e.pos is not None else None, "col": e.pos - ls + 1 if e.pos is not None else None}
        rep["parse_fallback"] = True
        rep.setdefault("warnings", []).append("код разобран построчно (parse_fallback): " + e.what)
        return _fallback_regions(text, lang)


def _merge(spans):
    out = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(e, out[-1][1]))
        else:
            out.append((s, e))
    return out


def diff_regions(text: str, lang: str):
    """Для diff: содержимое строк +/-/пробел как код (с состоянием блочного комментария для старой и новой стороны),
    остальное (заголовки, сообщение коммита) целиком."""
    spans, strings, comments, pos = [], [], [], 0
    carry = {"-": False, "+": False}
    for line in text.split("\n"):
        s, e = pos, pos + len(line)
        pos = e + 1
        if re.match(r"(?:diff --git|index |@@|--- |\+\+\+ )", line):
            if line.startswith(("diff", "---", "+++")):
                spans.append((s, e))
            carry = {"-": False, "+": False} if line.startswith("@@") else carry
            continue
        if line[:1] in ("+", "-", " "):
            sides = ("-", "+") if line[0] == " " else (line[0],)
            body, st = line[1:], ("pyline" if lang == "python" else lang)
            if len(sides) == 2 and carry["-"] != carry["+"]:
                continue                                    # стороны расходятся по блочному комментарию: строку не трогаем
            pre = "/*" if carry[sides[0]] and st in ("sql", "generic") else ""
            try:
                sg, cm, ob = code_regions(pre + body, st, partial=True)
            except ValueError:
                continue                                    # строка не разобрана: не eligible, утечка поймается проверкой
            for sd in sides:
                carry[sd] = ob
            off = s + 1 - len(pre)
            strings += [(off + a, off + b) for a, b in sg if b + off > s + 1]
            comments += [(max(off + a, s + 1), off + b) for a, b in cm]
        else:
            spans.append((s, e))
    return _merge(spans + strings + comments), sorted(strings), sorted(comments)


# ---------------------------------------------------------------- детекторы

_PATR = r"(?:ович|евич|ьич|овна|евна|ична|ьевна|ьевич|оглы|кызы)(?:а|у|ем|ом|е|ы|и|ой|ым)?"
_CAP = r"[А-ЯЁ][а-яё]{2,}"
_NOTFIRST = (r"(?!(?:Потом|Тогда|Затем|Вчера|Сегодня|Завтра|Здесь|Сейчас|Кстати|Также|Если|Когда|Вот|Тут|Там|Позже|Раньше|Ещё|Уже|Мне|Тебе|Ему|Ей|Нам|Вам|Им|Это|Эти|"
             r"Тот|Все|Всё|Для|При|После|Перед|Вместе|Спасибо|Привет|Добрый|Уважаемый|Дорогой|Коллеги|Позвонил|Написал|Ответил)\b)")      # слово фразы, не фамилия
_CUR = r"(?:руб\w*|р\.|BYN|BYR|USD|EUR|RUB|₽|\$|€|долл\w*|евро)"
_MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"

_FIXED = [   # (вид, regex, группа или 0, приоритет, проверка, порог уровня)
    ("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}"), 0, 1, None, "light"),
    ("URL", re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"')\]]+"), 0, 1, None, "light"),
    ("IP", re.compile(r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w]|\.\d)"), 0, 1,
     None, "light"),
    ("ТЕЛЕФОН", re.compile(r"(?<![\w+])(?:\+375|375|8[\s-]?0)[\s()-]*\d{2}[\s()-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)"), 0, 1, None, "light"),
    ("ТЕЛЕФОН", re.compile(r"(?<![\w+])(?:\+7|8)[\s()-]*\d{3}[\s()-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)"), 0, 1, None, "light"),
    ("ТЕЛЕФОН", re.compile(r"(?<![\w+])\+\d{1,3}[\s()-]*\d[\d\s()-]{7,13}\d(?!\d)"), 0, 2, None, "light"),
    ("ТЕЛЕФОН", re.compile(r"(?<![\w+.:-])(?:80|0)(?:25|29|33|44|17)[\s()-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?![\d:])"), 0, 1, None, "light"),     # РБ без +375
    ("ТЕЛЕФОН", re.compile(r"(?<![\w+.:-])\(?(?:25|29|33|44)\)?[\s-]\d{3}[\s-]\d{2}[\s-]\d{2}(?![\d:])"), 0, 2, None, "light"),            # (29) 123-45-67
    ("НИК", re.compile(r"(?<![\w/@.])@[A-Za-z]\w{4,31}"), 0, 1, None, "light"),
    ("ИНН", re.compile(r"(?i)\bинн\b\D{0,12}?(\d{12}|\d{10})(?!\d)"), 1, 1, None, "light"),
    ("УНП", re.compile(r"(?i)\bунп\b\D{0,12}?(\d{9})(?!\d)"), 1, 1, None, "light"),
    ("ИНН", re.compile(r"(?<!\d)(?:\d{12}|\d{10})(?!\d)"), 0, 3, _inn_ok, "light"),
    ("КАРТА", re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)"), 0, 1, lambda v: _luhn(re.sub(r"\D", "", v)), "light"),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), 0, 1, _iban_ok, "light"),
    ("ИДНОМЕР", re.compile(r"\b\d{7}[A-Z]\d{3}[A-Z]{2}\d\b"), 0, 1, None, "light"),
    ("ПАСПОРТ", re.compile(r"\b[A-Z]{2}\d{7}\b"), 0, 2, None, "light"),
    ("СНИЛС", re.compile(r"\b\d{3}-\d{3}-\d{3}[ -]\d{2}\b"), 0, 1, None, "light"),
    ("СЕРВЕР", re.compile(r"(?<![\w.-])([A-Za-z][\w-]*(?:\.[\w-]+)*):(?:15[4-9]\d)\b"), 1, 1, lambda v: v.lower() not in ("localhost",), "light"),   # сервер:порт кластера 1С
    ("СЕРВЕР", re.compile(r"(?i)\b(?:Srvr|Server|Data Source)\s*=\s*(?:\"{1,2})?([^\";\r\n]+)"), 1, 1, None, "light"),
    ("БАЗА", re.compile(r"(?i)\b(?:Ref|Database|Initial Catalog)\s*=\s*(?:\"{1,2})?([^\";\r\n]+)"), 1, 1, None, "light"),
    ("ПОЛЬЗОВАТЕЛЬ", re.compile(r"(?i)\b(?:Usr|Uid|User Id)\s*=\s*(?:\"{1,2})?([^\";\r\n]+)"), 1, 1, None, "light"),
    ("ПУТЬ", re.compile(r"(?i)\bFile\s*=\s*(?:\"{1,2})?([^\";\r\n]+)"), 1, 1, None, "light"),
    ("ПОЛЬЗОВАТЕЛЬ", re.compile(r"(?i)(?:/Users/|/home/|[A-Z]:\\Users\\)((?!Shared\b|Public\b|Default\b|runner\b|root\b)[^/\\\s]+)"), 1, 1, None, "light"),
    ("ЧЕЛОВЕК", re.compile(rf"\b{_NOTFIRST}{_CAP}[ \t]+{_CAP}[ \t]+[А-ЯЁ][а-яё]+{_PATR}\b"), 0, 3, None, "standard"),
    ("ЧЕЛОВЕК", re.compile(rf"\b{_NOTFIRST}{_CAP}[ \t]+[А-ЯЁ][а-яё]+{_PATR}\b(?:[ \t]+{_CAP}\b)?"), 0, 3, None, "standard"),
    ("ЧЕЛОВЕК", re.compile(rf"\b{_NOTFIRST}{_CAP}[ \t]+[А-ЯЁ]\.\s?[А-ЯЁ]\."), 0, 3, None, "standard"),
    ("ЧЕЛОВЕК", re.compile(rf"\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s+{_CAP}"), 0, 3, None, "standard"),
    ("АДРЕС", re.compile(r"\b(?:ул|пр|просп|пр-т|пер|бул|б-р|пл)\.?\s+[А-ЯЁ][\w.-]+(?:\s+[А-ЯЁ][\w-]+)?(?:,?\s*(?:д|дом)\.?\s*\d+[а-яА-Я/]?(?:\s*,?\s*(?:кв|оф)\.?\s*\d+)?)?"), 0, 3, None, "standard"),
    ("СУММА", re.compile(rf"(?<![\w.])\d{{1,3}}(?:[ ]\d{{3}})+(?:[.,]\d{{1,2}})?\s?{_CUR}(?![\w])|(?<![\w.])\d+(?:[.,]\d{{1,2}})?\s?{_CUR}(?![\w])|[$€]\s?\d[\d ,.]*\d"), 0, 3, None, "amounts"),
    ("ЧИСЛО", re.compile(r"(?<![\w.])\d{9,12}(?![\w])"), 0, 4, None, "strict"),
]
_DATES = [re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})\b"), re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
          re.compile(rf"\b\d{{1,2}}\s+({_MONTHS})\s+(\d{{4}})\b", re.I)]
_ENDINGS = {"", "ой", "ей", "ом", "ем", "ым", "им", "ою", "ею", "ах", "ях", "ам", "ям", "ами", "ями", "ов", "ев", "ых", "их",
            "а", "я", "у", "ю", "ы", "и", "е", "о", "ь", "й"}
_WORD = re.compile(r"[^\W\d_]+")
_FIRST = {n.replace("ё", "е") for n in "александр алексей анатолий андрей антон аркадий арсений артём артур борис вадим валентин валерий василий виктор виталий владимир владислав вячеслав геннадий георгий глеб григорий даниил денис дмитрий евгений егор иван игорь илья кирилл константин леонид максим михаил никита николай олег павел пётр роман руслан сергей станислав степан тимофей фёдор юрий ярослав яков александра алина алла анастасия анна антонина валентина валерия варвара василиса вероника виктория галина дарья диана евгения екатерина елена елизавета жанна инна ирина карина кристина ксения лариса лидия лилия людмила маргарита марина мария милена надежда наталья нина оксана олеся ольга полина раиса светлана софья татьяна ульяна юлия яна".split()}


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w[-1] in "аяйь" else w


_FIRST_STEMS = {_stem(n) for n in _FIRST}


class _Dict:
    """Скомпилированный словарь: основы со склонением и точные литералы."""

    def __init__(self, d: Dictionary | None):
        self.stems: dict = {}       # основа -> (вид, ключ, канон, точно)
        self.literals: list = []    # (вид, ключ, литерал)
        d = d or Dictionary()
        for gi, group in enumerate(d.persons):
            group = [group] if isinstance(group, str) else group
            canon = group[0] if group else ""
            for s in group:
                self._add("ЧЕЛОВЕК", f"p{gi}", canon, s)
        for s in d.orgs:
            if re.fullmatch(r"[^\W\d_]+", s.strip()):
                self._add("ОРГ", f"o{skel(s)}", s, s)
            elif s.strip():
                self.literals.append(("ОРГ", f"o{skel(s)}", s.strip()))
        for kind, lst in (("ПОЛЬЗОВАТЕЛЬ", d.users), ("ХОСТ", d.hosts), ("ТЕРМИН", d.terms)):
            for s in lst:
                if len(s.strip()) >= 2:
                    self.literals.append((kind, f"{kind}:{skel(s)}", s.strip()))
        lits = sorted(self.literals, key=lambda x: -len(x[2]))
        self.lit_rx = (re.compile("(?<![\\w.-])(?:" + "|".join(re.escape(skel(x[2])).replace("\\ ", r"\s+") for x in lits) + ")(?![\\w-])")
                       if lits else None)
        self.lit_by = {skel(x[2]): x for x in lits}

    def _add(self, kind, key, canon, s):
        words = _WORD.findall(skel(s))
        if len(words) > 1:
            for w in words:
                if len(w) >= 4:
                    self.stems.setdefault(_stem(w), (kind, key, canon, False))
        elif words:
            w = words[0]
            if len(w) >= 4:
                self.stems.setdefault(_stem(w), (kind, key, canon, False))
            elif len(w) >= 2:
                self.stems.setdefault(w, (kind, key, canon, True))

    def match_word(self, w: str):
        return _lookup(self.stems, w)


_NAME_END = {"", "а", "я", "у", "ю", "е", "ой", "ей", "ом", "ем", "ою", "ею", "ы", "и", "ь", "й"}      # без -ов/-ев: Петров не имя Пётр


@functools.lru_cache(maxsize=1 << 16)
def _is_first(w: str) -> bool:
    return len(w) >= 4 and any(w[:cut] in _FIRST_STEMS and w[cut:] in _NAME_END for cut in range(len(w), max(2, len(w) - 3), -1))


_LAT_FIRST = set("""ivan sergey sergei dmitry dmitriy alexander aleksandr alexey andrey andrei anton artem boris denis egor evgeny igor ilya kirill
konstantin maxim michael mikhail nikita nikolay oleg pavel peter roman ruslan vadim victor viktor vladimir vladislav yuri yury
anna maria elena olga natalia natalya tatiana tatyana julia yulia irina svetlana ekaterina daria diana john james robert william
thomas daniel david paul mark alex""".split())
_SPEECH = re.compile(r"(?i)(?:писал[аи]?|ответил[аи]?|спросил[аи]?|сказал[аи]?|согласовал[аи]?|просил[аи]?|попросил[аи]?|звонил[аи]?|"
                     r"уточнил[аи]?|подтвердил[аи]?|предложил[аи]?|отметил[аи]?|решил[аи]?|сообщил[аи]?|написать|спросить|передать|"
                     r"уточнить|ответить|позвонить|напомнить|по словам|со слов|сообщить|подсказал[аи]?|возразил[аи]?)[ \t]*[—–:,-]?[ \t]+")
_SUR = re.compile(r"[а-я]{2,}(?:ов|ев)(?:а|у|ым|ом|ой|ы|е|ых)?|[а-я]{2,}(?:ин|ын)(?:у|ым|ом|ой|ы|е|ых)?|[а-я]{2,}(?:ск|цк)(?:ий|ого|ому|им|ом|ая|ой|ую|ие|их)|"
                  r"[а-я]{2,}енко(?:м|у)?|[а-я]{2,}(?:чук|юк|ук)(?:а|у|ом|е)?|[а-я]{2,}(?:ов|ев)ич(?:а|у|ем)?")
_NOT_SUR = re.compile(r"магазин|господин|витамин|румын|армян|грузин|ческ|ическ|минск|москов|русск|белорусск|украинск|польск|европейск|"
                      r"американск|китайск|английск|немецк|французск|итальянск|испанск|греческ|латинск|славянск|казахск|литовск|"
                      r"латвийск|эстонск|грузинск|армянск|ростов|псков|львов|киров|харьков|ковров|горьк|мужск|женск|городск|"
                      r"сельск|общественн|человеческ|штатив|учебник|ботинок|глазов|коров|свинов|дробин|былин|причин|картин|"
                      r"витрин|машин|пружин|лавин|долин|равнин|кабин|рутин|бобин|ватин|дисциплин|медицин|гостин|тропин")


_NOT_SUR = re.compile(_NOT_SUR.pattern + r"|основ|слов|остров|обнов|плагин|админ|корзин|истин|тишин|длин|клиентск|консьюмер|файлов|этапов|"
                      r"строков|размер|ордер|таймер|сервер|бандл|шагов|узлов|блоков|модулов|заголов|образов|прототип|протокол|"
                      r"продуктов|проектов|вопросов|пунктов|логин|остин|пастырск|хозяин|часов|черновик|боев|достов|боевик|участков|производств")


@functools.lru_cache(maxsize=1 << 16)
def _notsur(w: str) -> bool:
    return _NOT_SUR.search(w) is not None


def _sentence_start(text: str, pos: int) -> bool:
    """Слово не в середине фразы: начало текста, после знака конца, разметки (#, *, |, `, >) или тире.
    Внутри фразы: после буквы, цифры, запятой, закрывающей скобки."""
    j = pos - 1
    while j >= 0 and text[j] in " \t(«\"'`":
        j -= 1
    return j < 0 or not (text[j].isalnum() or text[j] in ",)»")


def _name_heur(text, sk, out, dyn):
    """Имена без словаря и NER: Имя Фамилия по списку частых имён, фамилия по окончанию и после глаголов общения."""
    caps = [(m.start(), m.end()) for m in re.finditer(r"[А-ЯЁ][а-яё]{2,}", text)]
    speech_ends = {m.end() for m in _SPEECH.finditer(text)}                                 # где сразу после глагола общения идёт слово
    toks = collections.Counter(m.group() for m in re.finditer(r"[А-Яа-яЁё]+", text))             # один проход: слова текста
    stems_cnt = collections.Counter(_stem(t.lower().replace("ё", "е")) for t in toks.elements()) if len(toks) < 200000 else collections.Counter()
    taken = set()
    for (s1, e1), (s2, e2) in zip(caps, caps[1:]):
        if not re.fullmatch(r"[ \t]+", text[e1:s2]):
            continue
        w1, w2 = sk[s1:e1], sk[s2:e2]
        f1, f2 = _is_first(w1), _is_first(w2)
        if f1 == f2:
            continue
        sur, a, b = (w2, s2, e2) if f1 else (w1, s1, e1)
        lowseen = (text[a].lower() + text[a + 1:b]) in toks
        if (lowseen and not f1) or (not f1 and _NOT_SUR.search(sur)) or (not f1 and not _SUR.fullmatch(sur) and _sentence_start(text, s1)):
            continue                                       # «Если Роман», «Вот Егора»: перед именем слово фразы, не фамилия
        st = _stem(sur)
        _cand(out, s1, e2, "ЧЕЛОВЕК", "s" + st, text[s1:e2], 3)
        taken.update((s1, s2))
        if len(st) >= 4:
            dyn[st] = ("ЧЕЛОВЕК", "s" + st, text[s1:e2], False)
    for k, (s1, e1) in enumerate(caps):
        if not k & 0x3FFF:
            _tick()
        if s1 in taken or e1 - s1 < 5:
            continue
        w = sk[s1:e1]
        if _notsur(w) or _is_first(w):
            continue
        ctx = s1 in speech_ends
        sfx = _SUR.fullmatch(w) is not None
        linehead = sfx and bool(re.match(r"[ \t]*(?::|—|–| - )", text[e1:e1 + 4])) and (s1 == 0 or text[s1 - 1] == "\n")
        common = (text[s1].lower() + text[s1 + 1:e1]) in toks
        if sfx and not common and e1 - s1 >= 5 and _sentence_start(text, s1):
            sfx_rep = stems_cnt.get(_stem(w), 0) >= 2                                              # повторяется в документе: имя, не начало фразы
        else:
            sfx_rep = False
        if (sfx and not common and e1 - s1 >= 5 and not _sentence_start(text, s1)) or sfx_rep or (ctx and not _is_first(w) and not _NOT_SUR.search(w)) or linehead:
            st = _stem(w)
            _cand(out, s1, e1, "ЧЕЛОВЕК", "s" + st, text[s1:e1], 4)
            if len(st) >= 4:
                dyn[st] = ("ЧЕЛОВЕК", "s" + st, text[s1:e1], False)
    for m in re.finditer(r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b", text):
        a, b = m.group(1).lower(), m.group(2).lower()
        if (a in _LAT_FIRST) != (b in _LAT_FIRST):
            sur = b if a in _LAT_FIRST else a
            _cand(out, m.start(), m.end(), "ЧЕЛОВЕК", "s" + sur, m.group(), 3)


def _lookup(stems: dict, w: str):
    for cut in range(len(w), max(1, len(w) - 4), -1):
        e = stems.get(w[:cut])
        if e and (w[cut:] in _ENDINGS) and (not e[3] or cut == len(w)):
            return e
    return None


def _cand(out, s, e, kind, key, orig, prio):
    out.append((s, e, kind, key, orig, prio))


def _detect(text: str, dc: _Dict, profile: str, level: str, dyn: dict, ner_spans) -> list:
    out = []
    lv = LEVELS.index(level)
    for kind, rx, grp, prio, chk, thr in _FIXED:
        _tick()
        if thr == "amounts":
            if not (level == "strict" or False):
                continue
        elif LEVELS.index(thr) > lv:
            continue
        for m in rx.finditer(text):
            v = m.group(grp)
            if not v or "\x00" in v or (chk and not chk(v)) or PH_RX.fullmatch(v.strip()):
                continue
            if kind == "IP":
                if v.startswith(("127.", "0.0.0.0")):
                    _STATS["ip_loopback"] += 1
                    continue
                ctx = text[max(0, m.start(grp) - 40):m.start(grp)].lower().split("\n")[-1]
                host_ctx = re.search(r"srvr|server|host|адрес|url|http|\bip\b|сервер|узел|ssh|порт", ctx)
                ver_ctx = re.search(r"(?<![\wа-яё-])(?:верси\w*|релиз|version|release|платформ\w*|сборк\w*|build|ver\.?|v\.?)[\s:=\"'(]{0,6}$", ctx)
                ver_loose = re.search(r"(?<![\wа-яё-])(?:верси\w*|релиз|version|release|платформ\w*|ver\.?)\b[^,;\n\d]{0,24}[:=|—–-]\s*$", ctx) is not None   # метка, слово-два и разделитель (двоеточие, ячейка таблицы, тире) прямо перед значением
                host_adj = re.search(r"(?<![\wа-яё])(?:srvr|server|host|адрес|url|ip|сервер|узел)[\s:=\"'(/]{0,6}$", ctx)
                platform = re.fullmatch(r"8\.3\.(?:1\d|2\d)\.\d{1,4}", v) is not None        # платформа 1С 8.3.10-8.3.29
                if (ver_ctx and not host_adj) or (platform and not host_ctx) or (ver_loose and not host_ctx):
                    _STATS["ip_version"] += 1
                    _STATS["version_like"] += 1
                    continue                                # номер версии, не адрес (ceiling: адрес 8.x.y.z без контекста хоста не заменится)
            key = re.sub(r"\D", "", v) if kind in ("ТЕЛЕФОН", "КАРТА", "ИНН", "УНП") else v.lower().strip()
            if kind == "ЧЕЛОВЕК":
                allw = _WORD.findall(skel(v))
                if "." not in v.split()[0] and allw and _is_first(allw[0]) and len(allw) >= 2:
                    key = "n" + "|".join(_stem(x) for x in allw)     # «Имя Отчество»: ключ по всему имени, разные люди с одним именем не сливаются
                else:
                    w = _WORD.findall(skel(v.split()[0] if "." not in v.split()[0] else v.split()[-1]))
                    st = _stem(w[0]) if w else key
                    key = "s" + st
                    if len(st) >= 4 and level != "light":
                        dyn[st] = ("ЧЕЛОВЕК", key, v, False)
            _cand(out, m.start(grp), m.end(grp), kind, key, v, prio)
    sk = skel(text)
    if lv >= 1:
        _name_heur(text, sk, out, dyn)
    for k, m in enumerate(_WORD.finditer(sk)):
        if not k & 0x3FFF:
            _tick()
        e = dc.match_word(m.group())
        if e:
            _cand(out, m.start(), m.end(), e[0], e[1], e[2] or text[m.start():m.end()], 2)
    if dc.lit_rx:
        for m in dc.lit_rx.finditer(sk):
            lit = dc.lit_by.get(re.sub(r"\s+", " ", m.group())) or next((x for x in dc.literals if skel(x[2]).split() == m.group().split()), None)
            if lit:
                _cand(out, m.start(), m.end(), lit[0], lit[1], lit[2], 2)
    if level == "strict":
        for rx in _DATES:
            for m in rx.finditer(text):
                _cand(out, m.start(), m.end(), "ДАТА", "", m.group(), 3)
    for s, e, k in ner_spans:
        _cand(out, s, e, k, "n" + skel(text[s:e]), text[s:e], 4)
    return out


def _resolve(cands: list, elig) -> list:
    cands = sorted(cands, key=lambda c: (c[0], -(c[1] - c[0]), c[5]))
    out, end = [], -1
    for c in cands:
        if c[0] < end:
            continue
        if elig is not None and not _inside(elig, c[0], c[1]):
            continue
        out.append(c)
        end = c[1]
    return out


def _inside(elig, s, e) -> bool:
    i = bisect.bisect_right(elig, (s, 1 << 60)) - 1
    return i >= 0 and elig[i][0] <= s and e <= elig[i][1]


# ---------------------------------------------------------------- NER (необязательно)

_NER = {}


def ner_available() -> bool:
    try:
        import natasha  # noqa: F401
        return True
    except Exception:
        return False


def _ner(text: str, level: str):
    if "m" not in _NER:
        from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter
        emb = NewsEmbedding()
        _NER.update(m=(Segmenter(), NewsNERTagger(emb), Doc))
    seg, tag, Doc = _NER["m"]
    d = Doc(text)
    d.segment(seg)
    d.tag_ner(tag)
    kinds = {"PER": "ЧЕЛОВЕК", "ORG": "ОРГ"}
    if level == "strict":
        kinds["LOC"] = "МЕСТО"
    return [(s.start, s.stop, kinds[s.type]) for s in d.spans if s.type in kinds]


# ---------------------------------------------------------------- хранилище состояния

def _state(sub: str = "") -> Path:
    d = (STATE_DIR / sub) if sub else STATE_DIR
    d = Path(d).expanduser().absolute()
    if d in (Path.home(), Path(d.anchor), Path.cwd()):
        raise OSError("bad_state_dir")
    return _safe_dir(d)


def _safe_dir(path: Path) -> Path:
    path = Path(path).expanduser().absolute()
    home = Path.home()

    base = home if home in path.parents or path == home else Path(path.anchor)
    cur = base
    for part in path.relative_to(base).parts:
        cur = cur / part
        if cur.is_symlink():
            raise OSError("symlink_in_path")
        if not cur.exists():
            os.mkdir(cur, 0o700)
    if hasattr(os, "getuid"):
        st = os.lstat(cur)
        if st.st_uid != os.getuid():
            raise OSError("foreign_owner")
        if st.st_mode & 0o077:
            os.chmod(cur, 0o700)
    return cur


def _fsync_dir(d: Path):
    if hasattr(os, "O_DIRECTORY"):
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _publish(d: Path, name: str, data: bytes, purpose: str = "file") -> bool:
    """Атомарно создаёт d/name с содержимым data (0600), не заменяя существующий. False: файл уже есть."""
    tmp = d / f".depersonalize-{purpose}-tmp-{uuid.uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, d / name)
        except FileExistsError:
            return False
        _fsync_dir(d)
        return True
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


@contextlib.contextmanager
def _lock(path: Path, timeout: float = 30.0):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    try:
        if os.fstat(fd).st_size < 1:
            os.write(fd, b"\0")
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.lseek(fd, 0, 0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise OSError("map_locked")
                time.sleep(0.05)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                os.lseek(fd, 0, 0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _purge(d: Path, ttl_default: int = 86400):
    """Просроченные карты удаляются под блокировкой сессии с повторным чтением; битые уходят в карантин (.bad)."""
    now = time.time()
    for p in list((d / "maps").glob("*.json")):
        try:
            with _lock(p.with_suffix(".lock"), timeout=2.0):
                try:
                    meta = json.loads(p.read_text(encoding="utf-8"))
                    created, ttl = meta["created"], meta.get("ttl", ttl_default)
                    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (created, ttl)) or ttl <= 0:
                        raise ValueError("bad_meta")
                    if now - created > ttl:
                        p.unlink()
                except (ValueError, KeyError, TypeError):
                    with contextlib.suppress(OSError):
                        p.rename(p.with_suffix(".bad"))
        except OSError:
            continue
    for p in d.glob(".depersonalize-*-tmp-*"):
        with contextlib.suppress(OSError):
            if now - p.stat().st_mtime > 3600:
                p.unlink()


_EPHEMERAL_KEY = []


def _key(required: bool = True) -> bytes:
    """Ключ аудита и токенов. Недоступное хранилище: при required отказ (не молчаливая замена случайным ключом)."""
    try:
        d = _state()
        kp = d / "audit.key"
        for _ in range(3):
            if kp.exists():
                k = kp.read_bytes()
                if len(k) != 32:
                    raise OSError("audit_key_invalid")
                return k
            _publish(d, "audit.key", os.urandom(32), "key")
        raise OSError("audit_key_unavailable")
    except OSError:
        if required:
            raise
        return b"\0" * 32


class Session:
    """Карта соответствий сессии: одно значение, один плейсхолдер. persist: хранить в STATE_DIR/maps/<id>.json."""

    def __init__(self, id: str | None = None, persist: bool = False, ttl: int = 86400):
        if id is not None and not re.fullmatch(r"[0-9a-f]{32}", id):
            raise ValueError("bad_session_id")
        if not isinstance(ttl, (int, float)) or isinstance(ttl, bool) or not 1 <= ttl <= 30 * 86400:
            raise ValueError("bad_ttl")
        self._mu = threading.RLock()
        self.id, self.persist, self.ttl = id or uuid.uuid4().hex, persist, ttl
        self.items: dict = {}      # (вид, ключ) -> (плейсхолдер, оригинал)
        self.counters: dict = {}
        self.revision = 0
        self._loaded_rev = 0
        if persist:
            d = _state("maps")
            _purge(STATE_DIR)
            p = d / f"{self.id}.json"
            if p.exists():
                self._load(p)

    def _load(self, p: Path):
        meta = json.loads(p.read_text(encoding="utf-8"))
        self.revision = self._loaded_rev = meta["revision"]
        self.ttl = meta.get("ttl", self.ttl)
        for ph, v in meta["items"].items():
            self.items[(v["kind"], v["key"])] = (ph, v["orig"])
            k, n = PH_RX.fullmatch(ph).groups()
            self.counters[k] = max(self.counters.get(k, 0), int(n))

    def reserve(self, text: str):
        with self._mu:
            for k, n in PH_RX.findall(text):
                self.counters[k] = max(self.counters.get(k, 0), int(n))

    def token(self, kind: str, key: str, orig: str) -> str:
        with self._mu:
            it = self.items.get((kind, key))
            if it:
                return it[0]
            n = self.counters.get(kind, 0) + 1
            self.counters[kind] = n
            ph = f"[{kind}_{n}]"
            self.items[(kind, key)] = (ph, orig)
            return ph

    def mapping(self) -> dict:
        with self._mu:
            return {ph: o for ph, o in self.items.values()}

    def save(self):
        if not self.persist:
            return
        d = _state("maps")
        name = f"{self.id}.json"
        with _lock(d / f"{self.id}.lock"):
            p = d / name
            if p.exists() and json.loads(p.read_text(encoding="utf-8"))["revision"] != self._loaded_rev:
                raise OSError("map_conflict")
            self.revision += 1
            meta = {"format_version": FORMAT_VERSION, "created": time.time(), "ttl": self.ttl, "revision": self.revision,
                    "items": {ph: {"kind": k, "key": key, "orig": o} for (k, key), (ph, o) in self.items.items()}}
            data = json.dumps(meta, ensure_ascii=False).encode("utf-8")
            if not p.exists():
                _publish(d, name, data, "map")
            else:
                tmp = d / f".depersonalize-map-tmp-{uuid.uuid4().hex}"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(data)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, p)
                finally:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp)
            self._loaded_rev = self.revision


def write_map(path, mapping: dict, allow_synced: bool = False):
    """Карта в произвольный файл: 0600, существующий файл не трогаем, symlink-компоненты запрещены."""
    p = Path(path).expanduser().absolute()
    probe = str(p.parent.resolve() / p.name) + "|" + str(p)
    if not allow_synced and re.search(r"(?i)onedrive|dropbox|google ?drive|icloud|yandex|яндекс|mobile documents|nextcloud", probe):
        raise OSError("synced_dir")
    d = _safe_dir(p.parent)
    if not _publish(d, p.name, json.dumps(mapping, ensure_ascii=False, indent=1).encode("utf-8"), "mapout"):
        raise OSError("map_out_exists")


# ---------------------------------------------------------------- ядро

def _lang_for(lang):
    return lang if lang in ("bsl", "python", "sql", "generic") else "generic"


def _blocked(reason, report, **kw) -> Result:
    return Result(ok=False, clean="", reason=reason, report=report, **kw)


def _fp(dictionary) -> str:
    if not dictionary:
        return "none"
    blob = json.dumps(dictionary.__dict__, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(_key(False), blob, hashlib.sha256).hexdigest()[:12]


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def _token_sig(res_parts: list) -> str:
    return hmac.new(_key(), "|".join(res_parts).encode("utf-8"), hashlib.sha256).digest()


def _make_token(parts, issued):
    sig = base64.urlsafe_b64encode(_token_sig(parts + [str(issued)])).decode().rstrip("=")
    return f"v1.{issued}.{sig}"


def _check_token(tok, parts) -> bool:
    m = re.fullmatch(r"v1\.(\d{9,11})\.([A-Za-z0-9_-]{43})", tok or "")
    if not m:
        return False
    issued = int(m.group(1))
    now = time.time()
    if issued > now + 60 or now - issued > 1800:
        return False
    return hmac.compare_digest(_make_token(parts, issued), tok)


def depersonalize(text: str, *, profile: str = "toolkit", level: str = "standard", kind: str = "text", lang: str = "generic",
                  destination: str = "external", data_class: str | None = None, dictionary: Dictionary | None = None,
                  session: Session | None = None, require_dictionary: bool | None = None, ner: str | None = None,
                  review_token: str | None = None, max_bytes: int = MAX_BYTES, timeout: float | None = None) -> Result:
    if timeout is not None and not (isinstance(timeout, (int, float)) and timeout >= 0 and timeout == timeout and timeout != float("inf")):
        raise ValueError("bad_timeout")
    _DL.value = (time.monotonic() + timeout) if timeout is not None else None
    _STATS.clear()
    try:
        res = _depersonalize(text, profile=profile, level=level, kind=kind, lang=lang, destination=destination, data_class=data_class,
                              dictionary=dictionary, session=session, require_dictionary=require_dictionary, ner=ner,
                              review_token=review_token, max_bytes=max_bytes)
        if _STATS:
            res.report["kept"] = dict(_STATS)                   # что намеренно оставлено и почему (без значений)
        if not res.ok:
            res.report["reason"] = res.reason or "unknown_block"        # причина дублируется в отчёте
        if not res.ok and not res.reason:
            res.reason = "unknown_block"
            res.report["hint"] = res.report.get("hint") or "блок без причины; сообщите отчёт (leak_details, secret_details) разработчику"
        return res
    except TimeoutError:
        return Result(ok=False, reason="timeout", report={"reason": "timeout", "profile": profile, "level": level, "kind": kind, "hint": "превышено время; для больших файлов --chunk или больший --timeout"})
    finally:
        _DL.value = None
        _skel_big.cache_clear()                              # кэши нормализации держат копии больших документов только на время вызова
        _lat_big.cache_clear()


def _depersonalize(text, *, profile, level, kind, lang, destination, data_class, dictionary, session, require_dictionary, ner,
                   review_token, max_bytes) -> Result:
    if profile != "toolkit" or level not in LEVELS or kind not in KINDS or destination not in ("local", "external"):
        raise ValueError("bad_arguments")
    lang = _lang_for(lang)
    ner = ner or ("require" if level == "strict" else "auto")
    rep = {"profile": profile, "level": level, "kind": kind, "destination": destination, "engine_version": ENGINE_VERSION,
           "format_version": FORMAT_VERSION, "counts": {}, "lossy": 0, "quasi": [], "normalized": False}
    if text.startswith("\ufeff"):                    # BOM выгрузки 1С: не подозрительный символ; снимается, в Result.clean его нет, при записи возвращать по report["bom"] (CLI так и делает)
        text = text[1:]
        rep["bom"] = True
    dc_name = (data_class or "").strip().lower()
    if dc_name in HARD_CLASSES:
        return _blocked(f"hard_block:{dc_name}", rep)
    if destination == "external" and not dc_name:
        return _blocked("data_class_required", rep)
    if len(text.encode("utf-8")) > max_bytes:
        return _blocked("input_too_large", rep)
    if dictionary and dictionary.too_big():
        return _blocked("dictionary_too_large", rep)
    need_dict = require_dictionary if require_dictionary is not None else False
    if need_dict and not (dictionary and dictionary.size()):
        return _blocked("dictionary_unavailable", rep)
    rep["dictionary"] = _fp(dictionary)
    if kind in ("code", "diff"):
        if any(unicodedata.category(c) == "Cf" for c in text):
            return _blocked("unicode_suspicious", rep)
    else:
        n = normalize(text)
        rep["normalized"] = n != text
        text = n
    dc = _Dict(dictionary)
    # --- разметка кода (до жёстких блоков: в коде имена функций и идентификаторы маркерами запрета не считаются)
    elig, strings, comments = None, [], []
    try:
        if kind == "code":
            strings, comments = _regions(text, lang, rep)
            elig = _merge(strings + comments)
        elif kind == "diff":
            elig, strings, comments = diff_regions(text, lang)
    except ValueError as e:
        return _blocked(str(e), rep)
    # --- секреты
    where: list = []
    sdet: list = []
    sec = find_secrets(text, kind, strings, comments, where, sdet, fallback=bool(rep.get("parse_fallback")))
    if sec:
        rep["secrets"] = sec
        rep["secret_details"] = sdet
        rep["secret_lines"] = sorted(set(where))[:20]
        rep["hint"] = ("в строках " + ", ".join(map(str, rep["secret_lines"])) + " найдено секретное значение; уберите его или замените "
                       "ссылкой (${NAME}, os.environ[...]); хэш коммита оформляйте строкой «commit <sha>» или сокращайте до 12 знаков") if where else \
            "найден секрет (токен или ключ); уберите его из текста"
        return _blocked("secret:" + ",".join(sec), rep)
    # --- NER
    use_ner = ner != "off" and level != "light"
    if use_ner and not ner_available():
        if ner == "require":
            return _blocked("required_detector_unavailable", rep)
        use_ner = False
    rep["ner"] = use_ner
    cov = (["dictionary"] if dictionary and dictionary.persons else []) + (["ner"] if use_ner else []) + (["heuristic"] if level != "light" else [])
    rep["names_coverage"] = cov or ["none"]
    if destination == "external" and not (set(cov) & {"dictionary", "ner"}):
        rep["warnings"] = ["имена покрыты только эвристикой, recall ограничен: подключите словарь имён или natasha" if cov
                           else "имена не обезличиваются (уровень light без словаря)"]
    sess = session or Session()
    sess.reserve(text)
    dyn: dict = {}
    # --- замены
    if kind == "json":
        try:
            obj = json.loads(text)
            dec = json.dumps(obj, ensure_ascii=False, indent=1)        # декодированные строки (\\uXXXX) проверяются на секреты повторно
        except ValueError:
            return _blocked("json_invalid", rep)
        except RecursionError:
            return _blocked("json_too_deep", rep)
        if dec != text:
            sec2 = find_secrets(dec, "config")
            if sec2:
                rep["secrets"] = sec2
                return _blocked("secret:" + ",".join(sec2), rep)

        def walk(o):
            if isinstance(o, str):
                return _apply(o, None, dc, profile, level, dyn, sess, use_ner, rep)
            if isinstance(o, list):
                return [walk(x) for x in o]
            if isinstance(o, dict):
                return {k: walk(v) for k, v in o.items()}
            return o
        try:
            clean = json.dumps(walk(obj), ensure_ascii=False, indent=2)
        except RecursionError:
            return _blocked("json_too_deep", rep)
    else:
        clean = _apply(text, elig, dc, profile, level, dyn, sess, use_ner, rep)
    # --- инварианты кода
    if kind == "code" and lang == "python":
        import ast
        try:
            ast.parse(text)
            try:
                ast.parse(clean)
            except SyntaxError:
                return _blocked("code_syntax_changed", rep)
        except SyntaxError:
            pass
    # --- проверка утечек
    elig2 = None
    try:
        if kind == "code":
            elig2 = _merge(sum(_fallback_regions(clean, lang) if rep.get("parse_fallback") else code_regions(clean, lang), []))
        elif kind == "diff":
            elig2 = diff_regions(clean, lang)[0]
    except ValueError:
        return _blocked("code_syntax_changed", rep)
    saved = dict(_STATS)
    leaks = _leaks(clean, dc, profile, level, ner and use_ner, sess, elig2)
    _STATS.clear()
    _STATS.update(saved)                                    # повторный проход проверки утечек счётчики не удваивает
    rep["leaks"] = len(leaks)
    if leaks:
        rep["leak_details"] = [{"category": x.kind, "line": x.line, "col": x.col} for x in leaks[:40]]
    present = {m.group(0) for m in PH_RX.finditer(clean)}
    mapping = {ph: o for ph, o in sess.items.values() if ph in present}
    sent = frozenset(m.group(0) for m in PH_RX.finditer(clean) if m.group(0) in mapping)
    res = Result(ok=not leaks, clean=clean if not leaks else "", mapping=mapping, report=rep, leaks=leaks,
                 reason="leaks" if leaks else "", sent_tokens=sent, review=bool(rep.get("warnings")))
    if leaks:
        return res
    # --- косвенные признаки
    if level == "strict" and rep["ner"] is False and ner == "off":
        res.review = True
    return res


def _eligible_cands(text, elig, make):
    """Детекторы идут только по строкам и комментариям кода (склеенным через перевод строки), позиции возвращаются в координатах text."""
    if elig is None:
        return make(text)
    segs = [(a, b) for a, b in elig if b > a]
    starts, proper, parts, pos, prev_end = [], [], [], 0, 0
    for a, b in segs:
        ctx = text[max(a - 40, prev_end):a]                   # код перед строкой (Srvr=, server =) нужен детекторам как контекст
        starts.append(pos)
        proper.append(pos + len(ctx))
        parts.append(ctx + text[a:b])
        pos += len(ctx) + b - a + 1
        prev_end = b
    out = []
    for c in make("\n".join(parts)):
        i = bisect.bisect_right(starts, c[0]) - 1
        a, b = segs[i]
        if c[0] >= proper[i] and c[1] - proper[i] <= b - a:
            off = a - proper[i]
            out.append((c[0] + off, c[1] + off) + tuple(c[2:]))
    return out


def _split_chunks(text: str, kind: str, lang: str, limit: int) -> list:
    """Куски по границам процедур (BSL), пустым строкам или заголовкам diff; каждый не короче limit, кроме последнего."""
    bsl_end = re.compile(r"(?im)^\s*Конец(?:Процедуры|Функции)\b.*\n")
    lines = text.splitlines(keepends=True)
    out, cur, size = [], [], 0
    inside = []
    if kind == "code":
        try:
            inside = _merge(sum(code_regions(text, lang), [])) if lang != "bsl" else _merge(code_regions(text, lang)[0])
        except ValueError:
            inside = []
    off = 0
    prev_lit = False
    py = kind == "code" and lang == "python"
    nl_rows = set()
    if py:
        import io
        import tokenize
        try:
            nl_rows = {t.start[0] for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type == tokenize.NEWLINE}
        except (tokenize.TokenError, IndentationError, SyntaxError):
            nl_rows = set()
    row = 0
    for ln in lines:
        row += 1
        line_start, off = off, off + len(ln)
        in_lit = bool(inside) and _in(inside, off - 1)      # перевод строки в конце этой строки внутри литерала: резать нельзя
        if py and size >= limit and not prev_lit and (row - 1) in nl_rows and ln[:1] and (ln[0].isalpha() or ln[0] in "@_") and \
                not ln.startswith(("else", "elif", "except", "finally")) and cur and not cur[-1].rstrip("\n").endswith("\\"):
            out.append("".join(cur))                       # Python: резать только перед оператором на нулевом отступе
            cur, size = [], 0
        prev_lit = in_lit
        if kind == "diff" and ln.startswith("diff --git") and size >= limit:
            out.append("".join(cur))
            cur, size = [], 0
        cur.append(ln)
        size += len(ln)
        if kind != "diff" and not py and not in_lit and (size >= limit * 4 or (size >= limit and (bool(bsl_end.match(ln)) if (kind == "code" and lang == "bsl") else not ln.strip()))):
            out.append("".join(cur))
            cur, size = [], 0
    if cur:
        out.append("".join(cur))
    return out


def depersonalize_chunked(text: str, *, chunk: int = 1 << 20, timeout: float | None = None, **kw) -> Result:
    """Большой файл частями с общей картой. Имена, найденные в одном куске, в других ищутся только по словарю и своим шаблонам."""
    if kw.get("kind") == "json":
        return Result(ok=False, reason="chunk_not_supported_for_json", report={})
    sess = kw.pop("session", None) or Session()
    snap = (dict(sess.items), dict(sess.counters))            # транзакция: при неуспехе карта сессии возвращается как была
    res = _chunked(text, chunk, timeout, sess, kw)
    if not res.ok:
        sess.items, sess.counters = snap[0], snap[1]
    return res


def _chunked(text, chunk, timeout, sess, kw) -> Result:
    t0 = time.monotonic()
    bom = text.startswith("\ufeff")
    body = text[1:] if bom else text
    clean, rep_all, leaks_all, mapping, sent = [], None, [], {}, set()
    review_any, warns = False, []
    for i, part in enumerate(_split_chunks(body, kw.get("kind", "text"), kw.get("lang", "generic"), chunk), 1):
        left = None if timeout is None else timeout - (time.monotonic() - t0)
        if left is not None and left <= 0:
            return Result(ok=False, reason="timeout", report={"reason": "timeout", "chunk": i, "hint": "превышено общее время обработки"})
        r = depersonalize(part, session=sess, max_bytes=max(chunk * 8, MAX_BYTES), timeout=left, **kw)
        if r.reason == "quasi_review_required":
            return Result(ok=False, reason="quasi_review_required", report={**r.report, "chunk": i, "hint": "подтверждение косвенных признаков недоступно в режиме --chunk (один токен на файл): обработайте файл без --chunk"})
        if not r.ok:
            r.report["chunk"] = i
            r.report.setdefault("hint", "")
            r.report["hint"] = (r.report["hint"] + f" (кусок {i})").strip()
            return r
        clean.append(r.clean)
        review_any = review_any or r.review
        for w in r.report.get("warnings", []):
            if w not in warns:
                warns.append(w)
        mapping.update(r.mapping)
        sent |= set(r.sent_tokens)
        if rep_all is None:
            rep_all = dict(r.report)
            rep_all["counts"] = dict(r.report.get("counts", {}))
            rep_all["chunks"] = 0
        else:
            for k, v in r.report.get("counts", {}).items():
                rep_all["counts"][k] = rep_all["counts"].get(k, 0) + v
            rep_all["lossy"] += r.report.get("lossy", 0)
        rep_all["chunks"] += 1
    rep_all = rep_all or {"profile": kw.get("profile", "toolkit"), "chunks": 0, "counts": {}}
    if bom:
        rep_all["bom"] = True
    if warns:
        rep_all["warnings"] = warns
    return Result(ok=True, clean="".join(clean), mapping=mapping, report=rep_all, sent_tokens=frozenset(sent), review=review_any or bool(warns))


def _apply(text, elig, dc, profile, level, dyn, sess, use_ner, rep) -> str:
    def make(sub):
        ns = _ner(sub, level) if use_ner and sub.strip() else []
        return _detect(sub, dc, profile, level, dyn, ns) + _detect_dyn(sub, dyn)       # фамилии из шаблонов ищем по всему тексту
    cands = _eligible_cands(text, elig, make)
    chosen = _resolve(cands, elig)
    extra = _repeats(text, chosen, sess)
    if extra:
        chosen = _resolve(cands + extra, elig)                 # то же значение в других местах строки тоже заменяется
    merged = []
    for c in chosen:                      # соседние части одного человека (Имя Фамилия) — один плейсхолдер
        if merged and c[2] == merged[-1][2] == "ЧЕЛОВЕК" and c[3] == merged[-1][3] and re.fullmatch(r"[ \t-]*", text[merged[-1][1]:c[0]]):
            merged[-1] = (merged[-1][0], c[1], c[2], c[3], merged[-1][4], merged[-1][5])
        else:
            merged.append(c)
    out, pos = [], 0
    for k, (s, e, kind, key, orig, _p) in enumerate(merged):
        if not k & 0x3FFF:
            _tick()
        out.append(text[pos:s])
        if kind == "ДАТА":
            out.append(_gen_date(text[s:e]))
            rep["lossy"] += 1
        else:
            out.append(sess.token(kind, key or ("v" + skel(text[s:e])), orig))
        rep["counts"][kind] = rep["counts"].get(kind, 0) + 1
        pos = e
    out.append(text[pos:])
    return "".join(out)


def _detect_dyn(text, dyn):
    out = []
    if dyn:
        for m in _WORD.finditer(skel(text)):
            e = _lookup(dyn, m.group())
            if e:
                out.append((m.start(), m.end(), e[0], e[1], e[2], 3))
    return out


def _gen_date(v: str) -> str:
    m = _DATES[0].fullmatch(v) or _DATES[1].fullmatch(v)
    if _DATES[0].fullmatch(v):
        d, mo, y = m.groups()
        return f"{int(mo):02d}.{y if len(y) == 4 else '20' + y}"
    if _DATES[1].fullmatch(v):
        y, mo, _d = m.groups()
        return f"{mo}.{y}"
    m = _DATES[2].fullmatch(v)
    return f"{m.group(1)} {m.group(2)}"




_CAT = {"ЧЕЛОВЕК": "остаточное имя человека", "ОРГ": "остаточное имя организации", "EMAIL": "email", "IP": "IP-подобное значение",
        "ТЕЛЕФОН": "телефон", "URL": "ссылка", "НИК": "ник", "ИНН": "ИНН", "УНП": "УНП", "КАРТА": "номер карты", "IBAN": "IBAN",
        "СЕРВЕР": "адрес сервера в строке подключения", "БАЗА": "имя базы в строке подключения", "ПОЛЬЗОВАТЕЛЬ": "имя пользователя",
        "ХОСТ": "имя хоста из словаря", "ТЕРМИН": "слово из словаря", "ПУТЬ": "путь", "АДРЕС": "адрес", "СУММА": "сумма", "ЧИСЛО": "длинное число",
        "ПАСПОРТ": "паспорт", "СНИЛС": "СНИЛС", "ИДНОМЕР": "личный номер", "МЕСТО": "место"}


def _word_index(sk: str) -> dict:
    idx: dict = {}                                          # слово -> позиции начала: поиск значения по самому редкому слову
    for m in re.finditer(r"\w+", sk):
        idx.setdefault(m.group(), []).append(m.start())
    return idx


def _occurrences(sk: str, idx: dict, o: str):
    """Вхождения значения o (skel) в sk с границами слов; число с точками внутри более длинного номера не считается."""
    parts = [(m.group(), m.start()) for m in re.finditer(r"\w+", o)]
    if not parts or not all(w in idx for w, _ in parts):
        return
    w, off = min(parts, key=lambda x: len(idx[x[0]]))
    numeric = re.fullmatch(r"[\d.]+", o) is not None
    for pos in idx[w]:
        st = pos - off
        if st < 0 or not sk.startswith(o, st):
            continue
        end_ = st + len(o)
        if (st and (sk[st - 1].isalnum() or sk[st - 1] == "_")) or (end_ < len(sk) and (sk[end_].isalnum() or sk[end_] == "_")):
            continue
        if numeric and (re.match(r"\.\d", sk[end_:end_ + 2]) or (st >= 2 and sk[st - 1] == "." and sk[st - 2].isdigit())):
            continue
        yield st, end_


_NO_PROPAGATE = {"ДАТА", "СУММА", "ЧИСЛО", "ЧЕЛОВЕК"}


def _repeats(text: str, chosen: list, sess) -> list:
    """Значение, заменённое в одном месте, заменяется и во всех остальных местах документа (имя пользователя из пути встречается в строке лога и отдельно).
    Намеренно по всему документу: согласованность важнее; исключены люди (склонения и словарь документа ведёт отдельный механизм), даты, суммы и числа."""
    vals = {}
    for c in chosen:
        if c[2] in _NO_PROPAGATE or c[2] == "ДАТА":
            continue
        v = skel(text[c[0]:c[1]])
        if len(v) >= 4 and not (v.isdigit() and len(v) < 7):
            vals.setdefault(v, (c[2], c[3], c[4]))
    for (kind, key), (ph, orig) in list(sess.items.items()):
        v = skel(orig)
        if kind not in _NO_PROPAGATE and len(v) >= 4 and not (v.isdigit() and len(v) < 7):
            vals.setdefault(v, (kind, key, orig))
    if not vals:
        return []
    sk = skel(text)
    idx = _word_index(sk)
    out, covered = [], {(c[0], c[1]) for c in chosen}
    for k, (v, (kind, key, orig)) in enumerate(vals.items()):
        if not k & 0xFF:
            _tick()
        for st, end_ in _occurrences(sk, idx, v):
            if (st, end_) not in covered:
                out.append((st, end_, kind, key, orig, 5))
    return out


def _leaks(clean, dc, profile, level, use_ner, sess, elig=None) -> list:
    """Остатки после замены. elig: для кода только строки и комментарии (имена внутри идентификаторов утечкой не считаются)."""
    masked = PH_RX.sub(lambda m: "\x00" * len(m.group(0)), clean)
    dyn: dict = {}
    found = []

    def make(sub):
        return _detect(sub, dc, profile, level, dyn, _ner(sub, level) if use_ner else []) + _detect_dyn(sub, dyn)
    for s, e, kind, _k, _o, _p in _eligible_cands(masked, elig, make):
        if kind != "ДАТА":
            found.append((s, e, _CAT.get(kind, kind)))
    if elig is not None:                                    # числовые ПДн в самом коде (телефон, карта, IBAN, СНИЛС): заменить нельзя, значит блок
        for kind_, rx_, grp_, _p, chk_, thr_ in _FIXED:
            if kind_ not in ("ТЕЛЕФОН", "КАРТА", "IBAN", "СНИЛС", "ИДНОМЕР") and not (kind_ in ("ИНН", "УНП") and grp_ == 1):
                continue
            for m in rx_.finditer(masked):
                v = m.group(grp_)
                if v and "\x00" not in v and (not chk_ or chk_(v)) and not _inside(elig, m.start(grp_), m.end(grp_)):
                    found.append((m.start(grp_), m.end(grp_), _CAT.get(kind_, kind_) + " в коде вне строк"))
    sk = skel(masked)
    idx = _word_index(sk)
    for k, (ph, orig) in enumerate(list(sess.items.values())):
        if not k & 0xFF:
            _tick()
        o = skel(orig)
        if len(o) < 4 or (o.isdigit() and len(o) < 7) or not kind_ok(ph):
            continue                                        # короткие значения совпадают с обычными словами и числами: утечкой не считаем
        for st, end_ in _occurrences(sk, idx, o):
            if elig is None or _inside(elig, st, end_):
                mk = PH_RX.fullmatch(ph)
                found.append((st, end_, f"остаток значения из карты ({mk.group(1) if mk else '?'}, длина {len(o)})"))
    nl = [i for i, c in enumerate(clean) if c == "\n"]
    seen, out = set(), []
    for s, e, k in sorted(found):
        if (s, e) not in seen:
            seen.add((s, e))
            line = bisect.bisect_left(nl, s) + 1
            out.append(Leak(k, s, e, line, s - (nl[line - 2] + 1 if line > 1 else 0) + 1))
    return out


def kind_ok(ph: str) -> bool:
    return not ph.startswith(("[СУММА", "[ЧИСЛО"))


def scan(text: str, dictionary: Dictionary | None = None, *, profile: str = "toolkit", level: str = "standard") -> list:
    """Что осталось в тексте (проверка без замены): список Leak без значений."""
    t = normalize(text)
    dc = _Dict(dictionary)
    masked = PH_RX.sub(lambda m: "\x00" * len(m.group(0)), t)
    found = {(s, e, k) for s, e, k, *_ in _detect(masked, dc, profile, level, {}, [])}
    out = [Leak(k, s, e, _line_of(t, s)) for s, e, k in sorted(found) if k != "ДАТА"]
    if find_secrets(masked):
        out.append(Leak("секрет", 0, 0, 1))
    return out


# ---------------------------------------------------------------- обратная подстановка

def restore(text: str, mapping: dict) -> str:
    return PH_RX.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def restore_checked(text: str, mapping: dict, sent_tokens=None, allowed_missing=()) -> RestoreResult:
    sent = set(sent_tokens if sent_tokens is not None else mapping)
    allowed = set(allowed_missing)
    if not allowed <= sent:
        raise ValueError("invalid_allowed_missing")
    present = {m.group(0) for m in PH_RX.finditer(text)}
    unknown = sorted(t for t in present if t not in mapping or t not in sent)
    kinds = sorted({PH_RX.fullmatch(t).group(1) for t in mapping}, key=len, reverse=True)
    damaged = []
    if kinds:
        loose = re.compile(r"\[?\s*(" + "|".join(map(re.escape, kinds)) + r")\s*[_ ]\s*(\d+)\s*\]?", re.I)
        for m in loose.finditer(text):
            tok = f"[{m.group(1).upper()}_{m.group(2)}]"
            if m.group(0) != tok or not re.fullmatch(r"\[[А-ЯЁA-Z]+_\d+\]", m.group(0)):
                damaged.append(m.group(0))
    missing = sorted(t for t in sent - present - allowed)
    return RestoreResult(restore(text, mapping), not (unknown or damaged or missing), unknown, sorted(set(damaged)), missing)


# ---------------------------------------------------------------- аудит

def audit(res: Result, destination: str, note: str = "") -> str:
    """Запись решения в локальный журнал (без текста и карты). note: только код причины [a-z0-9_:-]{0,40}, иное отбрасывается."""
    from datetime import datetime, timezone
    did = uuid.uuid4().hex
    rep = res.report or {}
    rec = {"id": did, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "ok": bool(res.ok),
           "reason": (res.reason or "")[:80], "destination": destination[:40], "profile": rep.get("profile"), "level": rep.get("level"),
           "kind": rep.get("kind"), "engine": ENGINE_VERSION, "dictionary": rep.get("dictionary"),
           "note": note if re.fullmatch(r"[a-z0-9_:-]{0,40}", note or "") else "",
           "counts": {str(k)[:20]: int(v) for k, v in list((rep.get("counts") or {}).items())[:40]}}
    if res.clean:
        rec["payload"] = hmac.new(_key(), res.clean.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    if len(line) > 4096:
        rec["counts"] = {}
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        if len(line) > 4096:
            raise ValueError("audit_record_too_large")
    d = _state()
    with _lock(d / "audit.lock"):
        p = d / "audit.jsonl"
        if p.exists() and p.stat().st_size:
            with open(p, "rb") as fh:
                fh.seek(max(0, p.stat().st_size - 1))
                if fh.read(1) != b"\n":
                    raise OSError("audit_corrupt")
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
        try:
            b = line
            while b:
                b = b[os.write(fd, b):]
            os.fsync(fd)
        finally:
            os.close(fd)
    return did


# ---------------------------------------------------------------- файлы и CLI

def read_docx(path) -> str:
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > 200:
            raise ValueError("docx_invalid")
        total, wanted = 0, {}
        for zi in infos:
            total += zi.file_size
            if zi.file_size and zi.compress_size == 0 or (zi.compress_size and zi.file_size / zi.compress_size > 100):
                raise ValueError("docx_invalid")
            if re.fullmatch(r"word/(?:document|header\d*|footer\d*|footnotes|endnotes)\.xml", zi.filename):
                if zi.filename in wanted:
                    raise ValueError("docx_invalid")
                wanted[zi.filename] = zi
        if total > 20 * 1024 * 1024 or "word/document.xml" not in wanted:
            raise ValueError("docx_invalid")
        parts = []
        for name in sorted(wanted):
            raw = z.open(wanted[name]).read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("docx_invalid")
            xml = raw.decode("utf-8", "replace")
            xml = re.sub(r"</w:p>", "\n", xml)
            xml = re.sub(r"<w:tab/>", "\t", xml)
            t = re.sub(r"<[^>]+>", "", xml)
            parts.append(html.unescape(t))
    return "\n".join(parts)


_EXT = {".diff": ("diff", "generic"), ".patch": ("diff", "generic"), ".json": ("json", "generic"), ".bsl": ("code", "bsl"),
        ".os": ("code", "bsl"), ".py": ("code", "python"), ".sql": ("code", "sql"), ".env": ("config", "generic"),
        ".ini": ("config", "generic"), ".yaml": ("config", "generic"), ".yml": ("config", "generic"), ".toml": ("config", "generic"),
        ".cfg": ("config", "generic")}


class InputError(Exception):
    """Файл не прочитан: reason (read_error, cloud_placeholder, encoding_unknown, docx_invalid, file_too_large, lock_file) и текст без значений."""

    def __init__(self, reason: str, message: str, stage: str = "read"):
        super().__init__(message)
        self.reason, self.message, self.stage = reason, message, stage


_CLOUD_ATTRS = 0x400000 | 0x1000 | 0x40000                  # RECALL_ON_DATA_ACCESS, OFFLINE, RECALL_ON_OPEN (Windows, облачные заглушки OneDrive)
_TEXT_EXT = set(_EXT) | {".txt", ".md", ".log", ".vtt", ".srt", ".csv", ".tsv", ".xml", ".html", ".docx", ".1c", ".epf"} - {".epf", ".1c"}


def _cloud_hint(path: Path) -> bool:
    try:
        return bool(getattr(os.stat(path), "st_file_attributes", 0) & _CLOUD_ATTRS)
    except OSError:
        return False


def decode_bytes(raw: bytes, forced: str | None = None):
    """(текст, кодировка, префикс BOM для записи). UTF-8 (в том числе с BOM, он остаётся в тексте), UTF-16 с BOM, затем cp1251 (эвристика)."""
    if forced:
        try:
            return raw.decode(forced), forced, b""
        except (LookupError, UnicodeDecodeError) as e:
            raise InputError("encoding_unknown", f"не удалось прочитать как {forced}: {type(e).__name__}", "decode")
    for bom, codec in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if raw[:2] == bom:
            try:
                return raw[2:].decode(codec), codec, bom
            except UnicodeDecodeError as e:
                raise InputError("encoding_unknown", f"UTF-16 повреждён (байт {e.start})", "decode")
    try:
        return raw.decode("utf-8"), "utf-8", b""
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1251"), "cp1251 (эвристика)", b""
    except UnicodeDecodeError as e:
        raise InputError("encoding_unknown", f"кодировка не определена (байт {e.start}); укажите --encoding", "decode")


def load_input(path: Path, forced: str | None = None):
    """Читает файл с понятными причинами отказа. Возвращает (текст, метка кодировки, BOM-префикс, codec для записи)."""
    if path.name.startswith("~$"):
        raise InputError("lock_file", "служебный файл блокировки Office (~$…), пропущен")
    try:
        if path.stat().st_size > 256 * 1024 * 1024:
            raise InputError("file_too_large", "файл больше 256 МБ; разделите его вручную")
        if path.suffix.lower() == ".docx":
            try:
                return read_docx(path), "docx", b"", "utf-8"
            except zipfile.BadZipFile:
                raise InputError("docx_invalid", "повреждённый или не докачанный .docx (zip не читается)", "docx")
            except ValueError as e:
                raise InputError("docx_invalid", f".docx отклонён: {e}", "docx")
        raw = path.read_bytes()
    except InputError:
        raise
    except OSError as e:
        if _cloud_hint(path) or e.errno == 22:
            raise InputError("cloud_placeholder", "файл недоступен для чтения (возможно облачная заглушка OneDrive: скачайте файл, «Всегда хранить на этом устройстве»)")
        raise InputError("read_error", f"файл не прочитан ({type(e).__name__}, errno {e.errno})")
    text, label, bom = decode_bytes(raw, forced)
    codec = "utf-8" if label.startswith(("utf-8", "cp1251")) is False and label == "utf-8" else (forced or label.split(" ")[0])
    return text, label, bom, codec


def _pos_float(v):
    x = float(v)
    if not (x > 0 and x == x and x != float("inf")):
        raise argparse.ArgumentTypeError("нужно конечное число больше нуля")
    return x


def _pos_int(v):
    x = int(v)
    if x <= 0:
        raise argparse.ArgumentTypeError("нужно целое больше нуля")
    return x


def _target_ok(target: Path, out_root: Path | None, inputs: set) -> str:
    """'' если запись безопасна: итоговый путь (с раскрытыми symlink в любом звене) не совпадает с входным файлом и не выходит из каталога результатов."""
    try:
        real = target.parent.resolve(strict=False) / target.name
        final = target.resolve(strict=False)                 # раскрывает и последнюю ссылку
        if real in inputs or final in inputs or (real.exists() and any(real.samefile(i) for i in inputs if i.exists())):
            return "output_overwrites_input"                 # включая жёсткие ссылки (общий inode)
        if out_root is not None and (out_root != final.parent and out_root not in final.parents):
            return "output_outside_out_dir"
    except OSError:
        return "output_path_unresolved"
    return ""


def _write_private(path: Path, data: bytes):
    """Новый файл 0600 (O_EXCL) рядом, запись, fsync, замена целевого: права 0600 с момента создания и у существующего файла, ссылка не открывается."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def _dict_for_cli(a):
    dpath = a.dict_path or os.environ.get("DEPERSONALIZE_DICT")
    auto = Path(".depersonalize.dict.json")
    if not dpath and a.dict_auto and auto.is_file() and not auto.is_symlink():
        dpath = str(auto)                                    # только по явному --dict-auto, путь в вывод не попадает
    dic = None
    if dpath:
        if dpath == str(auto) and not a.dict_path:           # автопоиск: один раз открыть без символьной ссылки
            fd = os.open(dpath, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
            st, lst = os.fstat(fd), os.lstat(dpath)
            if (st.st_ino, st.st_dev) != (lst.st_ino, lst.st_dev) or os.path.islink(dpath):
                os.close(fd)
                raise ValueError("dictionary_symlink")
            if st.st_size > 5 * 1024 * 1024:
                os.close(fd)
                raise ValueError("dictionary_too_large")
            with os.fdopen(fd, "rb") as fh:
                dic = Dictionary.from_dict(json.loads(fh.read(5 * 1024 * 1024 + 1).decode("utf-8")))
        else:
            dic = Dictionary.from_json(dpath)
    return dic


DICT_HINT = ("словарь имён: создайте .depersonalize.dict.json вида {\"persons\": [[\"Фамилия Имя\", \"алиас\"]], \"orgs\": [\"Компания\"], "
             "\"users\": [], \"hosts\": []} и запустите с --dict-auto (или --dict путь)")


def _enc_label(label: str, text: str, bom: bytes) -> str:
    """Всегда непустая метка: utf-8, utf-8-sig, utf-16 (le/be, с BOM), cp1251 (heuristic), docx или заданная --encoding."""
    if label == "utf-8":
        return "utf-8-sig" if text.startswith("\ufeff") else "utf-8"
    if label.startswith("cp1251"):
        return "cp1251 (heuristic)"
    if label.startswith("utf-16"):
        return label + (" +bom" if bom else "")
    return label or "unknown"


def _one(path: Path, a, dic, out_path, map_path, quiet=False, guard=None):
    """Обработка одного файла. Возвращает (код, краткое состояние); отчёт и сообщения идут в stderr."""
    ext = path.suffix.lower()
    try:
        text, label, bom_prefix, codec = load_input(path, a.encoding)
    except InputError as e:
        print(json.dumps({"ok": False, "reason": e.reason, "stage": e.stage}, ensure_ascii=False), file=sys.stderr)
        print(f"ошибка: {e.message}", file=sys.stderr)
        return 2, e.reason
    kind, lang = _EXT.get(ext, ("text", "generic"))
    kind, lang = a.kind or kind, a.lang or lang
    kw = dict(profile=a.profile, level=a.level, kind=kind, lang=lang, destination=a.destination, data_class=a.data_class, dictionary=dic, ner=a.ner)
    if a.chunk:
        r = depersonalize_chunked(text, chunk=a.chunk, timeout=a.timeout, **kw)
    else:
        r = depersonalize(text, timeout=a.timeout, max_bytes=max(MAX_BYTES, 1), **kw)
    r.report["encoding"] = _enc_label(label, text, bom_prefix)
    r.report["reason"] = r.reason or r.report.get("reason") or ("" if r.ok else "unknown_block")
    print(json.dumps({"ok": r.ok, "reason": r.reason, "encoding": r.report["encoding"], "review": r.review, "report": r.report,
                      "leaks": [str(x) for x in r.leaks]},
                     ensure_ascii=False, indent=1), file=sys.stderr)
    for w in r.report.get("warnings", []):
        print("ВНИМАНИЕ: " + w, file=sys.stderr)
    if r.report.get("warnings") and any("только эвристикой" in w for w in r.report["warnings"]):
        print("ВНИМАНИЕ: " + DICT_HINT, file=sys.stderr)
    if r.report.get("parse_error"):
        pe = r.report["parse_error"]
        print(f"разбор кода: {pe['type']} (строка {pe['line']}, колонка {pe['col']})", file=sys.stderr)
    if r.reason == "quasi_review_required":
        print("Косвенные признаки: покажи текст человеку и повтори с подтверждением (API: review_token).", file=sys.stderr)
    if not r.ok:
        return (4 if r.reason.startswith("hard_block") else 3 if r.reason in ("leaks",) or r.reason.startswith(("secret", "quasi")) else 2), r.reason
    data_text = ("\ufeff" if r.report.get("bom") else "") + r.clean                      # BOM выгрузки 1С возвращается
    enc = a.encoding or (codec if ext != ".docx" else "utf-8")
    try:
        data = bom_prefix + data_text.encode(enc)                                         # запись в той же кодировке, что чтение; строго
    except (UnicodeEncodeError, LookupError):
        print(json.dumps({"ok": False, "reason": "encoding_unrepresentable", "stage": "write"}, ensure_ascii=False), file=sys.stderr)
        print(f"ошибка: результат не помещается в кодировку {enc}; задайте другую через --encoding", file=sys.stderr)
        return 2, "encoding_unrepresentable"
    if out_path:
        bad = _target_ok(out_path, *(guard or (None, {path.resolve()})))                      # сначала проверка пути, потом создание каталогов
        if not bad:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            bad = _target_ok(out_path, *(guard or (None, {path.resolve()})))
        if bad:
            print(json.dumps({"ok": False, "reason": bad, "stage": "write"}, ensure_ascii=False), file=sys.stderr)
            print(f"ошибка: запись отклонена ({bad}): путь через symlink ведёт на входной файл или за пределы каталога результатов", file=sys.stderr)
            return 2, bad
        _write_private(out_path, data)
    else:
        sys.stdout.buffer.write(data)
    if map_path:
        write_map(map_path, r.mapping, a.allow_synced)
    return 0, "ok"


def main(argv=None) -> int:
    for st in (sys.stdout, sys.stderr):                    # консоль Windows (cp1251/cp866): не падать на символах вне кодовой страницы
        with contextlib.suppress(Exception):
            st.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Обезличивание текста перед отправкой во внешние модели")
    ap.add_argument("file", help="файл или каталог (тогда нужен --out-dir)")
    ap.add_argument("--profile", default="toolkit", choices=["toolkit"])
    ap.add_argument("--level", default="standard", choices=LEVELS)
    ap.add_argument("--kind", choices=KINDS)
    ap.add_argument("--lang", choices=["bsl", "python", "sql", "generic"])
    ap.add_argument("--encoding", help="кодировка входа и выхода (по умолчанию определяется: UTF-8, UTF-16 с BOM, затем cp1251)")
    ap.add_argument("--dict", dest="dict_path", help="JSON-словарь имён и компаний (или переменная DEPERSONALIZE_DICT)")
    ap.add_argument("--dict-auto", action="store_true", help="взять .depersonalize.dict.json из текущего каталога (по умолчанию неявных источников нет)")
    ap.add_argument("--data-class", dest="data_class",
                    help="класс данных (для профиля toolkit по умолчанию code)")
    ap.add_argument("--destination", default="external", choices=["local", "external"])
    ap.add_argument("--ner", choices=["auto", "require", "off"])
    ap.add_argument("--timeout", type=_pos_float, default=60.0, help="секунд на файл (по умолчанию 60); по истечении код 2, причина timeout")
    ap.add_argument("--chunk", nargs="?", const=1 << 20, type=_pos_int, metavar="БАЙТ", help="большой файл частями (по умолчанию 1 МБ) с общей картой")
    ap.add_argument("--out")
    ap.add_argument("--out-dir", help="для каталога на входе: куда класть обезличенные файлы")
    ap.add_argument("--map-dir", help="для каталога: куда класть карты (по одной на файл, 0600); без него карты не сохраняются")
    ap.add_argument("--map-out")
    ap.add_argument("--allow-synced", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.data_class is None and a.profile == "toolkit":
            a.data_class = "code"
        dic = _dict_for_cli(a)
        src = Path(a.file)
        if src.is_dir():
            if not a.out_dir:
                print("ошибка: для каталога нужен --out-dir", file=sys.stderr)
                return 2
            counts = {"ok": 0, "blocked": 0, "errors": 0, "skipped_unavailable": 0, "skipped_other": 0}
            worst, failed, by_reason = 0, [], {}
            out_root = Path(a.out_dir).resolve()
            if out_root == src.resolve() or src.resolve() in out_root.parents or out_root in src.resolve().parents:
                print("ошибка: --out-dir не должен совпадать с входным каталогом, лежать внутри него или быть его родителем", file=sys.stderr)
                return 2
            files = [f for f in sorted(src.rglob("*")) if f.is_file() and f.suffix.lower() in _TEXT_EXT]
            inputs = {f.resolve() for f in src.rglob("*") if f.is_file()}
            targets, seen_t = {}, {}
            for f in files:                                  # все назначения проверяются до любых записей
                rel = f.relative_to(src)
                t = out_root / (Path(str(rel) + ".txt") if f.suffix.lower() == ".docx" else rel)
                seen_t.setdefault(str(t).lower(), []).append(rel)
                targets[f] = t
            if a.map_dir:                                    # карты не должны попасть в каталог результатов и пересекаться с ними
                map_root = Path(a.map_dir).resolve()
                if map_root == out_root or out_root in map_root.parents or map_root in out_root.parents or map_root == src.resolve() or src.resolve() in map_root.parents:
                    print("ошибка: --map-dir не должен совпадать с каталогом результатов или входным и вложен в них", file=sys.stderr)
                    return 2
            dup = [v for v in seen_t.values() if len(v) > 1]
            if dup:
                print("ошибка: output_collision, результаты разных файлов совпали бы: " + "; ".join(", ".join(map(str, d)) for d in dup), file=sys.stderr)
                return 2
            for f in files:
                rel = f.relative_to(src)
                code, why = _one(f, a, dic, targets[f], (Path(a.map_dir) / (str(rel) + ".map.json")) if a.map_dir else None, quiet=True,
                                 guard=(out_root, inputs))
                if code == 0:
                    counts["ok"] += 1
                elif why in ("cloud_placeholder", "read_error"):
                    counts["skipped_unavailable"] += 1
                elif why in ("lock_file",):
                    counts["skipped_other"] += 1
                elif code in (3, 4):
                    counts["blocked"] += 1
                    worst = max(worst, 3)
                else:
                    counts["errors"] += 1
                    worst = max(worst, 2)
                print(f"{rel}: код {code}, причина {why}", file=sys.stderr)
                if code != 0:
                    failed.append({"path": str(rel), "code": code, "reason": why})
                    by_reason[why] = by_reason.get(why, 0) + 1
            print(json.dumps({"batch": counts, "by_reason": by_reason, "failed": failed}, ensure_ascii=False), file=sys.stderr)
            return worst
        code, _why = _one(src, a, dic, Path(a.out) if a.out else None, a.map_out)
        return code
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile) as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
