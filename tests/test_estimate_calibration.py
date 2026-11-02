import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import estimate_calibration as ec  # noqa: E402

COLS = list(ec.REQUIRED)


def write(tmp_path, rows):
    p = tmp_path / "h.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for i, r in enumerate(rows):
            base = dict(task=f"T{i}", size="M", work_type="форма", role="dev", est_without_ai=10, fact=5,
                        close_reason="выполнена", scope_changed="нет", out_of_plan="нет", fact_complete="да")
            w.writerow({**base, **r})
    return p


def test_shrinks_to_prior_and_filters(tmp_path):
    rows = [{"fact": 5} for _ in range(10)]
    rows += [{"close_reason": "отменена"}, {"scope_changed": "да"}, {"out_of_plan": "да"},
             {"fact_complete": "нет"}, {"fact": 0}, {"est_without_ai": "abc"}]
    p = write(tmp_path, rows)
    got, q = ec.load(p)
    assert len(got) == 10 and sum(q.values()) == 6
    r = ec.coefficient(got, "M", "dev", "форма")
    assert 0.5 < r["coef"] < 0.8 and r["n"] == 10  # от 0,5 по данным к 1 по априору
    assert r["interval80"][0] < r["coef"] < r["interval80"][1]
    one = ec.coefficient(got[:1], "M", "dev", "форма")
    assert one["coef"] > r["coef"]  # меньше данных, ближе к 1


def test_risky_without_history_is_one_and_min(tmp_path):
    p = write(tmp_path, [{"work_type": "интеграция", "fact": 2} for _ in range(3)])
    got, _ = ec.load(p)
    r = ec.coefficient(got, "M", "dev", "интеграция", risky_min=0.8)
    assert r["coef"] == 1.0 and "истории по типу нет" in r["status"]
    many = ec.coefficient(got * 2, "M", "dev", "интеграция", risky_min=0.8)
    assert many["n"] == 6 and math.isclose(many["coef"], 0.8)
    assert many["raised_to_min"] == 0.8


def test_prior_default_and_duplicate_estimate(tmp_path):
    p = write(tmp_path, [{"task": "A", "fact": 5}, {"task": "A", "fact": 50}])
    got, q = ec.load(p)
    assert len(got) == 1 and "вторая оценка" in " ".join(q)
    assert ec.coefficient(got, "L", "dev", "отчёт")["status"] == "по умолчанию 1"
    assert ec.coefficient(got, "L", "dev", "отчёт", prior=0.7)["coef"] == 0.7


def test_capacity(tmp_path):
    p = write(tmp_path, [{"fact": 5} for _ in range(8)])
    got, _ = ec.load(p)
    c = ec.capacity(got)
    assert math.isclose(c["ratio"], 0.5) and c["interval80"][0] <= 0.5 <= c["interval80"][1]
