#!/usr/bin/env python3
"""Очередь ревью на мне: GitLab (to-do review_requested + открытые MR с мной ревьюером) и GitHub (gh).

  GITLAB_HOST=gitlab.example.ru GITLAB_TOKEN=... python scripts/review_inbox.py
  python scripts/review_inbox.py --no-gitlab        # только GitHub (нужен gh auth login)
  python scripts/review_inbox.py --self-test        # проверка разбора без сети

Только чтение. Токен GitLab (read_api) берётся из env GITLAB_TOKEN или GITLAB_PERSONAL_ACCESS_TOKEN
и уходит только на GITLAB_HOST/--host. Для GitHub нужен gh с входом (scope repo); токен в репозиторий не кладём.
--issues добавляет чужие открытые issue и PR репозитория (--repo owner/name или текущий каталог) без ответа
и помечает просрочку: issue 2 рабочих дня, чужой PR и ревью на вас 3 (понедельник-пятница, праздники не учитываются).
Код выхода: 0 очередь получена (пустая тоже), 1 есть просроченное, 2 источник недоступен.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mr_review import _gitlab_get, valid_host  # noqa: E402  # тот же клиент: без редиректов, хост только явный


LIMITS = {"issue": 2, "pr": 3, "review": 3}  # рабочих дней до первого ответа


def workdays(since: str, now: dt.datetime | None = None) -> int:
    """Сколько рабочих дней (пн-пт) прошло с ISO-даты since; праздники не учитываются."""
    a = dt.datetime.fromisoformat(since.replace("Z", "+00:00")).date()
    b = (now or dt.datetime.now(dt.timezone.utc)).date()
    return sum(1 for i in range(1, (b - a).days + 1) if (a + dt.timedelta(days=i)).weekday() < 5)


def gitlab_queue(host: str, token: str, get=_gitlab_get) -> list[dict]:
    """Список {src, kind, ref, title, author, url}; to-do и MR склеены по url. Первые 50 записей каждого списка."""
    if not valid_host(host):
        raise ValueError("host: допустимо имя узла с необязательным портом")
    api = f"https://{host}/api/v4"
    user = get(f"{api}/user", token)["username"]
    out: dict[str, dict] = {}
    try:
        todos = get(f"{api}/todos?state=pending&action=review_requested&type=MergeRequest&per_page=50", token)
    except Exception:  # старый GitLab без фильтра action: очередь всё равно даёт список MR ниже
        todos = [t for t in get(f"{api}/todos?state=pending&type=MergeRequest&per_page=50", token)
                 if t.get("action_name") == "review_requested"]
    for t in todos:
        tg = t.get("target") or {}
        out[t.get("target_url", "")] = {"src": "gitlab", "kind": "todo", "ref": f"!{tg.get('iid', '?')}",
                                        "title": tg.get("title", ""), "author": (t.get("author") or {}).get("username", ""),
                                        "url": t.get("target_url", ""), "since": t.get("created_at", ""), "limit": "review"}
    q = urllib.parse.urlencode({"reviewer_username": user, "state": "opened", "scope": "all", "per_page": 50})
    for m in get(f"{api}/merge_requests?{q}", token):
        u = m.get("web_url", "")
        out.setdefault(u, {"src": "gitlab", "kind": "mr", "ref": f"!{m.get('iid')}", "title": m.get("title", ""),
                           "author": (m.get("author") or {}).get("username", ""), "url": u, "since": m.get("created_at", ""), "limit": "review"})
    return list(out.values())


def github_queue(run=subprocess.run) -> list[dict]:
    r = run(["gh", "search", "prs", "--review-requested=@me", "--state=open", "--json",
             "repository,number,title,url,author", "--limit", "50"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "gh не ответил").strip()[:200])
    return [{"src": "github", "kind": "pr", "ref": f"{p['repository']['nameWithOwner']}#{p['number']}", "title": p["title"],
             "author": (p.get("author") or {}).get("login", ""), "url": p["url"]} for p in json.loads(r.stdout or "[]")]


_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def clean(v) -> str:
    """Заголовки и логины чужие: управляющие символы (ANSI/OSC, \\r, bidi) не доходят до терминала."""
    return _CTRL.sub(" ", str(v))


def github_attention(repo: str | None = None, run=subprocess.run) -> list[dict]:
    """Чужие открытые issue и PR репозитория, на которые никто кроме автора (и не бот) не ответил."""
    def gh(*a):
        r = run(["gh", *a], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "gh не ответил").strip()[:200])
        return r.stdout
    me = gh("api", "user", "--jq", ".login").strip()
    extra = ["--repo", repo] if repo else []
    out = []
    for kind, cmd, fields in (("issue", "issue", "number,title,url,author,createdAt,comments,labels"),
                              ("pr", "pr", "number,title,url,author,createdAt,comments,reviews,isDraft")):
        for x in json.loads(gh(cmd, "list", "--state", "open", "--limit", "100", "--json", fields, *extra) or "[]"):
            who = (x.get("author") or {}).get("login", "")
            if who == me or x.get("isDraft"):
                continue
            talk = (x.get("comments") or []) + (x.get("reviews") or [])

            def human_other(c):
                au = c.get("author") or {}
                return au.get("login") not in (None, who) and not au.get("is_bot") and not au["login"].endswith("[bot]")
            if any(human_other(c) for c in talk):
                continue
            out.append({"src": "github", "kind": kind, "ref": f"{kind}#{x['number']}", "title": x["title"], "author": who,
                        "url": x["url"], "since": x["createdAt"], "limit": kind})
    return out


def overdue(i: dict, now: dt.datetime | None = None) -> int:
    """Рабочих дней сверх срока, 0 если срок не нарушен или даты нет."""
    if not i.get("since") or i.get("limit") not in LIMITS:
        return 0
    return max(0, workdays(i["since"], now) - LIMITS[i["limit"]])


def render(items: list[dict]) -> str:
    def mark(i):
        o = overdue(i)
        return f"  [ПРОСРОЧЕНО на {o} р.д.]" if o else ""
    return "\n".join(f"{clean(i['src']):6} {clean(i['ref']):28} {clean(i['author']):16} {clean(i['title'])}{mark(i)}\n       {clean(i['url'])}" for i in items) or "Ревью на вас нет."


def self_test() -> None:
    def fake_get(url, token):
        if url.endswith("/user"):
            return {"username": "rev"}
        if "/todos" in url:
            return [{"target_url": "https://h/p/-/merge_requests/1", "author": {"username": "a"}, "target": {"iid": 1, "title": "T1"}}]
        return [{"iid": 1, "web_url": "https://h/p/-/merge_requests/1", "title": "T1", "author": {"username": "a"}},
                {"iid": 2, "web_url": "https://h/p/-/merge_requests/2", "title": "T2", "author": {"username": "b"}}]
    g = gitlab_queue("h", "x", fake_get)
    assert [i["ref"] for i in g] == ["!1", "!2"] and g[0]["kind"] == "todo", g
    gh_out = json.dumps([{"repository": {"nameWithOwner": "o/r"}, "number": 5, "title": "P", "url": "u", "author": {"login": "c"}}])
    h = github_queue(lambda *a, **k: subprocess.CompletedProcess(a, 0, gh_out, ""))
    assert h[0]["ref"] == "o/r#5", h
    try:
        gitlab_queue("h/evil", "x", fake_get)
        raise SystemExit("host check missing")
    except ValueError:
        pass
    assert render([]) == "Ревью на вас нет."
    # 2026-11-02 понедельник: с пятницы 30.10 прошёл 1 рабочий день, с вторника 27.10 - 4
    now = dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc)
    assert workdays("2026-10-30T10:00:00Z", now) == 1 and workdays("2026-10-27T10:00:00Z", now) == 4
    assert workdays("2026-10-31T10:00:00Z", now) == 1  # суббота -> понедельник
    assert overdue({"since": "2026-10-27T10:00:00Z", "limit": "issue"}, now) == 2
    assert overdue({"since": "2026-10-29T10:00:00Z", "limit": "pr"}, now) == 0
    ans = lambda name, bot=False: {"author": {"login": name, "is_bot": bot}}
    def fake_run(cmd, **k):
        a = " ".join(cmd)
        out = {"api user": "me\n",
               "issue list": json.dumps([
                   {"number": 1, "title": "t", "url": "u1", "author": {"login": "x"}, "createdAt": "2026-10-27T10:00:00Z", "comments": [], "labels": []},
                   {"number": 2, "title": "t", "url": "u2", "author": {"login": "x"}, "createdAt": "2026-10-27T10:00:00Z", "comments": [ans("me")], "labels": []},
                   {"number": 3, "title": "t", "url": "u3", "author": {"login": "x"}, "createdAt": "2026-10-27T10:00:00Z", "comments": [ans("dependabot", True)], "labels": []},
                   {"number": 4, "title": "t", "url": "u4", "author": {"login": "me"}, "createdAt": "2026-10-27T10:00:00Z", "comments": [], "labels": []}]),
               "pr list": json.dumps([{"number": 9, "title": "p", "url": "u9", "author": {"login": "y"}, "createdAt": "2026-10-27T10:00:00Z",
                                      "comments": [], "reviews": [ans("me")], "isDraft": False}])}
        key = next(k for k in out if k in a)
        return subprocess.CompletedProcess(cmd, 0, out[key], "")
    att = github_attention(None, fake_run)
    assert [i["ref"] for i in att] == ["issue#1", "issue#3"], att
    bad = render([{"src": "gitlab", "ref": "!1", "author": "a", "title": "x\x1b]52;c;QQ\x07\r\u202ey", "url": "u"}])
    assert "\x1b" not in bad and "\r" not in bad and "\u202e" not in bad and "\x07" not in bad, bad
    print("self-test ok")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="хост GitLab (или GITLAB_HOST)")
    ap.add_argument("--issues", action="store_true", help="добавить чужие issue и PR без ответа с просрочкой")
    ap.add_argument("--repo", help="owner/name для --issues (по умолчанию репозиторий текущего каталога)")
    ap.add_argument("--no-gitlab", action="store_true")
    ap.add_argument("--no-github", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        self_test()
        return 0
    items, rc = [], 0
    if not a.no_gitlab:
        host = a.host or os.environ.get("GITLAB_HOST") or ""
        token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GITLAB_PERSONAL_ACCESS_TOKEN") or ""
        if not host or not token:
            print("GitLab пропущен: нужны GITLAB_HOST (или --host) и GITLAB_TOKEN.", file=sys.stderr)
            rc = 2
        else:
            try:
                items += gitlab_queue(host, token)
            except Exception as e:  # сеть, 401, 403: коротко и без токена
                print(f"GitLab недоступен: {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
                rc = 2
    if not a.no_github:
        try:
            items += github_queue()
        except Exception as e:
            print(f"GitHub недоступен: {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
            rc = 2
    if a.issues:
        try:
            items += github_attention(a.repo)
        except Exception as e:
            print(f"GitHub issue недоступны: {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
            rc = 2
    print(render(items))
    return rc or (1 if any(overdue(i) for i in items) else 0)


if __name__ == "__main__":
    sys.exit(main())
