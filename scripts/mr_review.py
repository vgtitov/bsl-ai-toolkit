#!/usr/bin/env python3
"""Первый проход по merge request до человека: размер, описание, детерминированные проверки, список P1 и P2.

  python scripts/mr_review.py --base origin/main [--description mr.md] [--tz постановка.md]
                              [--second-opinion] [--risk normal|critical|low] [--json]
  python scripts/mr_review.py --mr 12 [--project группа/проект] [--no-second-opinion]
      MR по номеру из GitLab (токен GITLAB_TOKEN или GITLAB_PERSONAL_ACCESS_TOKEN, адрес из git remote):
      статус, размер, описание, наличие CI и по умолчанию второе мнение другой модели.
  python scripts/mr_review.py status|create|note|merge|sync-main ...
      работа с MR (запись в GitLab): см. `python scripts/mr_review.py merge -h` и docs/MR_REVIEW.md.
      merge запускают только по слову ревьюера.

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
import difflib
import json
import os
import re
import ssl
import subprocess
import sys
import time
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


# ---------- работа с MR: status / create / note / merge / sync-main ----------

MANAGE_COMMANDS = ("status", "create", "note", "merge", "sync-main")
LONG_LIVED = ("develop", "main", "master")
WAIT_STATUSES = ("checking", "unchecked", "preparing")  # GitLab ещё считает, можно ли вливать


def gitlab_token(token_env: str | None = None) -> tuple[str | None, str | None]:
    """(имя переменной, значение): --token-env, иначе GITLAB_TOKEN_ENV (имя переменной), иначе
    GITLAB_TOKEN / GITLAB_PERSONAL_ACCESS_TOKEN. Значение наружу не печатается."""
    name = token_env or os.environ.get("GITLAB_TOKEN_ENV")
    for key in ([name] if name else ["GITLAB_TOKEN", "GITLAB_PERSONAL_ACCESS_TOKEN"]):
        if os.environ.get(key):
            return key, os.environ[key]
    return None, None


def load_dotenv(*dirs: Path) -> None:
    """Подхватить .env (без перезаписи уже заданных переменных); значения не печатаются."""
    for d in dirs:
        p = Path(d) / ".env"
        if p.is_file():
            for ln in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                m = re.match(r"^\s*(?:export\s+)?([A-Za-z_]\w*)\s*=(.*)$", ln)
                if m:
                    v = m.group(2).strip()
                    v = v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"" else v.split(" #", 1)[0].strip()
                    os.environ.setdefault(m.group(1), v)


def should_remove_source(branch: str, protected) -> bool:
    """Удалять исходную ветку только у обычной: не develop/main/master и точно не защищённой
    (protected is False; None - не удалось узнать - считаем защищённой)."""
    return branch not in LONG_LIVED and protected is False


def _added_bounds(lines: list[str]):
    """(начало, конец) тела первого `### Added` внутри `[Unreleased]` или None."""
    start = next((i for i, ln in enumerate(lines) if ln.strip().startswith("## [Unreleased]")), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    pos = next((i for i in range(start + 1, end) if lines[i].strip() == "### Added"), None)
    if pos is None:
        return None
    stop = next((i for i in range(pos + 1, end) if lines[i].startswith("### ")), end)
    return pos + 1, stop


def merge_changelog(base: str, ours: str, theirs: str) -> str:
    """CHANGELOG при конфликте: версия theirs (цель) + строки, добавленные веткой к merge-base, после первого
    `### Added` в `[Unreleased]`. Ветка меняла/удаляла строки или добавляла их в другие разделы -> ValueError."""
    b, o = base.replace("\r\n", "\n").split("\n"), ours.replace("\r\n", "\n").split("\n")
    t = theirs.replace("\r\n", "\n").split("\n")
    added, outside = [], []
    bounds = _added_bounds(o)  # по версии ветки: что она добавила - только в [Unreleased] / ### Added
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, b, o, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            raise ValueError("ветка изменила или удалила строки CHANGELOG, а не только добавила - сведи вручную")
        if tag == "insert":
            for j in range(j1, j2):
                if not o[j].strip() or (bounds and j == bounds[0] - 1):  # пустая строка или сам заголовок Added
                    continue
                (added if bounds and bounds[0] <= j < bounds[1] else outside).append(o[j])
    if outside:
        raise ValueError("ветка добавила строки вне [Unreleased] / ### Added - сведи вручную:\n    "
                         + "\n    ".join(outside))
    added = [ln for ln in added if ln not in set(t)]
    if not added:
        return "\n".join(t)
    start = next((i for i, ln in enumerate(t) if ln.strip().startswith("## [Unreleased]")), None)
    if start is None:
        raise ValueError("в CHANGELOG цели нет раздела [Unreleased]")
    end = next((i for i in range(start + 1, len(t)) if t[i].startswith("## ")), len(t))
    pos = next((i for i in range(start + 1, end) if t[i].strip() == "### Added"), None)
    if pos is None:
        t[start + 1:start + 1] = ["", "### Added"]
        pos = start + 2
    t[pos + 1:pos + 1] = added
    return "\n".join(t)


def merge_problems(mr: dict, branch: str, current: str, head: str, origin_head: str, behind: bool) -> list[str]:
    """Почему вливать нельзя (пусто - можно)."""
    p = []
    if current != branch:
        p.append(f"в рабочем каталоге ветка {current}, а не {branch}")
    if mr.get("source_branch") != branch:
        p.append(f"MR из ветки {mr.get('source_branch')}, а не {branch}")
    if mr.get("state") != "opened":
        p.append(f"MR в состоянии {mr.get('state')}")
    if head != origin_head:
        p.append(f"локальный HEAD не равен origin/{branch} (не запушено или отстаёт)")
    if behind:
        p.append(f"ветка не содержит origin/{mr.get('target_branch')}: сначала sync-main")
    if mr.get("detailed_merge_status") != "mergeable":
        p.append(f"detailed_merge_status={mr.get('detailed_merge_status')}")
    if mr.get("sha") != head:
        p.append(f"sha MR {mr.get('sha')} не равен HEAD {head}")
    return p


def wait_mergeable(get_mr, timeout: float = 60, step: float = 3, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Ждать, пока GitLab досчитает статус (checking/unchecked/preparing), не дольше timeout секунд."""
    deadline = clock() + timeout
    mr = get_mr()
    while mr.get("detailed_merge_status") in WAIT_STATUSES and clock() < deadline:
        sleep(step)
        mr = get_mr()
    return mr


class GitLab:
    """Клиент записи: хост только явный (--host / GITLAB_HOST), без редиректов, TLS всегда проверяется."""

    def __init__(self, host: str, project: str, token: str, cafile: str | None = None, call=None):
        if not valid_host(host):
            raise ValueError("host: допустимо имя узла с необязательным портом, без логина, пути и параметров")
        self.base = f"https://{host}/api/v4/projects/{urllib.parse.quote(project, safe='')}"
        self.token = token
        self.ctx = ssl.create_default_context(cafile=cafile or None)
        self._call = call

    def call(self, method: str, path: str, data=None):
        if self._call:
            return self._call(method, path, data)
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, method=method, headers={
            "PRIVATE-TOKEN": self.token, "Content-Type": "application/json; charset=utf-8"})
        opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=self.ctx))
        with opener.open(req, timeout=60) as r:
            raw = r.read().decode("utf-8")
        return json.loads(raw) if raw else None

    def mr(self, iid: int) -> dict:
        return self.call("GET", f"/merge_requests/{iid}")

    def is_protected(self, branch: str):
        """Поле protected ветки; любая ошибка -> None (для удаления = защищена)."""
        try:
            return bool(self.call("GET", "/repository/branches/" + urllib.parse.quote(branch, safe=""))["protected"])
        except Exception:
            return None


def _git_rc(repo, *a: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *a], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip() or (r.stderr or "").strip()


def _git_ok(repo, *a: str) -> str:
    rc, out = _git_rc(repo, *a)
    if rc:
        raise SystemExit(f"git {' '.join(a)}: {out}")
    return out


def _show_mr(mr: dict) -> None:
    print(f"!{mr['iid']} [{mr['state']}] {mr['title']}")
    print(f"  {mr['source_branch']} -> {mr['target_branch']}  merge_status={mr.get('detailed_merge_status')}"
          f"  conflicts={mr.get('has_conflicts')}  draft={mr.get('draft')}")
    print(f"  sha={mr.get('sha')}  pipeline={(mr.get('head_pipeline') or {}).get('status')}")
    print(f"  {mr.get('web_url', '')}")


def _template(repo: Path) -> Path:
    own = repo / ".gitlab" / "merge_request_templates" / "Default.md"
    return own if own.is_file() else HERE.parent / "docs" / "templates" / "merge_request_description.md"


def sync_main(repo: Path, target: str = "main") -> int:
    _git_ok(repo, "fetch", "origin")
    rc, _ = _git_rc(repo, "merge", "--no-edit", f"origin/{target}")
    if rc == 0:
        print(f"origin/{target} влита (merge)")
        return 0
    conflicts = _git_ok(repo, "diff", "--name-only", "--diff-filter=U").split()
    if conflicts != ["CHANGELOG.md"]:
        print("Конфликт, сведи вручную (или git merge --abort):\n  " + "\n  ".join(conflicts))
        return 1
    mb = _git_ok(repo, "merge-base", "HEAD", f"origin/{target}")
    try:
        merged = merge_changelog(_git_ok(repo, "show", f"{mb}:CHANGELOG.md"), _git_ok(repo, "show", "HEAD:CHANGELOG.md"),
                                 _git_ok(repo, "show", f"origin/{target}:CHANGELOG.md"))
    except ValueError as e:
        print(f"CHANGELOG не собран автоматически: {e}\nСведи вручную или git merge --abort")
        return 1
    top = Path(_git_ok(repo, "rev-parse", "--show-toplevel"))
    (top / "CHANGELOG.md").write_bytes((merged.rstrip("\n") + "\n").encode("utf-8"))
    _git_ok(repo, "add", "CHANGELOG.md")
    _git_ok(repo, "commit", "--no-edit")
    print(f"origin/{target} влита (merge); конфликт CHANGELOG.md собран: цель + строки ветки в [Unreleased] / ### Added")
    return 0


def _manage(argv: list[str]) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", type=Path, default=Path.cwd(), help="рабочий каталог git (worktree)")
    common.add_argument("--host", help="хост GitLab (или GITLAB_HOST); не берётся из git remote")
    common.add_argument("--project", help="группа/проект; по умолчанию путь из git remote origin")
    common.add_argument("--token-env", help="имя переменной с токеном (или GITLAB_TOKEN_ENV)")
    common.add_argument("--cafile", help="pem сервера с самоподписанным сертификатом (или GITLAB_CA_FILE)")
    ap = argparse.ArgumentParser(prog="mr_review.py", description="Работа с MR GitLab: статус, создание, "
                                 "комментарий, вливание merge-коммитом, подтягивание цели в ветку.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", parents=[common])
    p.add_argument("iid", type=int)
    p = sub.add_parser("create", parents=[common])
    p.add_argument("--source", required=True)
    p.add_argument("--target", default="main")
    p.add_argument("--title", required=True)
    p.add_argument("--description-file", type=Path, help="по умолчанию шаблон описания MR")
    p = sub.add_parser("note", parents=[common])
    p.add_argument("iid", type=int)
    p.add_argument("--body-file", type=Path, required=True)
    p = sub.add_parser("merge", parents=[common], help="только по слову ревьюера")
    p.add_argument("iid", type=int)
    p.add_argument("--branch", required=True)
    p.add_argument("--wait", type=float, default=60, help="сколько секунд ждать пересчёта статуса GitLab")
    p = sub.add_parser("sync-main", parents=[common], help="git merge origin/<цель> в ветку (не rebase)")
    p.add_argument("--target", default="main")
    a = ap.parse_args(argv)
    if a.cmd == "sync-main":
        return sync_main(a.repo, a.target)
    load_dotenv(a.repo, Path.cwd())
    _, token = gitlab_token(a.token_env)
    host = a.host or os.environ.get("GITLAB_HOST") or ""
    project = a.project or remote_project(_git(a.repo, "remote", "get-url", "origin").strip())[1]
    if not (token and host and project):
        print("Нужны токен (GITLAB_TOKEN, GITLAB_PERSONAL_ACCESS_TOKEN или --token-env/GITLAB_TOKEN_ENV), "
              "--host или GITLAB_HOST и --project (или git remote origin).")
        return 2
    try:
        gl = GitLab(host, project, token, a.cafile or os.environ.get("GITLAB_CA_FILE"))
        return _run_manage(gl, a)
    except ValueError as e:
        print(str(e))
        return 2
    except urllib.error.HTTPError as e:
        print(f"GitLab ответил HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        return 2
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        hint = " (самоподписанный сертификат: --cafile или GITLAB_CA_FILE)" if "CERTIFICATE" in str(e).upper() else ""
        print(f"GitLab недоступен ({type(e).__name__}){hint}: проверьте сеть и --host.")
        return 2


def _run_manage(gl: GitLab, a) -> int:
    if a.cmd == "status":
        _show_mr(gl.mr(a.iid))
        return 0
    if a.cmd == "create":
        remove = should_remove_source(a.source, gl.is_protected(a.source))
        text = (a.description_file or _template(a.repo)).read_text(encoding="utf-8-sig")
        mr = gl.call("POST", "/merge_requests", {"source_branch": a.source, "target_branch": a.target,
                                                 "title": a.title, "description": text, "remove_source_branch": remove})
        _show_mr(mr)
        print(f"  remove_source_branch={remove}")
        return 0
    if a.cmd == "note":
        note = gl.call("POST", f"/merge_requests/{a.iid}/notes", {"body": a.body_file.read_text(encoding="utf-8-sig")})
        print(f"комментарий {note['id']} добавлен в !{a.iid}")
        return 0
    # merge
    _git_ok(a.repo, "fetch", "origin")
    mr = wait_mergeable(lambda: gl.mr(a.iid), timeout=a.wait)
    head = _git_ok(a.repo, "rev-parse", "HEAD")
    origin_head = _git_rc(a.repo, "rev-parse", f"origin/{a.branch}")[1]
    behind = _git_rc(a.repo, "merge-base", "--is-ancestor", f"origin/{mr.get('target_branch')}", "HEAD")[0] != 0
    problems = merge_problems(mr, a.branch, _git_ok(a.repo, "rev-parse", "--abbrev-ref", "HEAD"), head, origin_head, behind)
    if problems:
        print("merge отменён:\n  - " + "\n  - ".join(problems))
        return 1
    remove = should_remove_source(a.branch, gl.is_protected(a.branch))
    res = gl.call("PUT", f"/merge_requests/{a.iid}/merge",
                  {"sha": head, "squash": False, "should_remove_source_branch": remove})
    print(f"!{a.iid} влит: state={res.get('state')} merge_commit={res.get('merge_commit_sha')} remove_source_branch={remove}")
    if not remove:
        rc, _ = _git_rc(a.repo, "ls-remote", "--exit-code", "origin", f"refs/heads/{a.branch}")
        print(f"ветка {a.branch} на origin: {'на месте' if rc == 0 else 'ПРОПАЛА - восстанови'}")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in MANAGE_COMMANDS:
        return _manage(argv)
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
