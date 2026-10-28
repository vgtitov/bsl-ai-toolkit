"""Тесты depersonalize.py (pytest; без сети, данные вымышленные)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(HERE))
import depersonalize as dp  # noqa: E402


def run(text, **kw):
    kw.setdefault("data_class", "code")
    kw.setdefault("ner", "off")
    kw.setdefault("destination", "local")
    return dp.depersonalize(text, **kw)


def test_contacts_and_ids_replaced():
    r = run("Пишите test@example.com, тел +375 29 123-45-67, УНП 190123456, сервер 10.1.2.3")
    assert r.ok
    for bad in ("test@example.com", "123-45-67", "190123456", "10.1.2.3"):
        assert bad not in r.clean


def test_connection_string_and_secret_block():
    r = run('Srvr="srv-prod-01";Ref="erp";Usr="ivanov";')
    assert r.ok and "srv-prod-01" not in r.clean and "erp" not in r.clean
    assert run('Srvr="s";Pwd="Secret123";').reason.startswith("secret")


def test_tokens_block_not_replace():
    assert run("token=" + "ghp_" + "a" * 36).reason.startswith("secret")
    assert run("password=hunter").reason.startswith("secret")


def test_code_changes_only_strings_and_comments():
    code = '// Автор: Иванов Иван Иванович\nПроцедура Тест()\n    Сообщить("test@example.com");\nКонецПроцедуры\n'
    r = run(code, kind="code", lang="bsl")
    assert r.ok and "Иванов" not in r.clean and "test@example.com" not in r.clean and r.clean.startswith("// Автор: ")
    assert "Процедура Тест()" in r.clean


def test_names_from_dictionary_with_declension():
    d = dp.Dictionary(persons=[["Иван Петров"]], orgs=["Ромашка"])
    r = run("Ивану Петрову передали, Ромашка согласна", dictionary=d, data_class="code")
    assert "Петрову" not in r.clean and "Ромашка" not in r.clean


def test_version_numbers_not_replaced_addresses_are():
    r = run('Версия платформы: 8.3.22.1, сервер 10.20.30.40')
    assert "8.3.22.1" in r.clean and "10.20.30.40" not in r.clean


def test_bom_roundtrip_via_cli(tmp_path):
    src = tmp_path / "m.bsl"
    src.write_bytes(b"\xef\xbb\xbf// mail: test@example.com\n")
    out = tmp_path / "o.bsl"
    res = subprocess.run([sys.executable, str(HERE / "depersonalize.py"), str(src), "--out", str(out), "--ner", "off"], capture_output=True)
    assert res.returncode == 0
    data = out.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf") and b"test@example.com" not in data


def test_cp1251_input_is_detected(tmp_path):
    src = tmp_path / "a.log"
    src.write_bytes("Почта test@example.com\n".encode("cp1251"))
    out = tmp_path / "o.log"
    assert subprocess.run([sys.executable, str(HERE / "depersonalize.py"), str(src), "--out", str(out), "--ner", "off"], capture_output=True).returncode == 0
    assert out.read_bytes() == "Почта [EMAIL_1]\n".encode("cp1251")


def test_restore_roundtrip():
    r = run("Почта test@example.com")
    assert dp.restore(r.clean, r.mapping) == "Почта test@example.com"


def test_directory_mode_and_unreadable_files(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.txt").write_text("test@example.com", encoding="utf-8")
    out = tmp_path / "out"
    res = subprocess.run([sys.executable, str(HERE / "depersonalize.py"), str(src), "--out-dir", str(out), "--ner", "off"], capture_output=True)
    assert res.returncode == 0 and (out / "a.txt").read_text(encoding="utf-8") == "[EMAIL_1]"


def test_linear_time_on_large_module():
    text = "".join(f'// Автор {i}\nПроцедура П{i}()\n    Сообщить("test{i}@example.com");\nКонецПроцедуры\n' for i in range(3000))
    t0 = time.monotonic()
    r = run(text, kind="code", lang="bsl", max_bytes=1 << 28, timeout=60)
    assert r.ok and time.monotonic() - t0 < 20


def test_report_has_reason_for_every_block():
    r = run("password=hunter")
    assert not r.ok and r.reason and r.report.get("reason") == r.reason
