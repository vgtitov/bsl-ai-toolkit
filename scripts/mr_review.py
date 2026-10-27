#!/usr/bin/env python3
"""Первый проход по merge request до человека: размер, описание, детерминированные проверки, список P1 и P2.

  python scripts/mr_review.py --base origin/main [--description mr.md] [--tz постановка.md]
                              [--second-opinion] [--risk normal|critical|low] [--json]
  python scripts/mr_review.py --mr 12 [--project группа/проект] [--no-second-opinion]
      MR по номеру из GitLab (токен GITLAB_TOKEN или GITLAB_PERSONAL_ACCESS_TOKEN, адрес из git remote):
      статус, размер, описание, наличие CI и по умолчанию второе мнение другой модели.

Что делает (всё бесплатное и локальное, кроме необязательного второго мнения):
  1. размер по объектам метаданных и ручным строкам BSL (`mr_size.py`);
  2. описание MR: есть ли части «что», «зачем», «как проверить», «риск»;
  3. по изменённым .bsl: `bsl_guard` (обращение к базе в цикле, P1), `rights_guard` (права и RLS, P1 или P2),
     `bsl_ls_top` (частые замечания BSL LS, P2; нужны BSL_JAR и java, иначе молча пропускается);
  4. с `--second-opinion`: запрос второй модели с диффом, постановкой и чек-листом (`second_opinion.py ask`).
Код выхода 1, если есть P1; иначе 0. Вывод короткий: только находки и их место.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mr_size  # noqa: E402

CHECKLIST = HERE.parent / "core" / "skills" / "1c-dev" / "references" / "review-checklist.md"
PARTS = {"что": ("что", "суть", "изменения"), "зачем": ("зачем", "цель", "задача"),
         "как проверить": ("как проверить", "проверка", "тест"), "риск": ("риск", "откат")}


def _git(repo: Path, *a: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def changed_bsl(repo: Path, base: str) -> list[Path]:
    out = _git(repo, "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD")
    return [repo / p for p in out.splitlines() if p.lower().endswith(".bsl") and (repo / p).is_file()]


def description_gaps(text: str) -> list[str]:
    low = (text or "").casefold()
    return [part for part, keys in PARTS.items() if not any(k in low for k in keys)]


def _run(script: str, *args: str) -> tuple[int, str]:
    try:
        r = subprocess.run([sys.executable, str(HERE / script), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        return r.returncode, (r.stdout or "").strip()
    except Exception:
        return 0, ""


def findings(repo: Path, base: str, description: str | None, max_objects: int, max_bsl: int) -> tuple[list, dict]:
    out: list[tuple[str, str, str]] = []                # (P1|P2, место, суть)
    res = mr_size.measure(repo, base)
    v, why = mr_size.verdict(res, max_objects, max_bsl)
    if why and not (description and "обоснован" in description.casefold()):
        out.append(("P2", "MR", f"размер требует обоснования: {'; '.join(why)}. Добавьте абзац, почему не разбито на части"))
    if description is not None:
        gaps = description_gaps(description)
        if gaps:
            out.append(("P2", "описание MR", "не хватает частей: " + ", ".join(gaps)))
    for f in changed_bsl(repo, base):
        rel = os.path.relpath(f, repo)
        rc, o = _run("bsl_guard.py", str(f))
        if rc != 0 and o:
            out.append(("P1", rel, "обращение к базе в цикле: " + o.splitlines()[0][:160]))
        rc, o = _run("rights_guard.py", "--json", str(f))
        try:
            for it in json.loads(o) if o else []:
                out.append(("P1" if it.get("severity") == "error" else "P2", f'{rel}:{it.get("line")}',
                            f'права [{it.get("code")}] {it.get("message")}'))
        except ValueError:
            pass
        _, o = _run("bsl_ls_top.py", str(f))
        for ln in o.splitlines():
            out.append(("P2", f"{rel} ({ln.split(' ')[0]})", ln.split(' ', 1)[1] if ' ' in ln else ln))
    out.sort(key=lambda x: x[0])
    return out, res


def second_opinion_prompt(description: str | None, has_tz: bool, generic: bool = False) -> str:
    return ("Ты проверяющий merge request" + ("" if generic else " на 1С:Предприятие") + ". Во вложениях дифф, чек-лист ревью"
            + (", постановка задачи" if has_tz else "") + (" и описание MR" if description else "") + ". "
            "Найди дефекты и риски по приоритетам. P1 блокирует слияние: потеря данных, обход прав, запрос в цикле, "
            "транзакции, необработанные ошибки обменов и заданий" + ("" if generic else ", правка типовой") + ". P2 чинить до релиза. "
            "По каждой находке дай файл и место, причину и как исправить. Не пиши, что ошибок нет, пока не прошёл "
            "каждый раздел чек-листа; по разделам, где проблем не нашёл, напиши одну строку, что именно проверено. "
            "Сверяй дифф с постановкой: всё ли требуемое сделано и нет ли лишнего.")


def remote_project(url: str) -> tuple[str, str]:
    """(хост, путь проекта) из адреса git remote: ssh git@host:group/p.git или https://host/group/p.git."""
    u = url.strip()
    m = re.match(r"^(?:ssh|https?)://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+?)(?:\.git)?/?$", u) \
        or re.match(r"^(?:[^@/]+@)?([^/:]+):(?!//)(.+?)(?:\.git)?/?$", u)
    return (m.group(1), m.group(2)) if m else ("", "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # заголовок с токеном не должен уходить на другой хост
        return None


def _gitlab_get(url: str, token: str):
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d{1,5})?$")


def valid_host(host: str) -> bool:
    """Только имя узла и необязательный порт: без логина@, пути, запроса и фрагмента (токен не уйдёт на чужой адрес)."""
    return bool(_HOST_RE.match(host or ""))


def fetch_mr(host: str, project: str, number: int, token: str, get=_gitlab_get) -> dict:
    """Описание, статус, CI и дифф MR из GitLab API (только чтение)."""
    if not valid_host(host):
        raise ValueError("host: допустимо имя узла с необязательным портом, без логина, пути и параметров")
    base = f"https://{host}/api/v4/projects/{urllib.parse.quote(project, safe='')}/merge_requests/{number}"
    m = get(base, token)
    changes = get(base + "/changes?access_raw_diffs=true", token)
    ch = changes.get("changes", [])
    diff = "\n".join(f"--- {c['new_path']}\n{c['diff']}" for c in ch)
    lines = [ln for c in ch for ln in c["diff"].splitlines()]
    return {"title": m.get("title", ""), "description": m.get("description") or "", "state": m.get("state", ""),
            "pipeline": m.get("head_pipeline"), "overflow": bool(changes.get("overflow")), "source": m.get("source_branch", ""), "target": m.get("target_branch", ""),
            "files": [c["new_path"] for c in ch], "diff": diff,
            "added": sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++")),
            "removed": sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))}


def mr_findings(mr: dict) -> list[tuple[str, str, str]]:
    out = []
    pipe = mr.get("pipeline")
    if pipe is None:
        out.append(("P2", "MR", "head_pipeline пуст: CI не настроен или поле недоступно (права, версия GitLab)"))
    elif str(pipe.get("status", "")) not in ("success", "running", "pending"):
        out.append(("P1", "MR", f"CI в статусе {pipe.get('status')}: слияние без зелёного конвейера"))
    if mr.get("overflow"):
        out.append(("P2", "MR", "дифф усечён GitLab (overflow): ревью неполное, смотреть остальное руками"))
    gaps = description_gaps(mr.get("description", ""))
    if gaps:
        out.append(("P2", "описание MR", "не хватает частей: " + ", ".join(gaps)))
    return out


def _review_mr(a) -> int:
    token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GITLAB_PERSONAL_ACCESS_TOKEN") or ""
    # хост только явный (--host или GITLAB_HOST): токен не уходит на адрес из чужого git remote
    host = a.host or os.environ.get("GITLAB_HOST") or ""
    project = a.project or remote_project(_git(a.repo, "remote", "get-url", "origin").strip())[1]
    if not (token and host and project):
        print("Нужны GITLAB_TOKEN (или GITLAB_PERSONAL_ACCESS_TOKEN), --host или GITLAB_HOST и --project группа/проект (или git remote).")
        return 2
    if not valid_host(host):
        print("--host/GITLAB_HOST: нужно имя узла с необязательным портом (без логина@, пути и параметров).")
        return 2
    try:
        mr = fetch_mr(host, project, a.mr, token)
    except urllib.error.HTTPError as e:
        print(f"GitLab ответил HTTP {e.code}: проверьте токен, права, номер MR и проект.")
        return 2
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"GitLab недоступен ({type(e).__name__}): проверьте сеть и --host.")
        return 2
    except (ValueError, KeyError, TypeError):
        print("GitLab вернул неожиданный ответ (не JSON или другая структура).")
        return 2
    fl = mr_findings(mr)
    print(f'MR !{a.mr} {mr["state"]}: {mr["title"]} ({mr["source"]} -> {mr["target"]})')
    print(f'Размер: файлов {len(mr["files"])}, +{mr["added"]} -{mr["removed"]}. CI: {(mr["pipeline"] or {}).get("status", "нет")}.')
    for p, where, what in fl:
        print(f"{p}  {where}  {what}")
    if not a.no_second_opinion:
        import second_opinion
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="mr_review_"))
        diff, desc = tmp / f"mr{a.mr}.diff", tmp / f"mr{a.mr}.description.md"
        diff.write_text(mr["diff"], encoding="utf-8")
        desc.write_text(mr["description"], encoding="utf-8")
        bsl = any(f.lower().endswith(".bsl") for f in mr["files"])
        files = [str(diff), str(desc)] + ([str(CHECKLIST)] if bsl else [])
        who, body = second_opinion.ask(second_opinion_prompt(mr["description"], False, generic=not bsl), files=files, risk=a.risk)
        print(f"\n== Второе мнение ({who}) ==\n{body}" if body else "\nВторое мнение: никто не ответил (см. second_opinion.py status)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--description", type=Path)
    ap.add_argument("--tz", type=Path)
    ap.add_argument("--max-objects", type=int, default=mr_size.MAX_OBJECTS)
    ap.add_argument("--max-bsl", type=int, default=mr_size.MAX_BSL)
    ap.add_argument("--second-opinion", action="store_true")
    ap.add_argument("--risk", choices=["critical", "normal", "low"], default="normal")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--mr", type=int, help="номер MR в GitLab: ревью по API без локального diff")
    ap.add_argument("--project", help="группа/проект для --mr; по умолчанию из git remote origin")
    ap.add_argument("--host", help="хост GitLab для --mr (или GITLAB_HOST)")
    ap.add_argument("--no-second-opinion", action="store_true", help="с --mr второе мнение включено по умолчанию")
    a = ap.parse_args(argv)
    if a.mr is not None:
        if a.mr < 1:
            ap.error("--mr: номер MR должен быть положительным")
        return _review_mr(a)
    desc = a.description.read_text(encoding="utf-8") if a.description and a.description.is_file() else None
    fl, res = findings(a.repo, a.base, desc, a.max_objects, a.max_bsl)
    if a.json:
        print(json.dumps({"size": res, "findings": fl}, ensure_ascii=False))
    else:
        print(f'Размер: объектов {res["objects"]}, строк BSL {res["bsl_lines"]}.')
        if not fl:
            print("Детерминированные проверки: находок нет (это не значит, что ошибок нет: нужен человек и ревью по чек-листу).")
        for p, where, what in fl:
            print(f"{p}  {where}  {what}")
    if a.second_opinion:
        import second_opinion
        diff = Path(os.environ.get("TMPDIR", "/tmp")) / "mr_review.diff"
        diff.write_text(_git(a.repo, "diff", f"{a.base}...HEAD"), encoding="utf-8")
        files = [str(diff), str(CHECKLIST)] + ([str(a.tz)] if a.tz else []) + ([str(a.description)] if desc else [])
        who, body = second_opinion.ask(second_opinion_prompt(desc, bool(a.tz)), files=files, risk=a.risk)
        print(f"\n== Второе мнение ({who}) ==\n{body}" if body else "\nВторое мнение: никто не ответил (см. second_opinion.py status)")
    return 1 if any(p == "P1" for p, _, _ in fl) else 0


if __name__ == "__main__":
    sys.exit(main())
