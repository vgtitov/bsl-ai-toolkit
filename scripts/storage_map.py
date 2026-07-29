# /// script
# dependencies = []
# ///
"""Реестр карт структуры хранения 1С (метаданные ↔ таблицы СУБД) ПО БАЗАМ.

Зачем: SQL-улики мониторинга (pg_stat_statements, планы, топ таблиц) приходят в терминах
`_InfoRg32784`/`_Reference379` — без перевода в объекты 1С рекомендация разработчику неадресна.
Имена таблиц стабильны между реструктуризациями, но НЕ вечны: после обновления конфигурации
карту нужно переснять (status подсвечивает устаревшие).

Источник карты — выгрузка `ПолучитьСтруктуруХраненияБазыДанных(, Истина)` (сниппет — гайд
docs/zabbix-1c-monitoring-guide.md §«Структура хранения»), либо готовый отчёт «Названия таблиц»
(xls/xlsx/csv с колонками: Имя таблицы; Имя таблицы хранения; Метаданные; Назначение; Данные (КБ)).

Хранение: каталог карт (env ONEC_STORAGE_MAPS_DIR или --maps-dir), в нём `<База>.csv`
(=нормализованная карта) + `index.json` (реестр: какие базы покрыты, когда снято, из чего).
Нагруженные базы (ERP КЗ/РБ) держим постоянно; остальные импортируются точечно перед анализом.

Команды:
  import <файл> --base <ИмяБазы> [--maps-dir D]   — снять/обновить карту базы
  status [--expect "ERP_KZ,ERP_BY"] [--maps-dir D] — какие базы покрыты/нет, свежесть
  lookup <имя...> [--base B] [--maps-dir D]        — перевести имена таблиц (суффиксы _VT/X1 понимает)
  annotate [--base B] [--maps-dir D]               — прочитать текст со stdin, вывести расшифровку таблиц

Read-only к ИБ: работает только с выгрузками. Кроссплатформенно (pathlib, stdlib;
.xls — опционально xlrd, .xlsx — опционально openpyxl).
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

# Windows-консоль по умолчанию cp1251 — печать '→'/'—'/кириллицы роняет скрипт. Принудительно UTF-8.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

STALE_DAYS = 90  # конфигурация живая: карта старше — вероятно, отстала от реструктуризаций

_HEADER_HINTS = ("имя таблицы", "метаданные", "назначение", "storage", "table name")


def _dedup_add(seen, row):
    key = (row[1] or row[0]).lower()
    if not key or key in seen:
        return False
    seen.add(key)
    return True


def read_export(path):
    """Прочитать выгрузку (.xls/.xlsx/.csv/.txt) → строки [полное имя, таблица СУБД,
    метаданные, назначение, размер КБ]. Заголовок отбрасывается."""
    p = Path(path)
    ext = p.suffix.lower()
    rows = []
    if ext == ".xls":
        import xlrd  # опциональная зависимость: uv run --with xlrd
        sh = xlrd.open_workbook(str(p)).sheet_by_index(0)
        for r in range(sh.nrows):
            rows.append([str(sh.cell_value(r, c)).strip() for c in range(min(sh.ncols, 5))])
    elif ext in (".xlsx", ".xlsm"):
        import openpyxl
        ws = openpyxl.load_workbook(str(p), read_only=True, data_only=True).active
        for row in ws.iter_rows(values_only=True):
            rows.append([str(c).strip() if c is not None else "" for c in row[:5]])
    else:  # csv / txt («;» или запятая)
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        delim = ";" if text.count(";") >= text.count(",") else ","
        rows = [list(r)[:5] for r in csv.reader(text.splitlines(), delimiter=delim)]
    out = []
    for r in rows:
        r = (list(r) + [""] * 5)[:5]
        hay = " ".join(r).lower()
        if any(h in hay for h in _HEADER_HINTS) and not (r[1] or "").startswith("_"):
            continue  # строка заголовка
        if not (r[0] or r[1]):
            continue
        out.append([str(c).strip() for c in r])
    return out


def _index_path(maps_dir):
    return Path(maps_dir) / "index.json"


def _read_index(maps_dir):
    p = _index_path(maps_dir)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"maps": {}, "expected": []}


def import_rows(rows, base, maps_dir, source=""):
    """Нормализовать строки выгрузки в карту базы `<База>.csv` + обновить index.json.
    Повторный импорт той же базы = обновление карты (перезапись)."""
    maps_dir = Path(maps_dir)
    maps_dir.mkdir(parents=True, exist_ok=True)
    seen = set()
    norm = []
    for r in rows:
        r = (list(r) + [""] * 5)[:5]
        table = r[1] or r[0]
        if not table or not _dedup_add(seen, r):
            continue
        norm.append({"table": table, "metadata": r[2] or r[0], "purpose": r[3], "size_kb": r[4]})
    with open(maps_dir / f"{base}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["table", "metadata", "purpose", "size_kb"])
        for n in norm:
            w.writerow([n["table"], n["metadata"], n["purpose"], n["size_kb"]])
    idx = _read_index(maps_dir)
    rec = {"file": f"{base}.csv", "tables": len(norm),
           "imported_at": time.strftime("%Y-%m-%d %H:%M"), "source": source}
    idx["maps"][base] = rec
    _index_path(maps_dir).write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def load_maps(maps_dir, bases=None):
    """Загрузить карты (все или выбранные базы) → {база: {table_lower: (table, metadata, purpose)}}."""
    maps_dir = Path(maps_dir)
    idx = _read_index(maps_dir)
    out = {}
    for base, rec in idx["maps"].items():
        if bases and base not in bases:
            continue
        p = maps_dir / rec["file"]
        if not p.is_file():
            continue
        mp = {}
        with open(p, encoding="utf-8", newline="") as f:
            rd = csv.reader(f, delimiter=";")
            next(rd, None)
            for row in rd:
                if len(row) >= 3 and row[0]:
                    mp[row[0].lower()] = (row[0], row[1], row[2] if len(row) > 2 else "")
        out[base] = mp
    return out


# Кандидаты имени: точное → без X\d (алиас индекса в тексте запроса) → родитель ТЧ (_VT\d+…)
def _candidates(token):
    t = token.strip()
    cands = [t]
    t2 = re.sub(r"x\d+$", "", t, flags=re.I)
    if t2 != t:
        cands.append(t2)
    t3 = re.sub(r"_vt\d+.*$", "", t2, flags=re.I)
    if t3 not in cands:
        cands.append(t3)
    t4 = re.sub(r"(_chngr?|_tot|_sl|_opt)\d*.*$", "", t2, flags=re.I)
    if t4 not in cands:
        cands.append(t4)
    return cands


def lookup(token, maps):
    """Перевести имя таблицы СУБД в объект(ы) 1С по загруженным картам.
    → [{base, table, metadata, purpose, matched_by}] (пусто, если нигде нет)."""
    res = []
    for cand in _candidates(token):
        cl = cand.lower()
        for base, mp in maps.items():
            hit = mp.get(cl)
            if hit:
                res.append({"base": base, "table": hit[0], "metadata": hit[1],
                            "purpose": hit[2], "matched_by": cand})
        if res:
            break  # нашли на этом уровне нормализации — глубже не упрощаем
    return res


_TABLE_TOKEN_RE = re.compile(r"\b_[A-Za-z][A-Za-z]+\d+[A-Za-z0-9_]*")
_NOT_TABLE_RE = re.compile(r"^_(?:fld|q_|tt)", re.I)


def tables_in_text(text, maps):
    """Найти в тексте (SQL-улике) имена таблиц и перевести по картам.
    → [{table, base, metadata, purpose}] без дублей, только найденные в картах."""
    seen, out = set(), []
    for tok in _TABLE_TOKEN_RE.findall(text or ""):
        if _NOT_TABLE_RE.match(tok):
            continue
        base_tok = re.sub(r"x\d+$", "", tok, flags=re.I)
        if base_tok.lower() in seen:
            continue
        seen.add(base_tok.lower())
        for hit in lookup(tok, maps):
            out.append({"table": hit["matched_by"], "base": hit["base"],
                        "metadata": hit["metadata"], "purpose": hit["purpose"]})
    return out


def status(maps_dir, expected=None):
    """Учёт покрытия: какие базы имеют карту (и её свежесть), каких НЕ хватает."""
    idx = _read_index(maps_dir)
    expected = list(expected or idx.get("expected") or [])
    bases = []
    now = time.time()
    for base, rec in sorted(idx["maps"].items()):
        try:
            age = (now - time.mktime(time.strptime(rec["imported_at"], "%Y-%m-%d %H:%M"))) / 86400
        except (ValueError, KeyError):
            age = -1
        bases.append({"base": base, "present": True, "tables": rec.get("tables"),
                      "imported_at": rec.get("imported_at"), "source": rec.get("source", ""),
                      "age_days": round(age, 1), "stale": age > STALE_DAYS})
    have = {b["base"] for b in bases}
    for base in expected:
        if base not in have:
            bases.append({"base": base, "present": False})
    return {"maps_dir": str(maps_dir), "bases": bases,
            "missing": [b for b in expected if b not in have]}


def default_maps_dir(repo_root=None):
    """Каталог карт: env ONEC_STORAGE_MAPS_DIR, иначе <репозиторий>/data/storage_maps,
    если он существует (карты, зашитые в локализацию: git pull — и перевод таблиц работает
    у всей команды без настройки)."""
    env = os.environ.get("ONEC_STORAGE_MAPS_DIR", "")
    if env:
        return env
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
    bundled = root / "data" / "storage_maps"
    return str(bundled) if bundled.is_dir() else ""


def main():
    p = argparse.ArgumentParser(description="Карты структуры хранения 1С по базам (метаданные ↔ таблицы СУБД)")
    p.add_argument("--maps-dir", default=default_maps_dir(),
                   help="каталог карт (или env ONEC_STORAGE_MAPS_DIR)")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("import", help="импорт/обновление карты базы из выгрузки")
    pi.add_argument("file")
    pi.add_argument("--base", required=True, help="имя базы (как в СУБД/Zabbix, напр. OrgKZ_ERP)")
    ps = sub.add_parser("status", help="какие базы покрыты/нет, свежесть карт")
    ps.add_argument("--expect", default="", help="ожидаемые базы через запятую")
    pl = sub.add_parser("lookup", help="перевести имена таблиц")
    pl.add_argument("names", nargs="+")
    pl.add_argument("--base", default="", help="искать только в карте этой базы")
    pa = sub.add_parser("annotate", help="расшифровать таблицы в тексте со stdin")
    pa.add_argument("--base", default="")
    a = p.parse_args()
    if not a.maps_dir:
        p.error("нет каталога карт: --maps-dir или env ONEC_STORAGE_MAPS_DIR")

    if a.cmd == "import":
        rows = read_export(a.file)
        rec = import_rows(rows, a.base, a.maps_dir, source=os.path.basename(a.file))
        print(f"[ok] {a.base}: {rec['tables']} таблиц → {Path(a.maps_dir) / rec['file']} ({rec['imported_at']})")
    elif a.cmd == "status":
        st = status(a.maps_dir, expected=[b.strip() for b in a.expect.split(",") if b.strip()] or None)
        for b in st["bases"]:
            if b["present"]:
                mark = "⚠ устарела (переснять после обновления конфигурации)" if b["stale"] else "ok"
                print(f"[есть] {b['base']}: {b['tables']} таблиц, снята {b['imported_at']} "
                      f"({b['age_days']} дн, {mark}), источник: {b['source']}")
            else:
                print(f"[НЕТ ] {b['base']}: карты нет — снять выгрузку (гайд §Структура хранения)")
    else:
        maps = load_maps(a.maps_dir, bases=[a.base] if a.base else None)
        if not maps:
            sys.exit(f"в {a.maps_dir} нет карт" + (f" базы {a.base}" if a.base else ""))
        if a.cmd == "lookup":
            for name in a.names:
                hits = lookup(name, maps)
                if hits:
                    for h in hits:
                        print(f"{name} = {h['metadata']} [{h['purpose']}] ({h['base']}, таблица {h['table']})")
                else:
                    print(f"{name} = ? (нет в картах: {', '.join(maps)})")
        else:  # annotate
            notes = tables_in_text(sys.stdin.read(), maps)
            for n in notes:
                print(f"{n['table']} = {n['metadata']} [{n['purpose']}] ({n['base']})")


if __name__ == "__main__":
    main()
