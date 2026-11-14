# /// script
# dependencies = []
# ///
"""Детектор граблей BSL, которые молча проходят BSL LS и /CheckConfig по умолчанию.

1. Обращение к БД ВНУТРИ цикла (напр. печатная форма делала `Запрос…Выполнить()`
   внутри `Пока/Для`): создание/выполнение Запроса, подъём объекта
   (`.ПолучитьОбъект`) и чтение реквизита через `.Ссылка.` — в теле цикла или в
   вызванной из цикла функции своего модуля. ENFORCEMENT к performance.md §2.
2. Грабли из ai-pitfalls (каждая — реальный дефект, пойманный только рантаймом):
   блокирующие (error):
   - unknown-module-method — `ОбщийМодуль.НетТакогоМетода(` или метод без Экспорт.
     Индекс общих модулей строится от корня проекта (каталог с
     Configuration/Configuration.mdo или Configuration.xml) плюс корни из
     BSL_GUARD_EXTRA_ROOTS (через os.pathsep: исходники основной конфигурации для
     расширения). Модуль не найден или есть только заимствованным — молчит;
   - query-alias-keyword — псевдоним-ключевое слово в запросе (`КАК Где`, `КАК И`);
   - form-items-by-index — `Элементы[0]` / `Элементы[Сч]` при `Для Сч = …`;
   предупреждения (warning, exit 0):
   - dash-in-comment — «—»/«–» в комментарии (BSL LS InvalidCharacterInFile);
   - avro-date-arithmetic — `Число(Сообщение.Поле) * 86400` (AVRO date строкой).

Чистый stdlib, кроссплатформенно. Комментарии и строковые литералы вырезаются,
поэтому ключевые слова внутри строк/запросов не дают ложных срабатываний.

Осознанное исключение — отметка на строке с обязательной причиной:
  Х = Шаг(Стр); // bsl-guard: ignore db-call-in-loop - шаги последовательны, пакетом нельзя
Отдельной строкой отметка действует на следующую строку. Без причины или с
неизвестным кодом отметка не действует и сама даёт находку bad-ignore.

Запуск:
  python scripts/bsl_guard.py <файл|каталог> ...   # exit 1 если есть блокирующие находки
  python scripts/bsl_guard.py --json <path>        # находки в JSON
  python scripts/bsl_guard.py --quiet <path>       # только exit-код
Точка входа для pre-commit — scripts/git-hooks/pre-commit (только staged *.bsl).
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# --- Windows-консоль по умолчанию cp1251; печать кириллицы её роняет ------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


ERROR, WARNING = "error", "warning"


@dataclass
class Finding:
    file: str
    line: int
    code: str
    message: str
    severity: str = ERROR   # error валит хук/коммит (exit 1), warning только печатается


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


def _strip_comments_and_strings(text: str, comments: dict = None,
                                strings: list = None) -> str:
    """Заменить содержимое строковых литералов и комментариев пробелами,
    сохранив переносы строк (номера строк не смещаются).

    BSL: строка `"…"`, `""` внутри — экранированная кавычка; `//` — комментарий
    до конца строки (вне строки). Многострочные строки-запросы (с `|`) гасятся
    целиком до закрывающей кавычки — для нас важно лишь убрать их содержимое.
    Если передан `comments`, в него кладётся текст комментария по номеру строки;
    если `strings` — пары (строка начала, текст литерала без кавычек).
    """
    out = []
    i, n = 0, len(text)
    in_string = False
    in_comment = False
    lineno, comment_start = 1, 0
    string_start = string_line = 0
    while i < n:
        ch = text[i]
        if ch == "\n":
            if in_comment and comments is not None:
                comments[lineno] = text[comment_start:i].rstrip("\r")
            lineno += 1
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
                if strings is not None:
                    strings.append((string_line, text[string_start:i]))
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
            comment_start = i + 2
            out.append("  ")
            i += 2
            continue
        if ch == '"':
            in_string = True
            string_start, string_line = i + 1, lineno
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    if in_comment and comments is not None:
        comments[lineno] = text[comment_start:].rstrip("\r")
    return "".join(out)


_DB_CALL_CODE = "db-call-in-loop"
_TRAP_CODES = {"dash-in-comment", "query-alias-keyword", "form-items-by-index",
               "unknown-module-method", "avro-date-arithmetic"}
_KNOWN_CODES = {code for code, _, _ in _PATTERNS} | {_DB_CALL_CODE} | _TRAP_CODES
_IGNORE_RX = re.compile(r"^\s*bsl-guard:\s*ignore\b(.*)$", re.IGNORECASE)
_REASON_SEP = re.compile(r"\s+[-–—]\s+")


def _ignores(comments, lines, filename):
    """Отметки `bsl-guard: ignore <код>[, <код>] - <причина>`.

    Возвращает (строка → множество кодов, находки bad-ignore). Отметка в конце
    строки кода действует на эту строку, отдельной строкой — на следующую.
    """
    ignores, bad = {}, []
    for lineno, comment in comments.items():
        m = _IGNORE_RX.match(comment)
        if not m:
            continue
        parts = _REASON_SEP.split(m.group(1).strip(), maxsplit=1)
        codes = {c for c in re.split(r"[,\s]+", parts[0]) if c}
        reason = parts[1].strip() if len(parts) > 1 else ""
        unknown = codes - _KNOWN_CODES
        if not codes or unknown or not reason:
            what = ("неизвестный код " + ", ".join(sorted(unknown)) if unknown
                    else "нет кода правила" if not codes else "нет причины")
            bad.append(Finding(
                filename, lineno, "bad-ignore",
                f"отметка bsl-guard не действует: {what} — пиши "
                "`// bsl-guard: ignore <код> - <причина>`"))
            continue
        target = lineno if lines[lineno - 1].strip() else lineno + 1
        ignores.setdefault(target, set()).update(codes)
    return ignores, bad


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


# --- грабли, которые молча проходят BSL LS и /CheckConfig по умолчанию -------------

_DASH = re.compile("[—–]")   # «—» и «–»: BSL LS InvalidCharacterInFile

# Слова, которые платформа не принимает псевдонимом поля или таблицы запроса
# («Ожидается имя» в рантайме; BSL LS псевдонимы почти не проверяет). Список снят
# прогоном `ВЫБРАТЬ 1 КАК <слово>` на 8.3.27: ВЫБРАТЬ, КОНЕЦ, СОЕДИНЕНИЕ, ИТОГИ,
# ССЫЛКА, ЗНАЧЕНИЕ и т.п. платформа псевдонимом принимает, их здесь нет.
_QUERY_RESERVED = frozenset(
    "где и или не по из как в выбор когда тогда иначе между подобно есть внутреннее "
    "поместить разрешенные различные первые для возр убыв истина ложь неопределено null "
    "выразить общие только автоупорядочивание спецсимвол "
    "where and or not by from as in case when then else on".split())
_QUERY_TEXT = re.compile(r"\b(?:ВЫБРАТЬ|SELECT)\b", re.IGNORECASE)
_QUERY_ALIAS = re.compile(r"(?<!\w)(?:КАК|AS)[\s|]+(\w+)(?!\w)", re.IGNORECASE)

# Элементы формы по числовому индексу: у ВсеЭлементыФормы нет плоского индекса,
# `Элементы[0]` падает в рантайме. По имени (`Элементы[ИмяЭлемента]`) — можно, поэтому
# ловим только число или счётчик числового цикла `Для Сч = …`. Префикс — только сама
# форма: у `Объект.Элементы[0]` это табличная часть, там индекс законен.
_FORM_ITEMS_INDEX = re.compile(
    r"(?<![\w.])(?:(?:Форма|ЭтаФорма|Form|ThisForm)\s*\.\s*)?(?:Элементы|Items)\s*\[([^\[\]]+)\]",
    re.IGNORECASE)

# AVRO date/timestamp: компонента Kafka с 1.9 отдаёт их строкой, а не числом дней/мс,
# и `Число(Сообщение.Дата) * 86400` падает после обновления. Узко: Число(поле) * 86400.
_AVRO_DATE_ARITH = re.compile(r"\bЧисло\s*\(\s*[\w.]+\.\w+\s*\)\s*\*\s*86400\b", re.IGNORECASE)

# Объявление метода в любом модуле (для индекса экспортных методов общих модулей).
_METHOD_DECL = re.compile(
    r"^[ \t]*(?:(?:Асинх|Async)[ \t]+)?(?:Процедура|Функция|Procedure|Function)[ \t]+(\w+)"
    r"[ \t]*\(([^)]*)\)\s*(Экспорт|Export)?", re.IGNORECASE | re.MULTILINE)
# Перехватчики расширения: методов модуля не добавляют, их имена не вызываются извне.
_INTERCEPTOR = re.compile(
    r"&(?:Вместо|После|Перед|ИзменениеИКонтроль|Around|After|Before|ChangeAndValidate)\b",
    re.IGNORECASE)
_MODULE_CALL = re.compile(r"(?<![\w.&~])(\w+)\s*\.\s*(\w+)\s*\(")


def _query_alias_findings(strings, filename):
    out = []
    for start_line, s in strings:
        if not _QUERY_TEXT.search(s):
            continue
        # комментарии внутри текста запроса (и строки-комментарии BSL между `|`) — не код
        s = re.sub(r"//[^\n]*", lambda c: " " * len(c.group()), s)
        s = re.sub(r'""[^"]*""', lambda c: " " * len(c.group()), s)   # строковый литерал запроса
        for m in _QUERY_ALIAS.finditer(s):
            word = m.group(1)
            if word.lower() in _QUERY_RESERVED:
                out.append(Finding(
                    filename, start_line + s.count("\n", 0, m.start()), "query-alias-keyword",
                    f"псевдоним «{word}» в запросе — ключевое слово языка запросов, платформа "
                    "упадёт «Ожидается имя»; дай смысловой псевдоним"))
    return out


def _form_items_findings(lines, procs, filename):
    out = []
    spans = sorted(procs.values())
    in_proc = {ln for a, b in spans for ln in range(a, b + 1)}
    module_names = _local_names("\n".join(l for i, l in enumerate(lines, 1) if i not in in_proc))
    shadow_meta = None
    for lineno, line in enumerate(lines, start=1):
        for m in _FORM_ITEMS_INDEX.finditer(line):
            if line[:m.start()].rstrip().endswith("."):
                continue                         # `Объект . Элементы[0]` — свойство другого объекта
            arg = m.group(1).strip()
            a, b = next(((a, b) for a, b in spans if a <= lineno <= b), (1, len(lines)))
            body = "\n".join(lines[a - 1:b])
            bare = not re.match(r"(?:Форма|ЭтаФорма|Form|ThisForm)\b", m.group(0), re.IGNORECASE)
            if bare and {"элементы", "items"} & (module_names | _local_names(body)):
                continue                         # своя переменная/параметр «Элементы» (массив, коллекция)
            if bare:
                if shadow_meta is None:          # реквизит или табличная часть «Элементы» объекта
                    path = Path(filename)
                    root = _project_root(path.resolve()) if path.is_file() else None
                    shadow_meta = bool(root) and any(
                        _metadata_has_name(path.resolve(), root, n) for n in ("Элементы", "Items"))
                if shadow_meta:
                    continue
            if not re.fullmatch(r"\d+", arg):
                # только «Сч» или «Сч ± число»: склейка со строкой (`"Страница" + Сч`) — это имя
                counter = re.fullmatch(r"([^\W\d]\w*)(?:\s*[-+]\s*\d+)?", arg)
                if not counter:
                    continue
                if not re.search(r"\b(?:Для|For)\s+" + re.escape(counter.group(1)) + r"\s*=",
                                 body, re.IGNORECASE):
                    continue                     # индекс-строка (имя элемента) — законно
            out.append(Finding(
                filename, lineno, "form-items-by-index",
                "Элементы формы по числовому индексу: плоского индекса нет, в рантайме "
                "«Получение элемента по индексу для значения не определено» — бери "
                "Элементы.Найти(Имя) или обходи Группа.ПодчиненныеЭлементы"))
    return out


# ---- индекс экспортных методов общих модулей ------------------------------------

def _project_root(path: Path):
    """Корень исходников: каталог с Configuration/Configuration.mdo (EDT) или Configuration.xml."""
    for d in path.parents:
        if (d / "Configuration" / "Configuration.mdo").is_file() or (d / "Configuration.xml").is_file():
            return d
    return None


def _module_methods(text: str) -> dict:
    """{имя метода в нижнем регистре: экспортный?} без перехватчиков расширения."""
    methods = {}
    for m in _METHOD_DECL.finditer(text):
        before = text[max(0, m.start() - 400):m.start()].rstrip().split("\n")
        annotations = []
        for prev in reversed(before):            # подряд идущие строки-аннотации над объявлением
            if not prev.strip().startswith("&"):
                break
            annotations.append(prev)
        if any(_INTERCEPTOR.search(a) for a in annotations):
            continue
        name = m.group(1).lower()
        methods[name] = methods.get(name, False) or bool(m.group(3))
    return methods


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _root_modules(root: Path) -> dict:
    """{модуль в нижнем регистре: (имя, свой ли, {метод: экспорт})} для одного корня.

    Свой — модуль конфигурации или собственный модуль расширения. Заимствованный
    модуль расширения содержит только добавленное, поэтому полным считается лишь
    при наличии своего модуля в другом корне (BSL_GUARD_EXTRA_ROOTS).
    """
    base = root / "CommonModules"
    if not base.is_dir():
        return {}
    # Типовая — ~1600 модулей и ~120 МБ кода, разбор ~2 с на каждую правку в хуке. Кэш на
    # диске по mtime: пересчитываются только изменённые модули.
    cache_file = Path(tempfile.gettempdir()) / (
        "bsl_guard_idx_" + hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:16] + ".json")
    try:
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
        cache = raw["mods"] if isinstance(raw, dict) and raw.get("v") == _CACHE_VERSION else {}
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError, KeyError, TypeError):
        cache = {}
    mods, fresh, changed = {}, {}, False
    for d in base.iterdir():
        if d.is_dir() and (d / f"{d.name}.mdo").is_file():                    # EDT
            md, bsl = d / f"{d.name}.mdo", d / "Module.bsl"
        elif d.suffix.lower() == ".xml" and d.is_file():                       # Конфигуратор
            md, bsl = d, base / d.stem / "Ext" / "Module.bsl"
        else:
            continue
        try:
            stamp = [md.stat().st_mtime_ns, bsl.stat().st_mtime_ns if bsl.is_file() else 0]
        except OSError:
            continue
        hit = cache.get(md.stem)
        if not _cache_entry_ok(hit) or hit[0] != stamp:
            own = "belonging>adopted<" not in _read(md).lower().replace(" ", "")
            hit = [stamp, own, _module_methods(_read(bsl)) if stamp[1] else {}]
            changed = True
        fresh[md.stem] = hit
        mods[md.stem.lower()] = (md.stem, hit[1], hit[2])
    if changed or len(fresh) != len(cache):
        try:
            tmp = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"v": _CACHE_VERSION, "mods": fresh}, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, cache_file)
        except OSError:
            pass
    return mods


_INDEX_CACHE = {}
_CACHE_VERSION = 1


def _cache_entry_ok(hit) -> bool:
    return (isinstance(hit, list) and len(hit) == 3 and isinstance(hit[0], list)
            and isinstance(hit[1], bool) and isinstance(hit[2], dict))


def _is_extension(root: Path) -> bool:
    for f in (root / "Configuration" / "Configuration.mdo", root / "Configuration.xml"):
        if f.is_file():
            return "configurationextensionpurpose" in _read(f).lower()
    return False


def _modules_index(root: Path) -> dict:
    """Индекс общих модулей корня проекта и корней из BSL_GUARD_EXTRA_ROOTS (объединение).

    {модуль: (имя, свой ли, {метод: экспорт}, только_из_extra)}. Extra-корни подмешиваются
    только к расширению: переменная одна на машину, а хук общий для всех проектов. Модуль,
    который стал «своим» лишь благодаря extra-корню, проверяется предупреждением: корень
    мог быть от другой конфигурации."""
    extra = [Path(p) for p in os.environ.get("BSL_GUARD_EXTRA_ROOTS", "").split(os.pathsep) if p.strip()]
    if extra and not _is_extension(root):
        extra = []
    key = (root, tuple(extra))
    if key in _INDEX_CACHE:
        return _INDEX_CACHE[key]
    merged, root_own = {}, set()
    for i, r in enumerate([root, *extra]):
        if not (r / "CommonModules").is_dir() and (r / "src" / "CommonModules").is_dir():
            r = r / "src"
        for low, (name, own, methods) in _root_modules(r).items():
            if i == 0 and own:
                root_own.add(low)
            _, was_own, was, _ = merged.get(low, (name, False, {}, False))
            joined = dict(was)
            for meth, exp in methods.items():
                joined[meth] = joined.get(meth, False) or exp
            merged[low] = (name, was_own or own, joined, (was_own or own) and low not in root_own)
    _INDEX_CACHE[key] = merged
    return merged


def _current_module(path: Path):
    """Имя общего модуля, если файл — его Module.bsl (свой вызов через префикс не проверяем)."""
    parts = path.parts
    if path.name.lower() == "module.bsl" and len(parts) >= 3:
        name = parts[-3] if parts[-2].lower() == "ext" else parts[-2]
        return name.lower()
    return None


def _local_names(clean: str) -> set:
    """Имена, которые в модуле могут затенять общий модуль: присвоенные, Перем, параметры,
    переменные Для Каждого (в нижнем регистре; с запасом — сравнение тоже попадает)."""
    names = set(re.findall(r"(?<![\w.])(\w+)\s*=(?!=)", clean))
    names |= set(re.findall(r"(?:Для\s+Каждого|For\s+Each)\s+(\w+)", clean, re.IGNORECASE))
    for decl in re.findall(r"(?:^|\n)\s*(?:Перем|Var)\s+([^;]+);", clean, re.IGNORECASE):
        names |= set(re.findall(r"\w+", decl))
    for m in _METHOD_DECL.finditer(clean):
        names |= {re.sub(r"^(?:Знач|Val)\s+", "", p.strip(), flags=re.IGNORECASE).split("=")[0].strip()
                  for p in m.group(2).split(",")}
    return {n.lower() for n in names if n}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


_DATA_CHILDREN = {"attributes", "tabularsections", "dimensions", "resources",          # EDT
                  "attribute", "tabularsection", "dimension", "resource"}              # Конфигуратор


def _context_names_in(f: Path) -> set:
    """Имена, доступные в модуле напрямую: реквизиты, табличные части, измерения и ресурсы
    объекта; у формы — реквизиты формы. Элементы, команды, формы и обработчики — нет."""
    try:
        top = ET.parse(f).getroot()
    except (ET.ParseError, OSError):
        return set()
    names = set()
    if _local(top.tag) == "Form":
        for el in top:
            if _local(el.tag) == "attributes":                                   # EDT Form.form
                names.add(next((c.text for c in el if _local(c.tag) == "name"), None))
            elif _local(el.tag) == "Attributes":                                 # Конфигуратор Form.xml
                names |= {a.get("name") for a in el if _local(a.tag) == "Attribute"}
        return {n.lower() for n in names if n}
    if _local(top.tag) == "MetaDataObject":                                      # Конфигуратор: объект
        obj = next(iter(top), None)
        kids = next((c for c in obj if _local(c.tag) == "ChildObjects"), []) if obj is not None else []
        for el in kids:
            if _local(el.tag).lower() in _DATA_CHILDREN:
                props = next((c for c in el if _local(c.tag) == "Properties"), [])
                names.add(next((c.text for c in props if _local(c.tag) == "Name"), None))
    else:                                                                         # EDT .mdo
        for el in top:
            if _local(el.tag).lower() in _DATA_CHILDREN:
                names.add(next((c.text for c in el if _local(c.tag) == "name"), None))
    return {n.lower() for n in names if n}


def _metadata_has_name(path: Path, root: Path, name: str) -> bool:
    """Имя — реквизит/табличная часть объекта или реквизит формы, чей это модуль
    (`Пользователи.Количество()` в модуле объекта — табличная часть, не общий модуль)."""
    for d in path.parents:
        if d == root:
            break
        for f in (d / f"{d.name}.mdo", d.parent / f"{d.name}.xml", d / "Form.form",
                  d / "Form.xml", d / "Ext" / "Form.xml"):
            if f.is_file() and name.lower() in _context_names_in(f):
                return True
    return False


def _module_method_findings(lines, filename):
    path = Path(filename)
    root = _project_root(path.resolve()) if path.is_file() else None
    if root is None:
        return []
    index = _modules_index(root)
    me = _current_module(path)
    # затенение по области видимости: имена процедуры (параметры, Перем, присваивания) —
    # только в ней; переменные модуля (вне процедур) — во всём модуле
    spans = sorted(_module_procs(lines).values())
    in_proc = {ln for a, b in spans for ln in range(a, b + 1)}
    module_names = _local_names("\n".join(l for i, l in enumerate(lines, 1) if i not in in_proc))
    proc_names = {(a, b): _local_names("\n".join(lines[a - 1:b])) for a, b in spans}
    shadowed = {}
    out = []
    for lineno, line in enumerate(lines, start=1):
        if _PROC_OPEN.match(line):
            continue
        span = next(((a, b) for a, b in spans if a <= lineno <= b), None)
        local = module_names | proc_names.get(span, set())
        for m in _MODULE_CALL.finditer(line):
            mod, meth = m.group(1).lower(), m.group(2)
            entry = index.get(mod)
            if not entry or not entry[1] or mod == me or mod in local:
                continue                  # не общий модуль / заимствованный / свой / переменная
            exported = entry[2].get(meth.lower())
            if exported:
                continue
            if mod not in shadowed:
                shadowed[mod] = _metadata_has_name(path.resolve(), root, mod)
            if shadowed[mod]:
                continue
            what = "есть, но без Экспорт" if exported is False else "не найден"
            out.append(Finding(
                filename, lineno, "unknown-module-method",
                f"{entry[0]}.{meth}: метод {what} в общем модуле {entry[0]} — в рантайме "
                "«Метод объекта не обнаружен»; BSL LS и /CheckConfig без -ExtendedModulesCheck "
                "это не ловят. Сверь имя по модулю"
                + (" (модуль известен только по BSL_GUARD_EXTRA_ROOTS — сверь, что корень "
                   "от той же конфигурации)" if entry[3] else ""),
                WARNING if entry[3] else ERROR))
    return out


def _trap_findings(lines, procs, comments, strings, filename):
    out = [Finding(filename, ln, "dash-in-comment",
                   "тире «—»/«–» в комментарии: BSL LS даст InvalidCharacterInFile — замени на «-»",
                   WARNING)
           for ln, c in comments.items() if _DASH.search(c)]
    out += _query_alias_findings(strings, filename)
    out += _form_items_findings(lines, procs, filename)
    out += _module_method_findings(lines, filename)
    out += [Finding(filename, ln, "avro-date-arithmetic",
                    "арифметика над датой из сообщения (Число(поле) * 86400): компонента Kafka "
                    "с 1.9 отдаёт AVRO date строкой «ГГГГ-ММ-ДД», и код падает после обновления "
                    "компоненты — разбирай значение по типу", WARNING)
            for ln, line in enumerate(lines, start=1) if _AVRO_DATE_ARITH.search(line)]
    return out


def scan_text(text: str, filename: str = "<text>") -> list:
    """Вернуть список Finding по анти-паттернам «обращение к БД в цикле».

    Два прохода: прямые паттерны в теле цикла + межпроцедурный (вызов в цикле
    функции СВОЕГО модуля, которая — сама или транзитивно — лезет в БД; боевой
    пропуск: запрос сидел в функции, из цикла звали функцию).
    """
    comments, strings = {}, []
    clean = _strip_comments_and_strings(text, comments, strings)
    lines = clean.split("\n")
    procs = _module_procs(lines)
    tainted, call_rx = _db_access_procs(lines, procs)

    findings = _trap_findings(lines, procs, comments, strings, filename)
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
                            filename, lineno, _DB_CALL_CODE,
                            f"вызов {name}() в цикле, а внутри неё — обращение "
                            "к БД (напрямую или по цепочке вызовов) — переделай "
                            "на ОДИН пакетный запрос до цикла"))
        depth += len(opens) - len(closes)
        if depth < 0:
            depth = 0
    ignores, bad = _ignores(comments, lines, filename)
    findings = [f for f in findings if f.code not in ignores.get(f.line, ())]
    return sorted(findings + bad, key=lambda f: f.line)


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
        description="Детектор граблей BSL: обращение к БД в цикле, несуществующий метод "
                    "общего модуля, псевдоним-ключевое слово в запросе и др.")
    ap.add_argument("paths", nargs="+", help="файлы .bsl или каталоги")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    ap.add_argument("--quiet", action="store_true", help="без вывода, только exit")
    ns = ap.parse_args(argv)

    findings = scan_paths(ns.paths)
    errors = [f for f in findings if f.severity == ERROR]
    if ns.json:
        print(json.dumps([f.__dict__ for f in findings], ensure_ascii=False, indent=2))
    elif not ns.quiet:
        for f in findings:
            mark = "" if f.severity == ERROR else "предупреждение: "
            print(f"{f.file}:{f.line} — {mark}[{f.code}] {f.message}")
        if findings:
            print(f"\n[!] bsl-guard: блокирующих {len(errors)}, предупреждений "
                  f"{len(findings) - len(errors)}. Осознанное исключение — "
                  "`// bsl-guard: ignore <код> - <причина>`.", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
