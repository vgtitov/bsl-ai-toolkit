# /// script
# dependencies = ["fastavro>=1.9"]
# ///
"""Одно сообщение Kafka против Schema Registry: какой схемой оно записано и что получит контур,
который прочтёт его СВОЕЙ схемой. Только чтение, базы 1С не трогает.

  uv run scripts/kafka_msg.py schemas <топик> [--diff]
  uv run scripts/kafka_msg.py check <файл> [--topic <топик>] [--as latest|<версия>|id:<N>|<файл.avsc>]
  uv run scripts/kafka_msg.py fetch <топик> --partition <P> --offset <O> [--out <файл>]

Вход `check`:
  * сырые байты Confluent wire format (байт 00 + 4 байта id схемы + тело) — `.bin`;
  * запись из AKHQ (JSON с `value` и `valueSchemaId`/`schemaId`) — тело восстанавливается в байты схемой записи;
  * просто JSON значения (+ `--topic`) — проверка соответствия схеме.
Зачем `--as`: не каждый потребитель сверяет id схемы. Кто читает своей схемой без сверки, получит на
несовместимом сообщении либо ошибку, либо БЕЗ ошибки искажённые поля. `check` показывает, что именно.

env: KAFKA_SCHEMA_REGISTRY_URL; для `fetch` — AKHQ_URL, AKHQ_CLUSTER и AKHQ_COOKIE (cookie сессии
браузера после входа в AKHQ; секрет — только в .env/env, не в чат и не в git).
"""
import argparse
import base64
import datetime as dt
import decimal
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
try:
    import onec_ops_mcp as _ops
    _ops.load_dotenv_defaults()
except Exception:
    pass


# ---------- Schema Registry ----------

def _get_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Registry:
    def __init__(self, url=None):
        self.url = (url or os.environ.get("KAFKA_SCHEMA_REGISTRY_URL") or "").rstrip("/")
        if not self.url:
            raise SystemExit("нет KAFKA_SCHEMA_REGISTRY_URL (адрес Schema Registry) в .env/env")

    def versions(self, topic):
        return _get_json(f"{self.url}/subjects/{urllib.parse.quote(topic)}-value/versions")

    def version(self, topic, v):
        """{'id', 'version', 'schema'} версии (`latest` или номер)."""
        return _no_refs(_get_json(f"{self.url}/subjects/{urllib.parse.quote(topic)}-value/versions/{v}"))

    def by_id(self, sid):
        return _no_refs(_get_json(f"{self.url}/schemas/ids/{int(sid)}"))["schema"]


def _no_refs(info):
    # shortcut: схемы со ссылками на другие схемы (references) не разбираем; понадобятся — грузить их рекурсивно
    if info.get("references"):
        raise SystemExit("схема ссылается на другие схемы (references) — kafka_msg такие пока не разбирает")
    return info


def parse_schema(text):
    import fastavro
    return fastavro.parse_schema(json.loads(text) if isinstance(text, str) else text)


# ---------- вход ----------

def load_message(path: Path):
    """dict: writer_id (или None), payload (байты тела или None), value (значение или None), topic."""
    raw = path.read_bytes()
    if raw[:1] == b"\x00" and len(raw) >= 5:
        return {"writer_id": int.from_bytes(raw[1:5], "big"), "payload": raw[5:], "value": None, "topic": None}
    data = json.loads(raw.decode("utf-8-sig"))
    if isinstance(data, dict) and "value" in data and ("valueSchemaId" in data or "schemaId" in data):
        value = data["value"]
        if isinstance(value, str):
            value = json.loads(value)
        sid = data.get("valueSchemaId", data.get("schemaId"))
        return {"writer_id": int(sid) if sid is not None else None, "payload": None, "value": value,
                "topic": data.get("topic")}
    return {"writer_id": None, "payload": None, "value": data, "topic": None}


def to_avro_value(schema, v):
    """JSON (AKHQ или Avro-JSON с обёртками union) -> значение для fastavro по схеме записи."""
    if isinstance(schema, list):                                   # union
        if isinstance(v, dict) and len(v) == 1:                    # обёртка {"string": x} / {"ns.Rec": {...}}
            (k, inner), = v.items()
            for br in schema:
                name = br if isinstance(br, str) else br.get("name") or br.get("type")
                full = br.get("name") if isinstance(br, dict) else None
                if k in (name, full) or (isinstance(full, str) and full.endswith("." + k)):
                    return to_avro_value(br, inner)
        if v is None:
            return None
        for br in schema:
            if br == "null":
                continue
            try:
                return to_avro_value(br, v)
            except (ValueError, TypeError, decimal.InvalidOperation):
                continue
        return v
    if isinstance(schema, str):
        if schema in ("bytes", "fixed") and isinstance(v, str):
            return base64.b64decode(v)
        return v
    t, lt = schema.get("type"), schema.get("logicalType")
    if t == "record":
        v = v or {}
        missing = [f["name"] for f in schema["fields"] if f["name"] not in v and "default" not in f]
        if missing:                                                # иначе nullable-поле молча стало бы null
            raise ValueError(f"нет обязательных полей {missing} в записи {schema.get('name')}")
        return {f["name"]: to_avro_value(f["type"], v.get(f["name"], f.get("default")))
                for f in schema["fields"]}
    if t == "array":
        return [to_avro_value(schema["items"], x) for x in (v or [])]
    if t == "map":
        return {k: to_avro_value(schema["values"], x) for k, x in (v or {}).items()}
    if lt == "date" and isinstance(v, str):
        return dt.date.fromisoformat(v[:10])
    if lt in ("timestamp-millis", "timestamp-micros") and isinstance(v, str):
        return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    if lt == "decimal" and not isinstance(v, decimal.Decimal) and v is not None:
        return decimal.Decimal(str(v))
    if isinstance(t, (dict, list)):
        return to_avro_value(t, v)
    return to_avro_value(t, v) if isinstance(t, str) and t != schema else v


def encode(schema_text, value) -> bytes:
    import fastavro
    raw = json.loads(schema_text) if isinstance(schema_text, str) else schema_text
    buf = io.BytesIO()
    fastavro.schemaless_writer(buf, parse_schema(raw), to_avro_value(raw, value))
    return buf.getvalue()


def decode(schema_text, payload: bytes):
    """(значение, остаток байт). Читаем ЭТОЙ схемой как схемой записи — так читает потребитель без сверки id."""
    import fastavro
    fo = io.BytesIO(payload)
    val = fastavro.schemaless_reader(fo, parse_schema(schema_text), None)
    return val, len(payload) - fo.tell()


# ---------- сравнение ----------

def flatten(v, prefix=""):
    out = {}
    if isinstance(v, dict):
        for k, x in v.items():
            out.update(flatten(x, f"{prefix}.{k}" if prefix else k))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            out.update(flatten(x, f"{prefix}[{i}]"))
    else:
        out[prefix] = v
    return out


def diff_values(a, b, limit=20):
    fa, fb = flatten(a), flatten(b)
    keys = list(dict.fromkeys(list(fa) + list(fb)))
    return [(k, fa.get(k, "<нет>"), fb.get(k, "<нет>")) for k in keys if fa.get(k, "<нет>") != fb.get(k, "<нет>")][:limit]


def field_list(schema, prefix=""):
    """[(путь, тип)] полей записи по порядку (вложенные записи раскрываются)."""
    out = []
    if isinstance(schema, str):
        schema = json.loads(schema)
    if isinstance(schema, dict) and schema.get("type") == "record":
        for f in schema["fields"]:
            path = f"{prefix}.{f['name']}" if prefix else f["name"]
            out.append((path, _type_repr(f["type"])))
            inner = [x for x in (f["type"] if isinstance(f["type"], list) else [f["type"]]) if isinstance(x, dict)]
            for x in inner:
                if x.get("type") == "record":
                    out += field_list(x, path)
                elif x.get("type") == "array" and isinstance(x.get("items"), dict):
                    out += field_list(x["items"], path + "[]")
    return out


def _type_repr(t):
    if isinstance(t, list):
        return "|".join(_type_repr(x) for x in t)
    if isinstance(t, dict):
        return t.get("logicalType") or t.get("name") or t.get("type")
    return t


def schema_diff(old_text, new_text):
    """Изменения old -> new. danger=True — читать новое старой схемой без сверки id опасно."""
    old, new = field_list(old_text), field_list(new_text)
    od, nd = dict(old), dict(new)
    out = []
    for i, (p, t) in enumerate(new):
        if p not in od:
            parent = p.rsplit(".", 1)[0] if "." in p else ""
            if parent and parent.removesuffix("[]") not in od:     # поле внутри нового поля — уже учтено
                continue
            later = [q for q, _ in new[i + 1:] if (q.rsplit(".", 1)[0] if "." in q else "") == parent]
            # в конец — старая схема прочтёт префикс верно (новое поле потеряется); в середину — сдвиг байт
            out.append((f"+ {p}: {t}" + (" — вставлено в середину" if later else " — в конец"), bool(later)))
        elif od[p] != t:
            out.append((f"~ {p}: {od[p]} -> {t}", True))
    for p, t in old:
        if p not in nd:
            out.append((f"- {p}: {t}", True))
    # порядок полей задаёт порядок байт: перестановка общих полей одной записи — тоже сдвиг
    def _parent(q):
        return q.rsplit(".", 1)[0] if "." in q else ""
    for par in dict.fromkeys(_parent(q) for q, _ in old):
        o = [q for q, _ in old if _parent(q) == par and q in nd]
        n = [q for q, _ in new if _parent(q) == par and q in od]
        if o != n:
            out.append((f"↕ порядок полей {par or '<корень>'}: {', '.join(x.rsplit('.', 1)[-1] for x in n)}", True))
    return out


# ---------- команды ----------

def cmd_schemas(reg, topic, show_diff):
    vers = reg.versions(topic)
    prev = None
    for v in vers:
        info = reg.version(topic, v)
        print(f"версия {v}: id {info['id']}")
        if show_diff and prev is not None:
            for line, danger in schema_diff(prev, info["schema"]):
                print(("  ⚠ " if danger else "    ") + line)
        prev = info["schema"]


def resolve_as(reg, topic, spec):
    """Текст схемы потребителя по --as и её подпись."""
    p = Path(spec)
    if p.is_file():
        return p.read_text(encoding="utf-8-sig"), f"файл {p.name}"
    if spec.startswith("id:"):
        return reg.by_id(spec[3:]), spec
    if not topic:
        raise SystemExit("--as версия/latest требует --topic (или topic в записи AKHQ)")
    info = reg.version(topic, spec)
    return info["schema"], f"версия {info['version']} (id {info['id']})"


def cmd_check(reg, path, topic, as_spec):
    msg = load_message(path)
    topic = topic or msg["topic"]
    sid = msg["writer_id"]
    if sid is None:                                               # просто JSON — проверка по схеме
        if not topic:
            raise SystemExit("нет id схемы в сообщении: укажите --topic")
        text, label = resolve_as(reg, topic, as_spec or "latest")
        try:
            encode(text, msg["value"])
            print(f"OK: значение соответствует схеме {label}")
            return 0
        except Exception as e:
            print(f"НЕ соответствует схеме {label}: {e}")
            return 1
    writer = reg.by_id(sid)
    payload = msg["payload"] if msg["payload"] is not None else encode(writer, msg["value"])
    truth, rest = decode(writer, payload)
    print(f"схема записи: id {sid}")
    if rest:                                                       # тело длиннее схемы: битое или склеенное
        print(f"ОШИБКА: после чтения схемой записи остался хвост {rest} байт — тело не по этой схеме")
        return 4
    if not as_spec:
        print("верно прочитано схемой записи; укажите --as, чтобы проверить чтение схемой потребителя")
        return 0
    cons_text, label = resolve_as(reg, topic, as_spec)
    if json.loads(cons_text) == json.loads(writer):
        print(f"потребитель ({label}) читает той же схемой — прочтёт верно")
        return 0
    try:
        got, rest2 = decode(cons_text, payload)
    except Exception as e:
        print(f"потребитель ({label}): ЧТЕНИЕ УПАДЁТ — {type(e).__name__}: {e}")
        return 2
    diffs = diff_values(truth, got)
    if not diffs and not rest2:
        print(f"потребитель ({label}): прочтёт верно (байты совместимы)")
        return 0
    print(f"потребитель ({label}): прочтёт БЕЗ ОШИБКИ, НО С ИСКАЖЕНИЕМ"
          + (f"; непрочитанный хвост {rest2} байт" if rest2 else ""))
    for k, a, b in diffs:
        print(f"    {k}: должно {a!r}, получит {b!r}")
    return 3


def cmd_fetch(topic, partition, offset, out):
    """shortcut: адрес записи AKHQ 0.2x `/data/record/<раздел>/<смещение>` не сверен на нашем AKHQ; при 404 —
    скопировать запись из AKHQ вручную в файл."""
    url, cluster, cookie = (os.environ.get(k) for k in ("AKHQ_URL", "AKHQ_CLUSTER", "AKHQ_COOKIE"))
    if not (url and cluster and cookie):
        raise SystemExit("нужны AKHQ_URL, AKHQ_CLUSTER, AKHQ_COOKIE в .env (cookie — после входа в AKHQ в браузере)")
    host = urllib.parse.urlparse(url).hostname or ""
    if url.startswith("http://") and host not in ("localhost", "127.0.0.1") and os.environ.get("AKHQ_ALLOW_HTTP") != "1":
        raise SystemExit("AKHQ по http: cookie сессии и тело сообщения уйдут открытым текстом. "
                         "Только во внутренней сети и осознанно — AKHQ_ALLOW_HTTP=1")
    rec = _get_json(f"{url.rstrip('/')}/api/{cluster}/topic/{urllib.parse.quote(topic)}/data/record/"
                    f"{int(partition)}/{int(offset)}", headers={"Cookie": cookie, "Accept": "application/json"})
    rec = rec[0] if isinstance(rec, list) else rec
    rec.setdefault("topic", topic)
    Path(out).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"запись {topic}/{partition}/{offset} -> {out} (id схемы: {rec.get('valueSchemaId', rec.get('schemaId'))})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("schemas", help="версии схемы топика и id; --diff — что менялось и что опасно")
    p.add_argument("topic")
    p.add_argument("--diff", action="store_true")
    p = sub.add_parser("check", help="сообщение против схемы записи и схемы потребителя")
    p.add_argument("file", type=Path)
    p.add_argument("--topic")
    p.add_argument("--as", dest="as_spec", help="схема потребителя: latest | <версия> | id:<N> | <файл.avsc>")
    p = sub.add_parser("fetch", help="запись из AKHQ в файл")
    p.add_argument("topic")
    p.add_argument("--partition", required=True)
    p.add_argument("--offset", required=True)
    p.add_argument("--out", default="kafka_record.json")
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        return cmd_fetch(a.topic, a.partition, a.offset, a.out)
    reg = Registry()
    if a.cmd == "schemas":
        return cmd_schemas(reg, a.topic, a.diff)
    return cmd_check(reg, a.file, a.topic, a.as_spec)


if __name__ == "__main__":
    sys.exit(main() or 0)
