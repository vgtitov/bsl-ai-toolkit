# /// script
# dependencies = ["mcp"]
# ///
"""MCP-сервер onec-data — слой ДАННЫХ живой ИБ 1С (read-only). Архитектура и лестница
уровней: docs/data-access-architecture.md; план: docs/data-access-implementation-plan.md.

Два бэкенда за одним MCP:
  L1–L2  стандартный OData (odata/standard.odata) — метаданные, чтение, виртуальные
         таблицы функциями Balance/Turnovers/SliceLast/SliceFirst; ноль доработок ИБ;
  L3     отладочный HTTP-сервис расширения ai_debug (extensions/ai_debug) — произвольный
         запрос/СКД-отчёт/настройки/вызов whitelist-метода ОТ ИМЕНИ пользователя.

Режимы выполнения (аргумент mode у L3-инструментов):
  "user"        — сеанс аутентифицированного пользователя, RLS честный (по умолчанию);
  "privileged"  — сервер выполняет в привилегированном режиме (RLS обходится) ТОЛЬКО если
                  это разрешено настройками расширения на конкретной базе (тест-контур).
Двойной гейт: env ONEC_DATA_FORBID_PRIVILEGED=1 запрещает privileged ещё на клиенте
(для прод-профилей), серверный гейт — aidbg_Настройки в расширении.

Безопасность: write-инструментов НЕТ by design; ПДн-маскирование включено по умолчанию
(ONEC_DATA_MASK_FIELDS, выключение ONEC_DATA_MASK_OFF=1 — только для тест-баз); лимит
строк ответа (ONEC_DATA_MAX_ROWS); пароли — только из env/файла учёток, в аргументах и
выводе инструментов их нет; вызовы L3 журналируются сервером расширения.

ENV: ONEC_DATA_BASE_URL (обязателен, напр. http://srv/erp_test) · ONEC_DATA_ODATA_PATH
(=/odata/standard.odata) · ONEC_DATA_DEBUG_PATH (=/hs/aidbg) · учётки: ONEC_DATA_LOGIN/
ONEC_DATA_PASSWORD (пользователь по умолчанию) и/или ONEC_DATA_USERS=путь к json
{alias: {"login":…,"password":…}} + ONEC_DATA_DEFAULT_USER=alias · ONEC_DATA_MAX_ROWS
(=200) · ONEC_DATA_TIMEOUT (=120) · ONEC_DATA_MASK_FIELDS / ONEC_DATA_MASK_OFF ·
ONEC_DATA_FORBID_PRIVILEGED · ONEC_DATA_PRESETS=путь к json преднастроек.
"""
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def load_dotenv_defaults(*dirs):
    """Подхватить KEY=VALUE из .env; установленные env не перетираются. По умолчанию —
    ТОЛЬКО каталог скрипта и корень репо (НЕ произвольный CWD: MCP, запущенный из чужого
    каталога, не должен подхватить подложенные креды/BASE_URL)."""
    here = os.path.dirname(os.path.abspath(__file__))
    search = list(dirs) or [here, os.path.dirname(here)]
    added = {}
    for d in search:
        path = os.path.join(d, ".env")
        if not os.path.isfile(path):
            continue
        try:
            lines = open(path, "r", encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v
                added[k] = v
    return added


# ── конфигурация ──────────────────────────────────────────────────────────────

# Поля с ПДн, маскируемые по умолчанию (подстрока в имени поля, регистронезависимо).
DEFAULT_MASK = ("фио,фамилия,имя,отчество,телефон,инн,кпп,снилс,паспорт,email,"
                "почта,адресэп,датарождения")
MASK_VALUE = "***"


def _int_env(name, default):
    """int из env с фолбэком на дефолт (мусор в переменной не должен ронять каждый вызов)."""
    try:
        return int(os.environ.get(name, "") or default)
    except (ValueError, TypeError):
        return default


def _cfg():
    base = os.environ.get("ONEC_DATA_BASE_URL", "").rstrip("/")
    return {
        "base_url": base,
        "odata_path": os.environ.get("ONEC_DATA_ODATA_PATH", "/odata/standard.odata"),
        "debug_path": os.environ.get("ONEC_DATA_DEBUG_PATH", "/hs/aidbg"),
        "max_rows": _int_env("ONEC_DATA_MAX_ROWS", 200),
        "timeout": _int_env("ONEC_DATA_TIMEOUT", 120),
    }


def _users():
    """Реестр учёток: {alias: (login, password)}. Источники: файл ONEC_DATA_USERS (json)
    и/или пара ONEC_DATA_LOGIN/PASSWORD (alias 'default'). Пароли наружу не отдаются."""
    reg = {}
    path = os.environ.get("ONEC_DATA_USERS", "")
    if path and os.path.isfile(path):
        try:
            data = json.loads(open(path, "r", encoding="utf-8").read())
            for alias, cred in data.items():
                if isinstance(cred, dict) and "login" in cred:
                    reg[alias] = (str(cred["login"]), str(cred.get("password", "")))
        except (OSError, ValueError) as e:
            reg["__error__"] = ("", "файл учёток не разобран: %s" % e)
    login = os.environ.get("ONEC_DATA_LOGIN", "")
    if login and "default" not in reg:
        reg["default"] = (login, os.environ.get("ONEC_DATA_PASSWORD", ""))
    return reg


def _resolve_user(user=""):
    """alias → (login, password, alias). Пустой alias → ONEC_DATA_DEFAULT_USER → 'default'.
    Ошибка разбора файла учёток НЕ блокирует рабочие alias'ы (напр. default из env-логина);
    она всплывает только если запрошенный alias действительно не найден."""
    reg = _users()
    file_error = reg.pop("__error__", None)
    alias = user or os.environ.get("ONEC_DATA_DEFAULT_USER", "") or "default"
    if alias not in reg:
        if file_error:
            raise RuntimeError("учётка '%s' недоступна: %s" % (alias, file_error[1]))
        known = ", ".join(sorted(reg)) or "(пусто)"
        raise RuntimeError("учётка '%s' не найдена; известные: %s "
                           "(ONEC_DATA_USERS / ONEC_DATA_LOGIN)" % (alias, known))
    login, password = reg[alias]
    return login, password, alias


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _http(method, url, login, password, body=None, timeout=None):
    """Один HTTP-вызов с Basic-auth. Возвращает (status, data) — data распарсен из JSON,
    иначе сырой текст. Ошибки сети/HTTP не бросаются, а возвращаются кодом+телом."""
    timeout = timeout or _cfg()["timeout"]
    headers = {
        "Authorization": "Basic " + base64.b64encode(
            ("%s:%s" % (login, password)).encode("utf-8")).decode("ascii"),
        "Accept": "application/json",
    }
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        status = e.code
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": "сеть/URL: %s" % getattr(e, "reason", e)}
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def _err(status, data, where):
    msg = data.get("error") if isinstance(data, dict) else str(data)[:500]
    return {"error": "%s: HTTP %s — %s" % (where, status, msg)}


# ── маскирование и лимиты ─────────────────────────────────────────────────────

# Маскирование ПДн по ЗНАЧЕНИЮ (regex) — защита от обхода маски по имени поля алиасом
# в запросе (`ВЫБРАТЬ ИНН КАК X`). Ловит распространённые форматы независимо от имени поля.
_VALUE_PII = [
    re.compile(r"\b\d{10}\b|\b\d{12}\b"),                       # ИНН юр./физ.
    re.compile(r"\b\d{3}-\d{3}-\d{3}[ -]?\d{2}\b"),             # СНИЛС
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                    # email
    re.compile(r"(?<!\d)(?:\+7|8)[\s(-]?\d{3}[\s)-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)"),  # телефон РФ
    re.compile(r"\b(?:\d[ -]?){16}\b"),                          # карта 16 цифр
]


def _mask_tokens():
    if os.environ.get("ONEC_DATA_MASK_OFF", "") == "1":
        return []
    raw = os.environ.get("ONEC_DATA_MASK_FIELDS", DEFAULT_MASK)
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def _mask_scalar(value):
    """Маскирование по значению для строк (ПДн-паттерны). Не-строки — как есть."""
    if not isinstance(value, str):
        return value
    for rx in _VALUE_PII:
        value = rx.sub(MASK_VALUE, value)
    return value


def _mask_value(key, value, tokens):
    # 1) имя поля совпало с ПДн-токеном → маскируем ЦЕЛИКОМ (любой тип, включая dict/list:
    #    ссылка {_ref,uuid,presentation}, структура ДанныеПаспорта={Серия,Номер} и т.п.)
    if any(t in str(key).lower() for t in tokens):
        return MASK_VALUE
    # 2) иначе — рекурсия по составным и маскирование по значению для скаляров
    if isinstance(value, dict):
        return {k: _mask_value(k, v, tokens) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(key, v, tokens) for v in value]
    return _mask_scalar(value)


def mask_deep(obj):
    """Прогнать любой JSON-совместимый объект через маскирование (по имени ключа +
    по значению). Для результатов, где нет «строк-словарей»: settings, call, raw."""
    tokens = _mask_tokens()
    if not tokens:
        return obj
    return _mask_value("", obj, tokens)


def mask_rows(rows):
    tokens = _mask_tokens()
    if not tokens:
        return rows
    return [{k: _mask_value(k, v, tokens) for k, v in r.items()} if isinstance(r, dict)
            else _mask_scalar(r) for r in rows]


def _clamp_rows(rows, top=0):
    """Обрезать до лимита; вернуть (строки, показано, всего_получено)."""
    limit = _cfg()["max_rows"]
    if top and top > 0:
        limit = min(top, limit)
    total = len(rows)
    return rows[:limit], min(total, limit), total


# ── L1–L2: OData ──────────────────────────────────────────────────────────────

def _odata_url(tail, query_pairs=None):
    c = _cfg()
    if not c["base_url"]:
        raise RuntimeError("не задан ONEC_DATA_BASE_URL (адрес публикации ИБ)")
    # кириллица в именах сущностей и значениях ОБЯЗАНА кодироваться (http.client шлёт ascii);
    # скобки/кавычки/запятые оставляем — синтаксис OData-функций и литералов
    url = c["base_url"] + c["odata_path"] + "/" + urllib.parse.quote(tail, safe="'(),=_-.")
    pairs = [("$format", "json")] + (query_pairs or [])
    return url + "?" + "&".join("%s=%s" % (k, urllib.parse.quote(str(v), safe="'(),=_-.$"))
                                for k, v in pairs if v not in ("", None, 0))


def _eff_top(top):
    """Эффективный серверный $top: отрицательное → 0 (значит max_rows); больше max — max."""
    top = max(0, int(top or 0))
    return min(top, _cfg()["max_rows"]) if top else _cfg()["max_rows"]


def odata_read(entity, filter="", select="", top=0, skip=0, order="", expand="", user=""):
    """Чтение сущности OData (Catalog_…, Document_…, InformationRegister_… и т.д.)."""
    login, password, alias = _resolve_user(user)
    top = max(0, int(top or 0))
    pairs = [("$filter", filter), ("$select", select), ("$orderby", order),
             ("$expand", expand), ("$top", _eff_top(top)),
             ("$skip", max(0, int(skip or 0)))]
    url = _odata_url(entity, pairs)
    status, data = _http("GET", url, login, password)
    if status != 200:
        return _err(status, data, "OData %s" % entity)
    rows = data.get("value", []) if isinstance(data, dict) else []
    rows, shown, total = _clamp_rows(rows, top)
    return {"user": alias, "entity": entity, "rows": mask_rows(rows),
            "shown": shown, "fetched": total,
            "note": "получено в ответе %d, показано %d (лимит ONEC_DATA_MAX_ROWS; "
                    "полное число строк в базе неизвестно при $top)" % (total, shown)}


_ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _odata_literal(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if _ISO_DT.match(s):
        return "datetime'%s'" % (s if "T" in s else s + "T00:00:00")
    if _UUID.match(s):
        return "guid'%s'" % s
    return "'%s'" % s.replace("'", "''")


def odata_virtual(register, func, params=None, select="", top=0, user=""):
    """Виртуальная таблица регистра функцией OData: Balance | Turnovers |
    BalanceAndTurnovers | SliceLast | SliceFirst. register — имя сущности OData
    (напр. AccumulationRegister_ТоварыНаСкладах), params — {Period: ISO-дата,
    Condition: строка условия, …}."""
    login, password, alias = _resolve_user(user)
    top = max(0, int(top or 0))
    args = ",".join("%s=%s" % (k, _odata_literal(v)) for k, v in (params or {}).items())
    tail = "%s_%s(%s)" % (register, func, args)
    pairs = [("$select", select), ("$top", _eff_top(top))]
    url = _odata_url(tail, pairs)
    status, data = _http("GET", url, login, password)
    if status != 200:
        return _err(status, data, "OData %s_%s" % (register, func))
    rows = data.get("value", []) if isinstance(data, dict) else []
    rows, shown, total = _clamp_rows(rows, top)
    return {"user": alias, "function": "%s_%s" % (register, func), "rows": mask_rows(rows),
            "shown": shown, "fetched": total}


def odata_metadata(mask="", entity="", user="", limit=200):
    """$metadata: без entity — список сущностей (фильтр по подстроке mask);
    с entity — свойства конкретной сущности (имя, тип, nullable)."""
    login, password, alias = _resolve_user(user)
    c = _cfg()
    if not c["base_url"]:
        raise RuntimeError("не задан ONEC_DATA_BASE_URL")
    url = c["base_url"] + c["odata_path"] + "/$metadata"
    status, data = _http("GET", url, login, password)
    if status != 200:
        return _err(status, data, "OData $metadata")
    text = data if isinstance(data, str) else json.dumps(data)
    # защита от XXE/billion-laughs: в легитимном $metadata 1С DTD/ENTITY не бывает
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        return {"error": "$metadata содержит DTD/ENTITY — парсинг отклонён (безопасность)"}
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return {"error": "не разобран $metadata: %s" % e}
    types = [el for el in root.iter() if el.tag.endswith("}EntityType") or el.tag == "EntityType"]
    if entity:
        for el in types:
            if el.get("Name") == entity:
                props = [{"name": p.get("Name"), "type": p.get("Type"),
                          "nullable": p.get("Nullable", "true")}
                         for p in el if p.tag.endswith("Property") and not p.tag.endswith("NavigationProperty")]
                return {"user": alias, "entity": entity, "properties": props}
        return {"error": "сущность '%s' не найдена в $metadata" % entity}
    names = [n for el in types if (n := el.get("Name"))]
    if mask:
        ml = mask.lower()
        names = [n for n in names if ml in n.lower()]
    return {"user": alias, "entities": names[:limit], "shown": min(len(names), limit),
            "total": len(names)}


# ── L3: отладочный сервис ai_debug ────────────────────────────────────────────

def _debug_post(op, payload, user="", mode="user") -> dict:
    if mode not in ("user", "privileged"):
        return {"error": "mode должен быть 'user' или 'privileged'"}
    # fail-closed: ЛЮБОЕ непустое значение флага запрещает privileged (не только "1")
    if mode == "privileged" and os.environ.get("ONEC_DATA_FORBID_PRIVILEGED", "").strip():
        return {"error": "привилегированный режим запрещён на этом клиенте "
                         "(ONEC_DATA_FORBID_PRIVILEGED задан) — профиль прод-контура"}
    login, password, alias = _resolve_user(user)
    c = _cfg()
    if not c["base_url"]:
        raise RuntimeError("не задан ONEC_DATA_BASE_URL")
    url = c["base_url"] + c["debug_path"] + "/" + op
    body = dict(payload)
    body["mode"] = mode
    status, data = _http("POST", url, login, password, body=body)
    if status != 200:
        return _err(status, data, "ai_debug/%s" % op)
    if not isinstance(data, dict):  # сервис обязан отвечать JSON-объектом
        return {"raw": _mask_scalar(str(data)[:2000]), "user": alias, "mode": mode}
    data.setdefault("user", alias)
    data["mode"] = mode
    return data


def debug_query(text, params=None, user="", mode="user", top=0) -> dict:
    """Произвольный текст запроса 1С через ai_debug; RLS — по режиму."""
    res = _debug_post("query", {"text": text, "params": params or {}, "top": _eff_top(top)},
                      user, mode)
    if "rows" in res:
        res["rows"] = mask_rows(res["rows"])
    return res


def debug_report(report, variant="", params=None, filters=None, user="", mode="user", top=0) -> dict:
    """СКД-отчёт конфигурации/расширения: вариант настроек + параметры + отборы →
    таблица значений. filters: [{field, kind, value}], kind — вид сравнения
    (Равно, ВСписке, Содержит …)."""
    res = _debug_post("report", {"report": report, "variant": variant,
                                 "params": params or {}, "filters": filters or [],
                                 "top": _eff_top(top)},
                      user, mode)
    if "rows" in res:
        res["rows"] = mask_rows(res["rows"])
    return res


def debug_settings(what, names=None, params=None, target_user="", user="", mode="user"):
    """Настройки ИБ: what = constants | functional_options | user_settings | access.
    names — имена констант/ФО/ключи настроек; params — параметры ФО;
    target_user — чей контекст смотрим (для user_settings/access)."""
    res = _debug_post("settings", {"what": what, "names": names or [],
                                   "params": params or {}, "target_user": target_user},
                      user, mode)
    # хранилища настроек/представления могут содержать ПДн — маскируем весь ответ,
    # кроме служебных ключей
    return _mask_result(res)


def debug_call(method, args=None, user="", mode="user"):
    """Вызов экспортного метода 'ОбщийМодуль.Функция' из whitelist расширения
    (aidbg_Настройки.РазрешенныеМетоды). Не входящие в whitelist сервер отклоняет."""
    res = _debug_post("call", {"method": method, "args": args or []}, user, mode)
    return _mask_result(res)  # result метода получения данных тоже может нести ПДн


def _mask_result(res) -> dict:
    """Маскировать содержательную часть ответа L3, не трогая служебные ключи."""
    if not isinstance(res, dict):
        return {"raw": mask_deep(res)}
    service = {"user", "mode", "alias", "error"}
    return {k: (v if k in service else mask_deep(v)) for k, v in res.items()}


def debug_health(user="") -> dict:
    """Самопроверка канала L3: версия расширения, под кем сеанс, что разрешено."""
    login, password, alias = _resolve_user(user)
    c = _cfg()
    if not c["base_url"]:
        raise RuntimeError("не задан ONEC_DATA_BASE_URL")
    status, data = _http("GET", c["base_url"] + c["debug_path"] + "/health", login, password)
    if status != 200:
        return _err(status, data, "ai_debug/health")
    if not isinstance(data, dict):
        return {"user": alias, "raw": str(data)[:2000]}
    data.setdefault("user", alias)  # единый служебный ключ 'user' (как у прочих L3)
    return data


# ── RLS-матрица ───────────────────────────────────────────────────────────────

def rls_probe(users, query_text="", params=None, entity="", filter="", top=50, sample=5):
    """Одна и та же выборка под несколькими пользователями (строго mode=user) →
    матрица «кто сколько/что видит». query_text → через ai_debug, иначе entity → OData."""
    if not users:
        return {"error": "укажи users — список alias учёток (ONEC_DATA_USERS)"}
    if not query_text and not entity:
        return {"error": "нужен query_text (ai_debug) или entity (OData)"}
    top = max(0, top)
    matrix, counts = {}, {}
    truncated = False  # хоть у кого-то счётчик упёрся в лимит → равенство недостоверно
    for alias in users:
        try:
            if query_text:
                res = debug_query(query_text, params=params, user=alias, mode="user", top=top)
            else:
                # у OData fetched = число строк ПОСЛЕ серверного $top, а не всего в базе
                res = odata_read(entity, filter=filter, top=top, user=alias)
        except RuntimeError as e:  # напр. alias не найден — не рушим всю матрицу
            res = {"error": str(e)}
        if "error" in res:
            matrix[alias] = {"error": res["error"]}
            counts[alias] = None
        else:
            rows = res.get("rows", [])
            cnt = int(res.get("fetched", len(rows)) or 0)
            limit = min(top, _cfg()["max_rows"]) if top else _cfg()["max_rows"]
            hit_limit = (not query_text) and cnt >= limit  # усечение возможно только по OData-пути
            if hit_limit:
                truncated = True
            matrix[alias] = {"count": cnt, "truncated": hit_limit, "sample": rows[:sample]}
            counts[alias] = cnt
    seen = [c for c in counts.values() if c is not None]
    if not seen:
        verdict = "вердикт невозможен — все запросы завершились ошибкой (см. matrix)"
    elif len(set(seen)) > 1:
        verdict = "число строк РАЗЛИЧАЕТСЯ по пользователям — RLS (или права) влияет на выборку"
    elif truncated:
        verdict = ("счётчики равны, но упёрлись в лимит строк (OData $top) — равенство "
                   "недостоверно; повтори с бо́льшим top или через query_text (честный fetched)")
    else:
        verdict = "у всех пользователей одинаковое число строк — эффекта RLS в этой выборке не видно"
    return {"matrix": matrix, "counts": counts, "verdict": verdict}


# ── преднастройки (пресеты) ───────────────────────────────────────────────────

def load_presets():
    """Файл ONEC_DATA_PRESETS (json): {имя: {kind: query|report|read|virtual|call,
    args: {…}, params_allowed: […], mode: user|privileged, user: alias, description}}."""
    path = os.environ.get("ONEC_DATA_PRESETS", "")
    if not path:
        return {}
    if not os.path.isfile(path):
        raise RuntimeError("файл пресетов не найден: %s" % path)
    return json.loads(open(path, "r", encoding="utf-8").read())


_PRESET_KINDS = {"query", "report", "read", "virtual", "call"}


def run_preset(name="", params=None) -> dict:
    """Без name — список пресетов; с name — выполнить: kind диктует инструмент, args —
    зашитые аргументы, снаружи подставляются ТОЛЬКО ключи из params_allowed."""
    presets = load_presets()
    if not name:
        return {"presets": [{"name": k, "kind": v.get("kind"),
                             "description": v.get("description", ""),
                             "mode": v.get("mode", "user"),
                             "params_allowed": v.get("params_allowed", [])}
                            for k, v in sorted(presets.items())]}
    if name not in presets:
        return {"error": "пресет '%s' не найден; есть: %s" % (name, ", ".join(sorted(presets)) or "(пусто)")}
    p = presets[name]
    kind = p.get("kind", "")
    if kind not in _PRESET_KINDS:
        return {"error": "пресет '%s': kind '%s' не из %s" % (name, kind, sorted(_PRESET_KINDS))}
    allowed = set(p.get("params_allowed", []))
    extra = {k: v for k, v in (params or {}).items()}
    illegal = sorted(set(extra) - allowed)
    if illegal:
        return {"error": "пресет '%s': параметры %s не разрешены (params_allowed=%s)"
                         % (name, illegal, sorted(allowed))}
    args = dict(p.get("args", {}))
    mode = p.get("mode", "user")
    user = p.get("user", "")
    if kind == "query":
        qp = dict(args.get("params", {}))
        qp.update(extra)
        return debug_query(args.get("text", ""), params=qp, user=user, mode=mode,
                           top=args.get("top", 0))
    if kind == "report":
        rp = dict(args.get("params", {}))
        rp.update(extra)
        return debug_report(args.get("report", ""), variant=args.get("variant", ""),
                            params=rp, filters=args.get("filters", []),
                            user=user, mode=mode, top=args.get("top", 0))
    if kind == "read":
        # плейсхолдер {Ключ} в OData-фильтре подставляется как ЭКРАНИРОВАННЫЙ литерал —
        # значение не может расширить условие (защита от инъекции в $filter, инвариант 7).
        flt = args.get("filter", "")
        for k, v in extra.items():
            flt = flt.replace("{%s}" % k, _odata_literal(v))
        return odata_read(args.get("entity", ""), filter=flt, select=args.get("select", ""),
                          top=args.get("top", 0), order=args.get("order", ""), user=user)
    if kind == "virtual":
        vp = dict(args.get("params", {}))
        vp.update(extra)
        return odata_virtual(args.get("register", ""), args.get("function", "Balance"),
                             params=vp, select=args.get("select", ""),
                             top=args.get("top", 0), user=user)
    call_args = list(args.get("args", []))
    for k, v in extra.items():
        call_args = [v if a == "{%s}" % k else a for a in call_args]
    return debug_call(args.get("method", ""), args=call_args, user=user, mode=mode)


# ── регистрация MCP ───────────────────────────────────────────────────────────

try:
    from mcp.server.fastmcp import FastMCP

    load_dotenv_defaults()
    mcp = FastMCP("onec-data")

    @mcp.tool()
    def data_health(user: str = "") -> dict:
        """Самопроверка слоя данных: доступность отладочного сервиса ai_debug, версия
        расширения, ПОД КЕМ выполняется сеанс, разрешён ли привилегированный режим на базе.
        Начинай отладочную сессию с этого вызова. Read-only."""
        return debug_health(user)

    @mcp.tool()
    def data_metadata(mask: str = "", entity: str = "", user: str = "") -> dict:
        """L1: сущности OData живой базы. Без entity — список имён (mask — фильтр по
        подстроке, напр. 'ТоварыНаСкладах'); с entity — свойства сущности (имя/тип).
        Для структуры КОДА используй onec-code; это — структура ДАННЫХ live-базы."""
        return odata_metadata(mask=mask, entity=entity, user=user)

    @mcp.tool()
    def data_read(entity: str, filter: str = "", select: str = "", top: int = 0,
                  skip: int = 0, order: str = "", expand: str = "", user: str = "") -> dict:
        """L2: чтение сущности OData под пользователем user (RLS честный): Catalog_…,
        Document_…, InformationRegister_…, AccumulationRegister_… . filter/select/order —
        синтаксис OData v3 ($filter: Поле eq 'Значение'). Ответ лимитирован и маскирован.
        Read-only."""
        return odata_read(entity, filter=filter, select=select, top=top, skip=skip,
                          order=order, expand=expand, user=user)

    @mcp.tool()
    def data_virtual(register: str, function: str, params: dict | None = None,
                     select: str = "", top: int = 0, user: str = "") -> dict:
        """L2: виртуальная таблица регистра через OData-функцию: function = Balance |
        Turnovers | BalanceAndTurnovers | SliceLast | SliceFirst. params: {Period:
        '2026-07-01T00:00:00', Condition: "Склад_Key eq guid'…'"}. Read-only."""
        return odata_virtual(register, function, params=params, select=select, top=top, user=user)

    @mcp.tool()
    def data_query(text: str, params: dict | None = None, user: str = "",
                   mode: str = "user", top: int = 0) -> dict:
        """L3: ПРОИЗВОЛЬНЫЙ текст запроса 1С (соединения, виртуальные таблицы с
        параметрами, ВТ) через расширение ai_debug — от имени user. mode='user' — RLS
        честный (отладка «что видит пользователь»); mode='privileged' — эталон «всё»
        (только если разрешено настройками расширения на базе; тест-контур). Параметры:
        примитивы; даты ISO-строкой; ссылки {"ref":"Справочник.Номенклатура","uuid":"…"};
        перечисления {"enum":"Перечисление.СтавкиНДС","value":"НДС20"}. Read-only."""
        return debug_query(text, params=params, user=user, mode=mode, top=top)

    @mcp.tool()
    def data_report(report: str, variant: str = "", params: dict | None = None,
                    filters: list | None = None, user: str = "", mode: str = "user",
                    top: int = 0) -> dict:
        """L3: сформировать СКД-отчёт конфигурации/расширения как ПОЛЬЗОВАТЕЛЬ его видит:
        report — имя отчёта, variant — вариант настроек (пусто = основной), params —
        параметры СКД, filters — [{field, kind, value}] (kind: Равно/ВСписке/Содержит/
        Больше/Меньше…). Результат — плоская таблица значений. Сценарий S2 «почему отчёт
        пустой»: выполни под пользователем инцидента, затем privileged — сравни. Read-only."""
        return debug_report(report, variant=variant, params=params, filters=filters,
                            user=user, mode=mode, top=top)

    @mcp.tool()
    def data_settings(what: str, names: list | None = None, params: dict | None = None,
                      target_user: str = "", user: str = "", mode: str = "user") -> dict:
        """L3: настройки, влияющие на поведение: what = constants (значения констант по
        именам) | functional_options (ФО, в т.ч. с параметрами через params) |
        user_settings (хранилища настроек: names=[КлючОбъекта|КлючНастроек], target_user)
        | access (роли ИБ + группы доступа БСП пользователя target_user). Read-only.
        ВАЖНО: target_user ≠ себе (чужие настройки/роли) требует АДМИН-учётки в user или
        mode=privileged — иначе платформа откажет по правам (вернётся ошибка 400)."""
        return debug_settings(what, names=names, params=params, target_user=target_user,
                              user=user, mode=mode)

    @mcp.tool()
    def data_call(method: str, args: list | None = None, user: str = "", mode: str = "user") -> dict:
        """L3: дёрнуть экспортный МЕТОД ПОЛУЧЕНИЯ ДАННЫХ 'ОбщийМодуль.Функция' (например,
        API цены/остатка/заполнения) с контрольными аргументами — проверить, что вернёт
        код под конкретным пользователем. Разрешены ТОЛЬКО методы из whitelist расширения
        (aidbg_Настройки.РазрешенныеМетоды на базе); остальное сервер отклоняет."""
        return debug_call(method, args=args, user=user, mode=mode)

    @mcp.tool()
    def data_rls_probe(users: list, query_text: str = "", params: dict | None = None,
                       entity: str = "", filter: str = "", top: int = 50) -> dict:
        """S3/S7: одна выборка под НЕСКОЛЬКИМИ пользователями (строго mode=user) —
        матрица «кто сколько видит» + вердикт, влияет ли RLS. users — alias'ы учёток из
        ONEC_DATA_USERS (добавь туда исследуемых пользователей). query_text → через
        ai_debug; либо entity(+filter) → через OData без доработок. Read-only."""
        return rls_probe(users, query_text=query_text, params=params, entity=entity,
                         filter=filter, top=top)

    @mcp.tool()
    def data_preset(name: str = "", params: dict | None = None) -> dict:
        """S9: преднастроенные выборки/отчёты/вызовы («дежурные проверки») из файла
        ONEC_DATA_PRESETS. Без name — список с описаниями; с name — выполнить, подставив
        только разрешённые параметры (params_allowed). Режим/пользователь запинены в
        пресете. Read-only."""
        return run_preset(name, params=params)

    if __name__ == "__main__":
        # транспорт: stdio (локальный Claude Code) | sse | streamable-http (docker-центр)
        _t = os.environ.get("MCP_TRANSPORT", "stdio")
        mcp.run(transport="sse" if _t == "sse"
                else "streamable-http" if _t == "streamable-http" else "stdio")
except ImportError:  # pragma: no cover — модуль импортируем без пакета mcp (для тестов)
    mcp = None
