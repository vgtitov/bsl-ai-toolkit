"""kafka_msg: схема записи vs схема потребителя без сверки id — верно / упадёт / молча исказит."""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("fastavro")

_SPEC = importlib.util.spec_from_file_location(
    "kafka_msg", Path(__file__).resolve().parents[1] / "scripts" / "kafka_msg.py")
km = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(km)

V1 = {"type": "record", "name": "Order", "fields": [
    {"name": "id", "type": "string"},
    {"name": "qty", "type": "long"},
]}
# v2: union-поле вставлено В СЕРЕДИНУ — как в боевом случае, когда long 123456 читался как 1
V2 = {"type": "record", "name": "Order", "fields": [
    {"name": "id", "type": "string"},
    {"name": "note", "type": ["null", "string"], "default": None},
    {"name": "qty", "type": "long"},
]}
# v3: поле добавлено В КОНЕЦ
V3 = {"type": "record", "name": "Order", "fields": V1["fields"] + [
    {"name": "createdAt", "type": {"type": "int", "logicalType": "date"}, "default": 0}]}


class FakeReg:
    def __init__(self):
        self.ids = {1: json.dumps(V1), 2: json.dumps(V2), 3: json.dumps(V3)}

    def by_id(self, sid):
        return self.ids[int(sid)]

    def version(self, topic, v):
        sid = 2 if v == "latest" else int(v)
        return {"id": sid, "version": sid, "schema": self.ids[sid]}

    def versions(self, topic):
        return [1, 2, 3]


def _wire(sid, schema, value):
    return b"\x00" + sid.to_bytes(4, "big") + km.encode(json.dumps(schema), value)


def test_v2_message_read_with_v1_is_silently_corrupted(tmp_path, capsys):
    f = tmp_path / "m.bin"
    f.write_bytes(_wire(2, V2, {"id": "A1", "note": None, "qty": 123456}))
    rc = km.cmd_check(FakeReg(), f, "Orders", "1")
    out = capsys.readouterr().out
    assert rc == 3 and "БЕЗ ОШИБКИ, НО С ИСКАЖЕНИЕМ" in out and "qty" in out


def test_same_schema_reads_fine(tmp_path, capsys):
    f = tmp_path / "m.bin"
    f.write_bytes(_wire(2, V2, {"id": "A1", "note": "x", "qty": 5}))
    assert km.cmd_check(FakeReg(), f, "Orders", "2") == 0


def test_field_appended_at_end_reads_prefix_correctly(tmp_path, capsys):
    f = tmp_path / "m.bin"
    f.write_bytes(_wire(3, V3, {"id": "A1", "qty": 7, "createdAt": "2026-11-03"}))
    rc = km.cmd_check(FakeReg(), f, "Orders", "1")
    out = capsys.readouterr().out
    # префикс совпал, но хвост тела остался непрочитанным — это видно и считается искажением
    assert rc == 3 and "непрочитанный хвост" in out and "qty" not in out


def test_akhq_record_with_union_wrappers(tmp_path, capsys):
    rec = {"topic": "Orders", "valueSchemaId": 2,
           "value": json.dumps({"id": "A1", "note": {"string": "x"}, "qty": 9})}
    f = tmp_path / "rec.json"
    f.write_text(json.dumps(rec), encoding="utf-8")
    assert km.cmd_check(FakeReg(), f, None, "2") == 0
    assert km.cmd_check(FakeReg(), f, None, "1") == 3


def test_plain_json_validation(tmp_path, capsys):
    f = tmp_path / "v.json"
    f.write_text(json.dumps({"id": "A1", "qty": "не число"}), encoding="utf-8")
    assert km.cmd_check(FakeReg(), f, "Orders", "1") == 1


def test_schema_diff_flags_middle_insert_only():
    d = dict((line, danger) for line, danger in km.schema_diff(json.dumps(V1), json.dumps(V2)))
    assert any("note" in k and "середину" in k and v for k, v in d.items())
    d3 = km.schema_diff(json.dumps(V1), json.dumps(V3))
    assert d3 and not any(danger for _, danger in d3)
