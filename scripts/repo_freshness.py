# /// script
# dependencies = []
# ///
"""Свежесть рабочего репозитория в начале сессии агента (хук SessionStart).

Проблема, которую закрывает: правила, скиллы и реестры команды живут в git, а агент читает
локальную копию. Если человек не сказал «обнови», его агент неделями работает по старым
правилам — и уверенно отвечает то, что уже исправлено (инцидент 23.09.2026: агент сказал
разработчику, что SSH к препроду не настроен, хотя его подняли месяц назад).

Что делает (одна короткая строка в контекст агента, никогда не ломает сессию):
  1. не чаще раза в --every часов (метка в .git/) делает `git fetch` с таймаутом, без
     интерактивных запросов пароля;
  2. если текущая ветка отстаёт от своего upstream — печатает, на сколько коммитов и что
     сделать (`--hint`), чтобы агент предложил человеку обновиться;
  3. HEAD отсоединён (клон стоит на пин-теге, как toolkit внутри команды) — молчит: такой клон
     обновляет не git pull, а поднятие пина в командном репозитории.

Сам НЕ делает pull: чужое рабочее дерево может быть грязным, ветка — не той. Решение за
человеком, агент только знает и предлагает.

Примеры:
  python scripts/repo_freshness.py                       # репозиторий текущего каталога
  python scripts/repo_freshness.py --repo ../team --hint "скажи: подтяни изменения"
  python scripts/repo_freshness.py --every 0             # проверить сейчас, без кэша
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never",
            GIT_ASKPASS="", SSH_ASKPASS="", LC_ALL="C")


def _git(repo: Path, *args, timeout: int = 10):
    """(код, stdout) — любой сбой, включая таймаут, превращается в код != 0, не в исключение."""
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=_ENV)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def check(repo: Path, every_hours: float = 20, fetch_timeout: int = 15, hint: str = "") -> str:
    """Строка для контекста агента или '' (всё свежо / проверять не нужно / проверить нельзя)."""
    code, top = _git(repo, "rev-parse", "--show-toplevel")
    if code:
        return ""
    repo = Path(top)
    code, branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if code:
        return ""                                   # detached HEAD: клон на пин-теге
    code, upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if code:
        return ""                                   # ветка без upstream — сравнивать не с чем

    code, gitdir = _git(repo, "rev-parse", "--git-dir")
    stamp = (repo / gitdir if not os.path.isabs(gitdir) else Path(gitdir)) / "repo-freshness.stamp"
    fetched = True
    if every_hours > 0 and stamp.exists() and time.time() - stamp.stat().st_mtime < every_hours * 3600:
        fetched = False                             # недавно пробовали — сравниваем с тем, что есть
    else:
        # Метка ПОПЫТКИ, а не успеха: без сети/VPN иначе каждая сессия (и resume/compact) снова ждала бы
        # таймаут fetch. Не вышло сейчас — попробуем через every_hours, а сравним с последним удачным.
        try:
            stamp.touch()
        except OSError:
            pass
        code, _ = _git(repo, "fetch", "--quiet", "--no-tags", timeout=fetch_timeout)
        fetched = code == 0

    code, counts = _git(repo, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
    parts = counts.split()
    if code or len(parts) != 2 or not all(x.isdigit() for x in parts):
        return ""
    ahead, behind = int(parts[0]), int(parts[1])
    if behind == 0:
        return ""
    _, last = _git(repo, "log", "-1", "--format=%cs %s", upstream)
    note = "" if fetched else " (по последней удачной проверке)"
    head = (f"[свежесть] {repo.name}: ветка {branch} отстаёт от {upstream} на {behind} коммит(ов){note}; "
            f"последний там: {last}. Правила и знания в этой сессии могут быть устаревшими — ")
    if ahead:
        # pull --ff-only здесь заведомо упадёт: у человека свои коммиты. Решение за ним.
        return head + (f"и в ней {ahead} своих коммит(ов), ветка разошлась с upstream. Скажи об этом "
                       f"человеку до начала работы и не обновляй сам: нужен rebase/merge с его решением")
    what = hint or f"git -C \"{repo}\" pull --ff-only"
    return head + f"предложи человеку обновиться до начала работы: {what}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=".", help="каталог внутри репозитория (по умолчанию текущий)")
    p.add_argument("--every", type=float, default=20, help="fetch не чаще раза в N часов; 0 — каждый раз")
    p.add_argument("--timeout", type=int, default=15, help="таймаут git fetch, секунд")
    p.add_argument("--hint", default="", help="что предложить человеку, если репо отстал")
    a = p.parse_args(argv)
    try:
        msg = check(Path(a.repo), a.every, a.timeout, a.hint)
    except Exception:                               # хук сессии не имеет права падать
        msg = ""
    if msg:
        print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
