"""servicedesk.py attach: загрузка файла и привязка к заявке (upload, затем PUT списка вложений). Без сети."""
import json
import os
import sys
import uuid
import time
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import servicedesk as sd  # noqa: E402


class Fake:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, url, headers, data=None):
        self.calls.append((method, url, headers, data))
        return self.responses.pop(0)


UPLOAD_OK = (201, json.dumps({"attachment": {"name": "file.txt", "size": {"value": 5}, "content_url": "/api/v3/requests/attachments/509621/_download"}}))
GET_BEFORE = (200, json.dumps({"request": {"attachments": [{"id": "100", "name": "old.txt", "size": {"value": 3}}]}}))
PUT_OK = (200, json.dumps({"request": {"id": "5"}}))
GET_AFTER = (200, json.dumps({"request": {"attachments": [{"id": "100", "name": "old.txt", "size": {"value": 3}},
                                                         {"id": "509621", "name": "file.txt", "size": {"value": 5}}]}}))


def decision(tmp_path, payload, target):
    f = tmp_path / "dec.json"
    f.write_text(json.dumps({"approved_by": "Руководитель", "approved_at": int(time.time()),
                             "payload_sha256": sd.payload_digest(payload, target), "approval_id": uuid.uuid4().hex}), encoding="utf-8")
    return f


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SD_APPROVAL_LEDGER", str(tmp_path / "ledger"))
    monkeypatch.setenv("SD_AGENT_SEND", "1")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    p = tmp_path / "file.txt"
    p.write_bytes(b"hello")
    return tmp_path, p


def run(env, t, extra=(), name=None):
    tmp, p = env
    payload = sd.build_attach(p)
    f = decision(tmp, payload, "requests/5/upload")
    c = sd.SDClient("https://sd.example", "KEY", transport=t)
    return sd.main(["attach", "--request", "5", "--file", str(p), "--send", "--approve-file", str(f), *extra], client=c)


def test_dry_run_sends_nothing(env, capsys):
    t = Fake()
    c = sd.SDClient("https://sd.example", "KEY", transport=t)
    assert sd.main(["attach", "--request", "5", "--file", str(env[1])], client=c) == 0
    out = capsys.readouterr().out
    assert "Сухой прогон" in out and "file.txt" in out and "sha256" in out and t.calls == []


def test_upload_then_put_full_list_then_readback(env, capsys):
    t = Fake(GET_BEFORE, UPLOAD_OK, PUT_OK, GET_AFTER)
    assert run(env, t) == 0
    methods = [(m, u.split("/api/v3/")[1]) for m, u, _, _ in t.calls]
    assert methods == [("GET", "requests/5"), ("POST", "requests/5/upload"), ("PUT", "requests/5"), ("GET", "requests/5")]
    up = t.calls[1]
    assert up[2]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="input_file"' in up[3] and b'filename="file.txt"' in up[3] and b"hello" in up[3]
    assert up[3].count(b"Content-Disposition") == 1                       # поле только одно, addtoattachment запрещён
    body = urllib.parse.parse_qs(t.calls[2][3].decode())["input_data"][0]
    assert json.loads(body) == {"request": {"attachments": [{"id": "100"}, {"id": "509621"}]}}
    assert "вложение видно в заявке" in capsys.readouterr().out


def test_missing_in_readback_fails(env, capsys):
    t = Fake(GET_BEFORE, UPLOAD_OK, PUT_OK, GET_BEFORE)
    assert run(env, t) == 2
    assert "509621" in capsys.readouterr().err


def test_same_name_and_size_already_attached_is_refused(env, capsys):
    same = (200, json.dumps({"request": {"attachments": [{"id": "9", "name": "file.txt", "size": {"value": 5}}]}}))
    t = Fake(same)
    assert run(env, t) == 1 and len(t.calls) == 1
    assert "уже" in capsys.readouterr().err
    t2 = Fake(same, UPLOAD_OK, PUT_OK, (200, json.dumps({"request": {"attachments": [{"id": "9"}, {"id": "509621"}]}})))
    assert run(env, t2, ["--allow-duplicate"]) == 0


def test_upload_response_without_id_stops_before_put(env, capsys):
    t = Fake(GET_BEFORE, (201, json.dumps({"attachment": {"name": "file.txt", "content_url": "/api/v3/x"}})))
    assert run(env, t) == 2
    assert len(t.calls) == 2


def test_put_failure_reports_attachment_id(env, capsys):
    t = Fake(GET_BEFORE, UPLOAD_OK, (500, "{}"))
    assert run(env, t) == 2
    assert "509621" in capsys.readouterr().err


def test_decision_bound_to_file_content(env):
    tmp, p = env
    f = decision(tmp, sd.build_attach(p), "requests/5/upload")
    p.write_bytes(b"changed")
    t = Fake()
    c = sd.SDClient("https://sd.example", "KEY", transport=t)
    assert sd.main(["attach", "--request", "5", "--file", str(p), "--send", "--approve-file", str(f)], client=c) == 2
    assert not t.calls


def test_file_checks(env, tmp_path, capsys):
    t = Fake()
    c = sd.SDClient("https://sd.example", "KEY", transport=t)
    assert sd.main(["attach", "--request", "5", "--file", str(tmp_path / "none.bin")], client=c) == 2
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    assert sd.main(["attach", "--request", "5", "--file", str(empty)], client=c) == 2
    link = tmp_path / "link.txt"
    link.symlink_to(env[1])
    assert sd.main(["attach", "--request", "5", "--file", str(link)], client=c) == 2
    big = tmp_path / "big.bin"
    with open(big, "wb") as fh:
        fh.truncate(sd.MAX_ATTACH_BYTES + 1)
    assert sd.main(["attach", "--request", "5", "--file", str(big)], client=c) == 2
    assert not t.calls


def test_multipart_escapes_names():
    body, ctype = sd.multipart_file('a"b\r\nx.txt', b"x")
    assert b'\r\nx.txt"' not in body.split(b"\r\n\r\n")[0] and b'a\\"b' in body or b"a_b" in body
    body, _ = sd.multipart_file("отчёт.txt", b"x")
    head = body.split(b"\r\n\r\n")[0]
    assert b"filename*=UTF-8''" + urllib.parse.quote("отчёт.txt").encode() in head
    assert b'filename="' in head and "отчёт".encode() not in head.split(b"filename*")[0]


def test_attachment_id_extraction():
    assert sd.attachment_id({"attachment": {"content_url": "/api/v3/requests/attachments/509621/_download"}}) == "509621"
    assert sd.attachment_id({"attachment": {"content_url": "/api/v3/requests/5/attachments/77/_download"}}) == "77"
    with pytest.raises(sd.SDError):
        sd.attachment_id({"attachment": {}})
