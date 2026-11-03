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


def _st(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MENTOR_STATE", str(tmp_path / "s.json"))
    monkeypatch.setenv("AI_MENTOR_CARD", str(tmp_path / "card.md"))
    return tmp_path / "s.json"


st = pytest.fixture(_st)


def run(*a):
    return mentor.main(list(a))


def test_default_is_ask_and_hook_prints_one_line(st, capsys):
    run("hook")
    out = capsys.readouterr().out.strip()
    assert out.startswith("mentor ask") and "\n" not in out


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
    assert mentor.load()["mode"] == "ask"  # файла нет: значения по умолчанию
    st.write_text("{{{ не json")
    assert mentor.load()["mode"] == "off"  # файл есть, но не читается: согласие неизвестно
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


def test_offered_blocks_repeat_without_answer(st):
    run("offered")
    s = mentor.load()
    assert s["offers_made"] == 1 and not mentor.can_offer(s)[0]
    run("answer", "no")  # ответ в тот же день не удваивает счётчик
    assert mentor.load()["offers_made"] == 1


def test_status_prints_card_and_reset_uses_level_card(st, tmp_path, capsys):
    card = tmp_path / "ai-mentor-card-custom.md"
    card.write_text("Уровень: тест")
    s = mentor.load(); s["level_card"] = str(card); mentor.save(s)
    run("status")
    assert "Уровень: тест" in capsys.readouterr().out
    run("reset", "--card")
    assert not card.exists() and not st.exists()


def test_later_keeps_on_mode(st):
    run("set", "on"); run("answer", "later", "--days", "30")
    assert mentor.load()["mode"] == "on"


def test_wrong_field_types_mean_off(st):
    for bad in ({"offers_made": "1"}, {"last_offer": []}, {"level_card": {}}, {"remind_after": "не дата"}):
        st.write_text(json.dumps({"mode": "ask", **bad}))
        assert mentor.load()["mode"] == "off", bad


def test_offered_never_revives_declined(st):
    run("answer", "no"); run("offered")
    s = mentor.load()
    assert s["mode"] == "off" and s["offers_made"] == 1


def test_api_question_has_own_cooldown_and_keeps_on(st):
    run("set", "on"); assert mentor.can_offer_api(mentor.load())[0]
    run("api-asked", "--days", "30")
    s = mentor.load()
    assert s["mode"] == "on" and not mentor.can_offer_api(s)[0]
    assert mentor.can_offer_api(s, date.today() + timedelta(days=31))[0]


def test_days_range_and_new_state_dir(st, tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        run("answer", "later", "--days", "-1")
    monkeypatch.setenv("AI_MENTOR_STATE", str(tmp_path / "new" / "dir" / "s.json"))
    run("set", "on")
    assert (tmp_path / "new" / "dir" / "s.json").exists()


def test_late_answer_does_not_revive_after_no(st):
    run("answer", "no"); run("answer", "yes"); run("answer", "later")
    assert mentor.load()["mode"] == "off"


def test_api_decline_is_permanent(st):
    run("api-declined")
    s = mentor.load()
    assert not mentor.can_offer_api(s, date.today() + timedelta(days=400))[0]


def test_parallel_writers_keep_refusal(st):
    import threading
    def w(a):
        for _ in range(10):
            run(*a)
    ts = [threading.Thread(target=w, args=(("offered",),)), threading.Thread(target=w, args=(("api-asked",),))]
    [t.start() for t in ts]; run("answer", "no"); [t.join() for t in ts]
    assert mentor.load()["mode"] == "off"


def test_hook_paths_exist_and_foreign_card_path_ignored(st, tmp_path):
    import re as _re
    for mode in ("on", "ask"):
        s = mentor.default_state(); s["mode"] = mode
        path = _re.search(r"(\S+\.md)", mentor.hook_line(s)).group(1)
        assert (ROOT / path).is_file(), path
    victim = tmp_path / "important-ai-mentor-card-notes.md"; victim.write_text("x")
    s = mentor.load(); s["level_card"] = str(victim); mentor.save(s)
    run("reset", "--card")
    assert victim.exists()
