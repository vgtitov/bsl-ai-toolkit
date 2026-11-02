#!/usr/bin/env python3
"""Калибровка оценок трудозатрат по истории пар «оценка без ИИ и факт».

Вход: CSV истории (формат в core/skills/1c-estimation/references/estimate-history.md).
Модель: y = ln(факт/оценка); среднее mu с нормальным априором N(0, 0,5^2) (центр: «оценка верна»);
разброс sigma устойчиво (MAD), не меньше 0,3. Коэффициент = exp(mu), с 80% интервалом, и интервал
для следующей задачи. Цифр организации здесь нет: стартовый коэффициент для групп без истории
и минимум для рискованных типов задаются параметрами (в корпоративной локализации).

  python scripts/estimate_calibration.py history.csv                      # таблица групп
  python scripts/estimate_calibration.py history.csv --size M --role dev --type форма [--prior 0.8]
  python scripts/estimate_calibration.py history.csv --capacity           # ёмкость: сумма факта к сумме оценок
Код выхода 0.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import defaultdict

PRIOR_SD = 0.5
SIGMA_MIN = 0.3
Z80 = 1.2816
CLAMP = (0.3, 1.5)  # только страховка, не часть модели
MIN_N_RISKY = 5
DEFAULT_RISKY = ("интеграция", "обмен", "права")
REQUIRED = ("task", "size", "work_type", "role", "est_without_ai", "fact", "close_reason",
            "scope_changed", "out_of_plan", "fact_complete")
YES = {"да", "yes", "true", "1", "y"}


def _num(v):
    try:
        x = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def load(path):
    """Читает CSV; возвращает (строки, показатели качества данных)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        miss = [c for c in REQUIRED if c not in (rd.fieldnames or [])]
        if miss:
            raise SystemExit(f"в истории нет колонок: {', '.join(miss)}")
        rows = list(rd)
    q = defaultdict(int)
    seen, out = set(), []
    for r in rows:
        e, fa = _num(r["est_without_ai"]), _num(r["fact"])
        key = (r["task"], r["role"])
        if e is None or e <= 0:
            q["оценка не число или не больше нуля"] += 1
        elif fa is None or fa < 0:
            q["факт не число"] += 1
        elif key in seen:
            q["вторая оценка на ту же задачу и роль (не берём)"] += 1
        elif r["close_reason"].strip().lower() != "выполнена":
            q["не выполнена (перенесена или отменена)"] += 1
        elif r["scope_changed"].strip().lower() in YES:
            q["объём менялся после оценки"] += 1
        elif r["out_of_plan"].strip().lower() in YES:
            q["в оценку входили работы вне плана"] += 1
        elif r["fact_complete"].strip().lower() not in YES:
            q["списания неполные"] += 1
        elif fa == 0:
            q["нулевой факт"] += 1
        else:
            seen.add(key)
            if e == 1:
                q["оценка-заглушка 1 ч (в расчёте, помечена)"] += 1
            out.append({**r, "e": e, "f": fa, "y": math.log(fa / e)})
    return out, dict(q)


def fit(ys):
    """Апостериор среднего логарифма отношения: (mu, sd среднего, sigma разброса)."""
    n = len(ys)
    if n == 0:
        return 0.0, PRIOR_SD, PRIOR_SD
    med = statistics.median(ys)
    mad = statistics.median(abs(y - med) for y in ys) * 1.4826 if n > 1 else 0.0
    sigma = max(SIGMA_MIN, mad)
    prec = n / sigma**2 + 1 / PRIOR_SD**2
    mu = (sum(ys) / sigma**2) / prec
    return mu, math.sqrt(1 / prec), sigma


def coefficient(rows, size, role, wtype, prior=None, risky=DEFAULT_RISKY, risky_min=None):
    """Коэффициент для группы с источником и статусом. rows — сопоставимые пары из load()."""
    sel = [r for r in rows if r["size"] == size and r["role"] == role and r["work_type"] == wtype]
    n = len(sel)
    if wtype in risky and n < MIN_N_RISKY:
        k, src = 1.0, f"истории по типу нет (n={n}), коэффициент 1"
        res = {"coef": k, "n": n, "status": src}
    elif n == 0:
        k = prior if prior is not None else 1.0
        src = "предварительный из настроек" if prior is not None else "по умолчанию 1"
        res = {"coef": k, "n": 0, "status": src}
    else:
        mu, sd, sigma = fit([r["y"] for r in sel])
        pred = math.sqrt(sd**2 + sigma**2)
        k = math.exp(mu)
        res = {"coef": k, "n": n, "status": f"по истории, n={n}",
               "interval80": [math.exp(mu - Z80 * sd), math.exp(mu + Z80 * sd)],
               "next_task80": [math.exp(mu - Z80 * pred), math.exp(mu + Z80 * pred)],
               "p80_next": math.exp(mu + 0.8416 * pred)}
    kc = min(max(k, CLAMP[0]), CLAMP[1])
    if kc != k:
        res["clamped_from"] = k
    if wtype in risky and risky_min is not None and kc < risky_min:
        res["raised_to_min"], kc = risky_min, risky_min
    res["coef"] = kc
    return res


def capacity(rows, boots=2000, seed=1):
    """Ёмкость месяца: сумма факта к сумме оценок, 80% интервал бутстрепом по задачам."""
    if not rows:
        return None
    ratio = sum(r["f"] for r in rows) / sum(r["e"] for r in rows)
    rnd = random.Random(seed)
    bs = sorted(
        sum(x["f"] for x in s) / sum(x["e"] for x in s)
        for s in ([rnd.choice(rows) for _ in rows] for _ in range(boots)))
    return {"ratio": ratio, "n": len(rows), "interval80": [bs[int(boots * .1)], bs[int(boots * .9)]],
            "note": "оптимистично: затянувшиеся незакрытые задачи не входят"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("history")
    ap.add_argument("--size"), ap.add_argument("--role"), ap.add_argument("--type", dest="wtype")
    ap.add_argument("--prior", type=float, help="предварительный коэффициент для групп без истории")
    ap.add_argument("--risky", default=",".join(DEFAULT_RISKY), help="рискованные типы работ через запятую")
    ap.add_argument("--risky-min", type=float, help="повышенный минимум коэффициента для рискованных типов")
    ap.add_argument("--capacity", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows, quality = load(a.history)
    risky = tuple(x.strip() for x in a.risky.split(",") if x.strip())
    if a.capacity:
        out = {"capacity": capacity(rows), "quality": quality}
    elif a.size and a.role and a.wtype:
        out = {"result": coefficient(rows, a.size, a.role, a.wtype, a.prior, risky, a.risky_min), "quality": quality}
    else:
        groups = sorted({(r["size"], r["role"], r["work_type"]) for r in rows})
        out = {"groups": {" / ".join(g): coefficient(rows, *g, a.prior, risky, a.risky_min) for g in groups},
               "quality": quality}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    for g, v in out.get("groups", {}).items():
        print(f"{g}: {v['coef']:.2f}  ({v['status']})")
    if "result" in out:
        v = out["result"]
        print(f"коэффициент {v['coef']:.2f}  ({v['status']})")
        for k in ("interval80", "next_task80"):
            if k in v:
                print(f"  {k}: {v[k][0]:.2f} .. {v[k][1]:.2f}")
        if "p80_next" in v:
            print(f"  P80 для следующей задачи: {v['p80_next']:.2f}")
    if out.get("capacity"):
        c = out["capacity"]
        print(f"ёмкость: {c['ratio']:.2f} (80%: {c['interval80'][0]:.2f}..{c['interval80'][1]:.2f}), n={c['n']}, {c['note']}")
    if quality:
        print("не вошло в расчёт / пометки:", "; ".join(f"{k}: {v}" for k, v in quality.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
