# DISCIPLINE_ALLOW_TEST_EDIT: переведён на синтетические обезличенные фикстуры
"""CLI 1c-meta: подкоманды поверх ops (exit 0/2)."""
import shutil
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
CLI = Path(__file__).parents[2] / "bin" / "1c-meta"


def run(*args):
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True)


def test_detect(tmp_path):
    (tmp_path / "Configuration.xml").write_text("<x/>")
    r = run("detect", str(tmp_path))
    assert r.returncode == 0 and "CONFIGURATOR" in r.stdout


def test_detect_unknown(tmp_path):
    r = run("detect", str(tmp_path))
    assert r.returncode == 2


def test_template_add_column(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: синтетическая фикстура template.xml, якорь «Код»
    work = tmp_path / "template.xml"
    shutil.copy(FIXTURES / "template.xml", work)
    r = run("template", "add-column", str(work),
            "--after", "Код", "--header", "Сертификат",
            "--parameter", "Сертификат")
    assert r.returncode == 0, r.stderr
    r2 = run("template", "add-column", str(work),
             "--after", "Код", "--header", "Сертификат",
             "--parameter", "Сертификат")
    assert r2.returncode == 2  # дубль → предусловие
    assert "Ошибка предусловия" in r2.stderr


def test_subsystem_add_content(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: CLI-тест новой подкоманды subsystem add-content
    work = tmp_path / "subsystem.xml"
    shutil.copy(FIXTURES / "subsystem.xml", work)
    r = run("subsystem", "add-content", str(work), "--ref", "Document.Заказ")
    assert r.returncode == 0, r.stderr
    r2 = run("subsystem", "add-content", str(work), "--ref", "Document.Заказ")
    assert r2.returncode == 2  # дубль → предусловие
    assert "Ошибка предусловия" in r2.stderr


def test_role_grant(tmp_path):
    # DISCIPLINE_ALLOW_TEST_EDIT: CLI-тест подкоманды role grant
    work = tmp_path / "rights.xml"
    shutil.copy(FIXTURES / "rights.xml", work)
    r = run("role", "grant", str(work), "--ref", "Catalog.Справочник1",
            "--right", "Insert")
    assert r.returncode == 0, r.stderr
    r2 = run("role", "grant", str(work), "--ref", "ПлохаяСсылка", "--right", "Read")
    assert r2.returncode == 2
    assert "Ошибка предусловия" in r2.stderr
