# /// script
# dependencies = []
# ///
"""Детектор забытых ПРАВ в доработке 1С — то, что обычно всплывает у аналитика на тесте.

Три проверки, каждая по реальному коду/метаданным, без обращения к базе:

1. object-without-rights (ошибка) — СОБСТВЕННЫЙ объект расширения, на который нет
   ни одной строки прав ни в одной роли расширения. У ролей расширения флаг
   «устанавливать права для новых объектов» обычно выключен, поэтому право само
   не появится: объект просто не будет виден пользователю.

2. unconditional-right-on-adopted (предупреждение) — право Read/Insert/Update/Delete
   в роли расширения выдано на ЗАИМСТВОВАННЫЙ (типовой) объект. Права разных ролей
   складываются по ИЛИ: роль без ограничения СНИМАЕТ ограничение записей (RLS),
   которое стоит в типовой роли. Обычно это не исправление доступа, а дыра в нём.

3. query-without-allowed (предупреждение) — в тексте запроса есть обращение к
   таблице ДАННЫХ, но нет `РАЗРЕШЕННЫЕ`. В конфигурациях с RLS такой запрос падает
   целиком с «недостаточно прав», а не фильтрует лишнее.
   Легальные исключения (чтение настроек привилегированно) помечаются в коде:
   `// rights-guard: ok - <причина>` на строке запроса или в трёх строках выше.

Запуск:
  python scripts/rights_guard.py <файл|каталог> ...   # exit 1, если есть ошибки
  python scripts/rights_guard.py --strict <path>      # предупреждения тоже валят
  python scripts/rights_guard.py --json <path>
  python scripts/rights_guard.py --quiet <path>       # только exit-код
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_RIGHTS_NS = "{http://v8.1c.ru/8.2/roles}"

# Каталог видов объектов → префикс полного имени в правах.
# Виды, у которых прав нет вовсе (общие модули, картинки, подписки, перечисления),
# сюда не входят: у перечисления в роли прав не бывает, и его отсутствие в Rights.rights
# не является дефектом (проверено на реальном расширении: ни одна роль их не упоминает).
_KIND_BY_DIR = {
    "Catalogs": "Catalog",
    "Documents": "Document",
    "DocumentJournals": "DocumentJournal",
    "Reports": "Report",
    "DataProcessors": "DataProcessor",
    "InformationRegisters": "InformationRegister",
    "AccumulationRegisters": "AccumulationRegister",
    "AccountingRegisters": "AccountingRegister",
    "CalculationRegisters": "CalculationRegister",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "ChartsOfAccounts": "ChartOfAccounts",
    "ChartsOfCalculationTypes": "ChartOfCalculationTypes",
    "BusinessProcesses": "BusinessProcess",
    "Tasks": "Task",
    "ExchangePlans": "ExchangePlan",
    "Constants": "Constant",
}

_DATA_RIGHTS = {"Read", "Insert", "Update", "Delete"}

# Таблицы ДАННЫХ в тексте запроса (у виртуальных/временных таблиц префикса нет).
_DATA_TABLE = re.compile(
    r"\b(?:ИЗ|FROM|СОЕДИНЕНИЕ|JOIN)\b[\s|]+"   # | - перенос строки внутри текста запроса
    r"(?:Документ|Справочник|РегистрСведений|РегистрНакопления|РегистрБухгалтерии|"
    r"РегистрРасчета|ПланВидовХарактеристик|ПланСчетов|ПланВидовРасчета|БизнесПроцесс|"
    r"Задача|ПланОбмена|Document|Catalog|InformationRegister|AccumulationRegister)\.",
    re.IGNORECASE)
_SELECT = re.compile(r"\bВЫБРАТЬ\b|\bSELECT\b", re.IGNORECASE)
_ALLOWED = re.compile(r"\bРАЗРЕШЕННЫЕ\b|\bALLOWED\b", re.IGNORECASE)
_PRAGMA = re.compile(r"//\s*rights-guard:\s*ok", re.IGNORECASE)


@dataclass
class Finding:
    file: str
    line: int
    code: str
    severity: str          # error | warning
    message: str


# --------------------------------------------------------------------------- дерево

def find_src_root(path: Path):
    """Корень исходников расширения: каталог, где рядом лежат Roles и Configuration."""
    p = path if path.is_dir() else path.parent
    for cand in [p, *p.parents]:
        if (cand / "Roles").is_dir() and (cand / "Configuration").is_dir():
            return cand
    return None


def object_fqn(mdo_path: Path):
    """Catalogs/Имя/Имя.mdo → 'Catalog.Имя'. Не объект метаданных → None."""
    parts = mdo_path.parts
    if len(parts) < 3 or mdo_path.suffix != ".mdo":
        return None
    kind_dir, name = parts[-3], parts[-2]
    prefix = _KIND_BY_DIR.get(kind_dir)
    return f"{prefix}.{name}" if prefix else None


def is_adopted(mdo_path: Path):
    """Заимствованный из основной конфигурации объект (а не собственный объект расширения)."""
    try:
        text = mdo_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    return "objectBelonging>Adopted" in text


def collect_rights(src_root: Path):
    """{полное имя объекта: {право: значение}} по всем ролям расширения."""
    granted = {}
    for rights_file in sorted(src_root.glob("Roles/*/Rights.rights")):
        try:
            root = ET.parse(rights_file).getroot()
        except (ET.ParseError, OSError):
            continue
        for obj in root.findall(f"{_RIGHTS_NS}object"):
            name = (obj.findtext(f"{_RIGHTS_NS}name") or "").strip()
            if not name:
                continue
            bag = granted.setdefault(name, {})
            for right in obj.findall(f"{_RIGHTS_NS}right"):
                rname = (right.findtext(f"{_RIGHTS_NS}name") or "").strip()
                rval = (right.findtext(f"{_RIGHTS_NS}value") or "").strip().lower()
                if rname:
                    bag[rname] = (rval == "true")
    return granted


# --------------------------------------------------------------------------- проверки

def check_object_without_rights(mdo_path: Path, granted, findings):
    fqn = object_fqn(mdo_path)
    if not fqn or is_adopted(mdo_path):
        return
    # право может стоять как на сам объект, так и на его реквизит/табличную часть
    if any(name == fqn or name.startswith(fqn + ".") for name in granted):
        return
    findings.append(Finding(
        str(mdo_path), 1, "object-without-rights", "error",
        f"на объект {fqn} нет прав ни в одной роли расширения — у ролей выключен флаг "
        f"«права для новых объектов», право надо выдать явно, иначе объект не увидят"))


def check_unconditional_on_adopted(rights_path: Path, src_root: Path, findings):
    try:
        root = ET.parse(rights_path).getroot()
    except (ET.ParseError, OSError):
        return
    for obj in root.findall(f"{_RIGHTS_NS}object"):
        name = (obj.findtext(f"{_RIGHTS_NS}name") or "").strip()
        if not name or "." not in name:
            continue
        base = ".".join(name.split(".")[:2])           # Catalog.Имя[.Attribute.X]
        kind, obj_name = base.split(".", 1)
        dir_name = next((d for d, k in _KIND_BY_DIR.items() if k == kind), None)
        if not dir_name:
            continue
        mdo = src_root / dir_name / obj_name / f"{obj_name}.mdo"
        own = mdo.exists() and not is_adopted(mdo)
        if own:
            continue                                    # свой объект — выдавать права нормально
        for right in obj.findall(f"{_RIGHTS_NS}right"):
            rname = (right.findtext(f"{_RIGHTS_NS}name") or "").strip()
            rval = (right.findtext(f"{_RIGHTS_NS}value") or "").strip().lower()
            if rname in _DATA_RIGHTS and rval == "true":
                findings.append(Finding(
                    str(rights_path), 1, "unconditional-right-on-adopted", "warning",
                    f"право {rname} на заимствованный объект {base} выдано без ограничения: "
                    f"права ролей складываются по ИЛИ и это снимет ограничение записей (RLS) "
                    f"типовой роли — проверь, что доступ действительно должен быть полным"))
                break


def _query_blocks(lines):
    """Куски текста запроса: от строки с ВЫБРАТЬ до конца строкового литерала."""
    i, n = 0, len(lines)
    while i < n:
        if _SELECT.search(lines[i]):
            start = i
            block = [lines[i]]
            j = i + 1
            while j < n and j - start < 400:
                block.append(lines[j])
                stripped = lines[j].strip()
                if stripped.endswith('";') or stripped.endswith('"'):
                    break
                if not stripped.startswith("|") and '"' not in stripped and stripped:
                    break
                j += 1
            yield start, "\n".join(block)
            i = j + 1
        else:
            i += 1


def check_query_without_allowed(bsl_path: Path, findings):
    try:
        text = bsl_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return
    lines = text.splitlines()
    for start, block in _query_blocks(lines):
        if not _DATA_TABLE.search(block) or _ALLOWED.search(block):
            continue
        context = "\n".join(lines[max(0, start - 3):start + 1])
        if _PRAGMA.search(context) or _PRAGMA.search(block):
            continue
        findings.append(Finding(
            str(bsl_path), start + 1, "query-without-allowed", "warning",
            "запрос к таблице данных без РАЗРЕШЕННЫЕ: при RLS упадёт целиком, а не отфильтрует. "
            "Читаешь настройку привилегированно — пометь «// rights-guard: ok - причина»"))


# --------------------------------------------------------------------------- обход

def iter_targets(paths):
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            yield from sorted(p.rglob("*.mdo"))
            yield from sorted(p.rglob("Rights.rights"))
            yield from sorted(p.rglob("*.bsl"))
        elif p.exists():
            yield p


def scan(paths):
    findings = []
    targets = list(iter_targets(paths))
    roots = {}
    for t in targets:
        root = find_src_root(t)
        if root:
            roots.setdefault(root, None)
    granted_by_root = {r: collect_rights(r) for r in roots}

    for t in targets:
        root = find_src_root(t)
        if t.suffix == ".mdo" and root:
            check_object_without_rights(t, granted_by_root.get(root, {}), findings)
        elif t.name == "Rights.rights" and root:
            check_unconditional_on_adopted(t, root, findings)
        elif t.suffix == ".bsl":
            check_query_without_allowed(t, findings)
    return findings


def main():
    ap = argparse.ArgumentParser(description="Гвард прав 1С: забытые права, дыры в RLS, запросы без РАЗРЕШЕННЫЕ")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--strict", action="store_true", help="предупреждения тоже дают ненулевой код возврата")
    args = ap.parse_args()

    findings = scan(args.paths)
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]

    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    elif not args.quiet:
        for f in findings:
            mark = "ОШИБКА " if f.severity == "error" else "внимание"
            print(f"{mark} {f.file}:{f.line} [{f.code}] {f.message}")
        if findings:
            print(f"\nИтого: ошибок {len(errors)}, предупреждений {len(warnings)}")
        else:
            print("Права: находок нет")

    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
