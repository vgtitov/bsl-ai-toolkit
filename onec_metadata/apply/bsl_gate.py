"""Гейт производительности BSL перед загрузкой дерева в базу.

Боевой урок 2026-07-20: детектор запроса-в-цикле (scripts/bsl_guard.py)
существовал, но в конвейере ПРИМЕНЕНИЯ не стоял — и инструмент загрузил в базу
модуль с запросом в цикле. Этот модуль ставит детектор в сам конвейер:
`upload_tree` вызывает `enforce(local_dir)` и отказывается отправлять дерево,
если появились НОВЫЕ находки.

Легаси-код: в клиентских расширениях полно старых находок, не относящихся к
текущей правке. Механизм baseline-ratchet: `write_baseline(tree)` фиксирует
текущие находки в `.bsl_guard_baseline.json` в корне дерева; `enforce`
пропускает всё, что покрыто baseline, и падает только на превышении
(новый файл или рост числа находок кода в файле). Чинишь легаси — перегенерируй
baseline, счётчик только уменьшается.

Отключение (сознательное, в лог): env `ONEC_BSL_GUARD=off`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

from .dumpload import ApplyError

BASELINE_NAME = ".bsl_guard_baseline.json"

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def _bsl_guard():
    """Загрузить scripts/bsl_guard.py как модуль (scripts — не пакет)."""
    spec = importlib.util.spec_from_file_location(
        "bsl_guard", _SCRIPTS_DIR / "bsl_guard.py")
    if spec is None or spec.loader is None:
        raise ApplyError(f"bsl_gate: не найден {_SCRIPTS_DIR / 'bsl_guard.py'}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("bsl_guard", mod)
    spec.loader.exec_module(mod)
    return mod


def scan_tree(local_dir: Path) -> list[dict]:
    """Все находки bsl_guard по дереву; file — путь относительно local_dir."""
    guard = _bsl_guard()
    local_dir = Path(local_dir)
    findings = []
    for f in guard.scan_paths([local_dir]):
        rel = str(Path(f.file).resolve().relative_to(local_dir.resolve()))
        findings.append({"file": rel.replace("\\", "/"), "line": f.line,
                         "code": f.code, "message": f.message})
    return findings


def _counts(findings: list[dict]) -> Counter:
    return Counter((f["file"], f["code"]) for f in findings)


def write_baseline(local_dir: Path) -> Path:
    """Зафиксировать текущие находки как допустимое легаси."""
    local_dir = Path(local_dir)
    counts = _counts(scan_tree(local_dir))
    data = {f"{file}|{code}": n for (file, code), n in sorted(counts.items())}
    path = local_dir / BASELINE_NAME
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def _load_baseline(local_dir: Path) -> Counter:
    path = Path(local_dir) / BASELINE_NAME
    if not path.exists():
        return Counter()
    data = json.loads(path.read_text(encoding="utf-8"))
    counts: Counter = Counter()
    for key, n in data.items():
        file, _, code = key.rpartition("|")
        counts[(file, code)] = int(n)
    return counts


def new_findings(local_dir: Path) -> list[dict]:
    """Находки сверх baseline (новый файл/код или рост счётчика)."""
    findings = scan_tree(local_dir)
    baseline = _load_baseline(local_dir)
    over = _counts(findings) - baseline  # Counter-вычитание: только превышение
    # при превышении по ключу показываем ВСЕ находки этого (файл, код) —
    # номера строк легаси-находок могли сместиться, точнее не различить
    return [f for f in findings if over.get((f["file"], f["code"]), 0) > 0]


def enforce(local_dir: Path) -> None:
    """ApplyError, если дерево содержит находки сверх baseline."""
    import os
    if os.environ.get("ONEC_BSL_GUARD", "").lower() == "off":
        return
    fresh = new_findings(local_dir)
    if not fresh:
        return
    lines = "\n".join(
        f"  {f['file']}:{f['line']} — [{f['code']}] {f['message']}"
        for f in fresh[:20])
    more = "" if len(fresh) <= 20 else f"\n  … и ещё {len(fresh) - 20}"
    raise ApplyError(
        "bsl_gate: обращение к БД в цикле — загрузка в базу ЗАПРЕЩЕНА "
        "(performance.md §2). Переделай пакетно или, если это осознанное "
        "легаси, перегенерируй baseline (write_baseline / 1c-meta bsl-baseline):\n"
        + lines + more)
