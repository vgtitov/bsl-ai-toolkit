#!/usr/bin/env python3
"""Режим наставника ai-mentor: off | ask | on. Состояние в личном файле человека вне репозитория.

  python scripts/mentor.py status                 # что хранится (файл показывается целиком)
  python scripts/mentor.py set on|off|ask         # включает только явная команда человека; по умолчанию off
  python scripts/mentor.py answer yes|no|later [--days 30]   # ответ на предложение
  python scripts/mentor.py offered                # предложение показано: записать сразу, до ответа
  python scripts/mentor.py api-asked [--days 30]  # про Jira и Confluence по API спросили; can-offer-api проверяет срок
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
import tempfile
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MODES = ("off", "ask", "on")
SCHEMA = 1  # формат записи; чужой или отсутствующий в существующем файле = off, обновление наставника не включает
COOLDOWN_DAYS = 14  # не чаще одного предложения за этот срок (и не больше одного за сессию)
ROOT = Path(__file__).resolve().parent.parent


def skill_dir() -> str:
    """Где лежит скилл относительно корня: установленный (.claude) или исходный (core)."""
    for d in (".claude/skills/ai-mentor", "core/skills/ai-mentor"):
        if (ROOT / d / "SKILL.md").is_file():
            return d
    return ".claude/skills/ai-mentor"


def state_path() -> Path:
    return Path(os.environ.get("AI_MENTOR_STATE") or Path.home() / ".ai-mentor.json")


def card_path() -> Path:
    return Path(os.environ.get("AI_MENTOR_CARD") or Path.home() / ".ai-mentor-card.md")


def default_state() -> dict:
    return {"schema": SCHEMA, "mode": "off", "declined_at": None, "remind_after": None, "offers_made": 0,
            "last_offer": None, "api_remind_after": None, "api_declined_at": None, "onboarding_asked": None, "level_card": str(card_path()), "updated": None}


def load() -> dict:
    s = default_state()
    if not state_path().exists():
        return s
    try:
        d = json.loads(state_path().read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError
        if type(d.get("schema")) is not int or d["schema"] != SCHEMA:  # True и 1.0 не годятся
            raise ValueError("другой формат записи")
        s.update({k: d[k] for k in s if k in d})
        for k in ("declined_at", "remind_after", "last_offer", "api_remind_after", "api_declined_at", "onboarding_asked"):
            if s[k] is not None:
                date.fromisoformat(s[k])  # TypeError/ValueError на неверном типе или формате
        if type(s["offers_made"]) is not int or not isinstance(s["level_card"], str):
            raise ValueError
    except (OSError, ValueError, TypeError):  # файл есть, но не читается: согласие неизвестно, значит off
        s = default_state()
        s["mode"] = "off"
    if s["mode"] not in MODES:  # битый файл не должен включать ментора
        s["mode"] = "off"
    return s


def save(s: dict) -> None:
    s["updated"] = date.today().isoformat()
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".")  # уникальный файл, права 0600
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(s, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, p)


@contextmanager
def locked():
    """Блокировка на цикл чтение-изменение-запись, чтобы два агента не затёрли отказ друг друга."""
    lock = state_path().with_name(state_path().name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL))
            break
        except FileExistsError:
            try:  # блокировка старше минуты: хозяин умер, снимаем
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            time.sleep(0.05)  # ponytail: снятие устаревшей блокировки не атомарно, для личного файла достаточно
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


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


def can_offer_api(s: dict, today: date | None = None) -> tuple[bool, str]:
    """Предложение подключить Jira и Confluence по API: отдельный срок, работает и в режиме on."""
    if s["mode"] == "off" or s["api_declined_at"]:
        return False, "режим off или отказ от API"
    ra = _d(s["api_remind_after"])
    if ra and (today or date.today()) < ra:
        return False, f"про API спрашивали, не раньше {ra}"
    return True, "можно"


ONBOARDING_Q = "Включить наставника по работе с ИИ (подсказки, как лучше ставить задачи)? По умолчанию нет. [да/нет]"


ONBOARDING_REASK_DAYS = 30  # вопрос показан, ответа нет: переспросить не раньше


def onboarding_needed(s: dict, today: date | None = None) -> bool:
    """Спросить при первой установке и при обновлении до версии с наставником; ответ закрывает вопрос."""
    if s["mode"] != "off" or s["declined_at"]:
        return False
    asked = _d(s["onboarding_asked"])
    return asked is None or (today or date.today()) >= asked + timedelta(days=ONBOARDING_REASK_DAYS)


def onboarding_answer(s: dict, yes: bool, today: date | None = None) -> dict:
    today = (today or date.today()).isoformat()
    s["onboarding_asked"] = today
    if yes:
        s["mode"] = "on"
    else:
        s["declined_at"] = today
    return s


def hook_line(s: dict, today: date | None = None) -> str:
    if s["mode"] == "on":
        return f"ai-mentor on: read {skill_dir()}/SKILL.md"
    if can_offer(s, today)[0]:
        return f"mentor ask, weak prompt or pasted Jira text: {skill_dir()}/OFFER.md"
    return ""


def card_file(s: dict) -> Path:
    p = Path(s["level_card"]) if s.get("level_card") else card_path()
    # путь из файла состояния не доверяем: удаляем и показываем только файл, похожий на карточку
    return p if p.suffix == ".md" and p.name.startswith("ai-mentor-card") else card_path()


def mark_offered(s: dict, today: date | None = None) -> dict:
    if s["mode"] == "off":  # отказ не отменяется запоздавшей записью
        return s
    s["offers_made"] += 1
    s["last_offer"] = (today or date.today()).isoformat()
    return s


def cmd_answer(s: dict, ans: str, days: int, today: date | None = None) -> dict:
    today = today or date.today()
    if s["mode"] == "off":  # запоздавший ответ не отменяет отказ; включает только set on
        return s
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


def days_arg(v: str) -> int:
    n = int(v)
    if not 1 <= n <= 3650:
        raise argparse.ArgumentTypeError("дней от 1 до 3650")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("can-offer")
    sub.add_parser("offered")
    sub.add_parser("can-offer-api")
    sub.add_parser("api-declined")
    sub.add_parser("onboarding")
    p = sub.add_parser("onboarding-answer"); p.add_argument("answer", choices=("yes", "no"))
    p = sub.add_parser("api-asked"); p.add_argument("--days", type=days_arg, default=30)
    sub.add_parser("hook")
    p = sub.add_parser("set"); p.add_argument("mode", choices=MODES)
    p = sub.add_parser("answer"); p.add_argument("answer", choices=("yes", "no", "later"))
    p.add_argument("--days", type=days_arg, default=30)
    p = sub.add_parser("reset"); p.add_argument("--card", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd in WRITE:
        with locked():
            return run_cmd(a, load())
    return run_cmd(a, load())


WRITE = ("onboarding", "onboarding-answer", "set", "answer", "offered", "api-asked", "api-declined", "reset")


def run_cmd(a, s) -> int:
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
    elif a.cmd == "can-offer-api":
        ok, why = can_offer_api(s)
        print(("да: " if ok else "нет: ") + why)
        return 0 if ok else 1
    elif a.cmd == "api-asked":
        s["api_remind_after"] = (date.today() + timedelta(days=a.days)).isoformat()
        save(s)
        print(f"про API не спрашивать до {s['api_remind_after']}")
    elif a.cmd == "onboarding":
        if not onboarding_needed(s):
            return 0
        if sys.stdin.isatty():
            try:
                yes = input(ONBOARDING_Q + " ").strip().lower() in ("да", "д", "y", "yes")
            except EOFError:
                s["onboarding_asked"] = date.today().isoformat()  # показали, ответа нет
                save(s)
                return 0
            save(onboarding_answer(s, yes))
            print("наставник включён" if yes else "хорошо, наставник выключен")
        else:  # запуск агентом: вопрос задаёт агент человеку, затем onboarding-answer
            s["onboarding_asked"] = date.today().isoformat()  # показан; без ответа переспросим через 30 дней
            save(s)
            print(f"ВОПРОС ЧЕЛОВЕКУ: {ONBOARDING_Q}\nОтвет «да»: python scripts/mentor.py onboarding-answer yes; "
                  "любой другой: onboarding-answer no. Молчание: ничего не делать.")
    elif a.cmd == "onboarding-answer":
        if a.answer == "yes" and (s["declined_at"] or s["mode"] != "off"):  # запоздавшее «да» не отменяет выбор
            print("режим уже выбран, не меняю; включить явно: /ai-mentor on")
            return 0
        save(onboarding_answer(s, a.answer == "yes"))
        print("записано: " + a.answer)
    elif a.cmd == "api-declined":
        s["api_declined_at"] = date.today().isoformat()
        save(s)
        print("про API больше не спрашивать")
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
