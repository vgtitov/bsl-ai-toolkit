import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "public_check.py"


def run(*a, inp=None):
    return subprocess.run([sys.executable, str(SCRIPT), *a], capture_output=True, text=True, input=inp)


def test_self_test():
    assert "self-test ok" in run("--self-test").stdout


def test_clean_and_dirty_exit_codes(tmp_path):
    sl = tmp_path / "s.txt"
    sl.write_text("# коммент\nВымышленнаяОрг\n/конкурент\\d+/\n", encoding="utf-8")
    assert run("-", "--stoplist", str(sl), inp="Чистый текст про nonNegative.").returncode == 0
    r = run("-", "--stoplist", str(sl), inp="ок\nмы в вымышленнаяорг\n")
    assert r.returncode == 1 and "строка 2: стоп-лист" in r.stdout and "вымышленнаяорг" not in r.stdout.lower().replace("строка 2: стоп-лист", "")
    assert run("-", "--stoplist", str(sl), inp="как у Конкурент7").returncode == 1


def test_missing_file():
    assert run("/нет/такого.md").returncode == 2
