#!/usr/bin/env python3
"""Режим наставника ai-mentor: off | ask | on. Состояние в личном файле человека вне репозитория.

  python scripts/mentor.py status                 # что хранится (файл показывается целиком)
  python scripts/mentor.py set on|off|ask         # «включи» / «выключи» / «не предлагай» (= off)
  python scripts/mentor.py answer yes|no|later [--days 30]   # ответ на предложение
  python scripts/mentor.py offered                # предложение показано: записать сразу, до ответа
  python scripts/mentor.py can-offer              # код 0, если сейчас можно предложить; иначе 1 и причина
  python scripts/mentor.py hook                   # SessionStart: off молчит, ask и on одна строка
  python scripts/mentor.py reset [--card]         # сбросить состояние (и карточку уровня)

Файл: ~/.ai-mentor.json (переопределяется AI_MENTOR_STATE). Один для всех агентов: Claude Code, Codex,
Copilot в VS Code, Cursor читают его одинаково. Карточка уровня рядом: ~/.ai-mentor-card.md.
В файле нет имён, рабочих данных и токенов, только режим и даты. Только stdlib.
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MODES = ("off", "ask", "on")
COOLDOWN_DAYS = 14  # не чаще одного предложения за этот срок (и не больше одного за сессию)
SKILL_DIR = ".claude/skills/ai-mentor"


def state_path() -> Path:
    return Path(os.environ.get("AI_MENTOR_STATE") or Path.home() / ".ai-mentor.json")


def card_path() -> Path:
    return Path(os.environ.get("AI_MENTOR_CARD") or Path.home() / ".ai-mentor-card.md")


def default_state() -> dict:
    return {"mode": "ask", "declined_at": None, "remind_after": None, "offers_made": 0,
            "last_offer": None, "level_card": str(card_path()), "updated": None}


def load() -> dict:
    s = default_state()
    if not state_path().exists():
        return s
    try:
        d = json.loads(state_path().read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError
        s.update({k: d[k] for k in s if k in d})
    except (OSError, ValueError):  # файл есть, но не читается: согласие неизвестно, значит off
        s["mode"] = "off"
    if s["mode"] not in MODES:  # битый файл не должен включать ментора
        s["mode"] = "off"
    return s


def save(s: dict) -> None:
    s["updated"] = date.today().isoformat()
    p = state_path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _d(v):
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def can_offer(s: dict, today: date | None = None) -> tuple[bool, str]:
    today = today or date.today()
    if s["mode"] != "ask":
        return False, f"режим {s['mode']}"
    ra, lo = _d(s["remind_after"]), _d(s["last_offer"])
    if ra and today < ra:
        return False, f"просили напомнить после {ra}"
    if lo and today < lo + timedelta(days=COOLDOWN_DAYS):
        return False, f"предложение было {lo}, пауза {COOLDOWN_DAYS} дней"
    return True, "можно"


def hook_line(s: dict, today: date | None = None) -> str:
    if s["mode"] == "on":
        return f"ai-mentor on: read {SKILL_DIR}/SKILL.md"
    if can_offer(s, today)[0]:
        return f"mentor ask, weak prompt or pasted Jira text: {SKILL_DIR}/OFFER.md"
    return ""


def card_file(s: dict) -> Path:
    return Path(s["level_card"]) if s.get("level_card") else card_path()


def mark_offered(s: dict, today: date | None = None) -> dict:
    s["offers_made"] += 1
    s["last_offer"] = (today or date.today()).isoformat()
    return s


def cmd_answer(s: dict, ans: str, days: int, today: date | None = None) -> dict:
    today = today or date.today()
    if s["last_offer"] != today.isoformat():  # предложение уже записано командой offered
        mark_offered(s, today)
    if ans == "yes":
        s["mode"], s["remind_after"] = "on", None
    elif ans == "no":
        s["mode"], s["declined_at"], s["remind_after"] = "off", today.isoformat(), None
    else:
        s["mode"] = "on" if s["mode"] == "on" else "ask"  # «позже» на вопрос про API не выключает включённого
        s["remind_after"] = (today + timedelta(days=days)).isoformat()
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("can-offer")
    sub.add_parser("offered")
    sub.add_parser("hook")
    p = sub.add_parser("set"); p.add_argument("mode", choices=MODES)
    p = sub.add_parser("answer"); p.add_argument("answer", choices=("yes", "no", "later"))
    p.add_argument("--days", type=int, default=30)
    p = sub.add_parser("reset"); p.add_argument("--card", action="store_true")
    a = ap.parse_args(argv)

    s = load()
    if a.cmd == "hook":  # хук не должен ломать сессию
        try:
            line = hook_line(s)
            if line:
                print(line)
        except Exception:
            pass
        return 0
    if a.cmd == "status":
        print(f"файл: {state_path()}" + ("" if state_path().exists() else " (нет, значения по умолчанию)"))
        print(json.dumps(s, ensure_ascii=False, indent=2))
        c = card_file(s)
        print(f"карточка: {c}" + ("" if c.exists() else " (нет)"))
        if c.exists():
            print(c.read_text(encoding="utf-8"))
    elif a.cmd == "can-offer":
        ok, why = can_offer(s)
        print(("да: " if ok else "нет: ") + why)
        return 0 if ok else 1
    elif a.cmd == "offered":
        save(mark_offered(s))
        print("предложение записано")
    elif a.cmd == "set":
        s["mode"] = a.mode
        if a.mode == "off":
            s["declined_at"] = date.today().isoformat()
        save(s)
        print(f"режим {a.mode}")
    elif a.cmd == "answer":
        save(cmd_answer(s, a.answer, a.days))
        print(f"записано: {a.answer}, режим {load()['mode']}")
    elif a.cmd == "reset":
        for f in [state_path()] + ([card_file(s)] if a.card else []):
            f.unlink(missing_ok=True)
        print("сброшено" + (" вместе с карточкой" if a.card else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
