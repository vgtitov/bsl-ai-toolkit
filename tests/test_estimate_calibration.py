import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import estimate_calibration as ec  # noqa: E402

COLS = list(ec.REQUIRED) + ["est_date"]


def write(tmp_path, rows):
    p = tmp_path / "h.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for i, r in enumerate(rows):
            base = dict(task=f"T{i}", size="M", work_type="форма", role="dev", est_without_ai=10, fact=5,
                        est_date="", close_reason="выполнена", scope_changed="нет", out_of_plan="нет", fact_complete="да")
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
    p5 = write(tmp_path, [{"work_type": "интеграция", "fact": 2} for _ in range(6)])
    many = ec.coefficient(ec.load(p5)[0], "M", "dev", "интеграция", risky_min=0.8)
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


def test_outlier_zero_mad_and_clamp_consistency(tmp_path):
    p = write(tmp_path, [{"fact": 10} for _ in range(9)] + [{"fact": 1000}])
    r = ec.coefficient(ec.load(p)[0], "M", "dev", "форма")
    assert r["coef"] < 1.2  # выброс ln(100) не уводит к границе
    p = write(tmp_path, [{"fact": 0.1} for _ in range(10)])
    r = ec.coefficient(ec.load(p)[0], "M", "dev", "форма")
    assert r["coef"] == 0.3 and r["model_coef"] < 0.3
    assert all(0.3 <= v <= 1.5 for v in r["interval80"] + r["next_task80"])


def test_first_estimate_by_date_even_if_unfit(tmp_path):
    rows = [{"task": "A", "est_date": "2026-10-01", "fact": 5},
            {"task": "A", "est_date": "2026-09-01", "scope_changed": "да", "fact": 5}]
    got, q = ec.load(write(tmp_path, rows))
    assert got == [] and q["объём менялся после оценки"] == 1  # первая по дате непригодна, пара вне расчёта


def test_normalization_and_empty_and_params(tmp_path, capsys):
    p = write(tmp_path, [{"work_type": " Интеграция ", "fact": 2} for _ in range(2)])
    got, _ = ec.load(p)
    assert ec.coefficient(got, "m", "dev", "интеграция")["coef"] == 1.0
    assert ec.main([str(write(tmp_path, [{"fact": 0}])), "--capacity"]) == 3
    import pytest
    with pytest.raises(SystemExit):
        ec.main([str(p), "--prior", "inf"])
    with pytest.raises(SystemExit):
        ec.main([str(p), "--risky-min", "nan"])


def test_capacity_by_task(tmp_path):
    rows = []
    for i in range(6):
        rows += [{"task": f"T{i}", "role": "dev", "fact": 5}, {"task": f"T{i}", "role": "analyst", "fact": 5}]
    c = ec.capacity(ec.load(write(tmp_path, rows))[0])
    assert c["n"] == 6 and c["interval80"] == [0.5, 0.5]
