"""depersonalize.py: проверки через CLI на синтетических данных (реальных имён и значений здесь нет)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "depersonalize.py"
PII = "Заявку создал Иван Петров, тел +375 29 123-45-67, mail ivan.p@example.com, УНП 123456789, сервер 10.1.2.3\n"


ORG = "Тестовая-Ромашка".encode("utf-8")


def run(*args, stdin=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, env=env, input=stdin, timeout=120)


def write(tmp_path, name, data, enc="utf-8"):
    p = tmp_path / name
    p.write_bytes(data.encode(enc) if isinstance(data, str) else data)
    return p


def test_pii_removed_and_report_has_no_values(tmp_path):
    r = run(str(write(tmp_path, "a.txt", PII)))
    out, err = r.stdout.decode("utf-8"), r.stderr.decode("utf-8")
    assert r.returncode == 0
    for v in ("Петров", "123-45-67", "ivan.p@example.com", "123456789", "10.1.2.3"):
        assert v not in out and v not in err


def test_token_blocks_with_empty_stdout(tmp_path):
    token = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789AB"
    r = run(str(write(tmp_path, "b.txt", f"token {token}\n")))
    assert r.returncode == 3 and r.stdout == b""
    assert token not in r.stderr.decode("utf-8")


def test_bsl_bom_kept_and_password_identifiers_are_not_secrets(tmp_path):
    src = "Пароль = Параметры.Пароль;\nПараметры.Вставить(\"Пароль\", Пароль);\n// звонить Иван Петров 029 123-45-67\n"
    p = write(tmp_path, "m.bsl", b"\xef\xbb\xbf" + src.encode("utf-8"))
    out = tmp_path / "o.bsl"
    r = run(str(p), "--out", str(out))
    assert r.returncode == 0
    data = out.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    assert "Пароль = Параметры.Пароль;" in text and "123-45-67" not in text


def test_cp1251_in_and_out(tmp_path):
    p = write(tmp_path, "w.log", PII, enc="cp1251")
    out = tmp_path / "o.log"
    assert run(str(p), "--out", str(out)).returncode == 0
    data = out.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    assert "Петров" not in data.decode("cp1251")


def test_dictionary_only_when_explicit(tmp_path, monkeypatch):
    text = "Клиент ООО Тестовая-Ромашка, пользователь zzuser1.\n"
    f = write(tmp_path, "d.txt", text)
    d = write(tmp_path, "d.json", json.dumps({"persons": [], "orgs": ["Тестовая-Ромашка"], "users": ["zzuser1"],
                                                "hosts": [], "terms": []}, ensure_ascii=False))
    assert ORG in run(str(f)).stdout
    assert ORG not in run(str(f), "--dict", str(d)).stdout
    (tmp_path / ".depersonalize.dict.json").write_bytes(d.read_bytes())
    monkeypatch.chdir(tmp_path)
    assert ORG in run(str(f)).stdout  # неявного чтения из текущего каталога нет
    assert ORG not in run(str(f), "--dict-auto").stdout


def test_map_out_never_overwrites(tmp_path):
    f = write(tmp_path, "a.txt", PII)
    m = tmp_path / "map.json"
    assert run(str(f), "--map-out", str(m)).returncode == 0 and m.exists()
    assert run(str(f), "--map-out", str(m)).returncode == 2


def test_directory_mode_refuses_unsafe_outputs(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    write(src, "a.txt", PII)
    assert run(str(src), "--out-dir", str(src)).returncode == 2
    assert run(str(src), "--out-dir", str(src / "out")).returncode == 2
    out = tmp_path / "out"
    assert run(str(src), "--out-dir", str(out)).returncode == 0
    assert (out / "a.txt").exists() and "Петров" not in (out / "a.txt").read_text(encoding="utf-8")
