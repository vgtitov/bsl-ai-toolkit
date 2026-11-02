#!/usr/bin/env python3
"""Очередь ревью на мне: GitLab (to-do review_requested + открытые MR с мной ревьюером) и GitHub (gh).

  GITLAB_HOST=gitlab.example.ru GITLAB_TOKEN=... python scripts/review_inbox.py
  python scripts/review_inbox.py --no-gitlab        # только GitHub (нужен gh auth login)
  python scripts/review_inbox.py --self-test        # проверка разбора без сети

Только чтение. Токен GitLab (read_api) берётся из env GITLAB_TOKEN или GITLAB_PERSONAL_ACCESS_TOKEN
и уходит только на GITLAB_HOST/--host. Для GitHub нужен gh с входом (scope repo); токен в репозиторий не кладём.
Код выхода: 0 очередь получена (пустая тоже), 2 источник недоступен.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mr_review import _gitlab_get, valid_host  # noqa: E402  # тот же клиент: без редиректов, хост только явный


def gitlab_queue(host: str, token: str, get=_gitlab_get) -> list[dict]:
    """Список {src, kind, ref, title, author, url}; to-do и MR склеены по url. Первые 50 записей каждого списка."""
    if not valid_host(host):
        raise ValueError("host: допустимо имя узла с необязательным портом")
    api = f"https://{host}/api/v4"
    user = get(f"{api}/user", token)["username"]
    out: dict[str, dict] = {}
    for t in get(f"{api}/todos?state=pending&action=review_requested&type=MergeRequest&per_page=50", token):
        tg = t.get("target") or {}
        out[t.get("target_url", "")] = {"src": "gitlab", "kind": "todo", "ref": f"!{tg.get('iid', '?')}",
                                        "title": tg.get("title", ""), "author": (t.get("author") or {}).get("username", ""),
                                        "url": t.get("target_url", "")}
    q = urllib.parse.urlencode({"reviewer_username": user, "state": "opened", "scope": "all", "per_page": 50})
    for m in get(f"{api}/merge_requests?{q}", token):
        u = m.get("web_url", "")
        out.setdefault(u, {"src": "gitlab", "kind": "mr", "ref": f"!{m.get('iid')}", "title": m.get("title", ""),
                           "author": (m.get("author") or {}).get("username", ""), "url": u})
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


def render(items: list[dict]) -> str:
    return "\n".join(f"{clean(i['src']):6} {clean(i['ref']):28} {clean(i['author']):16} {clean(i['title'])}\n       {clean(i['url'])}" for i in items) or "Ревью на вас нет."


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
    bad = render([{"src": "gitlab", "ref": "!1", "author": "a", "title": "x\x1b]52;c;QQ\x07\r\u202ey", "url": "u"}])
    assert "\x1b" not in bad and "\r" not in bad and "\u202e" not in bad and "\x07" not in bad, bad
    print("self-test ok")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="хост GitLab (или GITLAB_HOST)")
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
    print(render(items))
    return rc


if __name__ == "__main__":
    sys.exit(main())
