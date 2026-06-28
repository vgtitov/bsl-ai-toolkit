# /// script
# dependencies = ["mcp"]
# ///
"""MCP-сервер onec-ops — инструменты ЭКСПЛУАТАЦИИ 1С (read-only). Первый инструмент:
`tech_journal_parse` — разбор технологического журнала по каталогу ИЛИ ГРУППЕ каталогов
(разные рабочие серверы кластера, несколько кластеров) для расследования проблем
производительности НА УРОВНЕ ВСЕЙ КОМПАНИИ. Каждое событие тегируется источником
(метка сервера/кластера) + процессом + файлом, чтобы локализовать проблему.

Строго read-only: только ЧИТАЕТ файлы ТЖ, ничего не меняет в ИБ/СУБД/кластере.
Безопасность: пути — из доверенных аргументов; значения свойств (Sql/Context) усекаются;
персональные данные в тексте запросов могут присутствовать — не отправлять сырой вывод
во внешние сервисы (см. SKILL.md ролей, контур данных).

Образцы-ориентиры (см. docs/ops-tools-catalog.md): medigor/tech-log-parser, OneSTools.TechLog.

ENV: ничего обязательного (пути передаются аргументами инструмента).
"""
import os
import re
from collections import Counter

# Стартовая строка события ТЖ: mm:ss.микросек-<длительность>,<Событие>,<остальное>
_EVENT_RE = re.compile(r"^(\d\d):(\d\d)\.(\d+)-(\d+),(.*)$")
_MAXVAL = 1000  # усечение длинных значений (Sql/Context) в выводе


def _split_props(s):
    """Quote-aware split строки свойств по запятым (значения могут быть в '...'/\"...\")."""
    out, cur, q = [], "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            cur += ch
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _parse_props(propstr):
    props = {}
    for tok in _split_props(propstr):
        tok = tok.strip()
        if not tok or "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] in "'\"" and v[-1] == v[0]:
            v = v[1:-1]
        if len(v) > _MAXVAL:
            v = v[:_MAXVAL] + "…"
        props[k.strip()] = v
    return props


def _norm_ts(s):
    """Нормализовать границу времени since/until к сортируемым 14 цифрам YYYYMMDDHHMMSS."""
    if not s:
        return None
    d = re.sub(r"\D", "", str(s))
    if not d:
        return None
    if len(d) == 12 and d[:2] not in ("19", "20"):  # YYMMDDHHMMSS → добавить век
        d = "20" + d
    return (d + "0" * 14)[:14]


def _file_base(stem):
    """Из имени файла ТЖ YYMMDDHH → (год, месяц, день, час) или None."""
    d = re.sub(r"\D", "", stem)
    if len(d) >= 8:
        return 2000 + int(d[0:2]), int(d[2:4]), int(d[4:6]), int(d[6:8])
    return None


def _normalize_roots(roots):
    if isinstance(roots, (str, os.PathLike)):
        roots = [roots]
    norm = []
    for r in roots:
        if isinstance(r, dict):
            path = r.get("path")
            label = r.get("label") or os.path.basename(os.path.normpath(path)) if path else None
        else:
            path = r
            label = os.path.basename(os.path.normpath(str(r)))
        if path:
            norm.append((label, str(path)))
    return norm


def parse_techlog(roots, events=None, min_duration=0, since=None, until=None, top=50):
    """Разобрать ТЖ по одному/нескольким каталогам с атрибуцией источника.

    roots: путь, список путей, или список {"path","label"} (label = метка сервера/кластера).
    events: список имён событий для отбора (напр. ["TLOCK","TDEADLOCK","DBMSSQL"]); None = все.
    min_duration: минимальная длительность (поле ТЖ, микросекунды [проверить под платформу]).
    since/until: границы времени (YYYY-MM-DD HH:MM или YYMMDDHHMM…).
    top: сколько самых длительных событий вернуть в events[].
    """
    events_filter = set(events) if events else None
    since_n, until_n = _norm_ts(since), _norm_ts(until)
    norm_roots = _normalize_roots(roots)

    collected = []
    warnings = []
    files_seen = 0
    by_event = Counter()
    by_source = Counter()

    for label, path in norm_roots:
        if not os.path.isdir(path):
            warnings.append(f"каталог не найден или не директория: {path}")
            continue
        for dirpath, _, files in os.walk(path):
            for fn in files:
                if not fn.lower().endswith(".log"):
                    continue
                full = os.path.join(dirpath, fn)
                files_seen += 1
                process = os.path.basename(dirpath)
                base = _file_base(os.path.splitext(fn)[0])
                try:
                    text = open(full, "r", encoding="utf-8", errors="replace").read()
                except OSError as e:
                    warnings.append(f"не прочитан {full}: {e}")
                    continue
                _parse_file(text, label, process, full, base,
                            events_filter, min_duration, since_n, until_n,
                            collected, by_event, by_source)

    collected.sort(key=lambda e: e["duration"], reverse=True)
    return {
        "summary": {
            "roots": [{"label": l, "path": p} for l, p in norm_roots],
            "files": files_seen,
            "events_total": len(collected),
            "by_event": dict(by_event.most_common()),
            "by_source": dict(by_source.most_common()),
        },
        "events": collected[: max(0, int(top))],
        "warnings": warnings,
    }


def _parse_file(text, label, process, full, base, events_filter, min_duration,
                since_n, until_n, collected, by_event, by_source):
    cur = None

    def finalize(ev):
        rest = ev.pop("_rest")
        parts = rest.split(",", 2)
        name = parts[0].strip()
        level = parts[1].strip() if len(parts) > 1 else ""
        propstr = parts[2] if len(parts) > 2 else ""
        if ev["_extra"]:
            propstr += ev["_extra"]
        ev.pop("_extra")
        if events_filter and name not in events_filter:
            return
        if ev["duration"] < min_duration:
            return
        ts14 = ev.pop("_ts14")
        if since_n and ts14 and ts14 < since_n:
            return
        if until_n and ts14 and ts14 > until_n:
            return
        ev["event"] = name
        ev["level"] = level
        ev["props"] = _parse_props(propstr)
        collected.append(ev)
        by_event[name] += 1
        by_source[label] += 1

    for line in text.splitlines():
        m = _EVENT_RE.match(line)
        if m:
            if cur is not None:
                finalize(cur)
            mm, ss, micro, dur, rest = m.groups()
            if base:
                y, mo, d, hh = base
                ts = f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mm}:{ss}.{micro}"
                ts14 = f"{y:04d}{mo:02d}{d:02d}{hh:02d}{mm}{ss}"
            else:
                ts, ts14 = f"??:?? {mm}:{ss}.{micro}", None
            cur = {
                "source": label, "process": process, "file": full,
                "timestamp": ts, "duration": int(dur),
                "_rest": rest, "_extra": "", "_ts14": ts14,
            }
        elif cur is not None:
            cur["_extra"] += "\n" + line
    if cur is not None:
        finalize(cur)


# --- MCP-обёртка (read-only) ---
try:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("onec-ops")

    @mcp.tool()
    def tech_journal_parse(roots, events=None, min_duration: int = 0,
                           since: str = "", until: str = "", top: int = 50) -> dict:
        """Разбор технологического журнала 1С по каталогу/группе каталогов (разные серверы
        кластера, несколько кластеров) для расследования проблем производительности по всей
        компании. Каждое событие тегируется источником/процессом/файлом. Только чтение.

        roots: путь, список путей или список {"path","label"} (label = метка сервера/кластера).
        events: имена событий для отбора, напр. ["TLOCK","TDEADLOCK","DBMSSQL","DBPOSTGRS","EXCP"].
        min_duration: минимальная длительность события (поле ТЖ). since/until: границы времени.
        top: сколько самых длительных событий вернуть.
        Возвращает summary (files, events_total, by_event, by_source) + events[] (топ) + warnings.
        """
        return parse_techlog(roots, events=events or None, min_duration=min_duration,
                             since=since or None, until=until or None, top=top)

    if __name__ == "__main__":
        mcp.run()
except ImportError:  # pragma: no cover — модуль остаётся импортируемым без пакета mcp (для тестов парсера)
    mcp = None
