# /// script
# dependencies = []
# ///
"""CLI для Jira и Confluence (Server/DC и Cloud) — чтение задач и базы знаний, публикация страниц.

Задумка: Claude Code (и человек) достаёт контекст задачи из Jira и знания из Confluence
одной командой, без MCP и внешних библиотек. Только stdlib, кроссплатформенно.

Примеры:
  python scripts/atlassian.py jira issue DCM-5488 --comments
  python scripts/atlassian.py jira search "project = DCM AND status = Done" --limit 10
  python scripts/atlassian.py conf page 209491238            # id, URL или точный заголовок
  python scripts/atlassian.py conf tree 148810173 --depth 2  # дерево раздела
  python scripts/atlassian.py conf search "критерии приёмки"
  python scripts/atlassian.py conf publish docs/page.md --parent 209491237

Окружение (.env в CWD/корне репо подхватывается, уже установленное не перетирается):
  JIRA_URL / CONFLUENCE_URL          — базовые URL (https://jira.example.com)
  JIRA_PAT / CONFLUENCE_PAT          — персональный токен → Bearer (Server/DC)
  JIRA_USER / CONFLUENCE_USER        — если задан, аутентификация Basic user:token (Cloud)
Публикация (conf publish) требует токен с правом записи; чтение работает и с read-only.
"""
import argparse
import base64
import html as _html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
try:
    import onec_ops_mcp as _ops
    _ops.load_dotenv_defaults()
except Exception:  # автономный запуск без mcp/ — мини-разбор .env
    for _d in (os.getcwd(), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        _p = os.path.join(_d, ".env")
        if os.path.isfile(_p):
            for _ln in open(_p, encoding="utf-8", errors="replace").read().splitlines():
                _ln = _ln.strip()
                if _ln and not _ln.startswith("#") and "=" in _ln:
                    _k, _v = _ln.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))


# ---------- общее ----------

def _base(kind: str) -> str:
    url = os.environ.get(f"{kind}_URL", "")
    if not url:
        raise SystemExit(f"Не задан {kind}_URL (в окружении или .env)")
    return url.rstrip("/")


def _headers(kind: str) -> dict:
    user = os.environ.get(f"{kind}_USER", "")
    tok = os.environ.get(f"{kind}_PAT", "") or os.environ.get(f"{kind}_TOKEN", "")
    if not tok:
        raise SystemExit(f"Не задан {kind}_PAT (токен; в окружении или .env)")
    if user:  # Cloud: Basic email:api_token
        cred = base64.b64encode(f"{user}:{tok}".encode()).decode()
        return {"Authorization": f"Basic {cred}", "Accept": "application/json"}
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


def _req(kind: str, path: str, payload=None, method: str | None = None):
    url = _base(kind) + path
    data = json.dumps(payload).encode() if payload is not None else None
    hdrs = _headers(kind)
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"), headers=hdrs)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def html_to_text(s: str) -> str:
    """view/storage-HTML → плоский текст (грубо, для чтения агентом)."""
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"</(p|div|li|tr|h[1-6]|table)>", "\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</t[dh]>", " | ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


# ---------- Jira ----------

_JIRA_FIELDS = "summary,status,issuetype,priority,assignee,reporter,created,updated,description,labels,components,timetracking,parent"


def _fmt_issue(d: dict, comments: bool) -> str:
    f = d.get("fields") or {}
    def nm(x, k="displayName"):
        return (x or {}).get(k) or (x or {}).get("name") or ""
    out = [f"{d.get('key')}  {f.get('summary', '')}",
           f"тип: {nm(f.get('issuetype'), 'name')} | статус: {nm(f.get('status'), 'name')} | "
           f"приоритет: {nm(f.get('priority'), 'name')}",
           f"исполнитель: {nm(f.get('assignee'))} | автор: {nm(f.get('reporter'))}",
           f"создана: {str(f.get('created', ''))[:10]} | обновлена: {str(f.get('updated', ''))[:10]}"]
    tt = f.get("timetracking") or {}
    if tt.get("originalEstimate") or tt.get("timeSpent"):
        out.append(f"оценка: {tt.get('originalEstimate', '—')} | затрачено: {tt.get('timeSpent', '—')}")
    if f.get("labels"):
        out.append("метки: " + ", ".join(f["labels"]))
    desc = f.get("description")
    if isinstance(desc, str) and desc.strip():
        out += ["", desc.strip()]
    if comments:
        for c in ((f.get("comment") or {}).get("comments") or []):
            out += ["", f"— {nm(c.get('author'))} {str(c.get('created', ''))[:16]}:",
                    str(c.get("body", "")).strip()]
    return "\n".join(out)


def jira_issue(key: str, comments: bool, as_json: bool) -> int:
    fields = _JIRA_FIELDS + (",comment" if comments else "")
    d = _req("JIRA", f"/rest/api/2/issue/{key}?fields={fields}")
    print(json.dumps(d, ensure_ascii=False, indent=1) if as_json else _fmt_issue(d, comments))
    return 0


def jira_search(jql: str, limit: int, as_json: bool) -> int:
    q = urllib.parse.urlencode({"jql": jql, "maxResults": limit,
                                "fields": "summary,status,issuetype,assignee,updated"})
    d = _req("JIRA", f"/rest/api/2/search?{q}")
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    total = d.get("total", 0)
    for it in d.get("issues") or []:
        f = it.get("fields") or {}
        print(f"{it.get('key'):<12} {(f.get('status') or {}).get('name', ''):<16} "
              f"{str(f.get('updated', ''))[:10]}  {f.get('summary', '')}")
    print(f"-- всего: {total} (показано {min(limit, total)})")
    return 0


# ---------- Confluence ----------

def conf_page_id(ref: str) -> str:
    """id страницы по id / URL (/pages/<id>) / точному заголовку."""
    if re.fullmatch(r"\d+", ref):
        return ref
    m = re.search(r"pageId=(\d+)", ref) or re.search(r"/pages/(\d+)", ref)
    if m:
        return m.group(1)
    q = urllib.parse.urlencode({"cql": f'title = "{ref}"', "limit": "1"})
    res = _req("CONFLUENCE", f"/rest/api/content/search?{q}").get("results") or []
    if not res:
        raise SystemExit(f"Страница не найдена: {ref}")
    return res[0]["id"]


def conf_page(ref: str, storage: bool, as_json: bool) -> int:
    pid = conf_page_id(ref)
    d = _req("CONFLUENCE", f"/rest/api/content/{pid}?expand=body.storage,version,space,ancestors")
    body = ((d.get("body") or {}).get("storage") or {}).get("value", "")
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    path = " / ".join(a.get("title", "") for a in d.get("ancestors") or [])
    print(f"{d.get('title')}  (id={pid}, space={(d.get('space') or {}).get('key')}, "
          f"v{(d.get('version') or {}).get('number')})")
    if path:
        print(f"путь: {path}")
    print()
    print(body if storage else html_to_text(body))
    return 0


def conf_tree(ref: str, depth: int) -> int:
    def walk(pid: str, level: int):
        res = _req("CONFLUENCE", f"/rest/api/content/{pid}/child/page?limit=200").get("results") or []
        for ch in res:
            print("  " * level + f"- {ch.get('title')} (id={ch.get('id')})")
            if level + 1 < depth:
                walk(ch["id"], level + 1)
    pid = conf_page_id(ref)
    d = _req("CONFLUENCE", f"/rest/api/content/{pid}")
    print(f"{d.get('title')} (id={pid})")
    walk(pid, 0)
    return 0


def conf_search(query: str, limit: int) -> int:
    cql = query[4:] if query.startswith("cql:") else f'type = page and text ~ "{query}"'
    q = urllib.parse.urlencode({"cql": cql, "limit": limit})
    res = _req("CONFLUENCE", f"/rest/api/content/search?{q}").get("results") or []
    for r in res:
        print(f"{r.get('id'):<12} {r.get('title')}")
    if not res:
        print("-- ничего не найдено")
    return 0


# --- markdown → storage (публикация одностраничников) ---

def _fmt_text(s: str) -> str:
    s = _html.escape(s, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


def _inline(s: str) -> str:
    parts = re.split(r"`([^`]*)`", s)
    out = []
    for idx, part in enumerate(parts):
        if idx % 2:
            out.append(f"<code>{_html.escape(part, quote=False)}</code>")
            continue
        pos, segs = 0, []
        for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", part):
            segs.append(_fmt_text(part[pos:m.start()]))
            href = urllib.parse.quote(m.group(2), safe="/:?#@!$&'()*+,;=%._~-")
            segs.append(f'<a href="{_html.escape(href, quote=True)}">{_fmt_text(m.group(1))}</a>')
            pos = m.end()
        segs.append(_fmt_text(part[pos:]))
        out.append("".join(segs))
    return "".join(out)


def md_to_storage(md: str) -> str:
    """Простой markdown → Confluence storage XHTML: ##/###, **bold**, `код`,
    [ссылки](url), - и 1. списки, | таблицы, абзацы. `# титул` пропускается."""
    lines = md.splitlines()
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if ln.startswith("# "):
            i += 1
            continue
        if ln.startswith("### "):
            out.append(f"<h3>{_inline(ln[4:].strip())}</h3>")
            i += 1
            continue
        if ln.startswith("## "):
            out.append(f"<h2>{_inline(ln[3:].strip())}</h2>")
            i += 1
            continue
        if ln.strip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            body = []
            for ri, cells in enumerate(rows):
                tag = "th" if ri == 0 else "td"
                body.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
            out.append("<table><tbody>" + "".join(body) + "</tbody></table>")
            continue
        if re.match(r"^\s*[-*] ", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*] ", lines[i]):
                items.append(f"<li>{_inline(re.sub(r'^\s*[-*] ', '', lines[i]))}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        if re.match(r"^\s*\d+\. ", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\. ", lines[i]):
                items.append(f"<li>{_inline(re.sub(r'^\s*\d+\. ', '', lines[i]))}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue
        out.append(f"<p>{_inline(ln.strip())}</p>")
        i += 1
    return "".join(out)


def conf_publish(path: str, parent: str, title: str | None, dry: bool) -> int:
    md = open(path, encoding="utf-8").read()
    if not title:
        m = re.search(r"^# (.+)$", md, re.M)
        title = m.group(1).strip() if m else os.path.splitext(os.path.basename(path))[0]
    storage = md_to_storage(md)
    if dry:
        print(f"TITLE: {title}\n{storage}")
        return 0
    parent_id = conf_page_id(parent)
    space = (_req("CONFLUENCE", f"/rest/api/content/{parent_id}?expand=space").get("space") or {}).get("key")
    q = urllib.parse.urlencode({"cql": f'space = "{space}" and title = "{title}"', "limit": "1"})
    hit = _req("CONFLUENCE", f"/rest/api/content/search?{q}").get("results") or []
    if hit:
        pid = hit[0]["id"]
        ver = (_req("CONFLUENCE", f"/rest/api/content/{pid}?expand=version").get("version") or {}).get("number", 0) + 1
        _req("CONFLUENCE", f"/rest/api/content/{pid}", method="PUT", payload={
            "id": pid, "type": "page", "title": title, "version": {"number": ver},
            "body": {"storage": {"value": storage, "representation": "storage"}}})
        print(f"updated: {title} id={pid} v{ver}")
    else:
        d = _req("CONFLUENCE", "/rest/api/content", payload={
            "type": "page", "title": title, "space": {"key": space},
            "ancestors": [{"id": int(parent_id)}],
            "body": {"storage": {"value": storage, "representation": "storage"}}})
        print(f"created: {title} id={d.get('id')}")
    return 0


# ---------- CLI ----------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Jira/Confluence CLI (stdlib): чтение задач и страниц, публикация md")
    sub = p.add_subparsers(dest="svc", required=True)

    pj = sub.add_parser("jira", help="Jira")
    sj = pj.add_subparsers(dest="cmd", required=True)
    ji = sj.add_parser("issue", help="карточка задачи")
    ji.add_argument("key")
    ji.add_argument("--comments", action="store_true")
    ji.add_argument("--json", action="store_true")
    js = sj.add_parser("search", help="поиск по JQL")
    js.add_argument("jql")
    js.add_argument("--limit", type=int, default=20)
    js.add_argument("--json", action="store_true")

    pc = sub.add_parser("conf", help="Confluence")
    sc = pc.add_subparsers(dest="cmd", required=True)
    cp = sc.add_parser("page", help="страница: текст (или --storage)")
    cp.add_argument("ref", help="id, URL или точный заголовок")
    cp.add_argument("--storage", action="store_true")
    cp.add_argument("--json", action="store_true")
    ct = sc.add_parser("tree", help="дерево дочерних страниц")
    ct.add_argument("ref")
    ct.add_argument("--depth", type=int, default=2)
    cs = sc.add_parser("search", help="поиск (текст или cql:<выражение>)")
    cs.add_argument("query")
    cs.add_argument("--limit", type=int, default=20)
    cb = sc.add_parser("publish", help="md → страница (создать/обновить по заголовку)")
    cb.add_argument("file")
    cb.add_argument("--parent", required=True, help="id/URL родительской страницы")
    cb.add_argument("--title")
    cb.add_argument("--dry", action="store_true")

    a = p.parse_args(argv)
    if a.svc == "jira":
        return jira_issue(a.key, a.comments, a.json) if a.cmd == "issue" else jira_search(a.jql, a.limit, a.json)
    if a.cmd == "page":
        return conf_page(a.ref, a.storage, a.json)
    if a.cmd == "tree":
        return conf_tree(a.ref, a.depth)
    if a.cmd == "search":
        return conf_search(a.query, a.limit)
    return conf_publish(a.file, a.parent, a.title, a.dry)


if __name__ == "__main__":
    raise SystemExit(main())
