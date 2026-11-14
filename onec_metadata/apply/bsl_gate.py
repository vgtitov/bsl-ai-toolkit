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


# Коды, которые гейт знал до появления правил-граблей. Baseline без версии (или его отсутствие)
# блокирует только их: иначе старые деревья встанут на загрузке из-за новых правил.
LEGACY_CODES = frozenset({"new-query-in-loop", "query-exec-in-loop", "get-object-in-loop",
                          "ref-dot-in-loop", "db-call-in-loop", "bad-ignore"})
BASELINE_VERSION = 2


def scan_tree(local_dir: Path) -> list[dict]:
    """Блокирующие (severity error) находки bsl_guard по дереву; file — путь от local_dir.
    Предупреждения (тире в комментарии и др.) загрузку не останавливают."""
    guard = _bsl_guard()
    local_dir = Path(local_dir)
    findings = []
    for f in guard.scan_paths([local_dir]):
        if getattr(f, "severity", "error") != "error":
            continue
        rel = str(Path(f.file).resolve().relative_to(local_dir.resolve()))
        findings.append({"file": rel.replace("\\", "/"), "line": f.line,
                         "code": f.code, "message": f.message})
    return findings


def _known_codes() -> list[str]:
    guard = _bsl_guard()
    return sorted(getattr(guard, "_KNOWN_CODES", set()) | LEGACY_CODES)


def _counts(findings: list[dict]) -> Counter:
    return Counter((f["file"], f["code"]) for f in findings)


def write_baseline(local_dir: Path) -> Path:
    """Зафиксировать текущие находки как допустимое легаси. Версия 2 хранит коды правил,
    известных на момент записи: правило, добавленное позже, не блокирует до перезаписи."""
    local_dir = Path(local_dir)
    counts = _counts(scan_tree(local_dir))
    data = {"version": BASELINE_VERSION, "codes": _known_codes(),
            "counts": {f"{file}|{code}": n for (file, code), n in sorted(counts.items())}}
    path = local_dir / BASELINE_NAME
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def _load_baseline(local_dir: Path) -> tuple[Counter, frozenset]:
    """(допустимые счётчики, коды, которые гейт блокирует)."""
    path = Path(local_dir) / BASELINE_NAME
    if not path.exists():
        return Counter(), LEGACY_CODES
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("version") == BASELINE_VERSION:
        raw, enforced = data.get("counts", {}), frozenset(data.get("codes", ())) | LEGACY_CODES
    else:                                        # версия 1: плоский словарь «файл|код: n»
        raw, enforced = data, LEGACY_CODES
    counts: Counter = Counter()
    for key, n in raw.items():
        file, _, code = key.rpartition("|")
        counts[(file, code)] = int(n)
    return counts, enforced


def _split(local_dir: Path) -> tuple[list[dict], list[dict]]:
    """(находки сверх baseline, находки новых правил, которые этот baseline не блокирует)."""
    findings = scan_tree(local_dir)
    baseline, enforced = _load_baseline(local_dir)
    over = _counts(findings) - baseline  # Counter-вычитание: только превышение
    # при превышении по ключу показываем ВСЕ находки этого (файл, код) —
    # номера строк легаси-находок могли сместиться, точнее не различить
    fresh = [f for f in findings if over.get((f["file"], f["code"]), 0) > 0]
    return ([f for f in fresh if f["code"] in enforced],
            [f for f in fresh if f["code"] not in enforced])


def new_findings(local_dir: Path) -> list[dict]:
    """Блокирующие находки сверх baseline (новый файл/код или рост счётчика)."""
    return _split(local_dir)[0]


def _fmt(items: list[dict]) -> str:
    lines = "\n".join(f"  {f['file']}:{f['line']} — [{f['code']}] {f['message']}" for f in items[:20])
    return lines + ("" if len(items) <= 20 else f"\n  … и ещё {len(items) - 20}")


def enforce(local_dir: Path) -> None:
    """ApplyError, если дерево содержит блокирующие находки сверх baseline."""
    import os
    if os.environ.get("ONEC_BSL_GUARD", "").lower() == "off":
        return
    fresh, advisory = _split(local_dir)
    if advisory:
        print("bsl_gate: находки правил, которых нет в baseline этого дерева (загрузку не "
              "останавливают; чтобы включить — перегенерируй baseline, 1c-meta bsl-gate baseline <корень>):\n"
              + _fmt(advisory), file=sys.stderr)
    if not fresh:
        return
    raise ApplyError(
        "bsl_gate: новые блокирующие находки bsl_guard — загрузка в базу ЗАПРЕЩЕНА. "
        "Исправь код (запрос в цикле — пакетно, performance.md §2) или, если это осознанное "
        "легаси, перегенерируй baseline (write_baseline / 1c-meta bsl-gate baseline <корень>):\n" + _fmt(fresh))
