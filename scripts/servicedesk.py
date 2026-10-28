#!/usr/bin/env python3
"""Коннектор ManageEngine ServiceDesk Plus API v3: чтение заявок и создание заявки по шаблону (stdlib, без MCP).

  python scripts/servicedesk.py list [--technician ИМЯ] [--group ИМЯ] [--status ИМЯ] [--since ГГГГ-ММ-ДД]
                                     [--until ГГГГ-ММ-ДД] [--limit 200] [--page-size 100] [--criteria-json ФАЙЛ]
                                     [--with-people] [--with-attachments] [--json]
  python scripts/servicedesk.py create --template ПСЕВДОНИМ --subject "тема" [--description "текст"]
                                       [--group ПСЕВДОНИМ] [--technician ИМЯ] [--priority ИМЯ] [--check] [--send]
  (отправка без терминала: --approve-file РЕШЕНИЕ.json, см. docs/SERVICEDESK.md)
  python scripts/servicedesk.py note --request НОМЕР --text "текст" [--send]
  python scripts/servicedesk.py update --request НОМЕР [--description "текст"] [--due|--start|--end ГГГГ-ММ-ДД] [--send]
  python scripts/servicedesk.py attach --request НОМЕР --file ПУТЬ [--allow-duplicate] [--send]
  python scripts/servicedesk.py meta [--what templates|technicians|observed|all] [--name ЧАСТЬ_ИМЕНИ]

Окружение (.env, не коммитить): SD_BASE_URL — адрес портала до /api/v3 (для облака вместе с /app/<портал>),
SD_API_KEY — ключ из профиля пользователя SDP (раздел API Key generation), права ключа: чтение и создание заявок.
Необязательно: SD_AUTH_SCHEME = authtoken (по умолчанию, ключ в заголовке authtoken) или oauth (Authorization:
Zoho-oauthtoken ...), SD_CONFIG — путь к конфигу шаблонов и групп (по умолчанию config/servicedesk.toml).

Безопасность:
  * структурные поля заявителя и исполнителя и вложения в вывод не попадают без явных флагов `--with-people`,
    `--with-attachments`; тема и описание выводятся как есть и могут содержать персональные данные, проверяйте их;
  * создание заявки по умолчанию сухой прогон (печатается тело запроса); отправка только с `--send` и только
    после ручного подтверждения в терминале (нужен интерактивный ввод), без него команда отказывает;
  * ключ нигде не печатается и не сохраняется; в ошибках его нет; адрес должен быть https (внутренний http только с
    SD_ALLOW_HTTP=1), узел можно ограничить SD_ALLOWED_HOSTS, перенаправление на другой узел отклоняется;
  * подтверждение отправки защищает от случайного запуска, но не является механизмом авторизации: процесс с терминалом
    и доступом к окружению его автоматизирует. Для строгого разделения выдайте ключу только нужные права.
Списки групп, приоритетов и категорий обычный ключ часто не читает (403, 404), поэтому их имена берутся из конфига и
сверяются по фактам: `meta observed` показывает группы и приоритеты, реально стоящие в последних заявках. Шаблоны и
техники читаются (`meta templates`, `meta technicians`), `create --check` сверяет по ним шаблон и исполнителя до отправки.
Заметка к заявке (`note`) работает как создание: сухой прогон, отправка только с `--send` и ручным подтверждением.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ACCEPT = "application/vnd.manageengine.sdp.v3+json"
ROOT = Path(__file__).resolve().parent.parent

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def load_env() -> None:
    for d in (Path.cwd(), ROOT):
        p = d / ".env"
        if p.is_file():
            for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, v = ln.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))


class SDError(RuntimeError):
    """Ошибка API с понятным текстом; ключ в сообщение не попадает."""


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Не пересылаем ключ на другой узел при перенаправлении."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            raise SDError("перенаправление на другой узел отклонено (ключ на него не отправляется)")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_transport(method: str, url: str, headers: dict, data: bytes | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.build_opener(_SameHostRedirect).open(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:      # адрес с параметрами запроса в сообщение не кладём
        raise SDError(f"сеть недоступна или ошибка TLS ({type(e).__name__}); проверьте SD_BASE_URL и доступ") from None


def validate_base_url(base: str) -> str:
    """HTTPS обязателен (SD_ALLOW_HTTP=1 разрешает http для внутреннего узла); узел можно ограничить SD_ALLOWED_HOSTS."""
    u = urllib.parse.urlsplit(base)
    if not u.hostname or u.scheme not in ("https", "http"):
        raise SystemExit("SD_BASE_URL должен быть абсолютным адресом (https://узел/...)")
    if u.scheme == "http" and os.environ.get("SD_ALLOW_HTTP") != "1":
        raise SystemExit("SD_BASE_URL с http отклонён: ключ шёл бы открытым текстом. Для внутреннего узла задайте SD_ALLOW_HTTP=1")
    allowed = {x.strip().lower() for x in os.environ.get("SD_ALLOWED_HOSTS", "").split(",") if x.strip()}
    if allowed and u.hostname.lower() not in allowed:
        raise SystemExit("узел из SD_BASE_URL отсутствует в SD_ALLOWED_HOSTS")
    return base.rstrip("/")


def explain_error(status: int, body: str) -> str:
    msg = ""
    try:
        rs = json.loads(body).get("response_status")
        rs = rs[0] if isinstance(rs, list) and rs else rs
        for m in (rs or {}).get("messages", []) or []:
            msg += f' [{m.get("status_code")}] {m.get("message")}'
    except Exception:
        pass
    hint = {401: "ключ не принят: проверьте SD_API_KEY и схему SD_AUTH_SCHEME",
            403: "у ключа нет права на эту операцию (чтение или создание заявок, списки шаблонов и групп обычному ключу часто закрыты)",
            404: "адрес или объект не найден: проверьте SD_BASE_URL (для облака с /app/<портал>), номер заявки, имя шаблона"
            }.get(status, "запрос не выполнен")
    return f"HTTP {status}: {hint}.{msg}".strip()


class SDClient:
    def __init__(self, base: str, key: str, scheme: str = "authtoken", transport=default_transport):
        self.base, self.key, self.scheme, self.transport = base.rstrip("/"), key, scheme, transport

    def _headers(self) -> dict:
        h = {"Accept": ACCEPT}
        if self.scheme == "oauth":
            h["Authorization"] = f"Zoho-oauthtoken {self.key}"
        else:
            h["authtoken"] = self.key
        return h

    def get(self, path: str, input_data: dict | None = None) -> dict:
        url = f"{self.base}/api/v3/{path}"
        if input_data is not None:
            url += "?" + urllib.parse.urlencode({"input_data": json.dumps(input_data, ensure_ascii=False)})
        status, body = self.transport("GET", url, self._headers(), None)
        return self._parse(status, body)

    def post(self, path: str, input_data: dict) -> dict:
        h = self._headers()
        h["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode({"input_data": json.dumps(input_data, ensure_ascii=False)}).encode()
        status, body = self.transport("POST", f"{self.base}/api/v3/{path}", h, data)
        return self._parse(status, body)

    def put(self, path: str, input_data: dict) -> dict:
        h = self._headers()
        h["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode({"input_data": json.dumps(input_data, ensure_ascii=False)}).encode()
        status, body = self.transport("PUT", f"{self.base}/api/v3/{path}", h, data)
        return self._parse(status, body)

    def upload(self, path: str, name: str, data: bytes) -> dict:
        """Загрузка одного файла в заявку: multipart, единственное поле input_file (остальные поля портал отвергает, 4001)."""
        h = self._headers()
        body, h["Content-Type"] = multipart_file(name, data)
        status, resp = self.transport("POST", f"{self.base}/api/v3/{path}", h, body)
        return self._parse(status, resp)

    @staticmethod
    def _parse(status: int, body: str) -> dict:
        if status >= 400:
            raise SDError(explain_error(status, body))
        try:
            d = json.loads(body or "{}")
        except ValueError:
            raise SDError(f"HTTP {status}: ответ не JSON") from None
        rs = d.get("response_status")
        rs = rs[0] if isinstance(rs, list) and rs else rs
        if isinstance(rs, dict) and rs.get("status") == "failed":
            raise SDError(explain_error(400, body))
        return d


def crit(field: str, value, condition: str = "is") -> dict:
    return {"field": field, "condition": condition, "values": [value], "logical_operator": "and"}


def epoch_ms(day: str, end: bool = False) -> str:
    d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    if end:
        d += dt.timedelta(days=1)
    return str(int(d.timestamp() * 1000))


def build_list_info(technician=None, group=None, status=None, since=None, until=None, extra=None,
                    start_index: int = 1, row_count: int = 100) -> dict:
    """list_info с search_criteria: фильтры соединены через AND. Имена полей по документации API v3."""
    sc = []
    if group:
        sc.append(crit("group.name", group))
    if status:
        sc.append(crit("status.name", status))
    if technician:
        sc.append(crit("technician.name", technician))
    if since:
        sc.append(crit("created_time", epoch_ms(since), "greater than"))
    if until:
        sc.append(crit("created_time", epoch_ms(until, end=True), "lesser than"))
    sc += list(extra or [])
    li = {"row_count": row_count, "start_index": start_index, "sort_field": "created_time", "sort_order": "desc",
          "get_total_count": True}
    if sc:
        sc[0] = {k: v for k, v in sc[0].items() if k != "logical_operator"}
        li["search_criteria"] = sc
    return {"list_info": li}


def fetch_requests(client: SDClient, limit: int = 200, page_size: int = 100, **filters) -> list:
    """Постранично: start_index растёт на row_count, пока has_more_rows, но не больше limit записей."""
    out, start = [], 1
    while len(out) < limit:
        n = min(page_size, limit - len(out))
        d = client.get("requests", build_list_info(start_index=start, row_count=n, **filters))
        rows = d.get("requests") or []
        out += rows
        if not (d.get("list_info") or {}).get("has_more_rows") or not rows:
            break
        start += len(rows)
    return out[:limit]


def _name(x):
    return x.get("name") if isinstance(x, dict) else x


def shape(req: dict, people: bool = False, attachments: bool = False) -> dict:
    """Короткая запись заявки. Люди и вложения только по явным флагам."""
    ct = req.get("created_time") or {}
    out = {"id": req.get("id"), "display_id": req.get("display_id"), "subject": req.get("subject"),
           "status": _name(req.get("status")), "group": _name(req.get("group")),
           "priority": _name(req.get("priority")), "template": _name(req.get("template")),
           "created": ct.get("display_value") if isinstance(ct, dict) else ct}
    if people:
        out["requester"] = _name(req.get("requester"))
        out["technician"] = _name(req.get("technician"))
    if attachments:
        out["attachments"] = req.get("attachments")
    return {k: v for k, v in out.items() if v is not None}


def fetch_templates(client: SDClient, page_size: int = 100, max_pages: int = 20) -> list:
    """Все шаблоны заявок постранично (сервер отдаёт не больше 100 за страницу)."""
    out, start = [], 1
    for _ in range(max_pages):
        d = client.get("requests/template", {"list_info": {"row_count": page_size, "start_index": start}})
        rows = d.get("template") or []
        out += rows
        if not (d.get("list_info") or {}).get("has_more_rows") or not rows:
            return out
        start += len(rows)
    raise SDError("список шаблонов прочитан не полностью (достигнут предел страниц)")


def fetch_technicians(client: SDClient, name: str | None = None, page_size: int = 100, max_pages: int = 10) -> list:
    """Пользователи-техники (is_technician is True) по части имени. Сервер не фильтрует по is_technician (проверено на
    портале), поэтому список всех пользователей слишком велик: ищем по имени (`contains`), роль проверяем на клиенте."""
    out, start = [], 1
    for _ in range(max_pages):
        li = {"row_count": page_size, "start_index": start}
        if name:
            li["search_criteria"] = [{"field": "name", "condition": "contains", "value": name}]
        d = client.get("users", {"list_info": li})
        rows = d.get("users") or []
        out += [{"name": u.get("name"), "id": u.get("id")} for u in rows if u.get("is_technician") is True]
        if not (d.get("list_info") or {}).get("has_more_rows") or not rows:
            return out
        start += len(rows)
    raise SDError("список техников прочитан не полностью (достигнут предел страниц)")


def observed_values(client: SDClient, limit: int = 200) -> dict:
    """Группы, приоритеты и шаблоны, реально стоящие в последних заявках: справочники групп и приоритетов API не отдаёт."""
    rows = fetch_requests(client, limit=limit)
    res = {"groups": {}, "priorities": {}}
    for r in rows:
        for key, fld in (("groups", "group"), ("priorities", "priority")):
            n = _name(r.get(fld))
            if n:
                res[key][n] = res[key].get(n, 0) + 1
    return res


def check_create(client: SDClient, payload: dict, cfg: dict | None = None) -> list:
    """Проверка тела создания по фактам портала (только чтение). Возвращает список проблем, пусто значит замечаний нет."""
    req, problems = payload.get("request") or {}, []
    tpl = req.get("template") or {}
    try:
        tpls = fetch_templates(client)
        names = {t.get("name") for t in tpls}
        ids = {str(t.get("id")) for t in tpls}
        if tpl.get("id") is not None and str(tpl["id"]) not in ids:
            problems.append(f'шаблон id={tpl["id"]} не найден в портале')
        if tpl.get("name") is not None and tpl["name"] not in names:
            problems.append(f'шаблон "{tpl["name"]}" не найден в портале (имя должно совпадать точно)')
    except SDError as e:
        problems.append(f"шаблоны проверить не удалось: {e}")
    tech = (req.get("technician") or {}).get("name")
    if tech:
        try:
            techs = {t["name"] for t in fetch_technicians(client, tech.split()[0])}
            if tech not in techs:
                near = sorted(n for n in techs if n.lower().startswith(tech.lower()))[:3]
                problems.append(f'техник "{tech}" не найден среди техников портала (имя пишется полностью, как в портале)'
                                + (f", похожие: {', '.join(near)}" if near else ""))
        except SDError as e:
            problems.append(f"техников проверить не удалось: {e}")
    grp = (req.get("group") or {}).get("name")
    try:
        if grp and not fetch_requests(client, limit=1, group=grp):
            problems.append(f'группа "{grp}" не найдена: нет ни одной заявки с такой группой (справочник групп API не отдаёт), проверьте имя')
    except SDError as e:
        problems.append(f"группу проверить не удалось: {e}")
    prio = (req.get("priority") or {}).get("name")
    try:
        if prio and prio not in observed_values(client)["priorities"]:
            problems.append(f'приоритет "{prio}" не встречается в последних заявках (справочник API недоступен): проверьте имя вручную')
    except SDError as e:
        problems.append(f"приоритет проверить не удалось: {e}")
    return problems


def load_config(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("SD_CONFIG") or ROOT / "config" / "servicedesk.toml")
    if not p.is_file():
        return {}
    import tomllib
    return tomllib.loads(p.read_text(encoding="utf-8"))


_ALLOWED_TAGS = {"p", "br", "ul", "ol", "li", "b", "i", "strong", "em", "div"}


class _Sanitizer(HTMLParser):
    """Готовый HTML пропускаем только через белый список: теги из _ALLOWED_TAGS без атрибутов и ссылки <a href> с
    протоколами http, https, mailto. Остальное (script, img, обработчики on*, style, javascript:) экранируется как текст."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []

    @staticmethod
    def _esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def handle_starttag(self, tag, attrs):
        if tag in _ALLOWED_TAGS:
            self.out.append(f"<{tag}>")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if re.match(r"(?i)(https?://|mailto:)", href.strip()):
                self.out.append('<a href="' + href.strip().replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;") + '">')
            else:
                self.out.append("<a>")
        else:
            self.out.append(self._esc(self.get_starttag_text() or ""))

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.out.append("<br>")
        else:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _ALLOWED_TAGS or tag == "a":
            self.out.append(f"</{tag}>")
        else:
            self.out.append(self._esc(f"</{tag}>"))

    def handle_data(self, data):
        self.out.append(self._esc(data))

    def handle_entityref(self, name):
        self.out.append(f"&{name};" if re.fullmatch(r"[A-Za-z0-9]+", name) else self._esc(f"&{name};"))

    def handle_charref(self, name):
        self.out.append(f"&#{name};" if re.fullmatch(r"[0-9xA-Fa-f]+", name) else self._esc(f"&#{name};"))

    def handle_comment(self, data):
        pass


def to_html(text: str) -> str:
    """Описание и заметки SDP хранит как HTML: переводы строк пропадают, абзацы и пункты сливаются в одну строку
    (инцидент 23.10.2026, заявка 920325). Простой текст экранируем, абзацы и строки отделяем тегами. Готовый HTML
    пропускаем через белый список (_Sanitizer), чтобы текст из недоверенного источника не внёс скрипт или обработчик."""
    if not text:
        return text
    if re.search(r"<(br|p|div|ul|ol|li|b|i|a|table)\b", text, re.I):
        sn = _Sanitizer()
        sn.feed(text)
        sn.close()
        return "".join(sn.out)
    paras = [p for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()) if p.strip()]
    esc = lambda t: t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join("<p>" + "<br>".join(esc(l.rstrip()) for l in p.split("\n")) + "</p>" for p in paras)


def build_update(description: str | None = None, due: str | None = None, start: str | None = None, end: str | None = None) -> dict:
    """Тело правки заявки: описание и сроки (даты ГГГГ-ММ-ДД, время по Минску: начало 09:00, срок и окончание 18:00)."""
    req = {}
    if description:
        req["description"] = to_html(description)
    minsk = dt.timezone(dt.timedelta(hours=3))
    for key, day, hour in (("due_by_time", due, 18), ("scheduled_start_time", start, 9), ("scheduled_end_time", end, 18)):
        if day:
            t = dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=hour, tzinfo=minsk)
            req[key] = {"value": str(int(t.timestamp() * 1000))}
    return {"request": req}


def build_create(cfg: dict, template: str, subject: str, description: str = "", group: str | None = None,
                 technician: str | None = None, priority: str | None = None, requester_id: str | None = None) -> dict:
    """Тело запроса создания заявки по шаблону. Шаблон, группа и техник берутся из конфига (псевдоним) или как есть.
    Значение шаблона в конфиге: строка это имя, целое число это id. Техник по умолчанию: [defaults] technician."""
    tpl = (cfg.get("templates") or {}).get(template, template)
    req = {"subject": subject, "template": {"id": str(tpl)} if isinstance(tpl, int) else {"name": tpl}}
    if description:
        req["description"] = to_html(description)
    if group:
        req["group"] = {"name": (cfg.get("groups") or {}).get(group, group)}
    tech = technician or (cfg.get("defaults") or {}).get("technician")
    if tech:
        req["technician"] = {"name": (cfg.get("technicians") or {}).get(tech, tech)}
    prio = priority or (cfg.get("defaults") or {}).get("priority")
    if prio:
        req["priority"] = {"name": prio}
    rid = requester_id or (cfg.get("defaults") or {}).get("requester_id")
    if rid:
        req["requester"] = {"id": str(rid)}
    return {"request": req}


MAX_ATTACH_BYTES = 20 * 1024 * 1024        # потолок коннектора; лимит портала не проверялся (самый большой проверенный файл 54 КБ)


def clean_filename(name: str) -> str:
    """Имя файла, как оно уйдёт на портал: без управляющих символов, слэши заменены (одно имя для хеша, проверки дубля и загрузки)."""
    return "".join(ch for ch in name if ch >= " " and ch != "\x7f").replace("\\", "_").replace("/", "_") or "file"


def multipart_file(name: str, data: bytes) -> tuple[bytes, str]:
    """Тело multipart/form-data с одним полем input_file. Имя очищается от управляющих символов и кавычек;
    не-ASCII имя уходит в filename* (UTF-8), в filename остаётся ASCII-замена."""
    clean = clean_filename(name)
    ascii_name = "".join(ch if (ch.isascii() and ch != '"') else "_" for ch in clean)
    disp = f'form-data; name="input_file"; filename="{ascii_name}"'
    if ascii_name != clean:
        disp += "; filename*=UTF-8''" + urllib.parse.quote(clean, safe="")
    boundary = "----sd" + uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: {disp}\r\nContent-Type: application/octet-stream\r\n\r\n").encode("ascii")
    return head + data + f"\r\n--{boundary}--\r\n".encode("ascii"), f"multipart/form-data; boundary={boundary}"


def read_attachment(path) -> tuple[str, bytes]:
    """Читает файл для вложения один раз и из одного дескриптора (то, что подтвердил человек, и то, что уйдёт, одни и те же
    байты; подмена файла ссылкой между проверкой и чтением не проходит)."""
    p = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)   # FIFO без писателя не повесит открытие
    try:
        if p.is_symlink():
            raise SDError(f"{p.name or p}: нужен обычный файл (не ссылка и не каталог)")
        fd = os.open(p, flags)
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not os.path.samestat(os.lstat(p), st) or not stat.S_ISREG(st.st_mode):      # путь подменён после открытия, или не обычный файл
                raise SDError(f"{p.name or p}: нужен обычный файл (не ссылка и не каталог)")
            if st.st_size == 0 or st.st_size > MAX_ATTACH_BYTES:
                raise SDError(f"{p.name}: размер {st.st_size} байт вне допустимого (1..{MAX_ATTACH_BYTES})")
            data = fh.read(MAX_ATTACH_BYTES + 1)
    except OSError as e:
        raise SDError(f"{p.name or p}: файл не прочитан ({type(e).__name__})") from None
    if not 1 <= len(data) <= MAX_ATTACH_BYTES:
        raise SDError(f"{p.name}: размер прочитанного вне допустимого (1..{MAX_ATTACH_BYTES}), файл менялся во время чтения")
    return clean_filename(p.name), data


def _att_size(att: dict):
    """Размер вложения: портал отдаёт {"value": N, ...}; число или строка тоже принимаем, непонятное это None."""
    sz = att.get("size")
    sz = sz.get("value") if isinstance(sz, dict) else sz
    return str(sz) if isinstance(sz, (int, str)) and str(sz).isdigit() else None


def attach_payload(name: str, data: bytes) -> dict:
    return {"attach": {"file": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}


def build_attach(path) -> dict:
    return attach_payload(*read_attachment(path))


def attachment_id(res: dict) -> str:
    """Id вложения из content_url ответа upload (вида .../attachments/<id>/_download); при отсутствии content_url запасной путь, поле id ответа."""
    att = (res.get("attachment") or {}) if isinstance(res, dict) else {}
    m = re.search(r"attachments/(\d+)(?:/|$)", str(att.get("content_url") or ""))
    if m:
        return m.group(1)
    if str(att.get("id") or "").isdigit():                      # запасной путь: поле id в ответе upload
        return str(att["id"])
    raise SDError("в ответе upload нет id вложения (content_url), файл мог загрузиться без привязки; проверьте заявку")


def current_attachments(client: "SDClient", request_id: str) -> list:
    got = client.get(f"requests/{urllib.parse.quote(str(request_id), safe='')}").get("request") or {}
    return [a for a in (got.get("attachments") or []) if isinstance(a, dict) and a.get("id") is not None]


def build_note(request_id: str, text: str, show_to_requester: bool = False) -> dict:
    """Тело заметки к заявке. По умолчанию заметка внутренняя (заявителю не показывается, уведомлений нет)."""
    return {"note": {"description": to_html(text), "show_to_requester": show_to_requester, "notify_technician": False}}


def confirm_send(payload: dict, isatty=None, ask=input, what: str = "заявку") -> bool:
    """Ручное подтверждение отправки: только из интерактивного терминала, строка `отправить`."""
    if not (isatty() if isatty else sys.stdin.isatty()):
        print("Отправка требует ручного подтверждения в интерактивном терминале. Выполните команду сами "
              "(в сессии агента с префиксом `! `), агент подтвердить за вас не может.", file=sys.stderr)
        return False
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return ask(f"Отправить {what} с этим телом? Введите слово 'отправить': ").strip().lower() == "отправить"


APPROVAL_TTL = 7200


def payload_digest(payload: dict, target: str = "requests") -> str:
    """sha256 цели и тела: решение для заметки к одной заявке не подходит к другой заявке."""
    return hashlib.sha256(json.dumps({"target": target, "body": payload}, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def ledger_dir() -> Path:
    """Каталог меток использованных решений и блокировок (по метке на решение, создаётся атомарно)."""
    return Path(os.environ.get("SD_APPROVAL_LEDGER") or Path.home() / ".cache" / "ai_toolkit_sd_approvals")


def _no_const(x):
    raise ValueError(x)


def check_approval(path: str, payload: dict, target: str = "requests", now: float | None = None) -> dict:
    """Решение человека файлом для среды без терминала. Привязано к цели и телу (sha256), живёт APPROVAL_TTL секунд,
    у каждого решения свой approval_id, одноразовое (см. claim_approval). Включается только SD_AGENT_SEND=1."""
    if os.environ.get("SD_AGENT_SEND") != "1":
        raise SDError("отправка по файлу решения выключена: человек включает её переменной SD_AGENT_SEND=1 в окружении")
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_no_const)
    except (OSError, ValueError):
        raise SDError("файл решения не прочитан или не JSON") from None
    who, at, dg, aid = d.get("approved_by"), d.get("approved_at"), d.get("payload_sha256"), d.get("approval_id")
    if (not isinstance(who, str) or not who.strip() or isinstance(at, bool) or not isinstance(at, (int, float))
            or not math.isfinite(at) or not isinstance(dg, str) or not isinstance(aid, str) or not re.fullmatch(r"[0-9a-f]{32}", aid)):
        raise SDError("файл решения: нужны approved_by (кто решил), approved_at (секунды epoch), payload_sha256 и approval_id")
    if dg != payload_digest(payload, target):
        raise SDError("файл решения относится к другой цели или телу запроса (sha256 не совпал): пересоздайте решение для этой заявки")
    t = time.time() if now is None else now
    if t - at > APPROVAL_TTL or at > t + 60:
        raise SDError(f"файл решения просрочен (живёт {APPROVAL_TTL // 60} минут) или время в будущем")
    return {"by": who.strip(), "id": aid}


def claim_approval(approval_id: str) -> Path:
    """Атомарно погасить решение до отправки: создание метки эксклюзивное, второй процесс получит отказ."""
    d = ledger_dir()
    unavailable = SDError("журнал решений недоступен для записи: без него одноразовость не гарантируется, отправка отменена")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:                                  # в том числе FileExistsError, когда на месте каталога файл (Windows)
        raise unavailable from None
    mark = d / f"approved-{approval_id}"
    try:
        with open(mark, "x", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except FileExistsError:
        raise SDError("это решение уже использовано (одноразовое)") from None
    except OSError:
        raise unavailable from None
    return mark


def _definitely_rejected(e: BaseException) -> bool:
    """Решение возвращаем только при доказанном отказе портала (HTTP 4xx). Таймаут, обрыв и 5xx дают неопределённый
    исход: заявка могла создаться, решение остаётся погашенным, результат сверяют по порталу."""
    return isinstance(e, SDError) and str(e).startswith("HTTP 4")


def release_claim(mark: Path) -> None:
    """Отправка не состоялась (ошибка до создания): вернуть решение, чтобы можно было повторить."""
    try:
        mark.unlink()
    except OSError:
        pass


class _SendLock:
    """Блокировка на пару заявитель и тема от проверки дубля до конца POST: два параллельных запуска не создадут два дубля."""
    def __init__(self, payload: dict, key: str | None = None):
        req = payload.get("request") or {}
        key = key or json.dumps([(req.get("requester") or {}).get("id"), req.get("subject")], ensure_ascii=False)
        self.path = ledger_dir() / ("lock-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24])
        self.token = uuid.uuid4().hex

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise SDError("каталог блокировок недоступен для записи, отправка отменена") from None
        try:
            if self.path.exists() and time.time() - self.path.stat().st_mtime > 3600:
                self.path.unlink()
            with open(self.path, "x", encoding="utf-8") as f:
                f.write(self.token)
        except FileExistsError:
            raise SDError("такая же заявка отправляется прямо сейчас другим запуском") from None
        except OSError:
            raise SDError("каталог блокировок недоступен для записи, отправка отменена") from None
        return self

    def __exit__(self, *a):
        try:
            if self.path.read_text(encoding="utf-8") == self.token:     # чужую (после устаревания) не удаляем
                self.path.unlink()
        except OSError:
            pass


def authorize_send(payload: dict, approve_file: str | None, what: str = "заявку", target: str = "requests"):
    """Интерактивное подтверждение человека (возвращает True) либо файл решения (возвращает решение), иначе False."""
    if approve_file:
        dec = check_approval(approve_file, payload, target)
        print(f"решение принято от: {dec['by']}")
        return dec
    return confirm_send(payload, what=what)


def find_duplicate(client: SDClient, payload: dict, window: int = 3600, now: float | None = None) -> dict | None:
    """Заявка с той же темой и тем же заявителем за последний час: повтор POST создал бы дубль."""
    req = payload.get("request") or {}
    since_ms = str(int(((time.time() if now is None else now) - window) * 1000))
    rows = fetch_requests(client, limit=5000, extra=[crit("created_time", since_ms, "greater than")])
    rid = (req.get("requester") or {}).get("id")
    for r in rows:
        if r.get("subject") != req.get("subject"):
            continue
        if rid and str((r.get("requester") or {}).get("id")) != str(rid):
            continue
        return r
    return None


def readback(client: SDClient, request_id: str, payload: dict) -> list:
    """Прочитать созданную заявку и сравнить с тем, что просили. Возвращает расхождения."""
    got = client.get(f"requests/{urllib.parse.quote(str(request_id), safe='')}").get("request") or {}
    req, diffs = payload.get("request") or {}, []
    for fld in ("template", "group", "priority", "technician", "requester"):
        want = req.get(fld) or {}
        have = got.get(fld) or {}
        for k in ("id", "name"):
            if want.get(k) is not None and str(have.get(k)) != str(want[k]):
                diffs.append(f"{fld}.{k}: просили {want[k]}, в портале {have.get(k)}")
    if got.get("subject") != req.get("subject"):
        diffs.append("тема в портале отличается от запрошенной")
    return diffs


def load_extra_criteria(path: str | None) -> list:
    if not path:
        return []
    try:
        v = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SDError(f"не удалось прочитать --criteria-json ({type(e).__name__})") from None
    if not isinstance(v, list) or not all(isinstance(x, dict) and "field" in x and "condition" in x for x in v):
        raise SDError("--criteria-json: нужен массив объектов с полями field и condition")
    return v


def make_client() -> SDClient:
    load_env()
    base, key = os.environ.get("SD_BASE_URL", ""), os.environ.get("SD_API_KEY", "")
    if not base or not key:
        raise SystemExit("Не заданы SD_BASE_URL и SD_API_KEY (в окружении или .env; шаблон в .env.example)")
    return SDClient(validate_base_url(base), key, os.environ.get("SD_AUTH_SCHEME", "authtoken"))


def main(argv=None, client: SDClient | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="список заявок по исполнителю, группе, статусу и периоду")
    for f in ("technician", "group", "status", "since", "until"):
        ls.add_argument(f"--{f}")
    ls.add_argument("--limit", type=int, default=200)
    ls.add_argument("--page-size", type=int, default=100)
    ls.add_argument("--criteria-json", help="файл с дополнительными search_criteria (массив)")
    ls.add_argument("--with-people", action="store_true", help="выводить заявителя и исполнителя (персональные данные)")
    ls.add_argument("--with-attachments", action="store_true", help="выводить вложения")
    ls.add_argument("--json", action="store_true")
    cr = sub.add_parser("create", help="создать заявку по шаблону (по умолчанию сухой прогон)")
    cr.add_argument("--template", required=True, help="псевдоним из конфига или точное имя шаблона")
    cr.add_argument("--subject", required=True)
    cr.add_argument("--description", default="")
    cr.add_argument("--group")
    cr.add_argument("--technician", help="имя техника или псевдоним из [technicians]; по умолчанию [defaults] technician")
    cr.add_argument("--priority", help="имя приоритета; по умолчанию [defaults] priority")
    cr.add_argument("--check", action="store_true", help="до отправки сверить шаблон, техника, группу и приоритет по порталу (только чтение)")
    cr.add_argument("--requester-id", help="id заявителя (структурой, не именем); по умолчанию [defaults] requester_id")
    cr.add_argument("--approve-file", help="файл решения человека вместо ввода в терминале (нужен SD_AGENT_SEND=1)")
    cr.add_argument("--allow-duplicate", action="store_true", help="не останавливаться на заявке с той же темой за последний час")
    cr.add_argument("--send", action="store_true", help="отправить после ручного подтверждения")
    cr.add_argument("--config")
    nt = sub.add_parser("note", help="добавить заметку к заявке (по умолчанию сухой прогон)")
    nt.add_argument("--request", required=True, help="номер заявки (id)")
    nt.add_argument("--text", required=True)
    nt.add_argument("--visible-to-requester", action="store_true", help="показать заметку заявителю (по умолчанию внутренняя)")
    nt.add_argument("--approve-file", help="файл решения человека вместо ввода в терминале (нужен SD_AGENT_SEND=1)")
    nt.add_argument("--send", action="store_true", help="отправить после ручного подтверждения")
    at = sub.add_parser("attach", help="прикрепить файл к заявке: upload, затем PUT списка вложений (по умолчанию сухой прогон)")
    at.add_argument("--request", required=True)
    at.add_argument("--file", required=True, help="один файл (до 20 МБ); для нескольких запускайте команду по разу")
    at.add_argument("--allow-duplicate", action="store_true", help="разрешить файл с тем же именем и размером, что уже в заявке")
    at.add_argument("--approve-file")
    at.add_argument("--send", action="store_true")
    up = sub.add_parser("update", help="поправить описание и сроки заявки (по умолчанию сухой прогон)")
    up.add_argument("--request", required=True)
    up.add_argument("--description")
    up.add_argument("--due", help="срок решения ГГГГ-ММ-ДД")
    up.add_argument("--start", help="плановое начало ГГГГ-ММ-ДД")
    up.add_argument("--end", help="плановое окончание ГГГГ-ММ-ДД")
    up.add_argument("--approve-file")
    up.add_argument("--send", action="store_true")
    ap_ = sub.add_parser("approve", help="записать решение человека для отправки без терминала (привязано к sha256 тела, живёт 2 часа)")
    ap_.add_argument("--digest", required=True, help="sha256 цели и тела из сухого прогона")
    ap_.add_argument("--by", required=True, help="кто принял решение")
    ap_.add_argument("--out", required=True)
    mt = sub.add_parser("meta", help="что реально есть в портале: шаблоны, техники, группы и приоритеты из последних заявок")
    mt.add_argument("--what", choices=("templates", "technicians", "observed", "all"), default="all")
    mt.add_argument("--name", help="часть имени для поиска техников (без неё список всех пользователей слишком велик)")
    mt.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "approve":
            Path(a.out).write_text(json.dumps({"approved_by": a.by, "approved_at": int(time.time()), "payload_sha256": a.digest,
                                               "approval_id": uuid.uuid4().hex}), encoding="utf-8")
            print(f"решение записано в {a.out}: действует {APPROVAL_TTL // 60} минут, одно использование, только для тела с этим sha256")
            return 0
        if a.cmd == "meta":
            c = client or make_client()
            out = {}
            if a.what in ("templates", "all"):
                try:
                    out["templates"] = [{"id": t.get("id"), "name": t.get("name")} for t in fetch_templates(c)]
                except SDError as e:
                    out["templates"] = f"недоступно: {e}"
            if a.what == "technicians" or (a.what == "all" and a.name):
                try:
                    out["technicians"] = [t["name"] for t in fetch_technicians(c, a.name)] if a.name else \
                        "укажите --name (часть имени): список всех пользователей портала слишком велик"
                except SDError as e:
                    out["technicians"] = f"недоступно: {e}"
            if a.what in ("observed", "all"):
                try:
                    out["observed"] = observed_values(c)
                except SDError as e:
                    out["observed"] = f"недоступно: {e}"
            if a.json:
                print(json.dumps(out, ensure_ascii=False))
            else:
                for k, v in out.items():
                    print(f"== {k}")
                    if isinstance(v, str):
                        print(v)
                    elif isinstance(v, dict):
                        for kk, vv in v.items():
                            print(f"{kk}: " + ", ".join(f"{n} ({cnt})" for n, cnt in sorted(vv.items(), key=lambda x: -x[1])))
                    else:
                        for it in v:
                            print(f'{it["id"]}\t{it["name"]}' if isinstance(it, dict) else it)
            return 0
        if a.cmd == "note":
            payload = build_note(a.request, a.text, a.visible_to_requester)
            target = f"requests/{urllib.parse.quote(str(a.request), safe='')}/notes"
            if not a.send:
                print("Сухой прогон, ничего не отправлено. Заметка к заявке " + str(a.request) + ", тело запроса:\n"
                      + json.dumps(payload, ensure_ascii=False, indent=1) + "\nsha256 цели и тела: " + payload_digest(payload, target))
                return 0
            dec = authorize_send(payload, a.approve_file, f"заметку к заявке {a.request}", target)
            if not dec:
                print("Отправка отменена.")
                return 1
            c = client or make_client()                     # ошибка окружения до того, как решение погашено
            mark = claim_approval(dec["id"]) if isinstance(dec, dict) else None
            try:
                c.post(target, payload)
            except BaseException as e:
                if mark and _definitely_rejected(e):
                    release_claim(mark)
                raise
            print(f"заметка добавлена к заявке {a.request}")
            return 0
        if a.cmd == "attach":
            if not str(a.request).isdigit() or int(a.request) <= 0:
                raise SDError("номер заявки должен быть положительным целым числом")
            a.request = str(int(a.request))                  # 0123 и 123 один замок и один адрес
            name, data = read_attachment(a.file)
            payload = attach_payload(name, data)
            target = f"requests/{urllib.parse.quote(str(a.request), safe='')}/upload"
            if not a.send:
                print("Сухой прогон, ничего не отправлено. Вложение к заявке " + str(a.request) + ": "
                      + json.dumps(payload["attach"], ensure_ascii=False) + "\nsha256 цели и тела: " + payload_digest(payload, target))
                return 0
            dec = authorize_send(payload, a.approve_file, f"вложение к заявке {a.request}", target)
            if not dec:
                print("Отправка отменена.")
                return 1
            c = client or make_client()                     # ошибка окружения до того, как решение погашено
            with _SendLock(payload, key="attach:" + str(a.request)):    # проверка дубля, upload и PUT одного запроса не пересекаются с другим запуском
                before = current_attachments(c, a.request)
                if not a.allow_duplicate and any(b.get("name") == name and _att_size(b) == str(len(data))
                                                 for b in before):
                    print(f"не отправлено: в заявке уже есть вложение {name} того же размера. Проверьте её, при необходимости --allow-duplicate.",
                          file=sys.stderr)
                    return 1
                mark = claim_approval(dec["id"]) if isinstance(dec, dict) else None
                try:
                    res = c.upload(target, name, data)
                except BaseException as e:
                    if mark and _definitely_rejected(e):
                        release_claim(mark)
                    raise
                aid = attachment_id(res)
                try:                                             # любой сбой после upload сообщает id загруженного вложения
                    known = []                                      # список читаем заново прямо перед PUT и берём только его: удалённое другим не возвращаем
                    for b in current_attachments(c, a.request):
                        if str(b["id"]) not in known:
                            known.append(str(b["id"]))
                    if aid not in known:
                        known.append(aid)
                    try:
                        c.put(f"requests/{urllib.parse.quote(str(a.request), safe='')}",
                              {"request": {"attachments": [{"id": i} for i in known]}})
                    except SDError as e:
                        raise SDError(f"файл загружен (id вложения {aid}), но не привязан к заявке: {e}") from None
                    seen = {str(b["id"]) for b in current_attachments(c, a.request)}
                    missing = [i for i in known if i not in seen]
                    if missing:
                        raise SDError(f"после привязки в заявке не видно вложений: {', '.join(missing)} (новое: {aid}); проверьте заявку")
                except SDError as e:
                    if str(aid) in str(e):
                        raise
                    raise SDError(f"вложение {aid} загружено, но привязка не подтверждена: {e}") from None
                except (OSError, TimeoutError) as e:
                    raise SDError(f"вложение {aid} загружено, но привязка не подтверждена ({type(e).__name__})") from None
            print(f"вложение видно в заявке {a.request}: {name} (id {aid})")
            return 0
        if a.cmd == "update":
            payload = build_update(a.description, a.due, a.start, a.end)
            target = f"requests/{urllib.parse.quote(str(a.request), safe='')}"
            if not payload["request"]:
                print("нечего менять: укажите --description, --due, --start или --end")
                return 1
            if not a.send:
                print("Сухой прогон, ничего не отправлено. Правка заявки " + str(a.request) + ", тело запроса:\n"
                      + json.dumps(payload, ensure_ascii=False, indent=1) + "\nsha256 цели и тела: " + payload_digest(payload, target))
                return 0
            dec = authorize_send(payload, a.approve_file, f"правку заявки {a.request}", target)
            if not dec:
                print("Отправка отменена.")
                return 1
            c = client or make_client()                     # ошибка окружения до того, как решение погашено
            mark = claim_approval(dec["id"]) if isinstance(dec, dict) else None
            try:
                c.put(target, payload)
            except BaseException as e:
                if mark and _definitely_rejected(e):
                    release_claim(mark)
                raise
            print(f"заявка {a.request} обновлена")
            return 0
        if a.cmd == "create":
            payload = build_create(load_config(a.config), a.template, a.subject, a.description, a.group,
                                   a.technician, a.priority, a.requester_id)
            if a.check:
                problems = check_create(client or make_client(), payload)
                for pr in problems:
                    print("замечание: " + pr)
                print("проверка по порталу: " + ("есть замечания" if problems else "замечаний нет"))
                if problems and a.send:
                    return 1
            if not a.send:
                print("Сухой прогон, ничего не отправлено. Тело запроса:\n" + json.dumps(payload, ensure_ascii=False, indent=1)
                      + "\nsha256 цели и тела: " + payload_digest(payload))
                return 0
            c = client or make_client()
            dec = authorize_send(payload, a.approve_file)
            if not dec:
                print("Отправка отменена.")
                return 1
            with _SendLock(payload):                       # после подтверждения: замок держится секунды, а не пока человек думает
                if not a.allow_duplicate:
                    dup = find_duplicate(c, payload)
                    if dup:
                        print(f'не отправлено: за последний час уже есть заявка с той же темой и заявителем (id={dup.get("id")}). '
                              "Проверьте её, при необходимости --allow-duplicate.", file=sys.stderr)
                        return 1
                mark = claim_approval(dec["id"]) if isinstance(dec, dict) else None
                try:
                    res = c.post("requests", payload)
                except BaseException as e:
                    if mark and _definitely_rejected(e):
                        release_claim(mark)
                    raise
            r = res.get("request") or {}
            print(f'создана заявка id={r.get("id")} display_id={r.get("display_id")}')
            if r.get("id"):
                try:
                    diffs = readback(c, r["id"], payload)
                    print("чтение обратно: " + ("совпало с запросом" if not diffs else "расхождения: " + "; ".join(diffs)))
                except SDError as e:
                    print(f"чтение обратно не удалось: {e}")
            return 0
        extra = load_extra_criteria(a.criteria_json)
        rows = fetch_requests(client or make_client(), a.limit, a.page_size, technician=a.technician, group=a.group,
                              status=a.status, since=a.since, until=a.until, extra=extra)
    except SDError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2
    items = [shape(r, a.with_people, a.with_attachments) for r in rows]
    if a.json:
        print(json.dumps(items, ensure_ascii=False))
    else:
        print("номер\tстатус\tгруппа\tтема")
        for it in items:
            print(f'{it.get("display_id") or it.get("id")}\t{it.get("status")}\t{it.get("group")}\t{it.get("subject")}')
        print(f"-- заявок: {len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
