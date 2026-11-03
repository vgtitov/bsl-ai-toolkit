"""Режим наставника: смена режима, пауза между предложениями, хук молчит в off."""
import json
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mentor  # noqa: E402


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MENTOR_STATE", str(tmp_path / "s.json"))
    monkeypatch.setenv("AI_MENTOR_CARD", str(tmp_path / "card.md"))
    return tmp_path / "s.json"


def run(*a):
    return mentor.main(list(a))


def test_default_is_ask_and_hook_prints_one_line(st, capsys):
    run("hook")
    out = capsys.readouterr().out.strip()
    assert out.startswith("ai-mentor ask") and "\n" not in out


def test_yes_sets_on_and_hook_points_to_skill(st, capsys):
    run("answer", "yes")
    assert json.loads(st.read_text())["mode"] == "on"
    capsys.readouterr(); run("hook")
    assert "SKILL.md" in capsys.readouterr().out


def test_no_sets_off_hook_silent_even_in_new_session(st, capsys):
    run("answer", "no")
    d = json.loads(st.read_text())
    assert d["mode"] == "off" and d["declined_at"] == date.today().isoformat()
    capsys.readouterr(); run("hook")
    assert capsys.readouterr().out == ""
    assert mentor.main(["can-offer"]) == 1


def test_later_silences_until_date(st, capsys):
    run("answer", "later", "--days", "30")
    d = json.loads(st.read_text())
    assert d["mode"] == "ask" and d["remind_after"] == (date.today() + timedelta(days=30)).isoformat()
    capsys.readouterr(); run("hook")
    assert capsys.readouterr().out == ""
    s = mentor.load()
    assert mentor.can_offer(s, date.today() + timedelta(days=31))[0]


def test_user_switches_on_off_ask(st):
    run("set", "on"); assert mentor.load()["mode"] == "on"
    run("set", "off"); assert mentor.load()["mode"] == "off"
    run("set", "ask"); assert mentor.load()["mode"] == "ask"


def test_cooldown_between_offers(st):
    s = mentor.cmd_answer(mentor.load(), "later", 0)
    assert not mentor.can_offer(s, date.today() + timedelta(days=3))[0]
    assert mentor.can_offer(s, date.today() + timedelta(days=15))[0]


def test_corrupt_file_means_off(st, capsys):
    st.write_text("{{{ не json")
    assert mentor.load()["mode"] == "ask"  # нечитаемый файл = значения по умолчанию
    st.write_text(json.dumps({"mode": "bogus"}))
    assert mentor.load()["mode"] == "off"


def test_reset_removes_state_and_card(st, tmp_path):
    run("set", "on"); (tmp_path / "card.md").write_text("x")
    run("reset", "--card")
    assert not st.exists() and not (tmp_path / "card.md").exists()


def test_skill_costs_nothing_in_context():
    t = (ROOT / "core/skills/ai-mentor/SKILL.md").read_text(encoding="utf-8")
    fm = t.split("---")[1]
    assert re.search(r"^disable-model-invocation: true$", fm, re.M)
    assert len(re.search(r"description: (.+)", fm).group(1)) < 160


def test_hook_cli_exits_zero_without_state(st):
    r = subprocess.run([sys.executable, str(ROOT / "scripts/mentor.py"), "hook"], capture_output=True, text=True,
                       env={"AI_MENTOR_STATE": str(st), "PATH": "/usr/bin"})
    assert r.returncode == 0
